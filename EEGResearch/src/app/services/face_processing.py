"""Camera samples to the sidecar's record shape.

**The heart block is unvalidated and switched off.** Read
`tests/fixtures/FACE_RPPG_ECG.md` before enabling it. Measured against a
simultaneous ECG on the cleanest recording this hardware produces: 47.7 bpm
at confidence 0.74 against a true 88, because the pulse simply wasn't in the
video. The confidence figure can't catch that -- it assumes the headband's
four contact channels, and POS produces one waveform. The code stays because
it's correct and is the front half of any future attempt on better hardware;
the claim that it measures a heart rate does not.

Mirrors `SignalProcessor.update`'s contract for the EEG path: take what the
adapter produced since the last tick, return a plain dict describing *now*.
Holds a rolling window only; session aggregates are computed later from
stored rows, not accumulated here.

Two payloads, not one
---------------------
`heart` and `face` are separate blocks because they're separately consented,
switchable, and can fail independently -- e.g. emotion on with rPPG idle,
where the camera runs FER+ but POS only scores on failover from the
headband. Folding them into one block would force nulling fields to express
that, which collapses "not consented", "not running", "failed" and "no
reading yet" into the same value.

Why `heart.source` is on every payload
--------------------------------------
The same field name carries a rate from a headband's contact sensor and one
from a webcam, and those aren't the same measurement -- camera rate is a
fallback, materially worse under movement and lighting. A consumer needs to
know which it has to weight it correctly.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from src.app.services.face_emotion import EmotionResult
from src.app.services.face_ingestion import FaceSample
from src.app.services.pos_rppg import largest_gap, pos_pulse, resample_uniform
from src.app.services.ppg_processing import estimate_window

# How much colour history the rate estimate is computed over. Validated at
# 25-30s windows against ECG; shorter windows weren't, and autocorrelation
# needs several beats to be decisive.
RATE_WINDOW_SECONDS = 25.0

# Fraction of the window that must be present before a rate is reported. A
# window half full of samples has gaps, and a gap is a missing measurement,
# not a slow heart.
MIN_WINDOW_COVERAGE = 0.80

# The camera's own quality figure, averaged over the window: the fraction of
# pixels that survived the luminance mask. Kept apart from the rate's own
# confidence, which is a different quantity.
MIN_MEAN_USABLE_FRACTION = 0.40

# The two gates on the time base.
#
# An earlier version gated on measured fps vs configured fps (reject beyond
# 15%), reasoning that a camera asked for 30 but delivering 22 would scale
# every rate by 30/22 -- a confident +36% error. That failure was real, but
# the gate was the wrong fix: a real webcam's frame intervals turned out
# **bimodal** (78% at 31ms, 21% at 47ms) -- a mixture of two spacings, not
# jitter around a mean, which a deviation gate can't distinguish from a
# broken camera either way.
#
# So samples are placed by their own timestamps instead (`resample_uniform`),
# making the even-spacing assumption true rather than checking it. That
# removes the 30/22 bug at the source. What's left to gate is what
# interpolation can't honestly fill in:
#
#  - a long gap, where the straight line between two samples is invented,
#    not measured, and shows up in the pulse band as a slow ramp
#  - a rate too low to carry the signal at all
MAX_GAP_SECONDS = 1.0

# Nyquist for MAX_BPM (220 bpm = 3.67 Hz) is 7.3 Hz. 10 Hz leaves margin for
# waveform shape, not just the fundamental. Below this it's not a slow
# camera, it's a different measurement.
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

    Returns a record whose `bpm` is None whenever the measurement is not
    trustworthy, with `rejected_by` naming which gate stopped it. Never returns
    a zero or a stale value: both would be read downstream as a real rate.
    """
    # Coverage in *seconds of clock*, not count of samples against the
    # configured rate -- a correctly-working 22fps camera holding a full 25s
    # of history would otherwise look short against a 30fps expectation.
    # What matters is enough elapsed time to contain enough beats.
    have = len(rgb_window)
    if timestamps is not None and len(timestamps) == have and have >= 2:
        span = float(timestamps[-1] - timestamps[0])
    else:
        span = (have / fps) if fps else 0.0
    # Not clamped -- reported as-is even above 1.0. Clamping to 1.0 would
    # hide a window that legitimately holds slightly more than asked for.
    coverage = span / RATE_WINDOW_SECONDS

    record: dict[str, Any] = {
        "source": "rppg",
        "bpm": None,
        "confidence": 0.0,
        # Seconds of history held over seconds wanted; may exceed 1.0.
        "window_coverage": round(coverage, 3),
        "face_quality": None,
        # `is not None`, not truthiness: 0.0 is a real measurement, and
        # reporting it as None would wrongly say "unmeasurable".
        "measured_fps": round(measured_fps, 2) if measured_fps is not None else None,
        "largest_gap_s": None,
        "rejected_by": None,
    }

    # Quality of the window being scored, not of whatever arrived this tick
    # -- a tick draining nothing (routine when ticks outpace the frame rate)
    # would otherwise leave the gate unapplied.
    if window_quality is not None:
        record["face_quality"] = round(float(window_quality), 3)
    elif samples:
        record["face_quality"] = round(
            float(np.mean([s.usable_fraction for s in samples])), 3
        )

    if coverage < MIN_WINDOW_COVERAGE:
        # Distinguished from a failed estimate. The first 25s of every
        # session, and every gap after the student looks away, land here --
        # none of that is a fault.
        record["rejected_by"] = "warming_up" if have else "no_samples"
        return record

    if (record["face_quality"] is not None
            and record["face_quality"] < MIN_MEAN_USABLE_FRACTION):
        record["rejected_by"] = "poor_face_quality"
        return record

    if measured_fps is None:
        # No measurement means no time base -- falling back to nominal is
        # exactly the assumption this parameter exists to remove.
        record["rejected_by"] = "unmeasured_frame_rate"
        return record

    if measured_fps < MIN_SAMPLE_RATE:
        record["rejected_by"] = "frame_rate_too_low"
        return record

    if timestamps is None or len(timestamps) != len(rgb_window):
        # No per-sample clock means the only time base left is sample index
        # -- the assumption that produced the 30/22 error above.
        record["rejected_by"] = "unmeasured_frame_rate"
        return record

    gap = largest_gap(timestamps)
    record["largest_gap_s"] = round(gap, 3)
    if gap > MAX_GAP_SECONDS:
        record["rejected_by"] = "sampling_gap"
        return record

    # Placed on an even grid by their own timestamps, so POS and the
    # autocorrelation can safely assume even sample spacing.
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

    `emotion_confidence` rather than `confidence`, deliberately, to keep
    "face is well-lit" and "classification is trusted" as two named
    quantities instead of one ambiguous number.

    **Two independent measurements, not one.** Emotion comes from FER+ over
    a crop; gaze comes from face-mesh landmarks. Either can succeed while
    the other fails, so `gaze_rejected_by` is its own field and
    `rejected_by` keeps meaning the *emotion* refusal -- same split as
    `rmssd_rejected_by` on the heart block, for the same reason: one field
    can't say which of two things failed.

    Gaze keys are **absent** when the channel is off, rather than null --
    the same three-state rule used everywhere else in the payload:

    * key absent          -- gaze not enabled on this deployment
    * `None` + a reason   -- measured and refused (no eye, closed eye, no face)
    * a number            -- a reading

    **`attention` is deliberately null and has no producer.** Blocked on a
    labelled reference, not on code: a head-direction attention score is
    least valid for this product's users, and it would render as an
    objective-looking percentage. It rides with the gaze keys because it
    would be derived from gaze and head pose, so it's absent for the same
    reason they are when the channel is off.
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
        # Head pose rides with gaze (same landmark set, cadence, enable flag)
        # but refuses independently -- near profile the pose fit refuses
        # while the eyes stay readable, and a closed eye refuses gaze while
        # pose is fine. `gaze_x` is where the eyes point *within the head*;
        # without pose, a student turned away with centred eyes would read
        # identically to one facing the screen.
        record["head_yaw"] = None
        record["head_pitch"] = None
        record["head_roll"] = None
        record["pose_rejected_by"] = (
            "no_reading" if pose is None else pose.rejected_by)
        if pose is not None and pose.yaw is not None:
            record.update(head_yaw=pose.yaw, head_pitch=pose.pitch,
                          head_roll=pose.roll)
        # "no_reading" is not a refusal, just the state before the landmarker
        # has produced anything (lasts one gaze interval at start-up).
        # Calling it a rejection would report a warming-up camera as broken.
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
    """Both blocks, with the disabled one absent rather than nulled.

    A channel that was switched off is not a channel that failed. Emitting
    `heart: null` for a student who refused heart-rate recording would be
    indistinguishable from a camera that got no reading.
    """
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
    # Either measurement is enough to warrant the block -- gating on emotion
    # alone would make a gaze-only deployment (a valid choice, since gaze
    # needs no FER+ model) emit nothing at all.
    if emotion_enabled or gaze_enabled:
        payload["face"] = build_face_record(
            emotion if emotion_enabled else None, emotion_meta,
            gaze=gaze, gaze_enabled=gaze_enabled, pose=pose)
    return payload
