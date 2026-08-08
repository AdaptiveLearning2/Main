"""Camera samples to payload records.

The gates matter more than the arithmetic here. Every one of them exists to keep
a distinct state distinct — "switched off", "warming up", "no face", "poor
light", "not confident" and "a real reading" must not collapse into each other,
because a viewer downstream has no way to recover a distinction that was lost at
this layer.
"""

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
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS, samples=_samples(750))
    assert record["bpm"] == pytest.approx(72.0, abs=3.0)
    assert record["rejected_by"] is None
    assert record["source"] == "rppg"


def test_the_source_is_on_every_payload_including_failures():
    """A rate from a headband and a rate from a webcam are not the same
    measurement, and a consumer that cannot tell which it has cannot weight it.
    The field must survive the failure paths too, or a chart loses the
    distinction exactly when the reading got unreliable."""
    for record in (
        build_heart_record(np.empty((0, 3)), FPS),
        build_heart_record(_rgb_window(seconds=5.0), FPS),
        build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS, samples=_samples(750)),
    ):
        assert record["source"] == "rppg"


def test_warming_up_is_distinguishable_from_no_samples():
    """The first 25 s of every session is a partial window, and so is every gap
    after the student looks away. Neither is a fault, and neither is the same as
    a camera that has produced nothing at all."""
    assert build_heart_record(np.empty((0, 3)), FPS)["rejected_by"] == "no_samples"
    partial = build_heart_record(_rgb_window(seconds=5.0), FPS)
    assert partial["rejected_by"] == "warming_up"
    assert partial["window_coverage"] < MIN_WINDOW_COVERAGE


def test_a_partial_window_never_reports_a_rate():
    """A gap is a missing measurement, not a slow heart -- and the sample-index
    time base would silently read it as one."""
    record = build_heart_record(_rgb_window(seconds=15.0), FPS)
    assert record["bpm"] is None


def test_poor_face_quality_stops_the_estimate_before_it_is_made():
    """Cheaper than deriving a rate and discarding it, and it names the actual
    problem: the light, not the heart."""
    record = build_heart_record(
        _rgb_window(72.0), FPS, measured_fps=FPS,
        samples=_samples(750, quality=MIN_MEAN_USABLE_FRACTION / 2)
    )
    assert record["bpm"] is None
    assert record["rejected_by"] == "poor_face_quality"


def test_face_quality_is_reported_separately_from_confidence():
    """Two different quantities. One is how much usable skin the camera saw; the
    other is how decisive the autocorrelation was. Collapsing them is the defect
    the original had under `quality.confidence`."""
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS, samples=_samples(750, 0.77))
    assert record["face_quality"] == pytest.approx(0.77, abs=0.01)
    assert record["confidence"] != record["face_quality"]


def test_noise_is_rejected_rather_than_reported():
    rng = np.random.default_rng(0)
    noise = 150 + 10 * rng.standard_normal((int(RATE_WINDOW_SECONDS * FPS), 3))
    record = build_heart_record(noise, FPS, measured_fps=FPS, samples=_samples(750))
    assert record["bpm"] is None
    assert record["rejected_by"] is not None


def test_a_rejected_estimate_never_reports_zero():
    """A zero would be read downstream as a real rate. Every failure path leaves
    bpm as None."""
    for record in (
        build_heart_record(np.empty((0, 3)), FPS),
        build_heart_record(_rgb_window(seconds=2.0), FPS),
        build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS, samples=_samples(750, 0.05)),
    ):
        assert record["bpm"] is None


# ── face ─────────────────────────────────────────────────────────────────────

def test_a_trusted_emotion_is_reported():
    record = build_face_record(EmotionResult("happy", 0.82, True))
    assert record["emotion"] == "happy"
    assert record["emotion_confidence"] == pytest.approx(0.82)
    assert record["trusted"]


def test_confidence_is_named_for_what_it_is():
    """`emotion_confidence`, not `confidence`. In the original the SQI was
    surfaced as quality.confidence while downstream read features.confidence as
    confidence in the reading, so a well-lit face and a trusted classification
    became one number."""
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
    """The state the original could not express: a broken session and a neutral
    student both produced trusted:false."""
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
        rgb_window=_rgb_window(72.0), fps=FPS, measured_fps=FPS, samples=_samples(750),
        emotion=EmotionResult("happy", 0.9, True),
    )
    assert set(payload) == {"heart", "face"}


