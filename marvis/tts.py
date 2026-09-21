import queue
import re
import threading
import time
from dataclasses import dataclass, field

import langid.langid as langid
import miniaudio
import numpy as np
import sounddevice as sd

from . import indicator
from .config import config
from .error_log import debug_logger

# The module-level langid.classify()/set_languages() only expose an unnormalized,
# unbounded score (not a probability), so a real 0-1 confidence needs the underlying
# identifier constructed with norm_probs=True instead.
#
# Building this from the packed model string takes the better part of a second, so it
# runs in a background thread kicked off at import time instead of blocking the import
# itself -- callers that need it (_resolve_edge_voice, wait_until_ready) block on
# _lang_identifier_ready, which lets this overlap with stt's model load instead of
# running after it.
_lang_identifier: langid.langid.LanguageIdentifier | None = None
_lang_identifier_ready = threading.Event()


def _load_lang_identifier() -> None:
    global _lang_identifier
    identifier = langid.LanguageIdentifier.from_modelstring(langid.model, norm_probs=True)
    identifier.set_languages(["en", "es"])
    _lang_identifier = identifier
    _lang_identifier_ready.set()


threading.Thread(target=_load_lang_identifier, daemon=True).start()


def wait_until_ready() -> None:
    """Blocks until the language-identification model has finished loading."""
    _lang_identifier_ready.wait()

_CODE_FENCE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_MARKDOWN_EMPHASIS = re.compile(r"[*_]{1,3}")
_WHITESPACE = re.compile(r"\s{2,}")


def _strip_markdown(text: str) -> str:
    # Sentence splitting in assistant.py treats a bare newline as a boundary, so a fenced
    # code block's opening/closing ``` line routinely arrives here as its own isolated
    # "sentence" (the paired regex above only matches when both fences land in the same
    # call, e.g. a one-line block) -- stripped down to nothing rather than spoken as literal
    # backticks. Confirmed live 2026-09-19: a reply containing a code block froze the whole
    # app, tracked to this text reaching edge-tts unsanitized; see prepare()'s empty-text
    # guard below for the other half of that fix.
    text = _CODE_FENCE.sub(" ", text)
    text = _INLINE_CODE.sub(r"\1", text)
    text = text.replace("`", " ")
    return _WHITESPACE.sub(" ", _MARKDOWN_EMPHASIS.sub(" ", text)).strip()

interrupt_event = threading.Event()

_utterance_voice: str | None = None


def begin_utterance() -> None:
    """Reset the locked edge-tts voice; call once per new reply so language detection re-runs.
    Also tears down any output stream a previous reply left open (e.g. one that ended without a
    matching end_utterance), so every reply starts from a clean device."""
    global _utterance_voice
    _utterance_voice = None
    _close_stream()


def _resolve_edge_voice(text: str) -> str:
    global _utterance_voice
    if _utterance_voice is None:
        if _lang_identifier is None:
            _lang_identifier_ready.wait()
        lang, confidence = _lang_identifier.classify(text)
        is_spanish = lang == "es" and confidence >= config.spanish_confidence_threshold
        _utterance_voice = config.edge_voice_es if is_spanish else config.edge_voice
    return _utterance_voice

_FRAME_SAMPLES = 1024
_LEVEL_SCALE = 6.0
_N_BANDS = 5


def _levels_from_pcm16(raw: bytes) -> list[float]:
    if not raw:
        return [0.0] * _N_BANDS
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if samples.size < 2:
        return [0.0] * _N_BANDS
    spectrum = np.abs(np.fft.rfft(samples))
    bands = np.array_split(spectrum, _N_BANDS)
    return [min(1.0, float(np.sqrt(np.mean(b**2))) * _LEVEL_SCALE) if b.size else 0.0 for b in bands]


def _write_with_levels(stream, raw: bytes) -> bool:
    frame_bytes = _FRAME_SAMPLES * 2
    for offset in range(0, len(raw), frame_bytes):
        if interrupt_event.is_set():
            return False
        frame = raw[offset : offset + frame_bytes]
        indicator.set_levels(_levels_from_pcm16(frame))
        stream.write(frame)
    return True


@dataclass
class PreparedSpeech:
    """Audio for one sentence, synthesized in a background thread as soon as the text is known."""

    chunks: "queue.Queue[bytes | None]"
    sample_rate: int
    channels: int = 1
    text: str = ""  # diagnostics only, see debug_logger calls below
    created_at: float = field(default_factory=time.monotonic)


class _QueueSource(miniaudio.StreamableSource):
    """Feeds mp3 bytes to the miniaudio decoder as they arrive from the network."""

    def __init__(self) -> None:
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._buffer = bytearray()
        self._eof = False

    def push(self, data: bytes) -> None:
        self._queue.put(data)

    def end(self) -> None:
        self._queue.put(None)

    def read(self, num_bytes: int) -> bytes:
        while not self._eof and len(self._buffer) < num_bytes:
            chunk = self._queue.get()
            if chunk is None:
                self._eof = True
                break
            self._buffer.extend(chunk)
        result = bytes(self._buffer[:num_bytes])
        del self._buffer[:num_bytes]
        return result


