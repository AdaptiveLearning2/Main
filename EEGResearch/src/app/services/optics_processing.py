"""Builds the `heart` record from one optical window.

`ppg_processing` computes the rate; this module decides whether to trust it.
Separate from `face_processing` so a headband-only deployment needs no `face`
extra. Validated seated only: through gait it reports step cadence. See docs/signals.md.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.app.services.eeg_ingestion import OpticsWindow
from src.app.services.hrv_processing import estimate_hrv
from src.app.services.ppg_processing import HeartRateTracker

# Seconds of optical history per rate; autocorrelation needs several beats.
RATE_WINDOW_SECONDS = 25.0

# Emit interval; `MAX_BPM_CHANGE_PER_S` is calibrated against this step. Also a row-rate limit.
EMIT_EVERY_SECONDS = 10.0

# Fraction of the window that must be present; a gap is not a slow heart.
MIN_WINDOW_COVERAGE = 0.80

# Longest inter-sample hole interpolation may fill, in seconds. Sample loss is
# gated by `MIN_SAMPLE_RATE` against `received_rate_hz`, not by this.
MAX_GAP_SECONDS = 1.0

# Hz; Nyquist for 180 bpm plus margin. Applied to both `fs` and `received_rate_hz`:
# below it, interpolation manufactures a confident wrong rate.
MIN_SAMPLE_RATE = 10.0


def build_heart_record(window: OpticsWindow, tracker: HeartRateTracker,
                       seconds_since_previous: float) -> dict[str, Any]:
    """Builds the `heart` block for one optical window.

    Always returns a dict with `source` set; an unmeasurable window has `bpm`
    None and `rejected_by` naming the gate. Callers check the reading, not the block.
    """
    record: dict[str, Any] = {
        # The sensor, not the signal: what consent is enforced against per row.
        "source": "muse_optics",
        # The reading's stamp, not the tick's, so each consumer records it exactly once.
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "bpm": None,
        "confidence": 0.0,
        "window_coverage": round(window.span_seconds / RATE_WINDOW_SECONDS, 3),
        # Measured, not nominal; reported even when it is the rejection reason.
        "sample_rate_hz": round(window.fs, 2) if window.fs is not None else None,
        "received_rate_hz": (round(window.received_rate_hz, 2)
                             if window.received_rate_hz is not None else None),
        "completeness": (round(window.completeness, 3)
                         if window.completeness is not None else None),
        "largest_gap_s": (round(window.largest_gap_seconds, 3)
                          if window.largest_gap_seconds is not None else None),
        "channel_count": window.channel_count or None,
        # This module's verdict, separate from raw `confidence`; always present.
        "trusted": False,
        "rejected_by": None,
        # RMSSD is an enrichment, never a reason to reject the rate.
        "rmssd_ms": None,
        "beat_coverage": None,
        "rmssd_rejected_by": None,
    }

    if window.synthetic:
        # Simulator only; present only when true so hardware records keep their shape.
        record["synthetic"] = True

    if len(window.channels) == 0:
        # A discarded window is not a headband that produced nothing.
        record["rejected_by"] = window.unusable_reason or "no_samples"
        return record

    if record["window_coverage"] < MIN_WINDOW_COVERAGE:
        # Not a fault: every session's first 25 s lands here.
        record["rejected_by"] = "warming_up"
        return record

    if window.fs is None:
        # No time base; falling back to nominal 64 Hz would skew every rate.
        record["rejected_by"] = "unmeasured_sample_rate"
        return record

    if window.fs < MIN_SAMPLE_RATE:
        record["rejected_by"] = "sample_rate_too_low"
        return record

    # Same bar on what arrived rather than what was sent.
    if window.received_rate_hz is None or window.received_rate_hz < MIN_SAMPLE_RATE:
        record["rejected_by"] = "effective_rate_too_low"
        return record

    if window.largest_gap_seconds is not None and window.largest_gap_seconds > MAX_GAP_SECONDS:
        record["rejected_by"] = "sampling_gap"
        return record

    # Through the tracker: octave errors need the previous window, since all channels share them.
    estimate = tracker.update(window.channels, window.fs, seconds_since_previous)
    record["confidence"] = round(float(estimate.confidence), 3)
    if estimate.bpm is None:
        record["rejected_by"] = estimate.rejected_by or "confidence"
        return record
    record["bpm"] = round(float(estimate.bpm), 1)
    record["trusted"] = True

    # Only adds to the record: nothing below may change `bpm`, `trusted` or `rejected_by`.
    hrv = estimate_hrv(window.channels, window.fs,
                       estimate.bpm, estimate.confidence)
    # None when detection never ran; 0.0 would claim no beats were found.
    if hrv.coverage is not None:
        record["beat_coverage"] = round(float(hrv.coverage), 3)
    if hrv.rmssd_ms is None:
        record["rmssd_rejected_by"] = hrv.rejected_by
        return record
    record["rmssd_ms"] = round(float(hrv.rmssd_ms), 1)
    return record
