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
_NEWEST_SOURCE = max(p.stat().st_mtime for p in (_BRIDGE_DIR / "src").rglob("*") if p.is_file())
# Stale counts as missing, as in start.ps1: an exe older than its source would test old code.
_FRESH = [p for p in (_BRIDGE_DIR / d / "Release" / "muse_native_bridge.exe" for d in ("build_off", "build"))
          if p.is_file() and p.stat().st_mtime >= _NEWEST_SOURCE]
_EXE = max(_FRESH, key=lambda p: p.stat().st_mtime, default=None)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or _EXE is None,
    reason="needs a muse_native_bridge.exe on Windows built after the last change to native_bridge/src",
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


def _wait_for_token(token_file: Path, proc: subprocess.Popen, err: Path, seconds: float = 20.0) -> None:
    """Until the bridge has written its token; a bridge that exits first fails at once, with its stderr."""
    deadline = time.monotonic() + seconds
    while not (token_file.is_file() and token_file.stat().st_size > 0):
        assert proc.poll() is None, err.read_bytes().decode("utf-8", "replace")
        assert time.monotonic() < deadline, f"no token at {token_file} after {seconds:.0f} s"
        time.sleep(0.1)


@pytest.mark.parametrize("folder", ["la-Łódź", "Zoë"], ids=["outside-cp1252", "inside-cp1252"])
def test_the_bridge_starts_and_writes_its_token_under_a_non_ansi_localappdata(folder, tmp_path, monkeypatch):
    base = tmp_path / folder
    base.mkdir()
    port = _free_port()
    proc, err = _launch(base, port, tmp_path)
    try:
        token_file = base / "AdaptiveLearning" / f"muse_bridge_{port}.token"
        _wait_for_token(token_file, proc, err)
        monkeypatch.setenv("LOCALAPPDATA", str(base))
        assert bridge_token_path(port) == str(token_file)
        token = token_file.read_text(encoding="ascii").strip()
        assert re.fullmatch(r"[0-9a-f]{64}", token)

        # The bridge's PROOF is keyed on the token it holds, so this ties the file to the live process.
        nonce = secrets.token_hex(16)
        with socket.create_connection(("127.0.0.1", port), timeout=10) as conn:
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
        proc.wait(timeout=30)
    finally:
        _stop(proc)
    stderr = err.read_bytes().decode("utf-8", "replace")
    assert proc.returncode != 0
    assert "Could not write the bridge token" in stderr
    assert str(base / "AdaptiveLearning" / f"muse_bridge_{port}.token") in stderr
    assert "does not exist and could not be created" in stderr, "the folder is missing, not unwritable"
    assert "already listening" not in stderr
