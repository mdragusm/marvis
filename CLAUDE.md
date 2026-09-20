# Text formatting

* Use straight quotes/apostrophes ('  ") not smart/curly ones - curly ones get mangled into garbled characters when displayed in Marcelo's PowerShell console.

# WhatsApp

* WhatsApp is handled directly via the `whatsup` MCP plugin (no more OpenClaw delegation) — sending, reading, and replying all happen in this session.
* Outward-facing actions (sending a message to someone) still need Marcelo's spoken confirmation first, per the system prompt. Reading, listing, and checking things do not.

# Speaking file names aloud

* Never read a literal file name with its extension out loud (e.g. "main.py", "marvis_todo.txt"). Describe it in plain spoken language instead: "the main Python script", "the Marvis to-do text file".
* Translate common extensions to their spoken form: .py -> Python script/file, .txt -> text file, .md -> markdown file, .json -> JSON file, .js -> JavaScript file, .ts -> TypeScript file, .yaml/.yml -> YAML file, .sh -> shell script, .ps1 -> PowerShell script.
* This applies to spoken/TTS responses. Written output (code blocks, file paths in tool calls) is unaffected.
