"""The calm source is sdk by decision, and both launchers write it so.

A hand-edited `local` would otherwise survive into a later plain run and
record rows on scale 3 with a different stressed line, for a session nobody
chose that for -- the same reason every FACE_* key and INGEST_MODE are
written on both branches.
"""

from __future__ import annotations

from pathlib import Path

from src.app.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_the_default_calm_source_is_sdk():
    assert Settings(_env_file=None, API_TOKEN="t", ADMIN_TOKEN="a").eeg_spectrum_source == "sdk"


def test_both_launchers_write_the_key_on_both_branches_from_a_flag():
    """Written unconditionally the key cannot go stale; written from a flag
    the feature is still reachable through the supported path, which a
    plain write of `sdk` on every run made it not."""
    ps1 = (ROOT / "start.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert ps1.count('Set-EnvKey $eegEnv "EEG_SPECTRUM_SOURCE" $spectrumSource') == 2
    assert '$spectrumSource = if ($LocalCalm) { "local" } else { "sdk" }' in ps1
    assert "[switch]$LocalCalm" in ps1 and "$LocalCalm -and -not $Muse" in ps1
    # macOS always runs the simulator (the script forces EEG_SOURCE=sim even
    # with --muse), so its guard refuses --local-calm outright rather than
    # testing a flag the run does not honour -- and it therefore writes the
    # literal sdk on both branches; a ternary feeding a `local` arm here
    # would be unreachable and would read as a capability the script lacks.
    assert sh.count('set_env_key "$EEG_ENV" "EEG_SPECTRUM_SOURCE" "sdk"') == 2
    assert 'SPECTRUM_SOURCE="local"' not in sh
    assert '--local-calm) LOCAL_CALM=true' in sh and 'if [ "$LOCAL_CALM" = true ]; then' in sh
    assert '"$LOCAL_CALM" = true ] && [ "$MUSE"' not in sh
