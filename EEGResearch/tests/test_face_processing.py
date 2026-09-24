"""Camera samples to payload records: each gate state must stay distinct, since a viewer cannot recover it."""

from __future__ import annotations

import numpy as np
import pytest

from src.app.services.face_emotion import EmotionResult
from src.app.services.face_ingestion import FaceSample
from src.app.services.face_processing import (
    MIN_MEAN_USABLE_FRACTION,
    MIN_WINDOW_COVERAGE,
    RATE_WINDOW_SECONDS,
    build_camera_payload,
    build_face_record,
    build_heart_record,
)

FPS = 30.0
PULSE_RGB = np.array([0.10, 0.60, 0.30])


def _rgb_window(bpm: float = 72.0, seconds: float = RATE_WINDOW_SECONDS,
                fps: float = FPS) -> np.ndarray:
    t = np.arange(int(seconds * fps)) / fps
    base = np.array([180.0, 120.0, 110.0])
    wave = 0.005 * np.sin(2 * np.pi * bpm / 60.0 * t)
    return base * (1.0 + np.outer(wave, PULSE_RGB))


def _samples(n: int, quality: float = 0.9) -> list[FaceSample]:
    return [FaceSample(float(i), (180.0, 120.0, 110.0), quality) for i in range(n)]


# ── heart ────────────────────────────────────────────────────────────────────

def test_a_clean_window_reports_a_rate():
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750))
    assert record["bpm"] == pytest.approx(72.0, abs=3.0)
    assert record["rejected_by"] is None
    assert record["source"] == "rppg"


def test_the_source_is_on_every_payload_including_failures():
    """A headband rate and a webcam rate are different measurements."""
    for record in (
        build_heart_record(np.empty((0, 3)), FPS),
        build_heart_record(_rgb_window(seconds=5.0), FPS),
        build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750)),
    ):
        assert record["source"] == "rppg"


def test_warming_up_is_distinguishable_from_no_samples():
    assert build_heart_record(np.empty((0, 3)), FPS)["rejected_by"] == "no_samples"
    partial = build_heart_record(_rgb_window(seconds=5.0), FPS)
    assert partial["rejected_by"] == "warming_up"
    assert partial["window_coverage"] < MIN_WINDOW_COVERAGE


def test_a_partial_window_never_reports_a_rate():
    """A gap is a missing measurement, not a slow heart rate."""
    record = build_heart_record(_rgb_window(seconds=15.0), FPS)
    assert record["bpm"] is None


def test_poor_face_quality_stops_the_estimate_before_it_is_made():
    record = build_heart_record(
        _rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS),
        samples=_samples(750, quality=MIN_MEAN_USABLE_FRACTION / 2)
    )
    assert record["bpm"] is None
    assert record["rejected_by"] == "poor_face_quality"


def test_face_quality_is_reported_separately_from_confidence():
    """Usable skin seen versus how decisive the autocorrelation was."""
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750, 0.77))
    assert record["face_quality"] == pytest.approx(0.77, abs=0.01)
    assert record["confidence"] != record["face_quality"]


def test_noise_is_rejected_rather_than_reported():
    rng = np.random.default_rng(0)
    noise = 150 + 10 * rng.standard_normal((int(RATE_WINDOW_SECONDS * FPS), 3))
    record = build_heart_record(noise, FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750))
    assert record["bpm"] is None
    assert record["rejected_by"] is not None


def test_a_rejected_estimate_never_reports_zero():
    for record in (
        build_heart_record(np.empty((0, 3)), FPS),
        build_heart_record(_rgb_window(seconds=2.0), FPS),
        build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750, 0.05)),
    ):
        assert record["bpm"] is None


# ── face ─────────────────────────────────────────────────────────────────────

def test_a_trusted_emotion_is_reported():
    record = build_face_record(EmotionResult("happy", 0.82, True))
    assert record["emotion"] == "happy"
    assert record["emotion_confidence"] == pytest.approx(0.82)
    assert record["trusted"]


def test_confidence_is_named_for_what_it_is():
    record = build_face_record(EmotionResult("sad", 0.7, True))
    assert "emotion_confidence" in record
    assert "confidence" not in record


def test_low_confidence_keeps_the_label_but_not_the_trust():
    record = build_face_record(
        EmotionResult("sad", 0.31, False, "low_confidence", "")
    )
    assert record["emotion"] == "sad"
    assert not record["trusted"]
    assert record["rejected_by"] == "low_confidence"


