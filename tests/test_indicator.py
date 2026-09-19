import queue

from marvis import indicator


def test_get_text_input_returns_submitted_text(monkeypatch):
    monkeypatch.setattr(indicator, "_text_input", queue.Queue())
    indicator._text_input.put("hello")
    assert indicator.get_text_input(mode_check=lambda: True) == "hello"


def test_get_text_input_returns_none_when_mode_check_turns_falsy(monkeypatch):
    # Regression: switching away from Text mode while _run_turn was blocked in
    # get_text_input() used to hang forever -- it had no way to notice the mode had
    # changed, so the main loop never returned to poll push-to-talk again, making
    # backtick look dead until a restart.
    monkeypatch.setattr(indicator, "_text_input", queue.Queue())
    monkeypatch.setattr(indicator, "_TEXT_INPUT_POLL_TIMEOUT", 0.01)
    assert indicator.get_text_input(mode_check=lambda: False) is None
