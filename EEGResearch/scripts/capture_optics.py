#!/usr/bin/env python3
"""Capture a raw optical recording from the native bridge to a JSONL fixture.

Exists so the heart-rate derivation can be developed and tested against a real
recording rather than against live hardware. A headband session is slow, needs
someone wearing it, and is not reproducible; a fixture is none of those things,
and a regression in beat detection should be catchable by `pytest` rather than
by putting the Athena back on.

Talks to the bridge's TCP port directly rather than through the sidecar. The
sidecar's job is to derive state, and asking it to also pass raw samples through
untouched would mean building the thing this script exists to avoid needing.

Usage, with the bridge already running and connected:

    python scripts/capture_optics.py --seconds 120 --out tests/fixtures/optics_rest.jsonl

Each line is one optics frame:  {"mono_ts_ms": ..., "n": 4, "ch": [...]}
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--seconds", type=float, default=60.0)
    ap.add_argument("--out", required=True)
    ap.add_argument("--connect", default="", help="headband name to connect first")
    args = ap.parse_args()

    sock = socket.create_connection((args.host, args.port), timeout=5)
    sock.settimeout(1.0)
    if args.connect:
        sock.sendall(json.dumps({"cmd": "connect", "name": args.connect}).encode() + b"\n")
        print(f"connect requested: {args.connect}", file=sys.stderr)

    frames: list[dict] = []
    buf = b""
    started = time.time()
    last_report = started
    while time.time() - started < args.seconds:
        try:
            chunk = sock.recv(65536)
        except socket.timeout:
            continue
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("kind") == "optics":
                frames.append({"mono_ts_ms": msg["mono_ts_ms"], "n": msg["n"], "ch": msg["ch"]})
        now = time.time()
        if now - last_report >= 10:
            last_report = now
            print(f"  {int(now - started):>3}s  {len(frames)} frames", file=sys.stderr)

    sock.close()
    if not frames:
        print("no optics frames captured -- is the headband connected and "
              "MUSE_ENABLE_OPTICS set?", file=sys.stderr)
        return 1

    with open(args.out, "w", encoding="utf-8") as fh:
        for f in frames:
            fh.write(json.dumps(f) + "\n")

    span_s = (frames[-1]["mono_ts_ms"] - frames[0]["mono_ts_ms"]) / 1000.0
    rate = len(frames) / span_s if span_s > 0 else 0.0
    print(f"wrote {len(frames)} frames to {args.out} "
          f"({span_s:.1f}s span, {rate:.1f} Hz, {frames[0]['n']} channels)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
