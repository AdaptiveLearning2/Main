"""Autonomic arousal (`stress_score`, `stress_category`) against the session's own heart-rate baseline.

Defined on heart rate; RMSSD only nudges the score, never the category. Every threshold here is
a guess, not a measurement. See docs/signals.md.
"""

from __future__ import annotations

import math
from collections import deque
from statistics import median
from typing import Any

# Trusted readings forming the baseline, one per 10 s emit: about a minute after the first rate.
BASELINE_READINGS = 6

# bpm; the baseline forms only once that many consecutive readings sit within this span.
BASELINE_MAX_SPREAD_BPM = 12.0

# Windows in a row with no trusted rate that calibration may bridge; one more and it restarts.
CALIBRATION_MAX_GAP = 3

# bpm above the baseline that reaches 100; the baseline itself scores 50.
HR_SPAN_BPM = 20.0

# bpm above the baseline for each category; docs/signals.md says what error they must clear.
MODERATE_DELTA_BPM = 5.0
HIGH_DELTA_BPM = 10.0

# Score points per halving of RMSSD against its baseline, and the bound either way.
RMSSD_POINTS = 10.0

# Calibration readings carrying an RMSSD needed for an RMSSD baseline; fewer and it is never used.
RMSSD_BASELINE_MIN = 3

# What this module writes; `heart_signals_stress_category_check` must allow each.
CATEGORIES = ("calibrating", "low", "moderate", "high")


class HeartStressScorer:
    """One session's baseline, and the score of each trusted reading against it."""

    def __init__(self) -> None:
        self._calibration: deque[tuple[float, float | None]] = deque(maxlen=BASELINE_READINGS)
        self._gap = 0
        self.baseline_bpm: float | None = None
        self.baseline_rmssd_ms: float | None = None

    def score(self, record: dict[str, Any]) -> None:
        """Adds the stress fields to a heart record, in place. Call once per window.

        Never touches `bpm`, `trusted` or `rejected_by`; a record with no trusted rate gets nulls.
        """
        record["stress_score"] = None
        record["stress_category"] = None
        record["stress_baseline_bpm"] = None
        bpm = record.get("bpm")
        if bpm is None or record.get("trusted") is not True:
            self._gap += 1
            if self.baseline_bpm is None and self._gap > CALIBRATION_MAX_GAP:
                # Readings either side of a long gap are not one settled run.
                self._calibration.clear()
            return
        self._gap = 0
        rmssd = record.get("rmssd_ms")
        rmssd = float(rmssd) if rmssd is not None and rmssd > 0 else None

        if self.baseline_bpm is None:
            self._calibration.append((float(bpm), rmssd))
            if not self._try_baseline():
                record["stress_category"] = "calibrating"
                return

        delta = float(bpm) - self.baseline_bpm
        score = 50.0 + 50.0 * delta / HR_SPAN_BPM
        if rmssd is not None and self.baseline_rmssd_ms is not None:
            # Lower RMSSD than at rest is vagal withdrawal, so it adds.
            nudge = -RMSSD_POINTS * math.log2(rmssd / self.baseline_rmssd_ms)
            score += max(-RMSSD_POINTS, min(RMSSD_POINTS, nudge))
        record["stress_score"] = round(max(0.0, min(100.0, score)), 1)
        record["stress_category"] = ("high" if delta >= HIGH_DELTA_BPM
                                     else "moderate" if delta >= MODERATE_DELTA_BPM else "low")
        record["stress_baseline_bpm"] = round(self.baseline_bpm, 1)

    def _try_baseline(self) -> bool:
        if len(self._calibration) < BASELINE_READINGS:
            return False
        rates = [b for b, _ in self._calibration]
        if max(rates) - min(rates) > BASELINE_MAX_SPREAD_BPM:
            # Not a settled state yet; the oldest reading slides out on the next append.
            return False
        self.baseline_bpm = median(rates)
        rmssds = [r for _, r in self._calibration if r is not None]
        self.baseline_rmssd_ms = median(rmssds) if len(rmssds) >= RMSSD_BASELINE_MIN else None
        return True
