"""Score a raw (bridge-source) capture: per-segment spectra, 1/f slopes, and
how well each feature separates eyes closed from eyes open per epoch.

    python scripts/analyze_raw_capture.py C:/eeg_captures/2026-09-14_raw.jsonl

This is the analysis EEG_REFERENCE.md's raw-stream section quotes, committed
so its numbers can be re-derived. Two caveats it prints with them:

* **The epochs are not independent.** Each segment is one continuous block
  sliced into adjacent epochs, so an AUC over them is a description of this
  recording, not an estimate with a confidence interval; it rises as the
  epoch count falls, and 1.00 computed on seven per class means only that
  seven adjacent epochs happened to order. Compare epoch lengths by the
  *medians*, and read the AUC as "how cleanly did this one recording
  separate", nothing more.
* **The 1/f slope separates the two states more than any band**, and is
  scored here beside the alpha residual for exactly that reason. It is not
  what calm is built on: whether the slope's shift is neural or the blink
  rate (blinks steepen it) is not something one capture can say, and the
  residual is the feature with a physiological name.

Nothing here writes inside the repo.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.app.services.eeg_spectrum import (  # noqa: E402
    ALPHA_HZ, SAMPLE_RATE_HZ, TEMPORAL, alpha_residual, one_over_f_fit, welch_log_psd,
)

CHANNELS = ("tp9", "af7", "af8", "tp10")


def read_segments(path: str) -> tuple[dict[str, dict[str, np.ndarray]], list[str], dict[str, float]]:
    opener = gzip.open if path.endswith(".gz") else open
    segs: dict[str, dict[str, list[float]]] = {}
    order: list[str] = []
    good: dict[str, list[float]] = {}
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            m = json.loads(line)
            if m.get("kind") != "eeg":
                continue
            s = m.get("segment") or "?"
            if s not in segs:
                segs[s] = {c: [] for c in CHANNELS}
                order.append(s)
                good[s] = []
            for c in CHANNELS:
                segs[s][c].append(float(m[c]))
            good[s].append(sum(1 for v in (m.get("is_good") or []) if v >= 1))
    return ({s: {c: np.asarray(v) for c, v in ch.items()} for s, ch in segs.items()},
            order, {s: float(np.mean(g)) for s, g in good.items()})


def band_residual(f, log_psd, lo, hi, slope, intercept):
    fitted = intercept + slope * np.log10(np.maximum(f, 1e-9))
    band = (f >= lo) & (f < hi)
    return float(np.mean(log_psd[band] - fitted[band]))


def segment_table(segs, order, good):
    print(f"{'segment':20} {'n_s':>5} {'good':>4} | {'slope':>6} | residual above the 1/f fit (log10)")
    print(f"{'':20} {'':>5} {'':>4} | {'':>6} | {'theta4-7':>9} {'alpha8-12':>9} {'beta13-30':>9} {'gamma30-44':>10} | region")
    for s in order:
        n = len(segs[s]["tp9"])
        if n < 8 * SAMPLE_RATE_HZ:
            continue
        for region, chans in (("temporal", TEMPORAL), ("frontal", ("af7", "af8"))):
            f = None
            acc = None
            for c in chans:
                f, lp = welch_log_psd(segs[s][c])
                acc = 10 ** lp if acc is None else acc + 10 ** lp
            log_psd = np.log10(acc / len(chans))
            r_alpha, slope = alpha_residual(f, log_psd)
            # The same fit the residual came from, for the other bands.
            slope, intercept = one_over_f_fit(f, log_psd)
            th = band_residual(f, log_psd, 4, 7, slope, intercept)
            be = band_residual(f, log_psd, 13, 30, slope, intercept)
            ga = band_residual(f, log_psd, 30, 44, slope, intercept)
            tag = s if region == "temporal" else ""
            print(f"{tag:20} {n / SAMPLE_RATE_HZ:5.0f} {good[s]:4.1f} | {slope:6.2f} | "
                  f"{th:9.3f} {r_alpha:9.3f} {be:9.3f} {ga:10.3f} | {region}")


def epochs(seg: dict[str, np.ndarray], seconds: float, chans=TEMPORAL) -> np.ndarray:
    """Per adjacent epoch: (alpha residual, slope), averaged over `chans`."""
    n = int(seconds * SAMPLE_RATE_HZ)
    out = []
    length = len(seg["tp9"])
    for i in range(0, length - n + 1, n):
        vals = []
        for c in chans:
            f, lp = welch_log_psd(seg[c][i:i + n], window_seconds=min(2.0, seconds))
            vals.append(alpha_residual(f, lp))
        out.append(np.mean(vals, axis=0))
    return np.asarray(out)


def auc(pos: np.ndarray, neg: np.ndarray) -> float:
    return float((pos[:, None] > neg[None, :]).mean() + 0.5 * (pos[:, None] == neg[None, :]).mean())


def separation_table(segs, a: str = "eyes_closed_rest", b: str = "eyes_open_rest"):
    print(f"\nPer-epoch separation, {a} against {b}, temporal pair. Adjacent epochs of one "
          "continuous block each: not independent samples, so the AUC describes this "
          "recording only and rises as the epoch count falls.")
    print(f"{'epoch':>6} {'n/class':>8} | {'alpha med A':>11} {'alpha med B':>11} {'AUC alpha':>9} | "
          f"{'slope med A':>11} {'slope med B':>11} {'AUC slope':>9}")
    for secs in (2, 4, 8, 16):
        ea, eb = epochs(segs[a], secs), epochs(segs[b], secs)
        if len(ea) < 2 or len(eb) < 2:
            continue
        print(f"{secs:5d}s {min(len(ea), len(eb)):8d} | {np.median(ea[:, 0]):+11.3f} {np.median(eb[:, 0]):+11.3f} "
              f"{auc(ea[:, 0], eb[:, 0]):9.3f} | {np.median(ea[:, 1]):+11.2f} {np.median(eb[:, 1]):+11.2f} "
              f"{auc(ea[:, 1], eb[:, 1]):9.3f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path")
    args = ap.parse_args(argv)
    segs, order, good = read_segments(args.path)
    segment_table(segs, order, good)
    if "eyes_closed_rest" in segs and "eyes_open_rest" in segs:
        separation_table(segs)
    if "arithmetic" in segs and "eyes_open_rest" in segs:
        separation_table(segs, "arithmetic", "eyes_open_rest")
    return 0


if __name__ == "__main__":
    sys.exit(main())
