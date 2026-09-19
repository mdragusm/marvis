import queue
import re
import threading
import time
import traceback

import keyboard

from . import history, indicator
from .audio import ptt_down, record_until_silence, record_while_key_held
from .config import Mode, config
from .error_log import logger as error_logger
from .llm import FLUSH_SIGNAL, ask, get_session_id, reset_session
from .stt import transcribe
from .tts import PreparedSpeech, begin_utterance, interrupt_event, play, prepare
from .wakeword import wait_for_wake_word

_mode = Mode.PUSH_TO_TALK
_lock = threading.Lock()

_MODE_CYCLE = [Mode.PUSH_TO_TALK, Mode.WAKE_WORD, Mode.TEXT]

_SENTENCE_BOUNDARY = re.compile(r"[.!?]+\s+|\n+")
# A run of 2+ dots (an ellipsis, "…" already normalized to "..." by llm.py's sanitizer) is
# excluded from counting as a sentence end -- it almost always marks a trailing-off or a
# pause *within* a thought (e.g. quoting "me... speculating"), not an actual sentence
# boundary, and splitting there produces an out-of-context fragment spoken on its own.
_ELLIPSIS = re.compile(r"^\.{2,}\s+$")

_CLEAR_PHRASES = {
    "clear the context",
    "clear context",
    "clear our conversation",
    "clear the conversation",
    "clear your memory",
    "start a new conversation",
    "start over",
    "forget everything",
    "forget everything we've talked about",
}


def _is_clear_command(text: str) -> bool:
    normalized = text.strip().strip(".!?").lower()
    return normalized in _CLEAR_PHRASES

# How many sentences may be synthesized concurrently ahead of playback. Since sentence text is
# known well before its turn to play, this overlaps TTS network/synthesis latency with the
# previous sentence's playback instead of stalling between every sentence.
_PIPELINE_DEPTH = 2
_prepare_gate = threading.Semaphore(_PIPELINE_DEPTH)

# How often the streaming loop re-checks interrupt_event while idle. Never speak an
# incomplete sentence -- a slow model and a model mid-tool-call are indistinguishable
# from here, so no flush timeout can separate them; see CHANGELOG 2026-09-17 (12).
_INTERRUPT_POLL_INTERVAL = 0.1


def _stream_to_queue(chunks, out_queue: "queue.Queue[str | None]") -> None:
    try:
        for chunk in chunks:
            out_queue.put(chunk)
    finally:
        out_queue.put(None)


def _split_sentences(buffer: str) -> tuple[list[str], str]:
    sentences = []
    last_end = 0
    for match in _SENTENCE_BOUNDARY.finditer(buffer):
        if _ELLIPSIS.match(match.group()):
            continue
        sentences.append(buffer[last_end:match.end()].strip())
        last_end = match.end()
    return sentences, buffer[last_end:]


def _enqueue(sentence_queue: "queue.Queue[PreparedSpeech | None]", sentence: str) -> None:
    _prepare_gate.acquire()
    try:
        prepared = prepare(sentence)
    except Exception:
        # A permit is only released by _speech_worker after playing its item, so a
        # prepare() failure must release it here or the pipeline deadlocks after
        # _PIPELINE_DEPTH leaks.
        _prepare_gate.release()
        error_logger.error("failed to prepare speech for %r:\n%s", sentence, traceback.format_exc())
        return
    sentence_queue.put(prepared)


def _speech_worker(prepared_queue: "queue.Queue[PreparedSpeech | None]") -> None:
    while True:
        prepared = prepared_queue.get()
        if prepared is None:
            return
        if not interrupt_event.is_set():
            play(prepared)
        _prepare_gate.release()


def get_mode() -> Mode:
    with _lock:
        return _mode


def toggle_mode() -> None:
    global _mode
    with _lock:
        next_index = (_MODE_CYCLE.index(_mode) + 1) % len(_MODE_CYCLE)
        _mode = _MODE_CYCLE[next_index]
        print(f"[marvis] mode -> {_mode.value}")
    indicator.set_mode(_mode.value)


