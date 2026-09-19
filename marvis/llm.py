import json
import shutil
import subprocess
import threading
import traceback
import uuid
from pathlib import Path
from typing import Iterator

from . import history
from .config import config
from .error_log import logger as error_logger


def _resolve_claude() -> str:
    """Absolute path to the real claude.exe rather than the bare "claude" name.

    The npm global dir only exposes shims (claude.cmd/.ps1/bash), not claude.exe, and
    subprocess.Popen([...], shell=False) launches via CreateProcess, which resolves .exe
    only (never .cmd, and it ignores PATHEXT) -- so Popen(["claude", ...]) would raise
    FileNotFoundError. The real exe lives under the shim dir at
    node_modules/@anthropic-ai/claude-code/bin/claude.exe; build that path explicitly
    instead of relying on PATH resolution. See CHANGELOG 2026-09-18 (4).
    """
    shim = shutil.which("claude")
    if shim:
        exe = Path(shim).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
        if exe.exists():
            return str(exe)
    return shutil.which("claude.exe") or "claude"


_CLAUDE = _resolve_claude()

SYSTEM_PROMPT = (
    "You are Marvis, a concise and dry-witted voice assistant. Keep replies short "
    "and phrased for being spoken aloud. Before taking any action that changes "
    "state on this machine (opening apps, sending messages, running commands), "
    "briefly ask the user for confirmation out loud first -- unless they've "
    "already told you it's fine to do that kind of thing automatically."
)

# Every launch starts a brand-new session by default -- no auto-resume of whatever was
# last active -- so past conversations only come back via the History button's explicit
# switch_session(). MARVIS_RESUME_SESSION remains a manual escape hatch for debugging.
if config.resume_session_id:
    _session_id = config.resume_session_id
    _session_started = True
else:
    _session_id = str(uuid.uuid4())
    _session_started = False


def get_session_id() -> str:
    return _session_id

# Yielded in place of a text chunk the instant a tool call starts, so the caller can speak
# whatever's been generated so far instead of waiting for a full sentence -- a tool call can
# take far longer than that wait. Fires on the actual tool-call boundary rather than a stall
# timeout (see assistant.py's _INTERRUPT_POLL_INTERVAL), so it can't misfire on a reply
# that's merely slow.
FLUSH_SIGNAL = "\x00"

_ASCII_PUNCTUATION = str.maketrans({
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "–": "-", "—": "-",
    "…": "...",
})


def _sanitize(text: str) -> str:
    return text.translate(_ASCII_PUNCTUATION)


def _drain_stderr(pipe, lines: list[str]) -> None:
    for line in pipe:
        lines.append(line)
    pipe.close()


def _log_claude_error(stderr_text: str, returncode: int) -> None:
    message = f"claude CLI exited with code {returncode}:\n{stderr_text}"
    print(message, flush=True)
    error_logger.error(message)


def reset_session() -> None:
    """Discard all prior conversation context; the next ask() call starts a brand-new claude session."""
    global _session_id, _session_started
    _session_id = str(uuid.uuid4())
    _session_started = False
    history.save_current_session(_session_id, started=False)


def switch_session(session_id: str) -> None:
    """Make a previously saved session the active one, so the next ask() resumes its context."""
    global _session_id, _session_started
    _session_id = session_id
    _session_started = True
    history.save_current_session(session_id, started=True)


def ask(text: str) -> Iterator[str]:
    global _session_id, _session_started

    creating_session = not _session_started
    command = [
        _CLAUDE, "-p", text,
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--dangerously-skip-permissions",
        "--append-system-prompt", SYSTEM_PROMPT,
        "--resume" if _session_started else "--session-id", _session_id,
    ]
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
            # Without this, Windows pops up a visible console for the child because
            # main.py itself runs under pythonw.exe with no console of its own to attach to.
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception:
        # This runs on assistant.py's bare _stream_to_queue daemon thread, which has no
        # except around it, and pythonw has no console -- an uncaught exception here would
        # fail completely silently. Log and fail loud instead of dying mid-call. Same
        # reasoning applies to the stream-reading guard below.
        error_logger.error("failed to launch claude CLI:\n%s", traceback.format_exc())
        yield "\nSorry, I hit an error talking to Claude."
        return

    # Stderr is drained on its own thread (rather than read after stdout finishes) because
    # the OS pipe buffer for stderr could otherwise fill up and stall the child process,
    # which would stop stdout too and deadlock this generator.
    stderr_lines: list[str] = []
    stderr_thread = threading.Thread(target=_drain_stderr, args=(process.stderr, stderr_lines), daemon=True)
    stderr_thread.start()

    is_error = False
    seen_text_block = False
    try:
        for line in process.stdout:
            line = line.strip()
            if not line:
                continue
            # This whole block runs on a background thread (assistant.py's _stream_to_queue) with
            # no except around it -- an uncaught exception here (a malformed/partial JSON line, or
            # a shape the CLI's output doesn't match) would print to stderr via Python's default
            # thread excepthook, which main.py redirects to a null device, so the reply would die
            # mid-sentence with no marvis_error.log entry at all. Catch and log instead of crashing.
            try:
                event = json.loads(line)
                event_type = event.get("type")
                if event_type == "stream_event":
                    stream_event = event["event"]
                    stream_event_type = stream_event.get("type")
                    if stream_event_type == "content_block_start":
                        block_type = stream_event.get("content_block", {}).get("type")
                        if block_type == "text":
                            if seen_text_block:
                                # Separate text blocks (e.g. across a tool call) so sentences
                                # don't get glued together with no space between them.
                                yield "\n"
                            seen_text_block = True
                        elif block_type == "tool_use":
                            yield FLUSH_SIGNAL
                    elif stream_event_type == "content_block_delta":
                        delta = stream_event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            yield _sanitize(delta["text"])
                elif event_type == "result" and event.get("is_error"):
                    is_error = True
            except Exception:
                error_logger.error("failed to parse claude CLI stream line %r:\n%s", line, traceback.format_exc())
    except Exception:
        # Anything that escapes the per-line handling above (e.g. a bad byte breaking the
        # text-mode stdout iterator itself) must not propagate past this generator uncaught,
        # for the same silent-thread-death reason as the Popen guard above. Fall into the
        # existing is_error path below to reuse its recovery logic instead of duplicating it.
        error_logger.error("claude CLI stream reading failed:\n%s", traceback.format_exc())
        is_error = True

    process.wait()
    stderr_thread.join()
    stderr_text = "".join(stderr_lines).strip()
    if is_error or process.returncode != 0:
        if creating_session:
            # The claude CLI never actually created this session (the call that
            # was supposed to create it just failed), so the next attempt must
            # retry with --session-id rather than --resume a session that
            # doesn't exist -- otherwise every future call fails the same way.
            _session_started = False
        elif "No conversation found" in stderr_text:
            # Our persisted session id is stale (e.g. claude's own session
            # storage was cleared) -- drop it so the next turn starts a fresh
            # session instead of failing forever on a dead id.
            reset_session()
        _log_claude_error(stderr_text, process.returncode)
        yield "\nSorry, I hit an error talking to Claude."
    else:
        _session_started = True
        if creating_session:
            history.save_current_session(_session_id, started=True)
