from marvis import history


def _use_tmp_state_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(history, "_SESSION_FILE", tmp_path / "session.json")


def test_load_current_session_returns_none_when_nothing_persisted(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    assert history.load_current_session() is None


def test_save_and_load_current_session_round_trips_started_flag(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_current_session("abc-123", started=True)
    assert history.load_current_session() == ("abc-123", True)


def test_load_current_session_treats_unstarted_session_as_such(monkeypatch, tmp_path):
    # Regression: a session id was persisted before the claude CLI confirmed creating
    # it, so a crash/close before that confirmation must not look "resumable".
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_current_session("abc-123", started=False)
    assert history.load_current_session() == ("abc-123", False)


def test_load_current_session_survives_corrupt_json(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history._SESSION_FILE.parent.mkdir(exist_ok=True)
    history._SESSION_FILE.write_text("not json", encoding="utf-8")
    assert history.load_current_session() is None


def test_append_and_load_transcript_round_trips_messages(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.append_message("session-1", "user", "hello")
    history.append_message("session-1", "marvis", "hi there")
    assert history.load_transcript("session-1") == [
        {"role": "user", "text": "hello"},
        {"role": "marvis", "text": "hi there"},
    ]


def test_append_message_skips_empty_text(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.append_message("session-1", "user", "")
    assert history.load_transcript("session-1") == []


def test_load_transcript_returns_empty_for_unknown_session(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    assert history.load_transcript("does-not-exist") == []


def test_list_sessions_marks_the_current_session(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_current_session("current-id", started=True)
    history.append_message("current-id", "user", "hi")
    history.append_message("other-id", "user", "hey")

    sessions = history.list_sessions()

    by_id = {s["session_id"]: s for s in sessions}
    assert by_id["current-id"]["current"] is True
    assert by_id["other-id"]["current"] is False


def test_list_sessions_skips_empty_transcripts(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    (tmp_path / "history_empty-session.jsonl").write_text("", encoding="utf-8")
    assert history.list_sessions() == []
