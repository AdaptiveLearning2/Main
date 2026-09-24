"""Replay a raw (bridge-source) capture through the local spectrum and the
processor on the "local" calm source, and print the per-segment result.

    python scripts/replay_raw_capture.py C:/eeg_captures/2026-09-14_raw.jsonl

Scores one tick per `--hz` as the sidecar would; the per-segment alpha-residual medians are
EEG_REFERENCE.md's numbers. Writes nothing inside the repo.
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.app.models import EegSample  # noqa: E402
from src.app.services.adaptation import CALM_HOLD_MAX_SECONDS, AdaptationEngine  # noqa: E402
from src.app.services.eeg_spectrum import SAMPLE_RATE_HZ, SpectrumEstimator, poisons_buffer  # noqa: E402
from src.app.services.signal_processing import SignalProcessor  # noqa: E402

BANDS = ("delta", "theta", "alpha", "beta", "gamma")


def read_frames(path: str):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            m = json.loads(line)
            if m.get("kind") == "eeg":
                yield m


def replay(path: str, hz: float = 4.0, arm_at: str | None = None,
           poison_seconds: float | None = None, calm_centre_on_arm: str = "keep") -> dict[str, dict]:
    """Replay under one poison duration and arm-centring choice; defaults are the shipped behaviour."""
    est = SpectrumEstimator(poison_seconds=poison_seconds)
    clock = [0.0]
    proc = SignalProcessor(calm_source="local", clock=lambda: clock[0],
                           calm_centre_on_arm=calm_centre_on_arm)
    eng = AdaptationEngine(clock=lambda: clock[0])
    per_tick = max(1, int(round(SAMPLE_RATE_HZ / hz)))
    pending: list[EegSample] = []
    out: dict[str, dict] = {}
    armed = False
    for i, m in enumerate(read_frames(path)):
        ts = datetime.fromisoformat(m["t"])
        pending.append(EegSample(timestamp=ts, channel_tp9=float(m["tp9"]), channel_af7=float(m["af7"]),
                                 channel_af8=float(m["af8"]), channel_tp10=float(m["tp10"])))
        if len(pending) < per_tick:
            continue
        seg = m.get("segment") or "?"
        if arm_at is not None and not armed and seg == arm_at:
            proc.restart_baseline()
            eng.restart()
            armed = True
        clock[0] = i / SAMPLE_RATE_HZ
        meta = {k: m.get(k) for k in (*BANDS, "hsi", "is_good", "band_channels_used")}
        spectrum = est.push(pending, meta)
        last, pending = pending[-1], []
        f = proc.update(last, meta, spectrum=spectrum)
        if poisons_buffer(f.get("artifact_reason")):
            # As DeviceSession._loop does: the window still holds the artifact.
            est.poison()
        state = eng.infer_state(f)
        bucket = out.setdefault(seg, {"alpha": [], "calm": [], "focus": [], "labels": {}, "n": 0,
                                      "fresh": 0, "artifact": 0, "poisoned": 0, "stale": 0})
        bucket["n"] += 1
        # Local-calm availability: fresh, artifact-held, poison-withheld, past the hold cap.
        bucket["fresh"] += bool(f["spectrum_ready"])
        bucket["artifact"] += poisons_buffer(f.get("artifact_reason"))
        bucket["poisoned"] += f.get("spectrum_reason") == "artifact"
        bucket["stale"] += (f.get("calm_held_seconds") or 0) > CALM_HOLD_MAX_SECONDS
        if f["calm_alpha_residual"] is not None:
            bucket["alpha"].append(f["calm_alpha_residual"])
        bucket["calm"].append(f["calm_score"])
        bucket["focus"].append(f["focus_score"])
        bucket["labels"][state.label] = bucket["labels"].get(state.label, 0) + 1
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    ap.add_argument("--hz", type=float, default=4.0)
    ap.add_argument("--arm-at", metavar="SEGMENT", help="restart the baseline at this segment")
    ap.add_argument("--poison-seconds", type=float, default=None,
                    help="how long an artifact withholds estimates (default: the 4 s buffer)")
    ap.add_argument("--calm-centre-on-arm", choices=("keep", "midpoint"), default="keep",
                    help="what calm is centred on between the arm and its new latch")
    ap.add_argument("--matrix", action="store_true",
                    help="run every combination of the two decisions (4 s / 2 s poison x keep / "
                         "midpoint centre) and print each")
    args = ap.parse_args(argv)
    settings = ([(p, c) for p in (None, 2.0) for c in ("keep", "midpoint")] if args.matrix
                else [(args.poison_seconds, args.calm_centre_on_arm)])
    for poison, centre in settings:
        out = replay(args.path, hz=args.hz, arm_at=args.arm_at,
                     poison_seconds=poison, calm_centre_on_arm=centre)
        print(f"\n=== poison {poison if poison is not None else 4.0:.0f} s, calm centre on arm: {centre}")
        print_report(out)
    return 0


LINES = (0.377, 0.30, 0.25, 0.20)


def print_report(out: dict[str, dict]) -> None:
    print(f"{'segment':20} {'ticks':>5} {'alpha resid':>11} {'calm':>6} {'focus':>6} | "
          f"{'fresh':>5} {'artif':>5} {'poisn':>5} {'stale':>5} | "
          + " ".join(f"{'<' + str(l).lstrip('0'):>6}" for l in LINES) + " | labels")
    for seg, b in out.items():
        med = lambda xs: f"{statistics.median(xs):+.3f}" if xs else "   --"
        pct = lambda k: f"{100 * b[k] / b['n']:4.0f}%"
        calm = [c / 100 for c in b["calm"]]
        # Share of ticks under each candidate stressed line.
        under = " ".join(f"{100 * sum(c < l for c in calm) / len(calm):5.0f}%" for l in LINES)
        print(f"{seg:20} {b['n']:5d} {med(b['alpha']):>11} {statistics.median(b['calm']):6.1f} "
              f"{statistics.median(b['focus']):6.1f} | {pct('fresh')} {pct('artifact')} "
              f"{pct('poisoned')} {pct('stale')} | {under} | {dict(sorted(b['labels'].items()))}")
    print("fresh: a new estimate this tick; artif: held by the artifact gate (poisons the buffer); "
          "poisn: no estimate because of an earlier artifact; stale: calm carried past "
          f"{CALM_HOLD_MAX_SECONDS:.0f} s, where the backend nulls stress; <x: share of ticks "
          "under that candidate stressed line.")


if __name__ == "__main__":
    sys.exit(main())
