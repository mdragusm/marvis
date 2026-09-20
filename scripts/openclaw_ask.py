"""Hand a task to the OpenClaw assistant and print its reply.

Marvis's own brain is a plain `claude` CLI session with no access to OpenClaw's
channels (WhatsApp, automations/reminders, gateway state, its memory files, ...).
This script bridges that gap: it runs one `openclaw agent` turn through the local
Gateway in a dedicated `marvis` session (so these requests keep their own context
and never mix into Marcelo's main OpenClaw chat) and prints only the assistant's
final text, so the caller can speak it back verbatim.

Usage:
    python scripts/openclaw_ask.py "send a WhatsApp to X saying Y"
    python scripts/openclaw_ask.py --timeout 120 "what reminders do I have set"

Exit 0 with the reply on stdout; non-zero with an error message on stdout otherwise
(stdout rather than stderr so the caller always has something to read aloud).
"""

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

AGENT_ID = "main"
SESSION_KEY = "marvis"


def _resolve_openclaw() -> list[str]:
    """`openclaw` on PATH is an npm .cmd shim; running it through cmd.exe mangles
    quoting on messages with special characters. Call node on the real entry
    script instead (same reasoning as llm.py's _resolve_claude)."""
    shim = shutil.which("openclaw.cmd") or shutil.which("openclaw")
    if shim:
        entry = Path(shim).parent / "node_modules" / "openclaw" / "openclaw.mjs"
        node = shutil.which("node")
        if entry.exists() and node:
            return [node, str(entry)]
    return ["openclaw"]


def ask(message: str, timeout: int) -> tuple[int, str]:
    command = [
        *_resolve_openclaw(), "agent",
        "--agent", AGENT_ID,
        "--session-key", SESSION_KEY,
        "--message", message,
        "--timeout", str(timeout),
        "--json",
    ]
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout + 30,
        )
    except FileNotFoundError:
        return 1, "OpenClaw isn't installed or isn't on the PATH."
    except subprocess.TimeoutExpired:
        return 1, "OpenClaw didn't answer in time."

    try:
        envelope = json.loads(completed.stdout)
    except json.JSONDecodeError:
        detail = (completed.stderr or completed.stdout).strip()
        return 1, f"OpenClaw returned something unreadable (exit {completed.returncode}): {detail[-500:]}"

    if envelope.get("ok") is False or envelope.get("status") not in (None, "ok"):
        error = envelope.get("error") or {}
        detail = error.get("message") if isinstance(error, dict) else str(error)
        return 1, f"OpenClaw failed: {detail or envelope.get('status') or 'unknown error'}"

    payloads = (envelope.get("result") or {}).get("payloads") or []
    text = "\n".join(p.get("text", "") for p in payloads if p.get("text")).strip()
    return 0, text or "OpenClaw finished but sent back no text."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("message", nargs="+", help="what to ask OpenClaw to do")
    parser.add_argument("--timeout", type=int, default=300, help="seconds to wait for the reply (default 300)")
    args = parser.parse_args()

    code, text = ask(" ".join(args.message), args.timeout)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
