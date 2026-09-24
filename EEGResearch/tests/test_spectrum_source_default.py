"""The calm source defaults to sdk, and both launchers write it on every run so a stale `local` cannot survive."""

from __future__ import annotations

from pathlib import Path

from src.app.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_the_default_calm_source_is_sdk():
    assert Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a").eeg_spectrum_source == "sdk"


def test_both_launchers_write_the_key_on_both_branches_from_a_flag():
    """Unconditional so the key cannot go stale; from a flag so `local` stays reachable."""
    ps1 = (ROOT / "start.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert ps1.count('Set-EnvKey $eegEnv "EEG_SPECTRUM_SOURCE" $spectrumSource') == 2
    assert '$spectrumSource = if ($LocalCalm) { "local" } else { "sdk" }' in ps1
    assert "[switch]$LocalCalm" in ps1 and "$LocalCalm -and -not $Muse" in ps1
    # start.sh forces EEG_SOURCE=sim, so it refuses --local-calm and writes literal sdk on both branches.
    assert sh.count('set_env_key "$EEG_ENV" "EEG_SPECTRUM_SOURCE" "sdk"') == 2
    assert 'SPECTRUM_SOURCE="local"' not in sh
    assert '--local-calm) LOCAL_CALM=true' in sh and 'if [ "$LOCAL_CALM" = true ]; then' in sh
    assert '"$LOCAL_CALM" = true ] && [ "$MUSE"' not in sh
