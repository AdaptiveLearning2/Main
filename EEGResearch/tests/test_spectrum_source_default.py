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


def test_both_launchers_write_the_key_on_both_branches():
    ps1 = (ROOT / "start.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "start.sh").read_text(encoding="utf-8")
    assert ps1.count('Set-EnvKey $eegEnv "EEG_SPECTRUM_SOURCE" "sdk"') == 2
    assert sh.count('set_env_key "$EEG_ENV" "EEG_SPECTRUM_SOURCE" "sdk"') == 2
