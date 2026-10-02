"""scripts/capture_optics.py talks to the bridge as the sidecar does: proof, then token; a scan before a connect."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import threading
import time

import pytest
from launcher_cases import ROOT
from test_bridge_token_path import _EXE

SCRIPT = ROOT / "EEGResearch" / "scripts" / "capture_optics.py"
TOKEN = "fake-bridge-token-" + "x" * 25
HEADBAND = "Muse-TEST"


class _FakeBridge(threading.Thread):
    """Answers the challenge "right", "wrong" or not at all; lists HEADBAND after a scan, streams after a connect."""

    def __init__(self, answer: str) -> None:
        super().__init__(daemon=True)
        self.answer = answer
        self.received = b""
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]

    def run(self) -> None:
        with self.server:
            conn, _ = self.server.accept()
        conn.settimeout(0.2)
        with conn:
            while b"\n" not in self.received and self._read(conn):
                pass
            if self.answer == "silent":
                while self._read(conn):
                    pass
                return
            nonce = self.received.split(b"\n", 1)[0].split()[1]
            right = hmac.new(TOKEN.encode(), nonce, hashlib.sha256).hexdigest()
            conn.sendall(f"PROOF {right if self.answer == 'right' else '0' * 64}\n".encode())
            seq = 0
            while self._read(conn):
                if b'"connect"' in self.received:
                    conn.sendall(json.dumps({"kind": "optics", "seq": seq, "mono_ts_ms": seq * 15.6, "n": 4,
                                             "ch": [1.0, 2.0, 3.0, 4.0]}).encode() + b"\n")
                    seq += 1
                elif b"AUTH " in self.received:
                    found = [HEADBAND] if b'"refresh"' in self.received else []
                    conn.sendall(json.dumps({"bridge_mode": "libmuse", "muse_devices": found}).encode() + b"\n")

    def _read(self, conn: socket.socket) -> bool:
        """False once the client hangs up."""
        try:
            data = conn.recv(4096)
        except socket.timeout:
            return True
        except OSError:
            return False
        self.received += data
        return bool(data)


def _capture(tmp_path, port: int, *extra: str) -> subprocess.CompletedProcess:
    token_dir = tmp_path / "AdaptiveLearning"
    token_dir.mkdir(exist_ok=True)
    (token_dir / f"muse_bridge_{port}.token").write_text(TOKEN, encoding="ascii")
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path))
    return subprocess.run([sys.executable, str(SCRIPT), "--port", str(port), "--out", str(tmp_path / "out.jsonl"),
                           *extra], env=env, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("answer", ["wrong", "silent"])
def test_a_process_on_the_port_that_does_not_prove_itself_is_refused_plainly_and_never_handed_the_token(
        tmp_path, answer):
    fake = _FakeBridge(answer)
    fake.start()
    out = _capture(tmp_path, fake.port, "--seconds", "2")
    fake.join(10)
    assert out.returncode != 0 and "did not prove it is the bridge" in out.stderr
    assert "Traceback" not in out.stderr  # a refusal, not a crash
    assert fake.received.startswith(b"CHALLENGE ") and TOKEN.encode() not in fake.received


def test_connect_scans_first_then_captures_what_the_bridge_streams(tmp_path):
    fake = _FakeBridge("right")
    fake.start()
    out = _capture(tmp_path, fake.port, "--connect", HEADBAND, "--seconds", "2", "--scan-seconds", "5")
    fake.join(10)
    assert out.returncode == 0, out.stderr
    assert f"AUTH {TOKEN}\n".encode() in fake.received
    assert fake.received.index(b'"refresh"') < fake.received.index(b'"connect"')  # the bridge scans only if asked
    frames = [json.loads(line) for line in (tmp_path / "out.jsonl").read_text(encoding="utf-8").splitlines()]
    assert frames and frames[0]["n"] == 4


@pytest.mark.skipif(sys.platform != "win32" or _EXE is None,
                    reason="needs a muse_native_bridge.exe built after the last change to its source")
def test_the_real_bridge_accepts_the_script_and_answers_it(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, LOCALAPPDATA=str(tmp_path), MUSE_BRIDGE_PORT=str(port))
    bridge = subprocess.Popen([str(_EXE)], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    try:
        token = tmp_path / "AdaptiveLearning" / f"muse_bridge_{port}.token"
        deadline = time.monotonic() + 20
        while not (token.is_file() and token.stat().st_size):
            assert time.monotonic() < deadline, "no bridge token after 20 s"
            time.sleep(0.1)
        out = subprocess.run([sys.executable, str(SCRIPT), "--port", str(port), "--seconds", "2",
                              "--out", str(tmp_path / "out.jsonl")], env=env, capture_output=True, text=True,
                             timeout=60)
    finally:
        bridge.kill()
        bridge.wait(10)
    assert "bridge answering: bridge_mode" in out.stderr, out.stderr  # no headband here, so no frames follow
