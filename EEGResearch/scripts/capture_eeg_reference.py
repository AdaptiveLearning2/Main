#!/usr/bin/env python3
"""Capture a labelled EEG reference for scoring focus / calm / confidence.

Nothing in the shipped pipeline has ground truth for the cognitive scores:
the simulator emits band values solved *from* the scoring formulas, so every
green test on that path is the formula agreeing with itself. This script
records what a real headband produces while the wearer does known things
(eyes closed, mental arithmetic, a jaw clench, ...) so the formulas can be
scored against something they did not generate. See HANDOFF.md, Phase 0.

Two sources, because the bridge accepts exactly one TCP client:

* ``--source sidecar`` (default) polls the sidecar's ``/api/v1/state`` and
  writes one row per sidecar tick -- the SDK band values, contact, the raw
  log ratios *before* baseline scaling, the scaled scores, confidence,
  quality and the label. This is what Phase 0 scores. It needs the sidecar
  running with ``EEG_SOURCE=muse`` (``./start.ps1 -Muse``).
* ``--source bridge`` connects to the native bridge directly and records
  every 256 Hz EEG frame, each carrying the band values and contact the
  bridge stamps on it. The sidecar must **not** be connected to the bridge
  at the time, because it would hold the one client slot. Features can be
  recovered from such a capture by replaying it through ``SignalProcessor``.

Each row carries a ``segment`` set by a prompted protocol: the script says
what to do, waits for Enter, then times the segment. ``--protocol none``
with ``--seconds N`` records a free-running capture instead.

Usage, sidecar running and the headband paired with good contact::

    python scripts/capture_eeg_reference.py --out ../../eeg_captures/2026-09-07_a.jsonl

``--summarize PATH`` re-reads a sidecar capture and prints the per-segment
figures without recording anything.

**The output must be outside the repository.** A named person's EEG is the
one artefact here that must never be committable, and ``git add -A`` does
not ask. Nothing about who the wearer is goes into the file: no name, no
account, only a date and the protocol.
"""

from __future__ import annotations

import argparse
import gzip
import json
import pathlib
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from statistics import fmean, pstdev
from typing import Any

SCRIPT_VERSION = 1

# (segment, seconds, instruction). HANDOFF.md Phase 0 step 3. Names are
# what the summary groups on; keep them stable across captures so two
# days' numbers line up.
DEFAULT_PROTOCOL: list[tuple[str, int, str]] = [
    ("eyes_closed_rest", 120, "Close your eyes and rest. Stay still."),
    ("eyes_open_rest", 120, "Eyes open, rest, look at a blank wall."),
    ("arithmetic", 120, "Mental arithmetic aloud: multiply two-digit numbers "
                        "(47 x 23, 68 x 19, ...), one after another, no pauses."),
    ("eyes_open_rest_2", 60, "Eyes open, rest."),
    ("jaw_clench", 10, "Clench your jaw firmly and hold it."),
    ("rest_after_clench", 10, "Relax the jaw. Stay still."),
    ("blinking", 10, "Blink deliberately, about once a second."),
    ("rest_after_blinking", 10, "Relax. Stay still."),
    ("fidget", 30, "Turn your head side to side and shift in your seat."),
]
SESSION_SEGMENT: tuple[str, int, str] = (
    "adaptive_session", 120, "Answer questions on the student page as normal.",
)
# Rows recorded while waiting for Enter, or with no protocol at all.
BETWEEN = "between"
FREE = "free"

# The features the summary reports per segment, in print order. Raw ratios
# first: they are what Phase 0 exists to look at.
SUMMARY_FIELDS = (
    "focus_log_ratio", "calm_log_ratio",
    "focus_score", "calm_score", "confidence",
    "alpha", "beta", "theta", "gamma", "delta",
)


# -- where a capture may be written --

def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def refuse_if_inside_repo(out: pathlib.Path) -> None:
    """A capture must not land anywhere git can reach it -- same guard as
    capture_face_video_ecg.py, for the same reason."""
    root = repo_root().resolve()
    try:
        out.resolve().relative_to(root)
    except ValueError:
        return
    raise SystemExit(
        f"refusing to write inside the repository ({root}): a person's EEG must "
        "not be committable. Pass an --out path outside it."
    )


def _opener(path: str):
    return gzip.open if path.endswith(".gz") else open


# -- rows --

