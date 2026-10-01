"""start.ps1 writes .env as UTF-8 with no BOM; see CLAUDE.md "start.ps1 rules that cost whole runs"."""

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


def _ansi() -> str:
    """Python's codec for the system ANSI code page, which Set-Content wrote."""
    import ctypes

    return f"cp{ctypes.windll.kernel32.GetACP()}"


@pytest.mark.parametrize("saved_as", ["utf-8", "ansi"])
@pytest.mark.parametrize("present", [False, True], ids=["append", "rewrite"])
def test_a_non_ascii_install_path_is_written_so_the_sidecar_can_read_it(tmp_path, monkeypatch, present, saved_as):
    folder = tmp_path / "Zoë kit"
    folder.mkdir()
    env = folder / ".env"
    landmark = str(folder / "models" / "face_landmarker.task")
    emotion = str(folder / "models" / "emotion-ferplus-8.onnx")
    comment = "# Zoë's station"
    # EEG_SOURCE first, where a BOM would land in the key; the typed lines must survive in either encoding.
    text = (f"EEG_SOURCE=sim\n{comment}\nFACE_EMOTION_MODEL_PATH={emotion}\n"
            + ("FACE_LANDMARK_MODEL_PATH=models/face_landmarker.task\n" if present else ""))
    try:
        env.write_bytes(text.encode("utf-8" if saved_as == "utf-8" else _ansi()))
    except (LookupError, UnicodeEncodeError):
        pytest.skip(f"this machine's ANSI code page cannot hold {folder.name!r}")
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
    raw = env.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "dotenv reads a BOM into the first key's name"
    assert comment in raw.decode("utf-8").splitlines(), "a line only a person wrote is kept, character for character"
    assert dotenv_values(env) == {"EEG_SOURCE": "sim", "FACE_EMOTION_MODEL_PATH": emotion,
                                  "FACE_LANDMARK_MODEL_PATH": landmark}
    monkeypatch.chdir(folder)
    monkeypatch.delenv("FACE_LANDMARK_MODEL_PATH", raising=False)
    monkeypatch.delenv("FACE_EMOTION_MODEL_PATH", raising=False)
    s = Settings()
    assert (s.face_landmark_model_path, s.face_emotion_model_path) == (landmark, emotion)
