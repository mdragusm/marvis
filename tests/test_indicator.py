import queue

from marvis import history, indicator, llm


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


def _use_tmp_state_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "_STATE_DIR", tmp_path)
    monkeypatch.setattr(history, "_SESSION_FILE", tmp_path / "session.json")
    monkeypatch.setattr(history, "_TABS_FILE", tmp_path / "tabs.json")
    monkeypatch.setattr(history, "_TITLES_FILE", tmp_path / "titles.json")


def test_init_tabs_starts_a_fresh_single_tab_strip(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "_session_id", "launch-id")
    history.save_tab_order(["stale-1", "stale-2"])

    result = indicator._Api().init_tabs()

    assert result == {
        "tabs": [{"session_id": "launch-id", "label": "New conversation", "current": True}],
        "transcript": [],
    }


def test_new_tab_adds_a_second_tab_and_activates_it(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "_session_id", "tab-1")
    monkeypatch.setattr(llm, "_session_started", True)
    history.save_tab_order(["tab-1"])
    history.append_message("tab-1", "user", "hi")

    result = indicator._Api().new_tab()

    ids = [t["session_id"] for t in result["tabs"]]
    assert ids[0] == "tab-1"
    assert len(ids) == 2
    assert ids[1] == llm.get_session_id()
    assert result["tabs"][1]["current"] is True
    assert result["transcript"] == []


def test_select_tab_switches_active_session_and_returns_its_transcript(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "_session_id", "tab-1")
    history.save_tab_order(["tab-1", "tab-2"])
    history.append_message("tab-2", "user", "hello from tab 2")

    result = indicator._Api().select_tab("tab-2")

    assert llm.get_session_id() == "tab-2"
    by_id = {t["session_id"]: t for t in result["tabs"]}
    assert by_id["tab-2"]["current"] is True
    assert by_id["tab-1"]["current"] is False
    assert result["transcript"] == [{"role": "user", "text": "hello from tab 2"}]


def test_close_tab_activates_the_previous_tab_when_closing_the_active_one(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "_session_id", "tab-2")
    history.save_tab_order(["tab-1", "tab-2"])

    result = indicator._Api().close_tab("tab-2")

    assert llm.get_session_id() == "tab-1"
    assert [t["session_id"] for t in result["tabs"]] == ["tab-1"]


def test_close_tab_activates_the_right_neighbor_when_closing_a_middle_tab(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "_session_id", "tab-2")
    history.save_tab_order(["tab-1", "tab-2", "tab-3"])

    result = indicator._Api().close_tab("tab-2")

    assert llm.get_session_id() == "tab-3"
    assert [t["session_id"] for t in result["tabs"]] == ["tab-1", "tab-3"]


def test_close_tab_opens_a_fresh_blank_tab_when_closing_the_only_one(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "_session_id", "tab-1")
    history.save_tab_order(["tab-1"])

    result = indicator._Api().close_tab("tab-1")

    assert llm.get_session_id() != "tab-1"
    assert len(result["tabs"]) == 1
    assert result["tabs"][0]["current"] is True
    assert result["tabs"][0]["label"] == "New conversation"


def test_select_history_reuses_a_blank_current_tab(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "_session_id", "blank-tab")
    history.save_tab_order(["blank-tab"])
    history.append_message("past-session", "user", "an old chat")

    result = indicator._Api().select_history("past-session")

    assert llm.get_session_id() == "past-session"
    assert [t["session_id"] for t in result["tabs"]] == ["past-session"]


def test_select_history_opens_a_new_tab_when_current_is_not_blank(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "_session_id", "active-tab")
    history.save_tab_order(["active-tab"])
    history.append_message("active-tab", "user", "already talking")
    history.append_message("past-session", "user", "an old chat")

    result = indicator._Api().select_history("past-session")

    assert llm.get_session_id() == "past-session"
    assert [t["session_id"] for t in result["tabs"]] == ["active-tab", "past-session"]


def test_select_history_activates_an_already_open_tab_instead_of_duplicating(monkeypatch, tmp_path):
    _use_tmp_state_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(llm, "_session_id", "active-tab")
    history.save_tab_order(["active-tab", "past-session"])
    history.append_message("active-tab", "user", "already talking")
    history.append_message("past-session", "user", "an old chat")

    result = indicator._Api().select_history("past-session")

    assert [t["session_id"] for t in result["tabs"]] == ["active-tab", "past-session"]
