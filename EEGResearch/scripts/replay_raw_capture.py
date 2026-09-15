"""Replay a raw (bridge-source) capture through the local spectrum and the
processor on the "local" calm source, and print the per-segment result.

    python scripts/replay_raw_capture.py C:/eeg_captures/2026-09-14_raw.jsonl

Feeds every frame to `SpectrumEstimator` in order at the bridge's nominal
rate and scores one tick per `--hz` through `SignalProcessor(calm_source=
"local")` with the frame's SDK bands and contact, as the sidecar would. The
per-segment medians of the alpha residual are the numbers EEG_REFERENCE.md
quotes for the capture; a change to the estimator is scored by whether they
move. Nothing here writes inside the repo.
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
from src.app.services.adaptation import AdaptationEngine  # noqa: E402
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


def replay(path: str, hz: float = 4.0, arm_at: str | None = None) -> dict[str, dict]:
    est = SpectrumEstimator()
    clock = [0.0]
    proc = SignalProcessor(calm_source="local", clock=lambda: clock[0])
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
            # As DeviceSession._loop does: the gate held this tick and the
            # window still holds the blink. Without it a blink contaminated
            # four seconds of estimates here that the sidecar withholds.
            est.poison()
        state = eng.infer_state(f)
        bucket = out.setdefault(seg, {"alpha": [], "calm": [], "focus": [], "labels": {}, "n": 0})
        bucket["n"] += 1
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
    args = ap.parse_args(argv)
    out = replay(args.path, hz=args.hz, arm_at=args.arm_at)
    print(f"{'segment':20} {'ticks':>5} {'alpha resid':>11} {'calm':>6} {'focus':>6}  labels")
    for seg, b in out.items():
        med = lambda xs: f"{statistics.median(xs):+.3f}" if xs else "   --"
        print(f"{seg:20} {b['n']:5d} {med(b['alpha']):>11} {statistics.median(b['calm']):6.1f} "
              f"{statistics.median(b['focus']):6.1f}  {dict(sorted(b['labels'].items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