def flatten_state(envelope: dict[str, Any], *, segment: str, t: str) -> dict[str, Any]:
    """One flat row from a /api/v1/state envelope.

    `.get` throughout and None for anything absent: an older sidecar that
    does not report `focus_log_ratio` produces a column of nulls, which is
    distinguishable from a ratio of 0.0 -- the summary counts the two apart.
    """
    data = envelope.get("data") or {}
    features = data.get("features") or {}
    state = data.get("state") or {}
    bands = data.get("bands") or {}
    ing = data.get("ingestion") or {}
    channels = data.get("channels") or {}
    return {
        "t": t,
        "segment": segment,
        "status": envelope.get("status"),
        "message": envelope.get("message") if envelope.get("status") != "ok" else None,
        "ts": data.get("timestamp"),
        # SDK band values as the bridge sent them (already channel-averaged).
        "delta": bands.get("delta"),
        "theta": bands.get("theta"),
        "alpha": bands.get("alpha"),
        "beta": bands.get("beta"),
        "gamma": bands.get("gamma"),
        # Contact and link, from the ingestion block.
        "band_channels_used": ing.get("band_channels_used"),
        "hsi": ing.get("hsi"),
        "is_good": ing.get("is_good"),
        "eeg_age_ms": ing.get("eeg_age_ms"),
        "active_preset": ing.get("active_preset"),
        "muse_connected": ing.get("muse_connected"),
        # The one raw sample the tick scored (the newest drained).
        "tp9": channels.get("tp9"),
        "af7": channels.get("af7"),
        "af8": channels.get("af8"),
        "tp10": channels.get("tp10"),
        # Unscaled log ratios, then everything downstream of them.
        "focus_log_ratio": features.get("focus_log_ratio"),
        "calm_log_ratio": features.get("calm_log_ratio"),
        "focus_score": features.get("focus_score"),
        "calm_score": features.get("calm_score"),
        "confidence": features.get("confidence"),
        "signal_quality": features.get("signal_quality"),
        "quality_basis": features.get("quality_basis"),
        "samples_rejected": features.get("samples_rejected"),
        "batch_size": features.get("batch_size"),
        "label": state.get("label"),
        "reason": state.get("reason"),
    }


def header(args: argparse.Namespace, protocol: list[tuple[str, int, str]]) -> dict[str, Any]:
    """Method, not identity. Deliberately no subject field."""
    return {
        "header": True,
        "script_version": SCRIPT_VERSION,
        "written": datetime.now(timezone.utc).isoformat(),
        "source": args.source,
        "poll_hz": args.hz,
        "protocol": [{"segment": s, "seconds": n} for s, n, _ in protocol],
        "notes": args.notes or None,
    }


# -- the protocol --

class SegmentClock:
    """Shared between the prompting thread and the capture loop."""

    def __init__(self, initial: str) -> None:
        self._lock = threading.Lock()
        self._segment = initial
        self.done = threading.Event()

    @property
    def segment(self) -> str:
        with self._lock:
            return self._segment

    @segment.setter
    def segment(self, value: str) -> None:
        with self._lock:
            self._segment = value


def run_protocol(clock: SegmentClock, protocol: list[tuple[str, int, str]],
                 *, prompt: bool) -> None:
    """Announce each segment, wait for Enter (or a 5 s settle), time it."""
    try:
        for i, (segment, seconds, instruction) in enumerate(protocol, 1):
            print(f"\n[{i}/{len(protocol)}] {segment} -- {seconds}s", file=sys.stderr)
            print(f"    {instruction}", file=sys.stderr)
            if prompt:
                input("    Press Enter to start...")
            else:
                print("    starting in 5s", file=sys.stderr)
                time.sleep(5)
            clock.segment = segment
            started = time.monotonic()
            while True:
                left = seconds - (time.monotonic() - started)
                if left <= 0:
                    break
                if seconds >= 30 and int(left) % 30 == 0:
                    print(f"    {int(left)}s left", file=sys.stderr)
                time.sleep(min(1.0, left))
            clock.segment = BETWEEN
        print("\nprotocol complete", file=sys.stderr)
    finally:
        clock.done.set()


# -- sidecar source --