_EDGE_SAMPLE_RATE = 24000


def _prepare_edge(text: str) -> PreparedSpeech:
    import edge_tts

    communicate = edge_tts.Communicate(text, _resolve_edge_voice(text), rate=config.edge_rate)
    source = _QueueSource()
    chunks: "queue.Queue[bytes | None]" = queue.Queue()

    def _fetch() -> None:
        try:
            for chunk in communicate.stream_sync():
                if interrupt_event.is_set():
                    break
                if chunk["type"] == "audio":
                    source.push(chunk["data"])
        finally:
            source.end()

    def _decode() -> None:
        try:
            pcm_stream = miniaudio.stream_any(
                source,
                source_format=miniaudio.FileFormat.MP3,
                output_format=miniaudio.SampleFormat.SIGNED16,
                nchannels=1,
                sample_rate=_EDGE_SAMPLE_RATE,
            )
            for frames in pcm_stream:
                if interrupt_event.is_set():
                    break
                if frames:
                    chunks.put(frames.tobytes())
        except miniaudio.DecodeError:
            if not interrupt_event.is_set():
                raise
        finally:
            chunks.put(None)

    threading.Thread(target=_fetch, daemon=True).start()
    threading.Thread(target=_decode, daemon=True).start()
    debug_logger.debug("PREPARE text=%r", text)
    return PreparedSpeech(chunks, _EDGE_SAMPLE_RATE, text=text)


def _empty_speech(text: str) -> PreparedSpeech:
    chunks: "queue.Queue[bytes | None]" = queue.Queue()
    chunks.put(None)
    return PreparedSpeech(chunks, _EDGE_SAMPLE_RATE, text=text)


def prepare(text: str) -> PreparedSpeech:
    """Kick off synthesis for one sentence in the background. Call as soon as the sentence's
    text is known, well before it's due to play, so the network/synthesis latency overlaps
    with whatever is currently playing instead of stalling playback."""
    stripped = _strip_markdown(text)
    # A "sentence" that's pure markdown noise (e.g. a lone code-fence line, see
    # _strip_markdown's comment) strips down to nothing -- skip the network call entirely
    # rather than asking edge-tts to synthesize empty text.
    if not stripped:
        return _empty_speech(text)
    return _prepare_edge(stripped)


# The output stream is kept open across every sentence in a reply and only torn down at the
# end (end_utterance) or on interrupt. Opening a stream is cheap (~6ms) but closing it costs
# ~215ms (measured) -- doing that per sentence was the entire audible gap between sentences.
_stream_lock = threading.Lock()
_stream: sd.RawOutputStream | None = None
_stream_format: tuple[int, int] | None = None


def _get_stream(sample_rate: int, channels: int) -> sd.RawOutputStream:
    global _stream, _stream_format
    with _stream_lock:
        if _stream is not None and _stream_format != (sample_rate, channels):
            _stream.stop()
            _stream.close()
            _stream = None
        if _stream is None:
            _stream = sd.RawOutputStream(samplerate=sample_rate, channels=channels, dtype="int16")
            _stream.start()
            _stream_format = (sample_rate, channels)
        return _stream


def _close_stream() -> None:
    global _stream, _stream_format
    with _stream_lock:
        if _stream is not None:
            _stream.stop()
            _stream.close()
            _stream = None
            _stream_format = None


def end_utterance() -> None:
    """Close the shared output stream once a whole reply has finished playing. Idempotent --
    safe to call when nothing is open."""
    _close_stream()


def play(prepared: PreparedSpeech) -> None:
    """Play back audio from prepare(), blocking until it finishes. The output stream is shared
    across the reply's sentences and left open here; the caller ends the reply with
    end_utterance(). An interrupt closes it immediately so playback is cut on the spot."""
    indicator.start_speaking()
    stream = None
    play_started = time.monotonic()
    first_chunk_logged = False
    interrupted = False
    try:
        while True:
            if interrupt_event.is_set():
                interrupted = True
                break
            try:
                chunk = prepared.chunks.get(timeout=0.1)
            except queue.Empty:
                continue
            if not first_chunk_logged:
                debug_logger.debug(
                    "PLAY text=%r lead=%.2fs wait_for_first_chunk=%.2fs",
                    prepared.text, play_started - prepared.created_at, time.monotonic() - play_started,
                )
                first_chunk_logged = True
            if chunk is None:
                break
            if stream is None:
                stream = _get_stream(prepared.sample_rate, prepared.channels)
            if not _write_with_levels(stream, chunk):
                interrupted = True
                break
    finally:
        # Between sentences the stream stays open (that's the whole point); only tear it down
        # when playback is actually ending -- on interrupt here, or via end_utterance() after
        # the reply's last sentence.
        if interrupted:
            _close_stream()
        indicator.stop_speaking()
