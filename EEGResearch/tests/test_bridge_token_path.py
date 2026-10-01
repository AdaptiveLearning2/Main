"""The built native bridge writes its token under a %LOCALAPPDATA% outside the ANSI code page."""

import hashlib
import hmac
import os
import re
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.app.services.eeg_ingestion import bridge_token_path

_BRIDGE_DIR = Path(__file__).resolve().parents[1] / "native_bridge"
_CANDIDATES = [_BRIDGE_DIR / d / "Release" / "muse_native_bridge.exe" for d in ("build_off", "build")]
_EXE = next((p for p in _CANDIDATES if p.is_file()), None)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or _EXE is None, reason="needs a built muse_native_bridge.exe on Windows"
)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _launch(local_app_data: Path, port: int, out_dir: Path) -> tuple[subprocess.Popen, Path]:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("MUSE_")}
    env["LOCALAPPDATA"] = str(local_app_data)
    env["MUSE_BRIDGE_PORT"] = str(port)
    err = out_dir / "bridge.err"
    with open(out_dir / "bridge.out", "wb") as out, open(err, "wb") as errf:
        proc = subprocess.Popen(
            [str(_EXE)], env=env, stdout=out, stderr=errf, stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    return proc, err


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=10)


@pytest.mark.parametrize("folder", ["la-Łódź", "Zoë"])
def test_the_bridge_starts_and_writes_its_token_under_a_non_ansi_localappdata(folder, tmp_path, monkeypatch):
    base = tmp_path / folder
    base.mkdir()
    port = _free_port()
    proc, err = _launch(base, port, tmp_path)
    try:
        time.sleep(2.0)
        assert proc.poll() is None, err.read_bytes().decode("utf-8", "replace")

        token_file = base / "AdaptiveLearning" / f"muse_bridge_{port}.token"
        monkeypatch.setenv("LOCALAPPDATA", str(base))
        assert bridge_token_path(port) == str(token_file)
        token = token_file.read_text(encoding="ascii").strip()
        assert re.fullmatch(r"[0-9a-f]{64}", token)

        # The bridge's PROOF is keyed on the token it holds, so this ties the file to the live process.
        nonce = secrets.token_hex(16)
        with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
            conn.sendall(f"CHALLENGE {nonce}\n".encode())
            reply = conn.makefile("rb").readline().decode().strip()
        expected = hmac.new(token.encode(), nonce.encode(), hashlib.sha256).hexdigest()
        assert reply == f"PROOF {expected}"
    finally:
        _stop(proc)


def test_an_unwritable_token_path_is_reported_as_such_not_as_a_port_conflict(tmp_path):
    blocker = tmp_path / "Łódź-file"
    blocker.write_text("not a directory")
    base = blocker / "sub"
    port = _free_port()
    proc, err = _launch(base, port, tmp_path)
    try:
        proc.wait(timeout=10)
    finally:
        _stop(proc)
    stderr = err.read_bytes().decode("utf-8", "replace")
    assert proc.returncode != 0
    assert "Could not write the bridge token" in stderr
    assert str(base / "AdaptiveLearning" / f"muse_bridge_{port}.token") in stderr
    assert "already listening" not in stderr
