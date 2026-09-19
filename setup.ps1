# Sets up Marvis on a fresh PC after copying this project folder over (skip .venv
# when copying -- it's machine-specific and gets recreated here). Safe to re-run.
#
# Before running this:
#   1. Install Claude Code (https://claude.com/claude-code) and log in.
#   2. Copy this whole "marvis" folder to the new PC.
#   3. Copy the .env file separately (it's gitignored, has secrets/config) into the
#      project root -- this script won't create one for you if it's missing.
#
# This script itself installs/configures: Node.js (if missing) and the Python venv +
# requirements.

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

function Refresh-Path {
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host "== Checking Python ==" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python not found on PATH. Install Python 3.11+ first, then re-run this script."
}
python --version

Write-Host "== Checking Node.js ==" -ForegroundColor Cyan
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Host "Node.js not found -- installing via winget..." -ForegroundColor Yellow
    winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements
    Refresh-Path
}
node --version

Write-Host "== Setting up the Python venv ==" -ForegroundColor Cyan
if (-not (Test-Path "$root\.venv")) {
    python -m venv "$root\.venv"
}
& "$root\.venv\Scripts\pip.exe" install -r "$root\requirements.txt"

Write-Host "== Downloading wake-word models ==" -ForegroundColor Cyan
# openWakeWord ships without its pretrained ONNX models -- they must be fetched on
# first setup, or wakeword.py crashes at import with NO_SUCHFILE for hey_jarvis_v0.1.onnx.
# download_models() grabs both the .tflite and .onnx variants and is a no-op if present.
& "$root\.venv\Scripts\python.exe" -c "import openwakeword.utils as u; u.download_models(['hey_jarvis'])"

Write-Host "== Checking for .env ==" -ForegroundColor Cyan
if (-not (Test-Path "$root\.env")) {
    Write-Host "No .env found. Copy it over from the old PC (or fill one in from .env.example) before running Marvis." -ForegroundColor Yellow
}

Write-Host "== Installing taskbar shortcut ==" -ForegroundColor Cyan
# Creates the Start Menu + root shortcuts stamped with the matching AppUserModel.ID so a
# pinned taskbar icon shows icon.ico instead of a generic Python-document icon. Non-fatal:
# the app runs fine without it, so a failure here shouldn't abort the whole setup.
try {
    & "$root\install-shortcut.ps1"
} catch {
    Write-Host "Shortcut install failed (non-fatal): $_" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "== Done ==" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Make sure .env is in place with your real config."
Write-Host "  2. Launch Marvis via Marvis.vbs (or 'pythonw main.py')."