def test_a_degraded_classifier_is_distinguishable_from_an_unsure_one():
    unsure = build_face_record(EmotionResult("neutral", 0.4, False, "low_confidence", ""))
    broken = build_face_record(
        EmotionResult(None, None, False, "inference_failed", "boom"),
        {"emotion_degraded": True},
    )
    assert not unsure["degraded"]
    assert broken["degraded"]
    assert unsure["rejected_by"] != broken["rejected_by"]


def test_no_emotion_at_all_is_reported_as_no_face():
    record = build_face_record(None)
    assert record["emotion"] is None
    assert record["rejected_by"] == "no_face"
    assert not record["trusted"]


# ── the two channels together ────────────────────────────────────────────────

def test_both_blocks_are_present_when_both_are_enabled():
    payload = build_camera_payload(
        rgb_window=_rgb_window(72.0), fps=FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750),
        emotion=EmotionResult("happy", 0.9, True),
    )
    assert set(payload) == {"heart", "face"}


def test_a_disabled_channel_is_absent_rather_than_null():
    """A declined channel must not read like one that failed."""
    heart_only = build_camera_payload(
        rgb_window=_rgb_window(72.0), fps=FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750),
        emotion_enabled=False,
    )
    assert "heart" in heart_only
    assert "face" not in heart_only

    face_only = build_camera_payload(
        rgb_window=None, fps=FPS,
        emotion=EmotionResult("happy", 0.9, True), heart_enabled=False,
    )
    assert "face" in face_only
    assert "heart" not in face_only


def test_the_common_case_is_emotion_on_and_heart_idle():
    """Standby: FER+ runs, POS waits for headband failover; heart with no window is not a fault."""
    payload = build_camera_payload(
        rgb_window=np.empty((0, 3)), fps=FPS,
        emotion=EmotionResult("neutral", 0.88, True),
    )
    assert payload["face"]["trusted"]
    assert payload["heart"]["bpm"] is None
    assert payload["heart"]["rejected_by"] == "no_samples"


def test_both_disabled_yields_an_empty_payload_not_a_payload_of_nulls():
    assert build_camera_payload(
        rgb_window=None, fps=FPS, heart_enabled=False, emotion_enabled=False
    ) == {}


def test_one_channel_failing_does_not_suppress_the_other():
    payload = build_camera_payload(
        rgb_window=_rgb_window(72.0), fps=FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750),
        emotion=EmotionResult(None, None, False, "inference_failed", "boom"),
        emotion_meta={"emotion_degraded": True},
    )
    assert payload["heart"]["bpm"] == pytest.approx(72.0, abs=3.0)
    assert payload["face"]["degraded"]


# ── the frame-rate gate ──────────────────────────────────────────────────────

def _stamps(fps: float, n: int | None = None) -> np.ndarray:
    """An evenly spaced clock at `fps`, matching the default window length."""
    if n is None:
        n = int(RATE_WINDOW_SECONDS * fps)
    return np.arange(n) / fps


def test_a_camera_delivering_fewer_frames_than_configured_still_reads_true():
    """An index time base would scale bpm by 30/22 with nothing to flag it."""
    rgb = _rgb_window(72.0, fps=22.0)
    record = build_heart_record(
        rgb, FPS, measured_fps=22.0, timestamps=_stamps(22.0, len(rgb)),
        samples=_samples(len(rgb)),
    )
    assert record["bpm"] == pytest.approx(72.0, abs=3.0)
    assert record["measured_fps"] == 22.0
    assert record["rejected_by"] is None


def test_unevenly_spaced_samples_are_placed_by_their_timestamps():
    """Real webcams deliver bimodal intervals (31 ms / 47 ms) when asked for 30 fps."""
    rng = np.random.default_rng(11)
    n = int(RATE_WINDOW_SECONDS * 32.0)
    intervals = np.where(rng.random(n) < 0.21, 0.047, 0.031)
    stamps = np.cumsum(intervals) - intervals[0]

    # Generated against the real clock, so 72 bpm is the true rate.
    wave = 0.005 * np.sin(2 * np.pi * 72.0 / 60.0 * stamps)
    rgb = np.array([180.0, 120.0, 110.0]) * (
        1.0 + np.outer(wave, np.array([0.10, 0.60, 0.30]))
    )

    record = build_heart_record(
        rgb, FPS, measured_fps=1.0 / float(np.median(intervals)),
        timestamps=stamps, samples=_samples(n),
    )
    assert record["rejected_by"] is None
    assert record["bpm"] == pytest.approx(72.0, abs=3.0)


