"""Camera samples to the sidecar's record shape.

**The heart block is unvalidated and switched off** (failed ECG validation; see
tests/fixtures/FACE_RPPG_ECG.md). `heart` and `face` are separate blocks: separately
consented, switchable and failing. `heart.source` says whether a rate came from the
headband or a webcam, which are not the same measurement.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from src.app.services.face_emotion import EmotionResult
from src.app.services.face_ingestion import FaceSample
from src.app.services.pos_rppg import largest_gap, pos_pulse, resample_uniform
from src.app.services.ppg_processing import estimate_window

# Seconds of colour history per rate estimate (the window validated against ECG).
RATE_WINDOW_SECONDS = 25.0

# Fraction of the window that must be present; a gap is missing, not a slow heart.
MIN_WINDOW_COVERAGE = 0.80

# Min mean luminance-mask pixel fraction over the window; not the rate's confidence.
MIN_MEAN_USABLE_FRACTION = 0.40

# Samples are placed by their own timestamps (`resample_uniform`); what remains to gate
# is a gap too long to interpolate honestly (seconds) and a rate too low to carry the signal.
MAX_GAP_SECONDS = 1.0

# Hz; Nyquist for 220 bpm is 7.3 Hz, plus margin for waveform shape.
MIN_SAMPLE_RATE = 10.0


def build_heart_record(
    rgb_window: np.ndarray,
    fps: float,
    *,
    measured_fps: float | None = None,
    window_quality: float | None = None,
    samples: list[FaceSample] | None = None,
    timestamps: np.ndarray | None = None,
) -> dict[str, Any]:
    """The `heart` block from a window of colour.

    `bpm` is None whenever untrustworthy, with `rejected_by` naming the gate.
    Never a zero or a stale value.
    """
    # Coverage in seconds of clock, not sample count against the configured rate.
    have = len(rgb_window)
    if timestamps is not None and len(timestamps) == have and have >= 2:
        span = float(timestamps[-1] - timestamps[0])
    else:
        span = (have / fps) if fps else 0.0
    # Not clamped: may exceed 1.0.
    coverage = span / RATE_WINDOW_SECONDS

    record: dict[str, Any] = {
        "source": "rppg",
        "bpm": None,
        "confidence": 0.0,
        # Seconds of history held over seconds wanted; may exceed 1.0.
        "window_coverage": round(coverage, 3),
        "face_quality": None,
        # `is not None`, not truthiness: 0.0 is a real measurement.
        "measured_fps": round(measured_fps, 2) if measured_fps is not None else None,
        "largest_gap_s": None,
        "rejected_by": None,
    }

    # Quality of the window being scored, not of whatever arrived this tick.
    if window_quality is not None:
        record["face_quality"] = round(float(window_quality), 3)
    elif samples:
        record["face_quality"] = round(
            float(np.mean([s.usable_fraction for s in samples])), 3
        )

    if coverage < MIN_WINDOW_COVERAGE:
        # Not a fault: session start and every look-away land here.
        record["rejected_by"] = "warming_up" if have else "no_samples"
        return record

    if (record["face_quality"] is not None
            and record["face_quality"] < MIN_MEAN_USABLE_FRACTION):
        record["rejected_by"] = "poor_face_quality"
        return record

    if measured_fps is None:
        # No time base; never fall back to nominal.
        record["rejected_by"] = "unmeasured_frame_rate"
        return record

    if measured_fps < MIN_SAMPLE_RATE:
        record["rejected_by"] = "frame_rate_too_low"
        return record

    if timestamps is None or len(timestamps) != len(rgb_window):
        # Without a per-sample clock, only sample index is left as a time base.
        record["rejected_by"] = "unmeasured_frame_rate"
        return record

    gap = largest_gap(timestamps)
    record["largest_gap_s"] = round(gap, 3)
    if gap > MAX_GAP_SECONDS:
        record["rejected_by"] = "sampling_gap"
        return record

    grid, grid_fps = resample_uniform(timestamps, rgb_window, measured_fps)
    estimate = estimate_window(pos_pulse(grid, grid_fps), grid_fps)
    record["confidence"] = round(float(estimate.confidence), 3)
    if estimate.bpm is None:
        record["rejected_by"] = estimate.rejected_by or "confidence"
        return record

    record["bpm"] = round(float(estimate.bpm), 1)
    return record


def build_face_record(
    emotion: EmotionResult | None,
    meta: dict[str, Any] | None = None,
    gaze: Any = None,
    gaze_enabled: bool = False,
    pose: Any = None,
) -> dict[str, Any]:
    """The `face` block from one emotion result, one gaze and one head pose.

    `rejected_by` is the emotion refusal; gaze and pose refuse in their own fields.
    Gaze keys are absent when the channel is off, None + reason when refused.
    `attention` is deliberately null with no producer (needs a labelled reference).
    """
    record: dict[str, Any] = {
        "emotion": None,
        "emotion_confidence": None,
        "trusted": False,
        "rejected_by": "no_face" if emotion is None else emotion.rejected_by,
        "degraded": False,
    }
    if emotion is not None:
        record.update(
            emotion=emotion.label,
            emotion_confidence=(round(emotion.confidence, 3)
                                if emotion.confidence is not None else None),
            trusted=emotion.trusted,
            rejected_by=emotion.rejected_by,
        )
    if meta:
        record["degraded"] = bool(meta.get("emotion_degraded", False))

    if gaze_enabled:
        record["attention"] = None
        record["gaze_x"] = None
        record["gaze_y"] = None
        # Pose rides with gaze but refuses independently; gaze_x alone can't tell a
        # turned head with centred eyes from one facing the screen.
        record["head_yaw"] = None
        record["head_pitch"] = None
        record["head_roll"] = None
        record["pose_rejected_by"] = (
            "no_reading" if pose is None else pose.rejected_by)
        if pose is not None and pose.yaw is not None:
            record.update(head_yaw=pose.yaw, head_pitch=pose.pitch,
                          head_roll=pose.roll)
        # "no_reading" is the warming-up state, not a refusal.
        record["gaze_rejected_by"] = "no_reading" if gaze is None else gaze.rejected_by
        if gaze is not None and gaze.x is not None:
            record.update(gaze_x=gaze.x, gaze_y=gaze.y)
    return record


def build_camera_payload(
    *,
    rgb_window: np.ndarray | None,
    fps: float,
    measured_fps: float | None = None,
    window_quality: float | None = None,
    samples: list[FaceSample] | None = None,
    timestamps: np.ndarray | None = None,
    emotion: EmotionResult | None = None,
    heart_enabled: bool = True,
    emotion_enabled: bool = True,
    emotion_meta: dict[str, Any] | None = None,
    gaze: Any = None,
    gaze_enabled: bool = False,
    pose: Any = None,
) -> dict[str, Any]:
    """Both blocks, with a disabled one absent rather than nulled (off is not failed)."""
    payload: dict[str, Any] = {}
    if heart_enabled:
        payload["heart"] = build_heart_record(
            rgb_window if rgb_window is not None else np.empty((0, 3)),
            fps,
            measured_fps=measured_fps,
            window_quality=window_quality,
            samples=samples,
            timestamps=timestamps,
        )
    # Either measurement warrants the block; gaze-only is a valid deployment.
    if emotion_enabled or gaze_enabled:
        payload["face"] = build_face_record(
            emotion if emotion_enabled else None, emotion_meta,
            gaze=gaze, gaze_enabled=gaze_enabled, pose=pose)
    return payload
