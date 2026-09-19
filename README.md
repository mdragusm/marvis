# Marvis

A local voice assistant. It listens (push-to-talk, wake-word, or typed text), transcribes with
Whisper, sends the text to the `claude` CLI, and speaks the streamed reply back with edge-tts --
all behind a small transparent overlay window showing an idle/thinking/speaking avatar and a
running chat log.

Marvis runs as a full coding agent under the hood (via the `claude` CLI with
`--dangerously-skip-permissions`), not a plain chatbot -- it can read/edit files and run commands
on your machine when asked, not just answer questions.

## Requirements

- Windows (uses Win32 APIs directly for global hotkeys and the taskbar icon; won't run as-is on
  macOS/Linux)
- A working microphone and speakers
- Python 3.11+ ([python.org](https://www.python.org/downloads/) -- check "Add python.exe to PATH"
  during install)
- Node.js (installed automatically by `setup.ps1` if missing)
- [Claude Code](https://claude.com/claude-code) CLI, installed and logged in (`claude` on your
  PATH, run `claude` once from any folder to confirm you're logged in)

## First-time setup

1. Clone or download this repo.
2. Open PowerShell in the project folder and run:
   ```powershell
   .\setup.ps1
   ```
   This creates a `.venv`, installs the Python dependencies from `requirements.txt`, downloads the
   openWakeWord model, and installs a Start Menu + desktop shortcut.
3. (Optional) Copy `.env.example` to `.env` if you want to override a default (TTS voice, Spanish
   detection threshold, wake-word sensitivity) -- see the comments in that file. Not required to
   run; every value already has a matching default baked into `marvis/config.py`.
4. The very first time you run `claude` from this specific folder, it may show a one-time "do you
   trust this folder?" prompt. Run `claude` once directly from the project folder in an ordinary
   terminal and accept it -- otherwise Marvis (which runs `claude` with no visible console) will
   hang silently on first launch.

## Running

Double-click `Marvis.vbs`, or run it from its Start Menu / desktop shortcut. This launches with no
console window.

For your first run, or if something seems wrong, launch with a visible console instead so you can
see what's happening:
```powershell
.\.venv\Scripts\python.exe main.py
```

## Using it

Marvis starts in **push-to-talk** mode. Hold the key, speak, release it, and Marvis replies. Three
modes exist, cycled with **Tab**:

| Mode | How it works |
|---|---|
| Push-to-talk | Hold the backtick key (`` ` ``) to record, release to send. Press it again while Marvis is speaking to interrupt. |
| Wake-word | Say "hey Jarvis" (the wake-word model's built-in name -- Marvis itself still answers as Marvis), then speak; Marvis records until you stop talking. |
| Text | Type into the text box in the overlay window instead of speaking. |

Say "clear the context" (or "start over" / "forget everything") to wipe the current conversation
and start fresh, without restarting the app.

A **History** button in the overlay lets you reopen a past conversation after a restart.

## Troubleshooting

- **Nothing happens / no reply, ever:** check `marvis_error.log` in the project folder first --
  Marvis is designed to log every failure there rather than crash silently. See "First-time setup"
  step 4 above for the most common first-run cause.
- **Push-to-talk key seems dead:** switch modes with Tab and back, or just restart via the overlay
  window's restart control.
- **Logs:** `marvis_error.log` (errors/crashes), `marvis_speech_debug.log` (TTS timing, only useful
  for diagnosing audio gaps).
- Still stuck: check `CHANGELOG.md` -- most bugs hit so far are documented there with their root
  cause and fix.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Project layout

- `main.py` -- entry point; sets up the indicator window and the assistant thread.
- `marvis/` -- the actual application (audio capture, transcription, the `claude` CLI wrapper,
  TTS, the overlay indicator, session history).
- `tests/` -- pytest suite.
- `sketches/` -- HTML prototypes used while designing the overlay's avatar.
- `CHANGELOG.md` -- dated log of bugs found/fixed and features added, with root causes.
- `WIP.md` -- currently open work.
