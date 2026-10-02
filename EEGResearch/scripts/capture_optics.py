#!/usr/bin/env python3
"""Capture a raw optical recording from the native bridge to a JSONL fixture.

Talks to the bridge on 127.0.0.1 as the sidecar does: its token, after it has proved it holds the same
one. `--connect NAME` asks for a scan first, since the bridge scans only when asked. `.gz` output is
gzipped (two minutes: ~640KB plain, ~97KB gzipped). One {"seq", "mono_ts_ms", "n", "ch"} frame per
line; warns on a `seq` gap (time base is by index).
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import hmac
import json
import os
import secrets
import socket
import sys
import time
from collections.abc import Iterator


def _token(port: int) -> str:
    """The token this port's bridge wrote; eeg_ingestion.bridge_token_path's rule."""
    path = os.path.join(os.environ.get("LOCALAPPDATA", ""), "AdaptiveLearning", f"muse_bridge_{port}.token")
    try:
        with open(path, encoding="ascii") as f:
            token = f.read().strip()
    except (OSError, UnicodeDecodeError):
        token = ""
    if not token:
        raise SystemExit(f"no bridge token at {path}: is muse_native_bridge running on port {port}?")
    return token


def _authenticate(sock: socket.socket, token: str) -> None:
    """The sidecar's handshake: the token goes only to a bridge that proved it read the same file."""
    nonce = secrets.token_hex(32)
    sock.sendall(f"CHALLENGE {nonce}\n".encode("ascii"))
    proof = b""
    while not proof.endswith(b"\n") and len(proof) < 256:
        chunk = sock.recv(1)
        if not chunk:
            break
        proof += chunk
    expected = "PROOF " + hmac.new(token.encode("ascii"), nonce.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(proof.rstrip(b"\r\n"), expected.encode("ascii")):
        raise SystemExit("the process on that port did not prove it is the bridge; not sending the token")
    sock.sendall(f"AUTH {token}\n".encode("ascii"))


def _messages(sock: socket.socket) -> Iterator[dict | None]:
    """Each JSON line the bridge sends, None on a quiet second; ends when it hangs up."""
    buf = b""
    while True:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            yield None
            continue
        if not chunk:
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                yield msg


def _pair(sock: socket.socket, messages: Iterator[dict | None], name: str, seconds: float) -> None:
    """A scan, then connect once NAME is in it: what the page does before every pairing."""
    sock.sendall(b'{"cmd":"refresh"}\n')
    deadline = time.monotonic() + seconds
    for msg in messages:
        if msg and name in (msg.get("muse_devices") or []):
            sock.sendall(json.dumps({"cmd": "connect", "name": name}).encode() + b"\n")
            print(f"connect requested: {name}", file=sys.stderr)
            return
        if time.monotonic() > deadline:
            break
    raise SystemExit(f"{name} not found in {seconds:.0f} s of scanning: is it on and nearby?")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--connect", default="", help="headband name to scan for and connect first")
    ap.add_argument("--scan-seconds", type=float, default=12.0, help="how long --connect waits for the headband")
    args = ap.parse_args()

    opener = gzip.open if args.out.endswith(".gz") else open

    count = 0
    first: dict | None = None
    last: dict | None = None
    channel_counts: set[int] = set()
    seqs: list[int] = []

    token = _token(args.port)
    sock = socket.create_connection(("127.0.0.1", args.port), timeout=5)
    try:
        _authenticate(sock, token)
        sock.settimeout(1.0)
        messages = _messages(sock)
        if args.connect:
            _pair(sock, messages, args.connect, args.scan_seconds)

        # Written as frames arrive, so a crash keeps what came before.
        with opener(args.out, "wt", encoding="utf-8") as fh:
            started = time.time()
            last_report = started
            reported_mode = False
            for msg in messages:
                if time.time() - started >= args.seconds:
                    break
                if msg is not None and not reported_mode and "bridge_mode" in msg:
                    reported_mode = True
                    print(f"bridge answering: bridge_mode {msg['bridge_mode']}", file=sys.stderr)
                if msg is not None and msg.get("kind") == "optics":
                    # Skip a malformed line rather than abort.
                    if msg.get("mono_ts_ms") is None or msg.get("ch") is None:
                        continue
                    frame = {
                        "seq": msg.get("seq"),
                        "mono_ts_ms": msg["mono_ts_ms"],
                        "n": msg.get("n", len(msg["ch"])),
                        "ch": msg["ch"],
                    }
                    fh.write(json.dumps(frame) + "\n")
                    count += 1
                    if first is None:
                        first = frame
                    last = frame
                    channel_counts.add(frame["n"])
                    if frame["seq"] is not None:
                        seqs.append(frame["seq"])
                now = time.time()
                if now - last_report >= 10:
                    last_report = now
                    print(f"  {int(now - started):>3}s  {count} frames", file=sys.stderr)
    finally:
        sock.close()

    if not count or first is None or last is None:
        print("no optics frames captured -- is the headband connected and "
              "MUSE_ENABLE_OPTICS set?", file=sys.stderr)
        return 1

    span_s = (last["mono_ts_ms"] - first["mono_ts_ms"]) / 1000.0
    rate = count / span_s if span_s > 0 else 0.0
    channels = sorted(channel_counts)
    print(f"wrote {count} frames to {args.out} "
          f"({span_s:.1f}s span, {rate:.1f} Hz, channels={channels})", file=sys.stderr)
    if len(channels) > 1:
        # A preset change mid-capture would make the channel count misleading.
        print("WARNING: channel count changed during capture", file=sys.stderr)
    if seqs:
        gaps = sum(1 for a, b in zip(seqs, seqs[1:]) if b != a + 1)
        missing = (seqs[-1] - seqs[0] + 1) - len(seqs)
        if gaps:
            print(f"WARNING: {gaps} sequence gap(s), {missing} sample(s) missing -- "
                  f"a clock reconstructed from sample index will be wrong across them",
                  file=sys.stderr)
        else:
            print("sequence contiguous: no samples lost", file=sys.stderr)
    else:
        print("NOTE: no seq field -- capture predates it, so sample loss "
              "cannot be ruled out", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
