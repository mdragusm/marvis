"""Regression tests for session-id isolation across tab switches mid-reply.

All three tests in this file target the same root-cause pattern: code that reads
get_session_id() (a module-level global) at the END of some operation, rather than
capturing it at the START. A tab switch mid-operation mutates that global, so the
late read sees the wrong session.

  Bug A (FIXED):   reply persisted to wrong tab when user switches mid-stream.
  Bug B (OPEN):    _report_turn_failure() re-reads get_session_id() at call time,
                   so a failure recorded after a mid-turn switch lands in the new tab.
  Bug C (OPEN):    ask() flips _session_started back to True at stream end, clobbering
                   the flag for a new tab opened mid-reply, so the first message to that
                   tab tries --resume on a session that was never created and fails once.

Each test drives the real _run_turn / ask machinery (via monkeypatching rather than
importing internals) so future renames can't hide new instances of the pattern.
"""

import threading

import pytest

import marvis.llm as llm
from marvis import assistant, history


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _noop(*a, **kw):
    pass


def _make_fake_ask(chunks, switch_session_id=None, switch_after_chunk=0, monkeypatch=None):
    """Returns a fake ask() that yields `chunks`, and optionally calls switch_session
    partway through (after yielding `switch_after_chunk` real chunks), simulating a
    tab switch mid-reply."""
    def _fake_ask(text):
        for i, chunk in enumerate(chunks):
            if switch_session_id and i == switch_after_chunk and monkeypatch:
                llm.switch_session(switch_session_id)
            yield chunk
        yield None  # signals stream end
    return _fake_ask


def _wire_indicator(monkeypatch):
    """Silence all indicator side-effects so _run_turn can run without a webview window."""
    monkeypatch.setattr(assistant.indicator, "add_user_message", _noop)
    monkeypatch.setattr(assistant.indicator, "show", _noop)
    monkeypatch.setattr(assistant.indicator, "hide", _noop)
    monkeypatch.setattr(assistant.indicator, "start_marvis_message", _noop)
    monkeypatch.setattr(assistant.indicator, "append_marvis_message", _noop)
    monkeypatch.setattr(assistant.indicator, "start_listening", _noop)
    monkeypatch.setattr(assistant.indicator, "stop_listening", _noop)


def _wire_tts(monkeypatch):
    """Replace TTS so nothing is actually spoken and no audio device is needed."""
    monkeypatch.setattr(assistant, "begin_utterance", _noop)
    monkeypatch.setattr(assistant, "end_utterance", _noop)
    monkeypatch.setattr(assistant, "prepare", lambda text: text)
    monkeypatch.setattr(assistant, "play", _noop)
    # Disable the pipeline gate so _enqueue never blocks in a single-threaded test.
    import threading as _threading
    monkeypatch.setattr(assistant, "_prepare_gate", _threading.Semaphore(9999))


def _wire_history(monkeypatch):
    """Replace disk history with an in-memory dict; returns that dict."""
    store: dict[tuple, list] = {}

    def _append(sid, role, text):
        store.setdefault((sid, role), []).append(text)

    def _load(sid):
        msgs = []
        for (s, r), texts in store.items():
            if s == sid:
                for t in texts:
                    msgs.append({"role": r, "text": t})
        return msgs

    monkeypatch.setattr(assistant.history, "append_message", _append)
    monkeypatch.setattr(assistant.history, "load_transcript", _load)
    monkeypatch.setattr(assistant.history, "save_current_session", _noop)
    return store


def _wire_text_input(monkeypatch, text):
    """Feed a fixed user utterance into get_text_input so _run_turn picks it up."""
    monkeypatch.setattr(assistant.indicator, "get_text_input", lambda mode_check=None: text)


# ---------------------------------------------------------------------------
# Bug A (already fixed): reply lands in the correct tab after a mid-stream switch
# ---------------------------------------------------------------------------

def test_reply_persisted_to_original_tab_after_mid_stream_switch(monkeypatch, tmp_path):
    """Regression for the original bug report: switching tabs while a reply is
    streaming used to file the whole reply under the NEW tab because the persist
    call re-read get_session_id() at the end of the turn.

    This test PASSES after the fix in this session. It is here as a non-regression
    guard so the same mistake can't reappear silently.
    """
    _wire_indicator(monkeypatch)
    _wire_tts(monkeypatch)
    store = _wire_history(monkeypatch)

    original_id = "session-original"
    other_id = "session-other"
    llm._session_id = original_id
    llm._session_started = True

    def _fake_ask(text):
        yield "Hello. "
        # Simulate tab switch after first chunk -- mutates the global.
        llm.switch_session(other_id)
        yield "World."
        yield None

    monkeypatch.setattr(assistant, "ask", _fake_ask)
    monkeypatch.setattr(assistant, "get_session_id", lambda: llm._session_id)
    monkeypatch.setattr(assistant, "generate_title", lambda text: None)
    _wire_text_input(monkeypatch, "hi")

    from marvis.config import Mode
    assistant._run_turn(Mode.TEXT)

    # The reply must be under the ORIGINAL session, not the one we switched to.
    assert ("session-original", "marvis") in store, (
        "reply was not persisted to the original session"
    )
    assert ("session-other", "marvis") not in store, (
        "reply was incorrectly filed under the new tab"
    )


