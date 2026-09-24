#!/usr/bin/env python3
"""Capture a raw optical recording from the native bridge to a JSONL fixture.

Reads the bridge's TCP port directly (bridge running and connected); `.gz` output is gzipped
(two minutes: ~640KB plain, ~97KB gzipped).
One {"seq", "mono_ts_ms", "n", "ch"} frame per line; warns on a `seq` gap (time base is by index).
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

        # Written as frames arrive, so a crash keeps what came before.
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
