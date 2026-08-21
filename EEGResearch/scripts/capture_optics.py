#!/usr/bin/env python3
"""Capture a raw optical recording from the native bridge to a JSONL fixture.

Lets heart-rate derivation be tested against a real recording instead of live
hardware, since a headband session is slow and not reproducible.

Talks to the bridge's TCP port directly rather than through the sidecar, so
this stays a raw capture rather than a second copy of the sidecar's logic.

Usage, with the bridge already running and connected:

    python scripts/capture_optics.py --seconds 120 --out tests/fixtures/optics_rest_64hz.jsonl.gz

`--out` ending in `.gz` is written gzipped, matching the committed fixture
(a two-minute recording is ~640KB plain, ~97KB compressed).

Each line is one optics frame:

    {"seq": 1234, "mono_ts_ms": ..., "n": 4, "ch": [...]}

`seq` is the bridge's sample counter. A gap means a sample was lost, which
matters because the derivation rebuilds its time base from sample index --
see tests/fixtures/README.md. This script warns when it finds a gap.
"""

from __future__ import annotations

import argparse
import gzip
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

    # .gz by extension, matching the committed fixture's format.
    opener = gzip.open if args.out.endswith(".gz") else open

    count = 0
    first: dict | None = None
    last: dict | None = None
    channel_counts: set[int] = set()
    seqs: list[int] = []

    sock = socket.create_connection((args.host, args.port), timeout=5)
    try:
        sock.settimeout(1.0)
        if args.connect:
            sock.sendall(json.dumps({"cmd": "connect", "name": args.connect}).encode() + b"\n")
            print(f"connect requested: {args.connect}", file=sys.stderr)

        # Written as frames arrive, not buffered, so a crash mid-capture
        # doesn't lose everything recorded before it.
        with opener(args.out, "wt", encoding="utf-8") as fh:
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
                    if msg.get("kind") != "optics":
                        continue
                    # .get() throughout: skip a malformed line rather than
                    # abort the capture.
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