# ---------------------------------------------------------------------------
# Bug B (OPEN): _report_turn_failure re-reads get_session_id() at failure time
# ---------------------------------------------------------------------------

def test_report_turn_failure_records_to_session_active_at_failure_not_at_turn_start(monkeypatch):
    """_report_turn_failure() calls get_session_id() fresh each time it is invoked.
    If a tab switch happened before it's called, the failure message is recorded
    under the NEW tab, not the one where the turn was running.

    This test is expected to FAIL until _report_turn_failure is fixed to accept an
    explicit session_id (or capture the id before any switching can happen).
    """
    calls = []

    # Simulate: turn started in "session-A", then tab switched to "session-B" mid-turn.
    active_session = {"id": "session-B"}
    monkeypatch.setattr(assistant, "get_session_id", lambda: active_session["id"])

    monkeypatch.setattr(assistant, "begin_utterance", _noop)
    monkeypatch.setattr(assistant, "end_utterance", _noop)
    monkeypatch.setattr(assistant.indicator, "start_marvis_message", _noop)
    monkeypatch.setattr(assistant.indicator, "append_marvis_message", _noop)
    monkeypatch.setattr(
        assistant.history, "append_message",
        lambda sid, role, text: calls.append(("persist", sid))
    )
    monkeypatch.setattr(assistant, "prepare", lambda text: text)
    monkeypatch.setattr(assistant, "play", _noop)
    assistant.interrupt_event.clear()

    # Pass the session that was active at turn START -- the function must use this,
    # not re-read get_session_id() (which now returns "session-B" after the switch).
    assistant._report_turn_failure("session-A")

    persisted_sessions = [sid for _, sid in calls]
    assert persisted_sessions == ["session-A"], (
        f"failure message was recorded under {persisted_sessions!r} "
        f"but should be 'session-A' (the session active when the turn started)"
    )


# ---------------------------------------------------------------------------
# Bug C (OPEN): ask() writeback clobbers _session_started for a new tab
# ---------------------------------------------------------------------------

def test_ask_writeback_does_not_clobber_started_flag_of_new_tab(monkeypatch, tmp_path):
    """When a new tab is opened mid-reply (reset_session sets _session_started=False
    for the fresh tab), ask()'s end-of-stream code sets _session_started=True,
    overwriting the new tab's flag. The next turn to that tab then sends --resume
    on a session the claude CLI never created, failing once before self-healing.

    This test is expected to FAIL until ask() is fixed to not touch _session_started
    when the session has changed from the one it was invoked for.
    """
    saved_states: list[tuple] = []

    real_save = history.save_current_session

    def _capturing_save(sid, started):
        saved_states.append((sid, started))

    monkeypatch.setattr(llm.history, "save_current_session", _capturing_save)

    # Stub the claude CLI subprocess so ask() completes without launching anything.
    # The stdout must contain at least one valid stream event so ask() yields a chunk
    # before finishing -- the switch has to happen mid-iteration to be realistic.
    import subprocess as _subprocess
    import io
    import json as _json

    _text_event = _json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
    })

    class _FakeProcess:
        returncode = 0
        stdout = io.StringIO(_text_event + "\n")
        stderr = io.StringIO('')

        def wait(self): pass

    monkeypatch.setattr(_subprocess, "Popen", lambda *a, **kw: _FakeProcess())

    # Start in session-A (already started, so ask() will go into the else branch).
    original_id = "session-ask-orig"
    llm._session_id = original_id
    llm._session_started = True

    # Consume the ask() generator in a thread while simulating a mid-stream new_tab.
    new_tab_id = "session-new-tab"

    def _drive_ask():
        gen = llm.ask("hello")
        for i, chunk in enumerate(gen):
            if i == 0:
                # Simulate: user opens a new tab while the first chunk arrives.
                # This calls reset_session(), which sets _session_started=False for the
                # new tab.  ask()'s end-of-stream writeback must not overwrite that.
                llm.reset_session()
                llm._session_id = new_tab_id

    t = threading.Thread(target=_drive_ask, daemon=True)
    t.start()
    t.join(timeout=5)

    # After ask() completes, the new tab's _session_started must still be False.
    # (It was never actually started -- ask() just set it because it finished
    # without knowing the session changed underneath it.)
    assert not llm._session_started, (
        "ask() set _session_started=True for the new tab even though that tab's "
        "claude session was never actually created -- the next turn will send "
        "--resume on a non-existent session and fail once before self-healing"
    )
