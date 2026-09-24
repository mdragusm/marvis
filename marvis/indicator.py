import ctypes
import json
import os
import queue
import subprocess
import threading
from pathlib import Path
from typing import Callable

import webview

# Without this, Windows keys the taskbar icon to pythonw.exe's own icon (a Python
# logo) instead of the one set on the window, since the process has no identity
# of its own. Must be set before the window is created.
ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Marvis.Assistant")

_WIDTH = 340
_HEIGHT = 600

_HTML_PATH = Path(__file__).parent / "indicator_assets" / "overlay.html"
_ICON_PATH = Path(__file__).parent / "indicator_assets" / "icon.ico"

_commands: "queue.Queue[tuple]" = queue.Queue()
_text_input: "queue.Queue[str]" = queue.Queue()
_window: webview.Window | None = None


class _Api:
    """Exposed to the overlay's JS as `window.pywebview.api`."""

    def restart(self) -> None:
        # Always relaunch via pythonw.exe + main.py (mirrors Marvis.vbs), rather than
        # reusing sys.executable/sys.argv, so a restart never reopens a console window
        # even if this instance itself was started from python.exe in a terminal.
        project_root = Path(__file__).resolve().parent.parent
        pythonw = project_root / ".venv" / "Scripts" / "pythonw.exe"
        subprocess.Popen([str(pythonw), str(project_root / "main.py")], cwd=str(project_root))
        os._exit(0)

    def toggle_mode(self) -> None:
        from marvis.assistant import toggle_mode

        toggle_mode()

    def send_text(self, text: str) -> None:
        _text_input.put(text)

    def get_sessions(self) -> list:
        from marvis import history

        return history.list_sessions()

    def get_session_transcript(self, session_id: str) -> list:
        from marvis import history

        return history.load_transcript(session_id)

    def init_tabs(self) -> dict:
        """Called once when the overlay first loads -- resets the tab strip to a single
        fresh tab for this launch's session (see history.reset_tabs)."""
        from marvis import history
        from marvis.llm import get_session_id

        session_id = get_session_id()
        history.reset_tabs(session_id)
        return {"tabs": history.get_tabs(session_id), "transcript": history.load_transcript(session_id)}

    def new_tab(self) -> dict:
        from marvis import history
        from marvis.llm import get_session_id, reset_session

        reset_session()
        session_id = get_session_id()
        history.add_tab(session_id)
        return {"tabs": history.get_tabs(session_id), "transcript": []}

    def select_tab(self, session_id: str) -> dict:
        from marvis import history
        from marvis.llm import switch_session

        switch_session(session_id)
        return {"tabs": history.get_tabs(session_id), "transcript": history.load_transcript(session_id)}

    def close_tab(self, session_id: str) -> dict:
        from marvis import history
        from marvis.llm import get_session_id, reset_session, switch_session

        closing_active = session_id == get_session_id()
        order = history.load_tab_order()
        closed_index = order.index(session_id) if session_id in order else None
        remaining = history.remove_tab(session_id)

        if closing_active:
            if remaining:
                # Mirrors Chrome: activate the tab that slides into the closed one's
                # spot (its right neighbor), or the new last tab if it was rightmost.
                next_active = remaining[closed_index] if closed_index is not None and closed_index < len(remaining) else remaining[-1]
                switch_session(next_active)
            else:
                # Closed the only tab -- mirrors app startup: land on one fresh blank
                # tab rather than leaving no active conversation at all.
                reset_session()
                history.add_tab(get_session_id())

        active_id = get_session_id()
        return {"tabs": history.get_tabs(active_id), "transcript": history.load_transcript(active_id)}

    def delete_session(self, session_id: str) -> list:
        from marvis import history

        history.delete_session(session_id)
        return history.list_sessions()

    def select_history(self, session_id: str) -> dict:
        """Opens a past conversation from the History panel. Reuses the current tab if
        it's still blank (never sent a message); otherwise opens it as a new tab next to
        the current one, or just activates it if it's already open in one."""
        from marvis import history
        from marvis.llm import get_session_id, switch_session

        current_id = get_session_id()
        open_ids = {t["session_id"] for t in history.get_tabs(current_id)}

        if session_id not in open_ids:
            if history.is_blank(current_id):
                history.replace_tab(current_id, session_id)
            else:
                history.add_tab(session_id, after=current_id)

        switch_session(session_id)
        return {"tabs": history.get_tabs(session_id), "transcript": history.load_transcript(session_id)}


