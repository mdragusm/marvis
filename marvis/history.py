import json
import time
from pathlib import Path

_STATE_DIR = Path(__file__).parent.parent / "state"
_SESSION_FILE = _STATE_DIR / "session.json"


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
