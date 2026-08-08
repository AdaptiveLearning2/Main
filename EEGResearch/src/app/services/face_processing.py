"""Camera samples to the sidecar's record shape.

Mirrors `SignalProcessor.update`'s contract for the EEG path: take what the
adapter produced since the last tick, return a plain dict describing *now*.
Holds a rolling window and nothing longer; the aggregates a session needs are
computed once per request from stored rows in a later phase, not accumulated
here.

Two payloads, not one
---------------------
`heart` and `face` are separate blocks because they are separately consented,
separately switchable, and separately capable of failing. A student may permit
expression and refuse heart rate, or the reverse, and the common runtime case is
emotion on with rPPG idle — the camera open and FER+ running while POS only
starts scoring on failover from the headband.

Folding them into one block would make that expressible only by nulling fields,
which is precisely the ambiguity the reporting rules exist to prevent: a null
would mean "not consented", "not running", "failed" and "no reading yet" all at
once.

Why `heart.source` is on every payload
--------------------------------------
The same field name carries a rate derived from a headband's contact sensor and
one derived from a webcam, and those are not the same measurement. Camera rate
is a fallback with materially worse accuracy under movement and lighting, and a
consumer that cannot tell which it has cannot weight it correctly — nor can a
teacher reading a chart know why the number got noisier halfway through a
lesson.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from src.app.services.face_emotion import EmotionResult
from src.app.services.face_ingestion import FaceSample
from src.app.services.pos_rppg import pos_pulse
from src.app.services.ppg_processing import estimate_window

# How much colour history the rate estimate is computed over. The rate
# derivation was validated at 25-30 s windows against ECG; shorter windows were
# not, and the autocorrelation needs several beats to be decisive.
RATE_WINDOW_SECONDS = 25.0

# Fraction of the window that must be present before a rate is reported. A
# window half full of samples has gaps, and a gap is not a slow heart -- it is a
# missing measurement that the sample-index time base would silently read as one.
MIN_WINDOW_COVERAGE = 0.80

# The camera's own quality figure, averaged over the window. Named for what it
# is -- the fraction of pixels that survived the luminance mask -- and kept
# apart from the rate's confidence, which is a different quantity entirely.
MIN_MEAN_USABLE_FRACTION = 0.40

# How far the measured frame rate may sit from the configured one before the
# window is rejected.
#
# This is the gate that matters most on this path. The time base is
# reconstructed from sample index, so a camera configured for 30 fps that
# actually delivers 22 does not produce a noisy rate -- it produces one scaled
# by 30/22, a confident +36% with nothing to indicate it. Webcams drop frames
# routinely under load or poor light, so this is the common case rather than a
# fault, and it is exactly the class of failure the motion rule in CLAUDE.md
# exists to prevent: a wrong number that looks like a right one.
MAX_FPS_DEVIATION = 0.15


def build_heart_record(
    rgb_window: np.ndarray,
    fps: float,
    *,
    measured_fps: float | None = None,
    window_quality: float | None = None,
    samples: list[FaceSample] | None = None,
) -> dict[str, Any]:
    """The `heart` block from a window of colour.

    Returns a record whose `bpm` is None whenever the measurement is not
    trustworthy, with `rejected_by` naming which gate stopped it. Never returns
    a zero or a stale value: both would be read downstream as a real rate.
    """
    wanted = int(RATE_WINDOW_SECONDS * fps)
    have = len(rgb_window)
    coverage = (have / wanted) if wanted else 0.0

    record: dict[str, Any] = {
        "source": "rppg",
        "bpm": None,
        "confidence": 0.0,
        "window_coverage": round(coverage, 3),
        "face_quality": None,
        # `is not None`, not truthiness: 0.0 is a measurement, and reporting it
        # as None would say "unmeasurable" about a camera that had stopped.
        "measured_fps": round(measured_fps, 2) if measured_fps is not None else None,
        "rejected_by": None,
    }

    # Quality of the window being scored, not of whatever arrived this tick.
    # Falling back to the drained samples left the gate unapplied on any tick
    # that drained nothing -- routine when ticks outpace the frame rate.
    if window_quality is not None:
        record["face_quality"] = round(float(window_quality), 3)
    elif samples:
        record["face_quality"] = round(
            float(np.mean([s.usable_fraction for s in samples])), 3
        )

    if coverage < MIN_WINDOW_COVERAGE:
        # Distinguished from a failed estimate. The first 25 s of every session
        # land here, and so does every gap after the student looks away; none of
        # those is a fault.
        record["rejected_by"] = "warming_up" if have else "no_samples"
        return record

    if (record["face_quality"] is not None
            and record["face_quality"] < MIN_MEAN_USABLE_FRACTION):
        record["rejected_by"] = "poor_face_quality"
        return record

    if measured_fps is None:
        # No measurement means no time base. Falling back to nominal is exactly
        # the assumption this parameter exists to remove.
        record["rejected_by"] = "unmeasured_frame_rate"
        return record

    if abs(measured_fps - fps) / fps > MAX_FPS_DEVIATION:
        record["rejected_by"] = "unstable_frame_rate"
        return record

    # Everything downstream uses the measured rate, not the configured one.
    estimate = estimate_window(pos_pulse(rgb_window, measured_fps), measured_fps)
    record["confidence"] = round(float(estimate.confidence), 3)
    if estimate.bpm is None:
        record["rejected_by"] = estimate.rejected_by or "confidence"
        return record

    record["bpm"] = round(float(estimate.bpm), 1)
    return record


def build_face_record(
    emotion: EmotionResult | None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The `face` block from one emotion result.

    `emotion_confidence` rather than `confidence`, deliberately. In the original
    the SQI was surfaced under `quality.confidence` while downstream read
    `features.confidence` as confidence in the reading, so a well-lit face and a
    trusted classification became one number. Two quantities, two names.
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
    return record


def build_camera_payload(
    *,
    rgb_window: np.ndarray | None,
    fps: float,
    measured_fps: float | None = None,
    window_quality: float | None = None,
    samples: list[FaceSample] | None = None,
    emotion: EmotionResult | None = None,
    heart_enabled: bool = True,
    emotion_enabled: bool = True,
    emotion_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Both blocks, with the disabled one absent rather than nulled.

    A channel that was switched off is not a channel that failed. Emitting
    `heart: null` for a student who refused heart-rate recording would be
    indistinguishable from a camera that could not get a reading, and a viewer
    would have no way to tell a respected refusal from a broken sensor.
    """
    payload: dict[str, Any] = {}
    if heart_enabled:
        payload["heart"] = build_heart_record(
            rgb_window if rgb_window is not None else np.empty((0, 3)),
            fps,
            measured_fps=measured_fps,
            window_quality=window_quality,
            samples=samples,
        )
    if emotion_enabled:
        payload["face"] = build_face_record(emotion, emotion_meta)
    return payload