def set_mode(mode_label: str) -> None:
    _commands.put(("mode", mode_label))


def show() -> None:
    _commands.put(("show",))


def hide() -> None:
    _commands.put(("hide",))


def start_speaking() -> None:
    _commands.put(("speak_start",))


def stop_speaking() -> None:
    _commands.put(("speak_stop",))


def start_listening() -> None:
    _commands.put(("listen_start",))


def stop_listening() -> None:
    _commands.put(("listen_stop",))


def set_usage(utilization: float, window_label: str) -> None:
    _commands.put(("usage", utilization, window_label))


def set_levels(levels) -> None:
    _commands.put(("levels", list(levels)))


def add_user_message(text: str) -> None:
    _commands.put(("user_msg", text))


def start_marvis_message() -> None:
    _commands.put(("marvis_start",))


def append_marvis_message(text: str) -> None:
    _commands.put(("marvis_append", text))


def set_tab_label(session_id: str, label: str) -> None:
    _commands.put(("tab_label", session_id, label))


_TEXT_INPUT_POLL_TIMEOUT = 0.1


def get_text_input(mode_check: Callable[[], bool] | None = None) -> str | None:
    """Blocks until the user submits text via the overlay's input box, or -- if given --
    returns None as soon as mode_check() turns falsy (checked periodically), matching how
    wakeword.wait_for_wake_word already responds to a mode switch instead of blocking
    forever regardless of it."""
    while True:
        try:
            return _text_input.get(timeout=_TEXT_INPUT_POLL_TIMEOUT)
        except queue.Empty:
            if mode_check is not None and not mode_check():
                return None


def _poll_commands() -> None:
    while True:
        cmd, *rest = _commands.get()
        try:
            if cmd == "show":
                _window.evaluate_js("marvisOverlay.show()")
            elif cmd == "hide":
                _window.evaluate_js("marvisOverlay.hide()")
            elif cmd == "speak_start":
                _window.evaluate_js("marvisOverlay.startSpeaking()")
            elif cmd == "speak_stop":
                _window.evaluate_js("marvisOverlay.stopSpeaking()")
            elif cmd == "listen_start":
                _window.evaluate_js("marvisOverlay.startListening()")
            elif cmd == "listen_stop":
                _window.evaluate_js("marvisOverlay.stopListening()")
            elif cmd == "usage":
                _window.evaluate_js(f"marvisOverlay.setUsage({json.dumps(rest[0])}, {json.dumps(rest[1])})")
            elif cmd == "levels":
                _window.evaluate_js(f"marvisOverlay.setLevels({json.dumps(rest[0])})")
            elif cmd == "mode":
                _window.evaluate_js(f"marvisOverlay.setMode({json.dumps(rest[0])})")
            elif cmd == "user_msg":
                _window.evaluate_js(f"marvisOverlay.addUserMessage({json.dumps(rest[0])})")
            elif cmd == "marvis_start":
                _window.evaluate_js("marvisOverlay.startMarvisMessage()")
            elif cmd == "marvis_append":
                _window.evaluate_js(f"marvisOverlay.appendMarvisMessage({json.dumps(rest[0])})")
            elif cmd == "tab_label":
                _window.evaluate_js(f"marvisOverlay.setTabLabel({json.dumps(rest[0])}, {json.dumps(rest[1])})")
        except Exception:
            pass  # the window may be mid-teardown


def _on_ready(main_func: Callable[[], None]) -> None:
    _window.events.shown.wait(10)
    _window.events.loaded.wait(10)
    threading.Thread(target=_poll_commands, daemon=True).start()
    main_func()


def run(main_func: Callable[[], None]) -> None:
    """Must be called on the main thread. Creates the indicator window, then once its
    event loop is live, runs `main_func` (the rest of the app) on a background thread
    while this thread keeps driving the window until the process exits."""
    global _window

    _window = webview.create_window(
        "Marvis",
        url=_HTML_PATH.as_uri(),
        resizable=True,
        on_top=False,
        width=_WIDTH,
        height=_HEIGHT,
        js_api=_Api(),
        # pywebview disables text selection app-wide by default (injects a global
        # `user-select: none` rule) -- without this, chat text can't be selected or
        # copied at all, regardless of any CSS in overlay.html itself.
        text_select=True,
    )
    webview.start(_on_ready, (main_func,), debug=False, icon=str(_ICON_PATH))
