import json
import time
from pathlib import Path

_STATE_DIR = Path(__file__).parent.parent / "state"
_SESSION_FILE = _STATE_DIR / "session.json"
_TABS_FILE = _STATE_DIR / "tabs.json"
_TITLES_FILE = _STATE_DIR / "titles.json"


def _history_file(session_id: str) -> Path:
    return _STATE_DIR / f"history_{session_id}.jsonl"


def load_current_session() -> tuple[str, bool] | None:
    """Returns (session_id, started) if a session was persisted, else None.

    `started` reflects whether the claude CLI actually confirmed creating this
    session -- not just that we generated an id for it -- so a session that
    never got past its first (failed) call is correctly treated as unresumable.
    """
    if not _SESSION_FILE.exists():
        return None
    try:
        data = json.loads(_SESSION_FILE.read_text(encoding="utf-8"))
        session_id = data.get("session_id")
        if not session_id:
            return None
        return session_id, bool(data.get("started", False))
    except (json.JSONDecodeError, OSError):
        return None


def save_current_session(session_id: str, started: bool) -> None:
    _STATE_DIR.mkdir(exist_ok=True)
    _SESSION_FILE.write_text(
        json.dumps({"session_id": session_id, "started": started}), encoding="utf-8"
    )


def append_message(session_id: str, role: str, text: str) -> None:
    if not text:
        return
    _STATE_DIR.mkdir(exist_ok=True)
    with _history_file(session_id).open("a", encoding="utf-8") as f:
        f.write(json.dumps({"role": role, "text": text}) + "\n")


def load_transcript(session_id: str) -> list[dict]:
    path = _history_file(session_id)
    if not path.exists():
        return []
    messages = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                messages.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return messages


def list_sessions() -> list[dict]:
    """All past sessions (one per history_<id>.jsonl file), newest first."""
    if not _STATE_DIR.exists():
        return []
    current = load_current_session()
    current_id = current[0] if current else None
    sessions = []
    for path in _STATE_DIR.glob("history_*.jsonl"):
        session_id = path.stem[len("history_"):]
        messages = load_transcript(session_id)
        if not messages:
            continue
        first_user = next((m["text"] for m in messages if m.get("role") == "user"), "")
        preview = (first_user[:60] + "...") if len(first_user) > 60 else first_user
        mtime = path.stat().st_mtime
        sessions.append({
            "session_id": session_id,
            "mtime": mtime,
            "label": time.strftime("%b %d, %I:%M %p", time.localtime(mtime)),
            "preview": preview or "(no messages)",
            "current": session_id == current_id,
        })
    sessions.sort(key=lambda s: s["mtime"], reverse=True)
    for s in sessions:
        del s["mtime"]
    return sessions


def load_titles() -> dict:
    if not _TITLES_FILE.exists():
        return {}
    try:
        return json.loads(_TITLES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_title(session_id: str, title: str) -> None:
    titles = load_titles()
    titles[session_id] = title
    _STATE_DIR.mkdir(exist_ok=True)
    _TITLES_FILE.write_text(json.dumps(titles), encoding="utf-8")


def _tab_label(session_id: str) -> str:
    # A generated title (see llm.generate_title) always wins once it exists -- it's only
    # missing for a brief window right after the first message, or for tabs from before
    # this feature existed, so fall back to the raw first message truncated generously
    # (the tab strip itself grows to fill window width and ellipsis-truncates in CSS,
    # so this is just a cap against pathologically long messages, not the main mechanism).
    titles = load_titles()
    if session_id in titles:
        return titles[session_id]
    messages = load_transcript(session_id)
    first_user = next((m["text"] for m in messages if m.get("role") == "user"), "")
    if not first_user:
        return "New conversation"
    return (first_user[:80] + "...") if len(first_user) > 80 else first_user


def is_blank(session_id: str) -> bool:
    """True if this session has never had a message sent in it -- the tab equivalent of
    a browser's empty new-tab page, used to decide whether opening a history item reuses
    the current tab or opens a new one."""
    return not load_transcript(session_id)


def load_tab_order() -> list[str]:
    if not _TABS_FILE.exists():
        return []
    try:
        data = json.loads(_TABS_FILE.read_text(encoding="utf-8"))
        return list(data.get("order", []))
    except (json.JSONDecodeError, OSError):
        return []


def save_tab_order(order: list[str]) -> None:
    _STATE_DIR.mkdir(exist_ok=True)
    _TABS_FILE.write_text(json.dumps({"order": order}), encoding="utf-8")


def reset_tabs(session_id: str) -> None:
    """Called once per launch: every startup begins with a single fresh tab (matching
    llm.py's "every launch starts a brand-new session" policy) rather than restoring
    whatever tab strip was open last time -- old conversations stay reachable through
    History instead."""
    save_tab_order([session_id])


def get_tabs(current_session_id: str) -> list[dict]:
    order = load_tab_order()
    if current_session_id not in order:
        # Keeps a tab for the active session on screen even if something external
        # (e.g. MARVIS_RESUME_SESSION) changed it out from under the tab strip.
        order.append(current_session_id)
        save_tab_order(order)
    return [
        {
            "session_id": session_id,
            "label": _tab_label(session_id),
            "current": session_id == current_session_id,
        }
        for session_id in order
    ]


def add_tab(session_id: str, after: str | None = None) -> None:
    order = load_tab_order()
    if session_id in order:
        return
    if after is not None and after in order:
        order.insert(order.index(after) + 1, session_id)
    else:
        order.append(session_id)
    save_tab_order(order)


def replace_tab(old_session_id: str, new_session_id: str) -> None:
    """Swaps a tab's session in place -- used when opening a history item into a blank
    tab, which should reuse that tab rather than spawning a new one."""
    order = load_tab_order()
    if old_session_id in order:
        order[order.index(old_session_id)] = new_session_id
    else:
        order.append(new_session_id)
    save_tab_order(order)


def remove_tab(session_id: str) -> list[str]:
    order = load_tab_order()
    if session_id in order:
        order.remove(session_id)
        save_tab_order(order)
    return order
