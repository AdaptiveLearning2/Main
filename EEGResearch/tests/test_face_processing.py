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
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750))
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
        build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750)),
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
        _rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS),
        samples=_samples(750, quality=MIN_MEAN_USABLE_FRACTION / 2)
    )
    assert record["bpm"] is None
    assert record["rejected_by"] == "poor_face_quality"


def test_face_quality_is_reported_separately_from_confidence():
    """Two different quantities. One is how much usable skin the camera saw; the
    other is how decisive the autocorrelation was. Collapsing them is the defect
    the original had under `quality.confidence`."""
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
    """A zero would be read downstream as a real rate. Every failure path leaves
    bpm as None."""
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
        rgb_window=_rgb_window(72.0), fps=FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750),
        emotion=EmotionResult("happy", 0.9, True),
    )
    assert set(payload) == {"heart", "face"}


def test_a_disabled_channel_is_absent_rather_than_null():
    """A channel switched off is not a channel that failed. `heart: null` for a
    student who refused heart-rate recording would be indistinguishable from a
    camera that could not get a reading, and a viewer could not tell a respected
    refusal from a broken sensor."""
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
        rgb_window=_rgb_window(72.0), fps=FPS, measured_fps=FPS, timestamps=_stamps(FPS), samples=_samples(750),
        emotion=EmotionResult(None, None, False, "inference_failed", "boom"),
        emotion_meta={"emotion_degraded": True},
    )
    assert payload["heart"]["bpm"] == pytest.approx(72.0, abs=3.0)
    assert payload["face"]["degraded"]


# ── the frame-rate gate ──────────────────────────────────────────────────────

def _stamps(fps: float, n: int | None = None) -> np.ndarray:
    """An even clock at `fps`, matching the default window length."""
    if n is None:
        n = int(RATE_WINDOW_SECONDS * fps)
    return np.arange(n) / fps


def test_a_camera_delivering_fewer_frames_than_configured_still_reads_true():
    """This test asserted the opposite until the samples carried a clock.

    The time base used to come from sample index, so a camera configured for 30
    and delivering 22 produced a bpm scaled by 30/22 -- a confident +36% with
    nothing to indicate it -- and the only safe response was to refuse the
    window. Webcams drop frames routinely, so that refused the common case.

    Placing the samples by their own timestamps removes the arithmetic error
    rather than declining to report when it fires. 22 fps is not a fault; it is
    the rate, and 72 bpm at 22 fps is still 72 bpm."""
    rgb = _rgb_window(72.0, fps=22.0)
    record = build_heart_record(
        rgb, FPS, measured_fps=22.0, timestamps=_stamps(22.0, len(rgb)),
        samples=_samples(len(rgb)),
    )
    assert record["bpm"] == pytest.approx(72.0, abs=3.0)
    assert record["measured_fps"] == 22.0
    assert record["rejected_by"] is None


def test_unevenly_spaced_samples_are_placed_by_their_timestamps():
    """The measured failure, reproduced.

    A real webcam asked for 30 fps delivered bimodal intervals -- 78% at 31 ms
    and 21% at 47 ms. Read as evenly spaced, those 47 ms samples are each
    stretched by half an interval and the derived rate is wrong by roughly the
    fraction of them present. Read by their own clock, they are simply where
    they say they are."""
    rng = np.random.default_rng(11)
    n = int(RATE_WINDOW_SECONDS * 32.0)
    intervals = np.where(rng.random(n) < 0.21, 0.047, 0.031)
    stamps = np.cumsum(intervals) - intervals[0]

    # The colour is generated against the *real* clock, so 72 bpm is the truth
    # regardless of how the samples happen to be spaced.
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
    """Interpolation fills a dropped frame honestly and a four-second absence
    dishonestly -- there the straight line is invention, and it lands in the
    pulse band as a slow ramp."""
    rgb = _rgb_window(72.0)
    stamps = _stamps(FPS, len(rgb))
    stamps[len(stamps) // 2:] += 4.0          # the student looked away

    record = build_heart_record(rgb, FPS, measured_fps=FPS, timestamps=stamps,
                                samples=_samples(len(rgb)))
    assert record["bpm"] is None
    assert record["rejected_by"] == "sampling_gap"
    assert record["largest_gap_s"] == pytest.approx(4.03, abs=0.05)


def test_a_rate_below_nyquist_is_refused():
    """Resampling places samples; it cannot manufacture ones never taken."""
    rgb = _rgb_window(72.0, fps=6.0)
    record = build_heart_record(rgb, FPS, measured_fps=6.0,
                                timestamps=_stamps(6.0, len(rgb)),
                                samples=_samples(len(rgb)))
    assert record["bpm"] is None
    assert record["rejected_by"] == "frame_rate_too_low"


def test_a_clock_that_does_not_match_the_colour_is_refused():
    """Rather than silently scoring against sample index again."""
    record = build_heart_record(_rgb_window(72.0), FPS, measured_fps=FPS,
                                timestamps=np.arange(5) / FPS,
                                samples=_samples(750))
    assert record["rejected_by"] == "unmeasured_frame_rate"


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
                                timestamps=_stamps(slow, len(rgb)),
                                samples=_samples(len(rgb)))
    assert record["rejected_by"] is None
    assert record["bpm"] == pytest.approx(72.0, abs=3.0)


