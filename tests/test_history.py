from marvis import history


def _use_tmp_state_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(history, "_SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(history, "_TABS_FILE", tmp_path / "tabs.json")
    monkeypatch.setattr(history, "_TITLES_FILE", tmp_path / "titles.json")


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


def test_is_blank_true_for_session_with_no_messages(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    assert history.is_blank("never-used") is True


def test_is_blank_false_once_a_message_exists(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.append_message("session-1", "user", "hi")
    assert history.is_blank("session-1") is False


def test_reset_tabs_collapses_the_strip_to_one_tab(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_tab_order(["old-1", "old-2"])
    history.reset_tabs("fresh-id")
    assert history.load_tab_order() == ["fresh-id"]


def test_get_tabs_labels_blank_session_as_new_conversation(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_tab_order(["session-1"])
    tabs = history.get_tabs("session-1")
    assert tabs == [{"session_id": "session-1", "label": "New conversation", "current": True}]


def test_get_tabs_uses_first_user_message_as_label(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_tab_order(["session-1"])
    history.append_message("session-1", "user", "what's the weather")
    tabs = history.get_tabs("session-1")
    assert tabs[0]["label"] == "what's the weather"


def test_get_tabs_adds_missing_current_session_to_the_strip(monkeypatch, tmp_path):
    # Regression: switching in a session id that isn't already tracked (e.g. via
    # MARVIS_RESUME_SESSION) must not leave the active conversation with no tab at all.
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_tab_order(["session-1"])
    tabs = history.get_tabs("session-2")
    assert [t["session_id"] for t in tabs] == ["session-1", "session-2"]
    assert history.load_tab_order() == ["session-1", "session-2"]


def test_add_tab_appends_by_default(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_tab_order(["session-1"])
    history.add_tab("session-2")
    assert history.load_tab_order() == ["session-1", "session-2"]


def test_add_tab_inserts_after_given_session(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_tab_order(["session-1", "session-3"])
    history.add_tab("session-2", after="session-1")
    assert history.load_tab_order() == ["session-1", "session-2", "session-3"]


def test_add_tab_does_not_duplicate_an_already_open_session(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_tab_order(["session-1", "session-2"])
    history.add_tab("session-2")
    assert history.load_tab_order() == ["session-1", "session-2"]


def test_replace_tab_swaps_a_session_in_place(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_tab_order(["session-1", "session-2"])
    history.replace_tab("session-1", "session-3")
    assert history.load_tab_order() == ["session-3", "session-2"]


def test_remove_tab_drops_the_given_session(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_tab_order(["session-1", "session-2"])
    remaining = history.remove_tab("session-1")
    assert remaining == ["session-2"]
    assert history.load_tab_order() == ["session-2"]


def test_save_and_load_titles_round_trip(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_title("session-1", "Trip Planning")
    assert history.load_titles() == {"session-1": "Trip Planning"}


def test_get_tabs_prefers_a_generated_title_over_the_first_message(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    history.save_tab_order(["session-1"])
    history.append_message("session-1", "user", "help me plan a trip to Japan")
    history.save_title("session-1", "Japan Trip Planning")

    tabs = history.get_tabs("session-1")

    assert tabs[0]["label"] == "Japan Trip Planning"