def test_a_long_gap_is_refused_rather_than_interpolated_across():
    """A line across a 4 s gap lands in the pulse band as a slow ramp."""
    rgb = _rgb_window(72.0)
    stamps = _stamps(FPS, len(rgb))
    stamps[len(stamps) // 2:] += 4.0          # student looked away

    record = build_heart_record(rgb, FPS, measured_fps=FPS, timestamps=stamps,
                                samples=_samples(len(rgb)))
    assert record["bpm"] is None
    assert record["rejected_by"] == "sampling_gap"
    assert record["largest_gap_s"] == pytest.approx(4.03, abs=0.05)


def test_a_rate_below_nyquist_is_refused():
    """Resampling can place samples but cannot manufacture ones never taken."""
    rgb = _rgb_window(72.0, fps=6.0)
    record = build_heart_record(rgb, FPS, measured_fps=6.0,
                                timestamps=_stamps(6.0, len(rgb)),
                                samples=_samples(len(rgb)))
    assert record["bpm"] is None
    assert record["rejected_by"] == "frame_rate_too_low"


def test_a_clock_that_does_not_match_the_colour_is_refused():
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS,
                                timestamps=np.arange(5) / FPS,
                                samples=_samples(750))
    assert record["rejected_by"] == "unmeasured_frame_rate"


def test_an_unmeasurable_rate_does_not_fall_back_to_nominal():
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=None,
                                samples=_samples(750))
    assert record["bpm"] is None
    assert record["rejected_by"] == "unmeasured_frame_rate"


def test_the_measured_rate_is_what_the_estimate_uses():
    slow = 27.0
    rgb = _rgb_window(72.0, fps=slow)
    record = build_heart_record(rgb, FPS, measured_fps=slow,
                                timestamps=_stamps(slow, len(rgb)),
                                samples=_samples(len(rgb)))
    assert record["rejected_by"] is None
    assert record["bpm"] == pytest.approx(72.0, abs=3.0)


def test_a_small_frame_rate_wobble_is_tolerated():
    rgb = _rgb_window(72.0, fps=29.0)
    record = build_heart_record(
        rgb, FPS, measured_fps=29.0, timestamps=_stamps(29.0, len(rgb)),
        samples=_samples(len(rgb)),
    )
    assert record["rejected_by"] is None


def test_quality_gating_uses_the_window_not_the_tick():
    """A tick that drained nothing must not skip the gate while the 25 s buffer is scored."""
    record = build_heart_record(
        _rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS),
        window_quality=MIN_MEAN_USABLE_FRACTION / 2,
        samples=[],                      # nothing drained this tick
    )
    assert record["bpm"] is None
    assert record["rejected_by"] == "poor_face_quality"


def test_the_window_quality_wins_over_the_tick_samples():
    record = build_heart_record(
        _rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS),
        window_quality=0.91, samples=_samples(750, quality=0.10),
    )
    assert record["face_quality"] == pytest.approx(0.91, abs=0.01)
    assert record["rejected_by"] is None


def test_a_zero_frame_rate_is_reported_rather_than_hidden():
    """A falsy `if measured_fps` would call a stopped camera "unmeasurable"."""
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=0.0,
                                window_quality=0.9)
    assert record["measured_fps"] == 0.0
    assert record["rejected_by"] == "frame_rate_too_low"


# ── gaze ─────────────────────────────────────────────────────────────────
# Record layer only; the geometry has its own tests.

from src.app.services.face_geometry import Gaze          # noqa: E402


def test_gaze_keys_are_absent_when_the_channel_is_off():
    """A switched-off channel must not read like one that failed."""
    record = build_face_record(EmotionResult("happy", 0.82, True))

    assert "gaze_x" not in record
    assert "gaze_y" not in record
    assert "gaze_rejected_by" not in record
    assert "attention" not in record


def test_a_gaze_reading_reaches_the_record():
    record = build_face_record(EmotionResult("happy", 0.82, True),
                               gaze=Gaze(0.42, -0.03, 2), gaze_enabled=True)

    assert record["gaze_x"] == 0.42
    assert record["gaze_y"] == -0.03
    assert record["gaze_rejected_by"] is None


