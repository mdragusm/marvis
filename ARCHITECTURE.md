# Marvis architecture map

Read this first when changing how Marvis behaves — it points you at the right file/function so you don't re-read the whole codebase. Keep it current: when a module's responsibility changes, update its line here in the same session.

## Entry point

- `main.py` — process bootstrap. Redirects stdout/stderr to the void under pythonw (no console), starts the indicator window on the main thread, runs the assistant on a background thread. Crashes go to `marvis_error.log`.

## Modules (`marvis/`)

- `assistant.py` — **the turn engine / orchestrator.** Owns the main loop: input producers (PTT, text, wake word) feed a turn queue; `_run_turn` streams the LLM reply, splits it into sentences, pipelines them to TTS, and drives the indicator. Also: clear-context phrases, project-switch phrases, STT hallucination filter, PTT key hook.
- `llm.py` — talks to Claude (spawns the `claude` CLI as a subprocess). `ask()` streams reply chunks; session management (`get_session_id`, `reset_session`, `switch_session`, cwd per session); `generate_title`; model-tier classification; usage reporting; `FLUSH_SIGNAL` emitted at tool-call boundaries.
- `tts.py` — **text to speech.** `_strip_markdown` sanitizes text for speech (code, tables, links, emphasis); `prepare()` kicks off edge-tts synthesis per sentence; `play()` streams PCM to the output device with level metering, interrupt handling, and Bluetooth drop/stall recovery + keepalive. Spanish/English voice auto-detect via langid.
- `stt.py` — **speech to text.** faster-whisper model load (background at import) + `transcribe()`.
- `wakeword.py` — openWakeWord "hey jarvis" detection; `wait_for_wake_word` with a noise-floor gate.
- `audio.py` — mic capture: `record_while_key_held` (PTT) and `record_until_silence` (wake word).
- `indicator.py` — **the window/UI bridge.** Python side of the pywebview overlay: a command queue pushes JS calls (`marvisOverlay.*`) to the HTML; `_Api` exposes methods callable from JS (tabs, history, send text, open_url, restart). Pairs with `indicator_assets/overlay.html`.
- `indicator_assets/overlay.html` — **the actual chat UI.** The animated face/level meter (canvas), chat transcript rendering (`renderMarkup`), tab strip, history panel, text input. All visual/display behavior lives here.
- `history.py` — JSONL transcript persistence + tab/session bookkeeping under `state/`. Titles, tab order, per-tab folder binding.
- `projects.py` — maps a spoken project name to a folder under `PROJECTS_BASE_DIR` (for "open the X project" tab switches).
- `config.py` — **all tunables in one place** (`Config` dataclass, env-backed via `.env`). Voices, rates, wake thresholds, PTT key, keepalive device, projects dir. Change a default here, not scattered in code.
- `single_instance.py` — kills other running Marvis instances on startup.
- `error_log.py` — the two loggers (`logger` → `marvis_error.log`, `debug_logger` → `jarvis_speech_debug.log`).
- `pronunciations.toml` (repo root) — word → spoken-form overrides applied in `tts.py`.

## A turn, end to end

1. Input producer in `assistant.py` captures speech/text → `audio.py` records → `stt.py` transcribes (speech) → turn queued.
2. `_run_turn`: shows UI, calls `llm.ask()` which streams chunks from the `claude` CLI.
3. Chunks are printed, appended to the on-screen message (`indicator.append_marvis_message` → `overlay.html renderMarkup`), and buffered.
4. `_split_sentences` cuts the buffer at sentence boundaries; each sentence → `tts.prepare()` (synthesis starts early) → `tts.play()` (audio + level meter).
5. Full reply persisted via `history.append_message`; UI returns to idle.

## "I want to change how X works" → go here

- How a reply **sounds** / what's spoken vs skipped (links, tables, code, pronunciations, voice, rate) → `tts.py` (`_strip_markdown`, `config.py` for voice/rate).
- How the chat **looks** / what's displayed (markdown rendering, colors, face, tabs, input box) → `indicator_assets/overlay.html`.
- How Marvis **decides what to do** with your words (new command phrases, routing, interrupts, sentence splitting) → `assistant.py`.
- Anything about the **model** (which Claude model, tiers, session/cwd handling, title generation) → `llm.py`.
- **Wake word** behavior/sensitivity → `wakeword.py` + `config.py` (`wake_*`).
- **Mic / recording** behavior → `audio.py`.
- **Transcripts, tabs, history** storage/retrieval → `history.py`.
- A **default/setting/threshold** → `config.py` first (it's likely already there).
- **Window-side glue** (a new JS call from Python, or a new JS→Python API) → `indicator.py` (`_poll_commands` and `_Api`) paired with `overlay.html`.
