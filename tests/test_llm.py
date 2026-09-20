import io
import json

from marvis import llm


def test_reset_session_clears_started_flag_and_persists(monkeypatch, tmp_path):
    saved = {}
    monkeypatch.setattr(llm.history, "save_current_session", lambda sid, started: saved.update(id=sid, started=started))
    monkeypatch.setattr(llm, "_session_id", "old-id")
    monkeypatch.setattr(llm, "_session_started", True)

    llm.reset_session()

    assert llm.get_session_id() != "old-id"
    assert saved == {"id": llm.get_session_id(), "started": False}


def test_switch_session_marks_the_chosen_session_as_started(monkeypatch):
    saved = {}
    monkeypatch.setattr(llm.history, "save_current_session", lambda sid, started: saved.update(id=sid, started=started))

    llm.switch_session("some-past-session")

    assert llm.get_session_id() == "some-past-session"
    assert saved == {"id": "some-past-session", "started": True}


def test_sanitize_normalizes_smart_punctuation_to_ascii():
    # Regression motivation: curly quotes/dashes from the LLM's output render as
    # garbled characters in Marcelo's PowerShell console.
    assert llm._sanitize("‘hi’ — “there”…") == "'hi' - \"there\"..."


class _FakeProcess:
    def __init__(self, stdout_lines):
        self.stdout = io.StringIO("\n".join(stdout_lines) + "\n")
        self.stderr = io.StringIO("")
        self.returncode = 0

    def wait(self):
        pass


def test_ask_logs_and_skips_a_malformed_stream_line_instead_of_crashing(monkeypatch):
    # Regression: ask() runs on a background thread (assistant.py's _stream_to_queue) with no
    # except around it there -- an uncaught exception from a bad line here used to crash that
    # thread silently (its traceback goes to stderr, which main.py redirects to a null device),
    # abandoning the reply mid-sentence with no marvis_error.log entry at all.
    good_delta = json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
    })
    monkeypatch.setattr(llm.subprocess, "Popen", lambda *a, **k: _FakeProcess(["not valid json{{{", good_delta]))
    monkeypatch.setattr(llm, "_session_started", True)
    errors = []
    monkeypatch.setattr(llm.error_logger, "error", lambda *a, **k: errors.append(a))

    assert list(llm.ask("hello")) == ["hi"]
    assert len(errors) == 1


def test_ask_yields_a_fallback_and_logs_when_launching_the_cli_fails(monkeypatch):
    # Regression: ask() runs on the same unguarded background thread as the test above, and the
    # Popen() call itself had no try/except -- if launching the CLI ever throws (e.g. a future
    # claude-cli layout change breaking _resolve_claude() again, like the shim-only-npm-layout
    # incident this guards against), the exception used to propagate out of this generator
    # uncaught and die completely silently instead of surfacing as the usual spoken error.
    def _raise(*args, **kwargs):
        raise FileNotFoundError("no claude.exe")

    monkeypatch.setattr(llm.subprocess, "Popen", _raise)
    monkeypatch.setattr(llm, "_session_started", True)
    errors = []
    monkeypatch.setattr(llm.error_logger, "error", lambda *a, **k: errors.append(a))

    assert list(llm.ask("hello")) == ["\nSorry, I hit an error talking to Claude."]
    assert len(errors) == 1


def _raising_stream_lines():
    yield json.dumps({
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}},
    })
    raise RuntimeError("boom")


class _FakeProcessRaisingStdout:
    def __init__(self):
        self.stdout = _raising_stream_lines()
        self.stderr = io.StringIO("")
        self.returncode = 0

    def wait(self):
        pass


def test_ask_yields_a_fallback_and_logs_when_reading_the_stream_raises(monkeypatch):
    # Regression: an exception escaping the per-line try/except above (e.g. a bad byte from the
    # child breaking the text-mode stdout iterator itself, not just a bad JSON line) used to
    # propagate past the whole for-loop uncaught, same silent-thread-death risk as the launch
    # failure above.
    monkeypatch.setattr(llm.subprocess, "Popen", lambda *a, **k: _FakeProcessRaisingStdout())
    monkeypatch.setattr(llm, "_session_started", True)
    errors = []
    monkeypatch.setattr(llm.error_logger, "error", lambda *a, **k: errors.append(a))

    # Two log entries: the raised exception itself, plus the existing is_error path's own
    # _log_claude_error call (reused deliberately -- see the comment in llm.py's ask()).
    assert list(llm.ask("hello")) == ["hi", "\nSorry, I hit an error talking to Claude."]
    assert len(errors) == 2


class _FakeCompletedProcess:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_generate_title_strips_quotes_and_whitespace(monkeypatch):
    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(stdout='  "Trip Planning"\n'))
    assert llm.generate_title("help me plan a trip") == "Trip Planning"


def test_generate_title_returns_none_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(llm.subprocess, "run", lambda *a, **k: _FakeCompletedProcess(returncode=1, stderr="boom"))
    errors = []
    monkeypatch.setattr(llm.error_logger, "error", lambda *a, **k: errors.append(a))

    assert llm.generate_title("hello") is None
    assert len(errors) == 1


def test_generate_title_returns_none_when_subprocess_raises(monkeypatch):
    # Same silent-failure risk as ask()'s own Popen guard -- this runs on a background
    # thread from assistant.py with no other except around it.
    def _raise(*args, **kwargs):
        raise FileNotFoundError("no claude.exe")

    monkeypatch.setattr(llm.subprocess, "run", _raise)
    errors = []
    monkeypatch.setattr(llm.error_logger, "error", lambda *a, **k: errors.append(a))

    assert llm.generate_title("hello") is None
    assert len(errors) == 1


def test_report_usage_reports_whichever_window_is_closer_to_its_cap(monkeypatch):
    reported = []
    monkeypatch.setattr(llm.indicator, "set_usage", lambda utilization, label: reported.append((utilization, label)))

    llm._report_usage({"unifiedWindows": {"five_hour": {"utilization": 0.04}, "seven_day": {"utilization": 0.78}}})

    assert reported == [(0.78, "7d")]


def test_report_usage_does_nothing_without_utilization_data(monkeypatch):
    reported = []
    monkeypatch.setattr(llm.indicator, "set_usage", lambda *a: reported.append(a))

    llm._report_usage({})

    assert reported == []


def test_ask_reports_usage_from_a_rate_limit_event_in_the_stream(monkeypatch):
    rate_limit_line = json.dumps({
        "type": "rate_limit_event",
        "rate_limit_info": {"unifiedWindows": {"five_hour": {"utilization": 0.1}, "seven_day": {"utilization": 0.62}}},
    })
    monkeypatch.setattr(llm.subprocess, "Popen", lambda *a, **k: _FakeProcess([rate_limit_line]))
    monkeypatch.setattr(llm, "_session_started", True)
    reported = []
    monkeypatch.setattr(llm.indicator, "set_usage", lambda utilization, label: reported.append((utilization, label)))

    list(llm.ask("hello"))

    assert reported == [(0.62, "7d")]