def fetch_state(url: str, token: str, timeout: float = 2.0) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def capture_sidecar(args: argparse.Namespace, clock: SegmentClock, fh) -> int:
    url = f"http://{args.host}:{args.port}/api/v1/state"
    period = 1.0 / args.hz
    rows = 0
    dupes = 0
    errors = 0
    last_ts: str | None = None
    last_report = time.monotonic()
    deadline = None if args.seconds is None else time.monotonic() + args.seconds
    while not clock.done.is_set():
        if deadline is not None and time.monotonic() >= deadline:
            break
        loop_started = time.monotonic()
        t = datetime.now(timezone.utc).isoformat()
        try:
            env = fetch_state(url, args.token)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors += 1
            if errors == 1 or errors % 40 == 0:
                print(f"  state read failed ({errors}): {exc}", file=sys.stderr)
            env = {"status": "error", "message": str(exc), "data": None}
        row = flatten_state(env, segment=clock.segment, t=t)
        # Polling faster than the sidecar ticks means seeing one tick twice;
        # the tick's own timestamp says which reads are new.
        if row["ts"] is not None and row["ts"] == last_ts and not args.keep_duplicates:
            dupes += 1
        else:
            fh.write(json.dumps(row) + "\n")
            rows += 1
            last_ts = row["ts"]
        now = time.monotonic()
        if now - last_report >= 10:
            last_report = now
            print(f"  {rows} rows  ({clock.segment}, quality={row['signal_quality']}, "
                  f"label={row['label']}, focus_lr={row['focus_log_ratio']})", file=sys.stderr)
        time.sleep(max(0.0, period - (time.monotonic() - loop_started)))
    print(f"wrote {rows} rows ({dupes} duplicate ticks skipped, {errors} read errors)",
          file=sys.stderr)
    return rows


# -- bridge source --

def capture_bridge(args: argparse.Namespace, clock: SegmentClock, fh) -> int:
    try:
        sock = socket.create_connection((args.host, args.port), timeout=5)
    except OSError as exc:
        print(f"cannot reach the bridge on {args.host}:{args.port}: {exc}\n"
              "The bridge takes one client. If the sidecar is running with "
              "EEG_SOURCE=muse it holds that slot -- stop it first.", file=sys.stderr)
        return 0
    frames = 0
    other = 0
    first_ms: float | None = None
    last_ms: float | None = None
    deadline = None if args.seconds is None else time.monotonic() + args.seconds
    last_report = time.monotonic()
    try:
        sock.settimeout(1.0)
        buf = b""
        while not clock.done.is_set():
            if deadline is not None and time.monotonic() >= deadline:
                break
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                print("bridge closed the connection", file=sys.stderr)
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
                kind = msg.get("kind")
                if kind not in ("eeg", "status"):
                    continue
                # The frame as sent, plus when and during what. Band values
                # and contact ride on every EEG frame already.
                msg["t"] = datetime.now(timezone.utc).isoformat()
                msg["segment"] = clock.segment
                fh.write(json.dumps(msg) + "\n")
                if kind == "eeg":
                    frames += 1
                    ms = msg.get("mono_ts_ms")
                    if isinstance(ms, (int, float)):
                        first_ms = ms if first_ms is None else first_ms
                        last_ms = ms
                else:
                    other += 1
            now = time.monotonic()
            if now - last_report >= 10:
                last_report = now
                print(f"  {frames} eeg frames ({clock.segment})", file=sys.stderr)
    finally:
        sock.close()
    if frames and first_ms is not None and last_ms is not None and last_ms > first_ms:
        rate = frames / ((last_ms - first_ms) / 1000.0)
        print(f"wrote {frames} eeg frames, {other} status lines ({rate:.1f} Hz by "
              "delivery stamp; EEG frames carry no seq, so loss cannot be counted)",
              file=sys.stderr)
    else:
        print(f"wrote {frames} eeg frames, {other} status lines", file=sys.stderr)
    return frames


# -- scoring --