def test_a_small_frame_rate_wobble_is_tolerated():
    """Every webcam jitters slightly. Rejecting on any deviation would reject
    everything."""
    rgb = _rgb_window(72.0, fps=29.0)
    record = build_heart_record(
        rgb, FPS, measured_fps=29.0, timestamps=_stamps(29.0, len(rgb)),
        samples=_samples(len(rgb)),
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
        _rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS),
        window_quality=MIN_MEAN_USABLE_FRACTION / 2,
        samples=[],                      # nothing drained this tick
    )
    assert record["bpm"] is None
    assert record["rejected_by"] == "poor_face_quality"


def test_the_window_quality_wins_over_the_tick_samples():
    """They can disagree -- the window spans 25 s, the tick spans one frame
    interval -- and the window is what was scored."""
    record = build_heart_record(
        _rgb_window(72.0), FPS, measured_fps=FPS, timestamps=_stamps(FPS),
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
    assert record["rejected_by"] == "frame_rate_too_low"


# ── gaze (Phase 11, step 2) ─────────────────────────────────────────────────
#
# `face_signals.gaze_x` and `gaze_y` shipped in 20260625000000 with nothing
# writing them. These cover the record layer; the geometry has its own tests and
# was verified against a camera on 2026-08-12.

from src.app.services.face_geometry import Gaze          # noqa: E402


def test_gaze_keys_are_absent_when_the_channel_is_off():
    """A channel that was switched off is not a channel that failed. Emitting
    `gaze_x: null` for a deployment with no landmark model would be
    indistinguishable from a camera that could not get a reading."""
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
    """0.0 is a valid gaze — dead centre. A refusal recorded as 0.0 would be
    averaged by every aggregate as "looking straight ahead", which is the
    can't-tell-no-data-from-zero failure this codebase refuses everywhere."""
    record = build_face_record(EmotionResult("happy", 0.82, True),
                               gaze=Gaze(None, None, 0, "no_eye"),
                               gaze_enabled=True)

    assert record["gaze_x"] is None
    assert record["gaze_y"] is None
    assert record["gaze_rejected_by"] == "no_eye"


def test_before_the_first_reading_is_not_a_refusal():
    """At start-up the landmarker has not run yet. Calling that a rejection
    would report a warming-up camera as a broken one."""
    record = build_face_record(EmotionResult("happy", 0.82, True),
                               gaze=None, gaze_enabled=True)

    assert record["gaze_x"] is None
    assert record["gaze_rejected_by"] == "no_reading"


def test_attention_stays_null_and_is_nobody_s_to_fill():
    """Phase 11 step 3, and blocked on a labelled reference rather than on
    code. It renders to a parent as a percentage, which reads as objective, and
    "attention" inferred from head direction is least valid for exactly this
    product's users. The key is emitted so a consumer can tell "no producer"
    from "a key I forgot to read"."""
    record = build_face_record(EmotionResult("happy", 0.82, True),
                               gaze=Gaze(0.42, -0.03, 2), gaze_enabled=True)

    assert record["attention"] is None


def test_emotion_and_gaze_refusals_do_not_share_a_field():
    """Two measurements, two refusals. One field for both cannot say which
    failed — the same split as `rmssd_rejected_by` beside `rejected_by` on the
    heart block."""
    record = build_face_record(
        EmotionResult(None, None, False, "low_confidence", ""),
        gaze=Gaze(0.42, -0.03, 2), gaze_enabled=True)

    assert record["rejected_by"] == "low_confidence"
    assert record["emotion"] is None
    assert record["gaze_rejected_by"] is None
    assert record["gaze_x"] == 0.42, "a usable gaze was lost to an emotion refusal"


def test_gaze_alone_still_produces_a_face_block():
    """Gaze needs no 35 MB FER+ model, so gaze-without-emotion is a coherent
    deployment. Gating the block on emotion would make it emit nothing."""
    payload = build_camera_payload(
        rgb_window=None, fps=FPS, heart_enabled=False, emotion_enabled=False,
        gaze=Gaze(0.42, -0.03, 2), gaze_enabled=True)

    assert payload["face"]["gaze_x"] == 0.42
    assert payload["face"]["emotion"] is None
