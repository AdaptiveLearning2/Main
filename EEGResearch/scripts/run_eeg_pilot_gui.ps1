$ErrorActionPreference = "Stop"

$repoRoot = Split-Path $PSScriptRoot -Parent
$py = Join-Path $repoRoot ".venv\Scripts\python.exe"
$script = Join-Path $PSScriptRoot "eeg_pilot_gui.py"

if (!(Test-Path $py)) {
    throw "Missing venv python at $py. Create/activate venv first."
}
if (!(Test-Path $script)) {
    throw "Missing script: $script"
}

& $py $script