def _stats(values: list[float]) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    return fmean(values), (pstdev(values) if len(values) > 1 else 0.0)


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-segment mean and spread of each score, with None counted apart
    from zero: a null ratio is a frame with no usable bands, and folding it
    into the mean as 0 would make bad contact read as low focus."""
    segments: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        seg = row.get("segment") or FREE
        if seg not in segments:
            segments[seg] = {"rows": 0, "labels": {}, "quality": {}, "fields": {}}
            order.append(seg)
        s = segments[seg]
        s["rows"] += 1
        s["labels"][row.get("label")] = s["labels"].get(row.get("label"), 0) + 1
        s["quality"][row.get("signal_quality")] = s["quality"].get(row.get("signal_quality"), 0) + 1
        for field in SUMMARY_FIELDS:
            f = s["fields"].setdefault(field, {"values": [], "missing": 0})
            v = row.get(field)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                f["values"].append(float(v))
            else:
                f["missing"] += 1
    out: dict[str, Any] = {"segments": {}, "order": order}
    for seg in order:
        s = segments[seg]
        fields = {}
        for field, f in s["fields"].items():
            mean, sd = _stats(f["values"])
            fields[field] = {"n": len(f["values"]), "missing": f["missing"], "mean": mean, "sd": sd}
        out["segments"][seg] = {
            "rows": s["rows"], "labels": s["labels"], "quality": s["quality"], "fields": fields,
        }
    # What the baseline latched on: BASELINE_SAMPLES (60) usable ticks, so
    # the first 60 rows with a ratio say what the wearer was doing then.
    usable = [r for r in rows if isinstance(r.get("focus_log_ratio"), (int, float))]
    first = usable[:60]
    latch: dict[str, int] = {}
    for r in first:
        seg = r.get("segment") or FREE
        latch[seg] = latch.get(seg, 0) + 1
    out["baseline_window_segments"] = latch
    out["baseline_window_rows"] = len(first)
    return out


def print_summary(summary: dict[str, Any]) -> None:
    def fmt(x: float | None) -> str:
        return "   --  " if x is None else f"{x:7.3f}"

    for seg in summary["order"]:
        s = summary["segments"][seg]
        print(f"\n== {seg}  ({s['rows']} rows)")
        print(f"   labels:  {s['labels']}")
        print(f"   quality: {s['quality']}")
        print(f"   {'field':<18} {'n':>5} {'null':>5} {'mean':>8} {'sd':>8}")
        for field, f in s["fields"].items():
            print(f"   {field:<18} {f['n']:>5} {f['missing']:>5} {fmt(f['mean']):>8} {fmt(f['sd']):>8}")
    print(f"\nbaseline window: first {summary['baseline_window_rows']} usable ticks fell in "
          f"{summary['baseline_window_segments']}")


def read_rows(path: str) -> list[dict[str, Any]]:
    rows = []
    with _opener(path)(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("header"):
                continue
            rows.append(row)
    return rows


# -- entry --

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", help="capture path, outside the repo; .gz is gzipped")
    ap.add_argument("--summarize", metavar="PATH",
                    help="print per-segment figures for an existing sidecar capture and exit")
    ap.add_argument("--source", choices=("sidecar", "bridge"), default="sidecar")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=None,
                    help="8001 for the sidecar, 8765 for the bridge (default by --source)")
    ap.add_argument("--token", default=None,
                    help="sidecar API_TOKEN (default: env API_TOKEN); sidecar source only")
    ap.add_argument("--hz", type=float, default=8.0,
                    help="poll rate for the sidecar source; twice the tick rate so no tick is missed")
    ap.add_argument("--protocol", choices=("default", "none"), default="default")
    ap.add_argument("--with-session", action="store_true",
                    help="append the optional adaptive-session segment")
    ap.add_argument("--no-prompt", action="store_true",
                    help="auto-advance segments after a 5s settle instead of waiting for Enter")
    ap.add_argument("--seconds", type=float, default=None,
                    help="stop after this long (required with --protocol none)")
    ap.add_argument("--keep-duplicates", action="store_true",
                    help="write a row on every poll even when the sidecar tick has not advanced")
    ap.add_argument("--notes", default="", help="method notes for the header -- no names")
    return ap


def main(argv: list[str] | None = None) -> int:
    import os

    args = build_parser().parse_args(argv)
    if args.summarize:
        print_summary(summarize(read_rows(args.summarize)))
        return 0
    if not args.out:
        print("--out is required (or --summarize PATH)", file=sys.stderr)
        return 2
    out = pathlib.Path(args.out)
    refuse_if_inside_repo(out)
    if args.port is None:
        args.port = 8001 if args.source == "sidecar" else 8765
    if args.source == "sidecar":
        args.token = args.token or os.environ.get("API_TOKEN")
        if not args.token:
            print("sidecar source needs --token or API_TOKEN", file=sys.stderr)
            return 2
    if args.protocol == "none" and args.seconds is None:
        print("--protocol none needs --seconds", file=sys.stderr)
        return 2

    protocol = list(DEFAULT_PROTOCOL) if args.protocol == "default" else []
    if args.with_session:
        protocol.append(SESSION_SEGMENT)
    clock = SegmentClock(BETWEEN if protocol else FREE)
    if protocol:
        threading.Thread(target=run_protocol, args=(clock, protocol),
                         kwargs={"prompt": not args.no_prompt}, daemon=True).start()

    out.parent.mkdir(parents=True, exist_ok=True)
    # Written as rows arrive, not buffered, so a crash mid-capture keeps
    # everything recorded before it.
    with _opener(args.out)(args.out, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps(header(args, protocol)) + "\n")
        if args.source == "sidecar":
            n = capture_sidecar(args, clock, fh)
        else:
            n = capture_bridge(args, clock, fh)
    if not n:
        return 1
    if args.source == "sidecar":
        print_summary(summarize(read_rows(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