def _run_turn(mode: Mode) -> None:
    if mode == Mode.PUSH_TO_TALK:
        while not ptt_down.is_set():
            if get_mode() != mode:
                break
            time.sleep(0.05)
        else:
            audio = record_while_key_held()
        if get_mode() != mode:
            return

        if audio.size == 0:
            return
        text = transcribe(audio)
    elif mode == Mode.WAKE_WORD:
        if not wait_for_wake_word(mode_check=lambda: get_mode() == mode):
            return
        audio = record_until_silence()

        if audio.size == 0:
            return
        text = transcribe(audio)
    else:
        text = indicator.get_text_input(mode_check=lambda: get_mode() == mode)
        if text is None:
            return

    if not text.strip():
        return

    if mode != Mode.TEXT:
        print(f"[you] {text}")
    indicator.add_user_message(text)
    history.append_message(get_session_id(), "user", text)

    if _is_clear_command(text):
        reset_session()
        interrupt_event.clear()
        begin_utterance()
        indicator.start_marvis_message()
        indicator.append_marvis_message("Starting fresh.")
        history.append_message(get_session_id(), "marvis", "Starting fresh.")
        play(prepare("Starting fresh."))
        indicator.hide()
        print()
        return

    indicator.show()
    interrupt_event.clear()
    begin_utterance()

    speech_queue: "queue.Queue[PreparedSpeech | None]" = queue.Queue()
    speech_thread = threading.Thread(target=_speech_worker, args=(speech_queue,), daemon=True)
    speech_thread.start()

    chunk_queue: "queue.Queue[str | None]" = queue.Queue()
    threading.Thread(target=_stream_to_queue, args=(ask(text), chunk_queue), daemon=True).start()

    buffer = ""
    full_reply = ""
    first_chunk = True
    interrupted = False
    stream_done = False
    while not stream_done:
        try:
            chunk = chunk_queue.get(timeout=_INTERRUPT_POLL_INTERVAL)
        except queue.Empty:
            if interrupt_event.is_set():
                interrupted = True
                break
            continue

        if chunk is None:
            stream_done = True
            continue

        if chunk == FLUSH_SIGNAL:
            # A tool call is starting right now, which can run far longer than waiting for
            # one more clause -- speak whatever's already been generated instead of holding
            # it hostage to a sentence boundary that may not arrive for a while.
            remainder = buffer.strip()
            if remainder:
                _enqueue(speech_queue, remainder)
            buffer = ""
            continue

        if first_chunk:
            # Deliberately not indicator.hide() here: the model is still generating (and
            # may hand off to a tool before the first sentence is even ready to speak), so
            # this stays in "thinking" -- visibly still working -- rather than idle. It only
            # flips to "speaking" once tts.play() actually starts the first sentence's audio.
            print("[marvis] ", end="", flush=True)
            indicator.start_marvis_message()
            first_chunk = False
        print(chunk, end="", flush=True)
        indicator.append_marvis_message(chunk)
        full_reply += chunk
        buffer += chunk
        sentences, buffer = _split_sentences(buffer)
        for sentence in sentences:
            if sentence:
                _enqueue(speech_queue, sentence)
        if interrupt_event.is_set():
            interrupted = True
            break

    if first_chunk:
        indicator.hide()
    remainder = buffer.strip()
    if remainder and not interrupted:
        _enqueue(speech_queue, remainder)

    speech_queue.put(None)
    speech_thread.join()
    # Only now is the turn actually over (every sentence has either played or been
    # skipped by an interrupt) -- safe to finally show idle instead of "thinking".
    indicator.hide()

    if full_reply.strip():
        history.append_message(get_session_id(), "marvis", full_reply.strip())

    print()


def _on_push_to_talk(event) -> bool:
    """Single source of truth for push-to-talk key state, driven by the suppressing hook's
    own callback (see `audio.ptt_down`'s docstring for why GetAsyncKeyState can't be used
    here) so detection can never disagree with what's being suppressed. Also sets
    `interrupt_event` on key-down to interrupt Marvis mid-speech. Always returns False to
    keep the key from leaking into whatever window has focus."""
    if event.event_type == keyboard.KEY_DOWN:
        ptt_down.set()
        interrupt_event.set()
    elif event.event_type == keyboard.KEY_UP:
        ptt_down.clear()
    return False


_ptt_hook = None


def _rehook_watchdog(interval: float = 20.0) -> None:
    """Periodically re-registers the suppressing push-to-talk hook. Windows can silently
    drop a low-level keyboard hook (e.g. under GIL contention) with no error and no
    recovery from the `keyboard` library, so re-arming it on a timer bounds how long a
    dropped hook can stay dead instead of requiring a full restart."""
    global _ptt_hook
    while True:
        time.sleep(interval)
        keyboard.unhook_key(_ptt_hook)
        _ptt_hook = keyboard.hook_key(config.push_to_talk_key, _on_push_to_talk, suppress=True)


_TURN_FAILURE_MESSAGE = "Sorry, something went wrong that turn."


def _report_turn_failure() -> None:
    """Speaks and records the same generic fallback a failed LLM call already gets (see
    llm.ask's "Sorry, I hit an error talking to Claude"), so any other turn-level exception
    fails loud instead of silently -- see run()'s except block for why that matters here."""
    interrupt_event.clear()
    begin_utterance()
    indicator.start_marvis_message()
    indicator.append_marvis_message(_TURN_FAILURE_MESSAGE)
    history.append_message(get_session_id(), "marvis", _TURN_FAILURE_MESSAGE)
    play(prepare(_TURN_FAILURE_MESSAGE))
    indicator.hide()


def run() -> None:
    global _ptt_hook
    indicator.set_mode(get_mode().value)
    keyboard.add_hotkey(config.mode_toggle_key, toggle_mode)

    _ptt_hook = keyboard.hook_key(config.push_to_talk_key, _on_push_to_talk, suppress=True)
    threading.Thread(target=_rehook_watchdog, daemon=True).start()

    print(
        f"[marvis] mode -> {get_mode().value} "
        f"({config.push_to_talk_key}=talk/interrupt, {config.mode_toggle_key}=switch mode)"
    )

    while True:
        mode = get_mode()
        try:
            _run_turn(mode)
        except Exception:
            # pythonw has no console, so an uncaught exception here would kill Marvis
            # silently -- log it, hide the indicator, and speak/record a fallback message
            # instead of leaving the process dead with no visible sign anything went wrong.
            error_logger.error("turn failed:\n%s", traceback.format_exc())
            indicator.hide()
            try:
                _report_turn_failure()
            except Exception:
                # Reporting the failure must not itself become an unreported failure.
                error_logger.error("failed to report turn failure:\n%s", traceback.format_exc())


if __name__ == "__main__":
    run()
