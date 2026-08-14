"""Camera samples to the sidecar's record shape.

**The heart block is unvalidated and switched off.** Read
`tests/fixtures/FACE_RPPG_ECG.md` before enabling it. Measured against a
simultaneous ECG it reported 47.7 bpm at confidence 0.74 against a true 88, on
the cleanest recording this hardware produces, because the pulse was not present
in the video at all. The confidence figure below cannot catch that: every term
in it assumes the headband's four contact channels, and POS produces one
waveform. The code stays because it is correct and is the front half of any
future attempt on hardware whose exposure locks; the claim that it measures a
heart rate does not.

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
from src.app.services.pos_rppg import largest_gap, pos_pulse, resample_uniform
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

# The two gates on the time base.
#
# An earlier revision had one gate here -- measured fps against configured fps,
# reject beyond 15% -- on the reasoning that the time base came from sample
# index, so a camera asked for 30 and delivering 22 would report a rate scaled
# by 30/22: a confident +36% with nothing to indicate it. The failure was real.
# The gate was the wrong repair, and measuring a real webcam showed why.
#
# That camera's frame intervals were **bimodal**: 78% at 31 ms, 21% at 47 ms.
# Not jitter around a mean -- a mixture of two spacings, which no single rate
# describes. A deviation gate has nothing correct to do with that. Set tight it
# rejects a camera that is working; set loose it admits the distortion it exists
# to catch. Either way it is asking whether the samples are evenly spaced, when
# they simply are not and will not be.
#
# So the samples are placed by their own timestamps instead (`resample_uniform`)
# and the assumption of even spacing is *made true* rather than checked. The
# configured rate stops being part of the arithmetic entirely, which removes the
# 30/22 bug at its source instead of declining to report when it fires.
#
# What is left to gate is what interpolation cannot honestly fill:
#
#  - a long gap, where the straight line between two samples is invention rather
#    than measurement, and lands in the pulse band as a slow ramp
#  - a rate too low to carry the signal at all
MAX_GAP_SECONDS = 1.0

# Nyquist for MAX_BPM (220 bpm = 3.67 Hz) is 7.3 Hz. 10 Hz leaves margin for the
# waveform's shape rather than only its fundamental. Below this the recording is
# not a slow camera, it is a different measurement.
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
    # Coverage in *seconds of clock*, not in count of samples against the
    # configured rate. Counting samples asks "did we get as many frames as a
    # 30 fps camera would have", which a correctly-working 22 fps camera fails
    # while holding a full 25 seconds of history -- the same nominal-fps
    # assumption the time base itself had to shed. What the estimate needs is
    # enough elapsed time to contain enough beats, and that is what this counts.
    have = len(rgb_window)
    if timestamps is not None and len(timestamps) == have and have >= 2:
        span = float(timestamps[-1] - timestamps[0])
    else:
        span = (have / fps) if fps else 0.0
    # Not clamped, and reported as-is above 1.0. The window is sliced by
    # timestamp so it will sit near 1.0 in normal running, but a camera whose
    # samples outlive the slice boundary can carry slightly more than asked for,
    # and rounding that down to 1.0 would hide it. It is a ratio of seconds held
    # to seconds wanted, which is a more useful thing to read than a fraction
    # that has been quietly capped.
    coverage = span / RATE_WINDOW_SECONDS

    record: dict[str, Any] = {
        "source": "rppg",
        "bpm": None,
        "confidence": 0.0,
        # Seconds of history held over seconds wanted. May exceed 1.0; see the
        # note where it is computed.
        "window_coverage": round(coverage, 3),
        "face_quality": None,
        # `is not None`, not truthiness: 0.0 is a measurement, and reporting it
        # as None would say "unmeasurable" about a camera that had stopped.
        "measured_fps": round(measured_fps, 2) if measured_fps is not None else None,
        "largest_gap_s": None,
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

    if measured_fps < MIN_SAMPLE_RATE:
        record["rejected_by"] = "frame_rate_too_low"
        return record

    if timestamps is None or len(timestamps) != len(rgb_window):
        # No per-sample clock means the only available time base is sample
        # index, which is the assumption that produced the 30/22 error above.
        record["rejected_by"] = "unmeasured_frame_rate"
        return record

    gap = largest_gap(timestamps)
    record["largest_gap_s"] = round(gap, 3)
    if gap > MAX_GAP_SECONDS:
        record["rejected_by"] = "sampling_gap"
        return record

    # Placed on an even grid by their own timestamps. Everything downstream is
    # then reading a signal whose sample spacing it can safely assume, which
    # neither POS nor the autocorrelation could before.
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

    `emotion_confidence` rather than `confidence`, deliberately. In the original
    the SQI was surfaced under `quality.confidence` while downstream read
    `features.confidence` as confidence in the reading, so a well-lit face and a
    trusted classification became one number. Two quantities, two names.

    **Two independent measurements, not one.** Emotion comes from FER+ over a
    64x64 crop; gaze comes from face-mesh landmarks through `face_geometry`.
    Either can succeed while the other fails, so `gaze_rejected_by` is its own
    field and `rejected_by` continues to mean the *emotion* refusal -- the same
    split as `rmssd_rejected_by` beside `rejected_by` on the heart block, and
    for the same reason: one field for two refusals cannot say which failed.

    Gaze keys are **absent** when the channel is off, rather than null. A
    channel that was switched off is not a channel that failed, and this is the
    same three-state rule the payload keeps everywhere else:

    * key absent          -- gaze is not enabled on this deployment
    * `None` + a reason   -- measured and refused (no eye, closed eye, no face)
    * a number            -- a reading

    **`attention` is deliberately null and has no producer.** That is Phase 11
    step 3, and it is blocked on a labelled reference rather than on code: an
    attention score inferred from head direction is least valid for exactly this
    product's users, and unlike a FER+ label it renders as a percentage, which
    reads as objective. Nothing may fill it without that measurement.

    It rides with the gaze keys rather than always being present, because it
    would be derived from gaze and head pose -- so it belongs to that channel
    and is absent for the same reason they are when the channel is off.
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
        # Head pose rides with gaze -- same landmark set, same cadence, same
        # enable flag -- but refuses independently, so it gets its own field.
        # Near profile the fit refuses while the eyes are still readable, and a
        # closed eye refuses gaze while the pose is fine. `gaze_x` is where the
        # eyes point *within the head*; without these it says nothing about
        # where the head points, and a student turned away with centred eyes
        # reads identically to one facing the screen.
        record["head_yaw"] = None
        record["head_pitch"] = None
        record["head_roll"] = None
        record["pose_rejected_by"] = (
            "no_reading" if pose is None else pose.rejected_by)
        if pose is not None and pose.yaw is not None:
            record.update(head_yaw=pose.yaw, head_pitch=pose.pitch,
                          head_roll=pose.roll)
        # "no_reading" is not a refusal: it is the state before the landmarker
        # has produced anything, which at start-up lasts one gaze interval.
        # Calling it a rejection would report a warming-up camera as a broken
        # one on every session's first tick.
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
            timestamps=timestamps,
        )
    # Either measurement is enough to warrant the block. Gating it on emotion
    # alone would make a gaze-only deployment -- which is a coherent thing to
    # want, since gaze needs no 35 MB FER+ model -- emit nothing at all.
    if emotion_enabled or gaze_enabled:
        payload["face"] = build_face_record(
            emotion if emotion_enabled else None, emotion_meta,
            gaze=gaze, gaze_enabled=gaze_enabled, pose=pose)
    return payload