def test_a_refused_gaze_is_null_with_a_reason_never_zero():
    """0.0 is a valid gaze reading (dead centre)."""
    record = build_face_record(EmotionResult("happy", 0.82, True),
                               gaze=Gaze(None, None, 0, "no_eye"),
                               gaze_enabled=True)

    assert record["gaze_x"] is None
    assert record["gaze_y"] is None
    assert record["gaze_rejected_by"] == "no_eye"


def test_before_the_first_reading_is_not_a_refusal():
    record = build_face_record(EmotionResult("happy", 0.82, True),
                               gaze=None, gaze_enabled=True)

    assert record["gaze_x"] is None
    assert record["gaze_rejected_by"] == "no_reading"


def test_attention_stays_null_and_is_nobody_s_to_fill():
    """Blocked on a labelled reference; the key is emitted so "no producer" differs from "unread"."""
    record = build_face_record(EmotionResult("happy", 0.82, True),
                               gaze=Gaze(0.42, -0.03, 2), gaze_enabled=True)

    assert record["attention"] is None


def test_emotion_and_gaze_refusals_do_not_share_a_field():
    record = build_face_record(
        EmotionResult(None, None, False, "low_confidence", ""),
        gaze=Gaze(0.42, -0.03, 2), gaze_enabled=True)

    assert record["rejected_by"] == "low_confidence"
    assert record["emotion"] is None
    assert record["gaze_rejected_by"] is None
    assert record["gaze_x"] == 0.42, "a usable gaze was lost to an emotion refusal"


def test_gaze_alone_still_produces_a_face_block():
    """Gaze without the FER+ model is a valid deployment."""
    payload = build_camera_payload(
        rgb_window=None, fps=FPS, heart_enabled=False, emotion_enabled=False,
        gaze=Gaze(0.42, -0.03, 2), gaze_enabled=True)

    assert payload["face"]["gaze_x"] == 0.42
    assert payload["face"]["emotion"] is None


# ── head pose ────────────────────────────────────────────────────────────
# Gaze is eye-in-head; without pose, a turned-away head with centred eyes reads as facing the screen.

from src.app.services.face_geometry import HeadPose      # noqa: E402


def test_a_pose_reading_reaches_the_record():
    record = build_face_record(EmotionResult("happy", 0.82, True),
                               gaze=Gaze(0.1, 0.0, 2), gaze_enabled=True,
                               pose=HeadPose(-12.5, 3.0, -1.0, 8))

    assert record["head_yaw"] == -12.5
    assert record["head_pitch"] == 3.0
    assert record["head_roll"] == -1.0
    assert record["pose_rejected_by"] is None


def test_pose_keys_are_absent_when_gaze_is_off():
    """Pose shares gaze's enable flag."""
    record = build_face_record(EmotionResult("happy", 0.82, True))

    for key in ("head_yaw", "head_pitch", "head_roll", "pose_rejected_by"):
        assert key not in record


def test_a_refused_pose_is_null_with_a_reason_never_zero():
    """0.0 yaw means facing the camera, the most common real reading."""
    record = build_face_record(EmotionResult("happy", 0.82, True),
                               gaze=Gaze(0.1, 0.0, 2), gaze_enabled=True,
                               pose=HeadPose(None, None, None, 3,
                                             "implausible_pose"))

    assert record["head_yaw"] is None
    assert record["pose_rejected_by"] == "implausible_pose"


def test_pose_and_gaze_refuse_independently():
    eyes_only = build_face_record(
        EmotionResult("happy", 0.82, True), gaze=Gaze(0.44, 0.0, 2),
        gaze_enabled=True, pose=HeadPose(None, None, None, 3, "implausible_pose"))
    pose_only = build_face_record(
        EmotionResult("happy", 0.82, True), gaze=Gaze(None, None, 0, "no_eye"),
        gaze_enabled=True, pose=HeadPose(-30.0, 2.0, 1.0, 8))

    assert eyes_only["gaze_x"] == 0.44 and eyes_only["head_yaw"] is None
    assert pose_only["head_yaw"] == -30.0 and pose_only["gaze_x"] is None
    assert eyes_only["gaze_rejected_by"] is None
    assert pose_only["pose_rejected_by"] is None