def test_a_disabled_channel_is_absent_rather_than_null():
    """A channel switched off is not a channel that failed. `heart: null` for a
    student who refused heart-rate recording would be indistinguishable from a
    camera that could not get a reading, and a viewer could not tell a respected
    refusal from a broken sensor."""
    heart_only = build_camera_payload(
        rgb_window=_rgb_window(72.0), fps=FPS, measured_fps=FPS, samples=_samples(750),
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
    """Warm standby: the camera is open and FER+ runs, while POS only starts
    scoring on failover from the headband. Heart is enabled but has no window,
    which must read as warming up rather than as a fault."""
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
    """They fail independently because they are independent measurements. A
    broken emotion model must not take the heart rate down with it."""
    payload = build_camera_payload(
        rgb_window=_rgb_window(72.0), fps=FPS, measured_fps=FPS, samples=_samples(750),
        emotion=EmotionResult(None, None, False, "inference_failed", "boom"),
        emotion_meta={"emotion_degraded": True},
    )
    assert payload["heart"]["bpm"] == pytest.approx(72.0, abs=3.0)
    assert payload["face"]["degraded"]


# ── the frame-rate gate ──────────────────────────────────────────────────────

def test_a_camera_delivering_fewer_frames_than_configured_is_rejected():
    """The gate that matters most on this path.

    The time base is reconstructed from sample index, so a camera configured for
    30 fps that actually delivers 22 does not produce a *noisy* rate -- it
    produces one scaled by 30/22, a confident +36% with nothing to indicate it.
    Webcams drop frames routinely under load or poor light, so this is the
    common case rather than a fault."""
    record = build_heart_record(
        _rgb_window(72.0), FPS, measured_fps=22.0, samples=_samples(750)
    )
    assert record["bpm"] is None
    assert record["rejected_by"] == "unstable_frame_rate"
    assert record["measured_fps"] == 22.0


def test_an_unmeasurable_rate_does_not_fall_back_to_nominal():
    """Falling back to the configured rate is exactly the assumption the
    measurement exists to remove."""
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=None,
                                samples=_samples(750))
    assert record["bpm"] is None
    assert record["rejected_by"] == "unmeasured_frame_rate"


def test_the_measured_rate_is_what_the_estimate_uses():
    """Not the configured one. A camera running slightly slow should report a
    correct bpm, not one scaled by the ratio."""
    slow = 27.0
    rgb = _rgb_window(72.0, fps=slow)
    record = build_heart_record(rgb, FPS, measured_fps=slow,
                                samples=_samples(len(rgb)))
    assert record["rejected_by"] is None
    assert record["bpm"] == pytest.approx(72.0, abs=3.0)


def test_a_small_frame_rate_wobble_is_tolerated():
    """Every webcam jitters slightly. Rejecting on any deviation would reject
    everything."""
    record = build_heart_record(
        _rgb_window(72.0, fps=29.0), FPS, measured_fps=29.0, samples=_samples(725)
    )
    assert record["rejected_by"] is None


def test_quality_gating_uses_the_window_not_the_tick():
    """A tick that drained no samples must not skip the gate.

    Quality was derived from the samples drained since the last tick, so a tick
    that drained nothing left face_quality None while the 25 s colour buffer was
    still full and scored -- the gate silently did not run. Ticks faster than the
    frame rate hit that routinely, making the gate intermittent rather than
    applied per window."""
    record = build_heart_record(
        _rgb_window(72.0), FPS, measured_fps=FPS,
        window_quality=MIN_MEAN_USABLE_FRACTION / 2,
        samples=[],                      # nothing drained this tick
    )
    assert record["bpm"] is None
    assert record["rejected_by"] == "poor_face_quality"


def test_the_window_quality_wins_over_the_tick_samples():
    """They can disagree -- the window spans 25 s, the tick spans one frame
    interval -- and the window is what was scored."""
    record = build_heart_record(
        _rgb_window(72.0), FPS, measured_fps=FPS,
        window_quality=0.91, samples=_samples(750, quality=0.10),
    )
    assert record["face_quality"] == pytest.approx(0.91, abs=0.01)
    assert record["rejected_by"] is None


def test_a_zero_frame_rate_is_reported_rather_than_hidden():
    """`if measured_fps` would report 0.0 as None -- saying "unmeasurable" about
    a camera that had demonstrably stopped."""
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=0.0,
                                window_quality=0.9)
    assert record["measured_fps"] == 0.0
    assert record["rejected_by"] == "unstable_frame_rate"
