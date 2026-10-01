"""start.ps1 writes .env as UTF-8 with no BOM; see CLAUDE.md "Three start.ps1 rules"."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values

from src.app.config import Settings

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which("powershell")
pytestmark = pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="start.ps1 is Windows")


def _extract(text: str, start: str) -> str:
    """The function body from `start` to the first `}` at column 0."""
    m = re.search(re.escape(start) + r".*?^\}", text, re.S | re.M)
    assert m, start
    return m.group(0)


@pytest.mark.parametrize("present", [False, True], ids=["append", "rewrite"])
def test_a_non_ascii_install_path_is_written_so_the_sidecar_can_read_it(tmp_path, monkeypatch, present):
    folder = tmp_path / "Zoë kit"
    folder.mkdir()
    env = folder / ".env"
    landmark = str(folder / "models" / "face_landmarker.task")
    emotion = str(folder / "models" / "emotion-ferplus-8.onnx")
    # EEG_SOURCE first, where a BOM would land in the key; the UTF-8 emotion line must survive the write.
    env.write_text(f"EEG_SOURCE=sim\nFACE_EMOTION_MODEL_PATH={emotion}\n"
                   + ("FACE_LANDMARK_MODEL_PATH=models/face_landmarker.task\n" if present else ""), encoding="utf-8")
    src = (ROOT / "start.ps1").read_text(encoding="utf-8")
    script = tmp_path / "t.ps1"
    # Paths ride in the environment: PowerShell 5.1 reads a BOM-less script as ANSI.
    script.write_text(_extract(src, "function Set-EnvKey {") + "\n" + _extract(src, "function Get-EnvValue {") + "\n"
                      "Set-EnvKey $env:T_ENV 'FACE_LANDMARK_MODEL_PATH' $env:T_VALUE\n"
                      "\"READBACK=$((Get-EnvValue $env:T_ENV 'FACE_LANDMARK_MODEL_PATH') -ceq $env:T_VALUE)\"\n",
                      encoding="utf-8")
    r = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                       capture_output=True, text=True, env={**os.environ, "T_ENV": str(env), "T_VALUE": landmark})
    assert r.returncode == 0, r.stderr
    assert "READBACK=True" in r.stdout.splitlines(), (r.stdout, r.stderr)
    assert not env.read_bytes().startswith(b"\xef\xbb\xbf"), "dotenv reads a BOM into the first key's name"
    assert dotenv_values(env) == {"EEG_SOURCE": "sim", "FACE_EMOTION_MODEL_PATH": emotion,
                                  "FACE_LANDMARK_MODEL_PATH": landmark}
    monkeypatch.chdir(folder)
    monkeypatch.delenv("FACE_LANDMARK_MODEL_PATH", raising=False)
    monkeypatch.delenv("FACE_EMOTION_MODEL_PATH", raising=False)
    s = Settings()
    assert (s.face_landmark_model_path, s.face_emotion_model_path) == (landmark, emotion)
