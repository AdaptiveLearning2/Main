"""Headless capture with injected frame source and locator, so no OpenCV is needed."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from src.app.services.face_ingestion import (
    BUFFER_SECONDS,
    MISSING_FACE_TOLERANCE,
    QUEUE_MAX,
    FaceCaptureAdapter,
)
from src.app.services.pos_rppg import WINDOW_SECONDS

# Small, since mean_rgb is O(pixels); the box still fits the 64x64 model input.
FRAME_H, FRAME_W = 120, 160
FACE_BOX = (20, 10, 100, 100)


class FakeSource:
    """Yields a fixed frame, optionally failing or ending; `read()` blocks like a real sensor."""

    # A plausible camera rate; faster lets the size cap bind before the time bound.
    READ_SECONDS = 0.008

    def __init__(self, frame=None, fail_after=None, raise_after=None):
        self.frame = frame if frame is not None else _flat_frame()
        self.fail_after = fail_after
        self.raise_after = raise_after
        self.reads = 0
        self.released = False

    def read(self):
        self.reads += 1
        time.sleep(self.READ_SECONDS)
        if self.raise_after is not None and self.reads > self.raise_after:
            raise RuntimeError("camera exploded")
        if self.fail_after is not None and self.reads > self.fail_after:
            return None
        return self.frame

    def release(self):
        self.released = True


class FakeLocator:
    def __init__(self, box=FACE_BOX):
        self.box = box

    def locate(self, gray):
        return self.box


def _flat_frame(colour=(180, 120, 110)):
    return np.tile(np.array(colour, dtype=np.uint8), (FRAME_H, FRAME_W, 1))


def _adapter(source=None, locator=None, fps=500.0, buffer_seconds=2.0,
             queue_max=QUEUE_MAX, error_backoff=0.0, warmup_seconds=0.0):
    """High fps, small buffer, no backoff and no warm-up, so tests run in milliseconds."""
    src = source or FakeSource()
    loc = locator or FakeLocator()
    adapter = FaceCaptureAdapter(lambda: src, lambda: loc, fps=fps,
                                 buffer_seconds=buffer_seconds,
                                 queue_max=queue_max,
                                 error_backoff=error_backoff,
                                 warmup_seconds=warmup_seconds)
    return adapter, src, loc


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# -- lifecycle --

def test_captures_and_emits_samples():
    adapter, _, _ = _adapter()
    adapter.connect()
    try:
        assert _wait_for(lambda: len(adapter.drain_samples(10)) > 0)
    finally:
        adapter.disconnect()


def test_disconnect_joins_the_thread_and_releases_the_camera():
    """A daemon thread logging during interpreter shutdown is a fatal stdout-lock abort."""
    adapter, source, _ = _adapter()
    adapter.connect()
    assert _wait_for(lambda: source.reads > 2)

    adapter.disconnect()

    assert source.released
    names = [t.name for t in threading.enumerate()]
    assert "face-capture" not in names


def test_connect_is_idempotent():
    adapter, _, _ = _adapter()
    adapter.connect()
    try:
        adapter.connect()
        assert sum(1 for t in threading.enumerate() if t.name == "face-capture") == 1
    finally:
        adapter.disconnect()


def test_disconnect_before_connect_is_harmless():
    adapter, _, _ = _adapter()
    adapter.disconnect()


def test_disconnect_clears_buffered_state():
    """POS would splice two unrelated recordings across the join."""
    # fps=20: Windows' ~15ms Event.wait makes a nominal 500 fps run near 65.
    adapter, _, _ = _adapter(fps=20.0, buffer_seconds=2.0)
    adapter.connect()
    assert _wait_for(adapter.has_full_window, timeout=6.0)
    adapter.disconnect()

    assert adapter.rgb_window(10.0)[0].shape == (0, 3)
    assert adapter.drain_samples(100) == []


# -- the capture thread never blocks and never grows --

def test_the_buffer_is_bounded():
    """An eight-hour session must cost the same per frame as the first minute."""
    held = 2.5
    adapter, _, _ = _adapter(fps=20.0, buffer_seconds=held)
    adapter.connect()
    try:
        # Bounded by elapsed time, not sample count: a burst of buffered frames
        # would otherwise leave less history than the rate window needs.
        assert _wait_for(lambda: len(adapter.rgb_buffer()) > 5, timeout=5.0)
        time.sleep(held + 0.5)

        _, _, _, stamps = adapter.rgb_window(float("inf"))
        span = stamps[-1] - stamps[0]
        assert span <= held + 0.2, f"the buffer holds {span:.2f}s, past its bound"
        assert span >= held - 0.5, f"the buffer holds only {span:.2f}s of {held}s"
        assert adapter.get_ingestion_meta()["buffer_capped"] == 0, (
            "the size cap bound the buffer before time did"
        )
    finally:
        adapter.disconnect()
    assert BUFFER_SECONDS == 40.0, "production default changed; check the comment"


def test_a_stalled_consumer_drops_samples_rather_than_stalling_capture():
    """A stalled grab corrupts the uniform sample interval POS and the rate assume."""
    adapter, source, _ = _adapter(fps=2000.0, queue_max=5)
    adapter.connect()
    try:
        assert _wait_for(
            lambda: adapter.get_ingestion_meta()["samples_dropped"] > 0, timeout=10.0
        )
        before = source.reads
        time.sleep(0.05)
        assert source.reads > before, "capture stopped when the queue filled"
    finally:
        adapter.disconnect()


def test_the_queue_never_exceeds_its_bound():
    adapter, _, _ = _adapter(fps=2000.0, queue_max=7)
    adapter.connect()
    try:
        time.sleep(0.2)
        assert len(adapter.drain_samples(1000)) <= 7
    finally:
        adapter.disconnect()


def test_a_buffer_too_short_for_one_pos_window_is_rejected():
    """It would report healthy while never producing a reading."""
    with pytest.raises(ValueError, match="shorter than one POS window"):
        FaceCaptureAdapter(FakeSource, FakeLocator,
                           buffer_seconds=WINDOW_SECONDS / 2)


# -- failure handling --

def test_a_camera_that_stops_yielding_frames_does_not_kill_the_thread():
    adapter, source, _ = _adapter(source=FakeSource(fail_after=3))
    adapter.connect()
    try:
        assert _wait_for(lambda: source.reads > 20)
        assert adapter.get_ingestion_meta()["camera_connected"]
    finally:
        adapter.disconnect()


def test_an_exception_is_caught_at_the_thread_boundary_and_surfaced():
    """Not a process-wide excepthook, which would silence every other thread's errors."""
    adapter, _, _ = _adapter(source=FakeSource(raise_after=2))
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.get_ingestion_meta()["last_error"] is not None)
        assert "camera exploded" in adapter.get_ingestion_meta()["last_error"]
        assert adapter.get_ingestion_meta()["camera_connected"], "thread died"
    finally:
        adapter.disconnect()


def test_no_face_is_reported_as_degraded_not_as_a_reading():
    class NoFace:
        def locate(self, gray):
            return None

    adapter, _, _ = _adapter(locator=NoFace(), fps=2000.0)
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.get_ingestion_meta()["face_degraded"])
        meta = adapter.get_ingestion_meta()
        assert meta["faces_found"] == 0
        assert meta["face_degraded_reason"] == "no face detected"
        assert adapter.drain_samples(10) == [], "emitted a sample with no face"
    finally:
        adapter.disconnect()


def test_an_unusable_frame_yields_no_sample_rather_than_a_zero():
    """A zero would enter POS as a real measurement and step the waveform."""
    dark = np.zeros((480, 640, 3), dtype=np.uint8)
    adapter, _, _ = _adapter(source=FakeSource(frame=dark), fps=2000.0)
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.get_ingestion_meta()["frames_read"] > 5)
        assert adapter.drain_samples(10) == []
        assert adapter.rgb_window(5.0)[0].shape == (0, 3)
    finally:
        adapter.disconnect()


def test_one_missed_frame_does_not_trip_degraded():
    """Only a sustained absence is a fault, or the state would flap on every movement."""
    adapter, _, _ = _adapter()
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.get_ingestion_meta()["frames_read"] > 2)
        assert not adapter.get_ingestion_meta()["face_degraded"]
        assert MISSING_FACE_TOLERANCE > 1
    finally:
        adapter.disconnect()


# -- the contract with the rest of the pipeline --

def test_rgb_window_returns_a_copy_not_a_view():
    """A view would let a consumer read a half-written window."""
    adapter, _, _ = _adapter()
    adapter.connect()
    try:
        assert _wait_for(lambda: len(adapter.rgb_buffer()) > 3)
        window = adapter.rgb_buffer()
        window[:] = 0
        assert adapter.rgb_buffer().any(), "mutating the copy changed the buffer"
    finally:
        adapter.disconnect()


def test_meta_names_quality_as_quality_not_as_confidence():
    adapter, _, _ = _adapter()
    adapter.connect()
    try:
        meta = adapter.get_ingestion_meta()
        assert "confidence" not in meta
        assert "face_found_ratio" in meta
    finally:
        adapter.disconnect()


def test_a_pulse_survives_the_full_capture_chain():
    from src.app.services.ppg_processing import estimate_window
    from src.app.services.pos_rppg import pos_pulse

    fps, bpm = 30.0, 72.0
    base = np.array([180.0, 120.0, 110.0])
    weights = np.array([0.10, 0.60, 0.30])

    class PulsingSource:
        def __init__(self):
            self.i = 0

        def read(self):
            value = 0.005 * np.sin(2 * np.pi * bpm / 60.0 * self.i / fps)
            self.i += 1
            colour = np.clip(base * (1 + value * weights), 0, 255).astype(np.uint8)
            return np.tile(colour, (FRAME_H, FRAME_W, 1))

        def release(self):
            pass

    # Synchronous: a threaded 25 s at 30 fps is 25 s of wall clock.
    adapter = FaceCaptureAdapter(PulsingSource, FakeLocator, fps=fps,
                                 buffer_seconds=30.0)
    adapter._source = PulsingSource()
    adapter._locator = FakeLocator()
    for _ in range(int(25 * fps)):
        adapter._capture_once()
    rgb = adapter.rgb_buffer()

    assert len(rgb) >= int(24 * fps)
    assert estimate_window(pos_pulse(rgb, fps), fps).bpm == pytest.approx(bpm, abs=3.0)


# -- the two channels, independently switchable --

class FakeClassifier:
    """Counts calls so the emotion cadence can be measured."""

    def __init__(self, result="happy"):
        from src.app.services.face_emotion import EmotionResult

        self.result = EmotionResult(result, 0.9, True)
        self.calls = 0

    def classify(self, crop):
        self.calls += 1
        return self.result

    def get_meta(self):
        return {"emotion_classified": self.calls, "emotion_degraded": False}


def test_opening_a_camera_with_every_channel_off_is_refused():
    """Frames read with nothing produced would look like a student out of shot."""
    with pytest.raises(ValueError, match="heart, emotion and gaze all disabled"):
        FaceCaptureAdapter(FakeSource, FakeLocator,
                           heart_enabled=False, emotion_enabled=False)


def test_a_gaze_only_camera_is_allowed():
    adapter = FaceCaptureAdapter(
        FakeSource, FakeLocator, heart_enabled=False, emotion_enabled=False,
        gaze_enabled=True, landmarker_factory=lambda: FakeLandmarker())

    assert adapter.gaze_enabled is True
    assert adapter.emotion_enabled is False


def test_emotion_enabled_without_a_classifier_is_refused():
    with pytest.raises(ValueError, match="requires an emotion_classifier_factory"):
        FaceCaptureAdapter(FakeSource, FakeLocator, emotion_enabled=True)


def test_emotion_runs_far_less_often_than_frames_are_captured():
    """Expression changes over seconds; per-frame classification wastes ~30x the CPU."""
    clf = FakeClassifier()
    adapter = FaceCaptureAdapter(
        lambda: FakeSource(), lambda: FakeLocator(), fps=30.0,
        buffer_seconds=2.0, emotion_enabled=True, emotion_classifier_factory=lambda: clf,
        emotion_interval_s=0.2,
    )
    adapter._source, adapter._locator = FakeSource(), FakeLocator()
    adapter._emotion = clf if adapter.emotion_enabled else None
    for _ in range(60):
        adapter._capture_once()

    assert clf.calls > 0
    assert clf.calls < 20, f"classified {clf.calls} times in 60 frames"


def test_emotion_only_captures_no_colour():
    """An idle heart channel must not look like a stalled one."""
    clf = FakeClassifier()
    adapter = FaceCaptureAdapter(
        lambda: FakeSource(), lambda: FakeLocator(), fps=30.0,
        buffer_seconds=2.0, heart_enabled=False,
        emotion_enabled=True, emotion_classifier_factory=lambda: clf, emotion_interval_s=0.0,
    )
    adapter._source, adapter._locator = FakeSource(), FakeLocator()
    adapter._emotion = clf if adapter.emotion_enabled else None
    for _ in range(10):
        adapter._capture_once()

    assert clf.calls == 10
    assert adapter.rgb_buffer().shape == (0, 3)
    assert adapter.latest_emotion().label == "happy"


def test_heart_only_never_classifies():
    clf = FakeClassifier()
    adapter = FaceCaptureAdapter(
        lambda: FakeSource(), lambda: FakeLocator(), fps=30.0,
        buffer_seconds=2.0, emotion_enabled=False,
    )
    adapter._source, adapter._locator = FakeSource(), FakeLocator()
    adapter._emotion = clf if adapter.emotion_enabled else None
    for _ in range(10):
        adapter._capture_once()

    assert clf.calls == 0
    assert len(adapter.rgb_buffer()) == 10
    assert adapter.latest_emotion() is None


def test_meta_reports_which_channels_are_on():
    """The classifier is built at connect(), so its meta is absent before, not fabricated."""
    clf = FakeClassifier()
    adapter = FaceCaptureAdapter(
        lambda: FakeSource(), lambda: FakeLocator(),
        buffer_seconds=2.0, emotion_enabled=True, emotion_classifier_factory=lambda: clf,
    )
    assert adapter.get_ingestion_meta()["emotion_enabled"] is True
    assert "emotion_classified" not in adapter.get_ingestion_meta()

    adapter.connect()
    try:
        meta = adapter.get_ingestion_meta()
        assert meta["heart_enabled"] is True
        assert meta["emotion_enabled"] is True
        assert "emotion_classified" in meta, "classifier meta was not merged in"
    finally:
        adapter.disconnect()


def test_disconnect_forgets_the_last_emotion():
    clf = FakeClassifier()
    adapter = FaceCaptureAdapter(
        lambda: FakeSource(), lambda: FakeLocator(), fps=30.0,
        buffer_seconds=2.0, emotion_enabled=True, emotion_classifier_factory=lambda: clf,
        emotion_interval_s=0.0, warmup_seconds=0.0,
    )
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.latest_emotion() is not None)
    finally:
        adapter.disconnect()
    assert adapter.latest_emotion() is None


class _Toggle:
    """A locator or source whose answer the test flips."""

    def __init__(self, on_value):
        self.on_value, self.on = on_value, True

    def locate(self, gray):
        return self.on_value if self.on else None

    def read(self):
        return self.on_value if self.on else None

    def release(self):
        pass


def _emotion_adapter(source, locator, emotion_interval_s=0.0, **kwargs):
    clf = FakeClassifier("sad")
    adapter = FaceCaptureAdapter(
        lambda: source, lambda: locator, fps=30.0, buffer_seconds=2.0,
        emotion_enabled=True, emotion_classifier_factory=lambda: clf,
        emotion_interval_s=emotion_interval_s, **kwargs)
    adapter._source, adapter._locator, adapter._emotion = source, locator, clf
    return adapter


def test_a_face_that_leaves_takes_its_emotion_with_it():
    """Every tick sends `latest_emotion()` as a new row, so a stale one repeats at 4 Hz."""
    locator = _Toggle(FACE_BOX)
    adapter = _emotion_adapter(FakeSource(), locator)
    adapter._capture_once()
    assert adapter.latest_emotion().label == "sad"

    locator.on = False
    adapter._capture_once()

    assert adapter.latest_emotion() is None


def test_a_camera_that_stops_takes_every_reading_with_it():
    """Gaze and pose become a named refusal, not None, which reads as warming up."""
    source = _Toggle(_flat_frame())
    lm = FakeLandmarker(_eyes_looking(+6.0))
    adapter = _emotion_adapter(source, FakeLocator(), gaze_enabled=True,
                               landmarker_factory=lambda: lm, gaze_interval_s=0.0)
    adapter._landmarker = lm
    adapter._capture_once()
    assert adapter.latest_emotion() is not None
    assert adapter.latest_gaze().x is not None

    source.on = False
    adapter._capture_once()

    assert adapter.latest_emotion() is None
    assert adapter.latest_gaze().x is None
    assert adapter.latest_gaze().rejected_by == "no_frame"
    assert adapter.latest_pose().rejected_by == "no_frame"


class _Clock:
    """`now_seconds` moved by hand, so a grace period needs no sleep."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_one_missed_detection_keeps_the_emotion_and_a_whole_interval_drops_it(monkeypatch):
    """Haar misses single frames in poor light; only a whole interval's absence clears it."""
    clock = _Clock()
    monkeypatch.setattr("src.app.services.face_ingestion.now_seconds", clock)
    locator = _Toggle(FACE_BOX)
    adapter = _emotion_adapter(FakeSource(), locator, emotion_interval_s=0.25)
    adapter._capture_once()
    assert adapter.latest_emotion().label == "sad"

    locator.on = False
    clock.t += 0.1
    adapter._capture_once()
    assert adapter.latest_emotion().label == "sad", "a flicker is not a departure"

    clock.t += 0.2                      # 0.3 s since the face was last seen
    adapter._capture_once()
    assert adapter.latest_emotion() is None


def test_one_dropped_frame_keeps_gaze_and_a_whole_interval_refuses_it(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr("src.app.services.face_ingestion.now_seconds", clock)
    source = _Toggle(_flat_frame())
    lm = FakeLandmarker(_eyes_looking(+6.0))
    adapter = _emotion_adapter(source, FakeLocator(), gaze_enabled=True,
                               landmarker_factory=lambda: lm, gaze_interval_s=0.2)
    adapter._landmarker = lm
    adapter._capture_once()
    assert adapter.latest_gaze().x is not None

    source.on = False
    clock.t += 0.05
    adapter._capture_once()
    assert adapter.latest_gaze().x is not None, "one dropped frame is not a stopped camera"

    clock.t += 0.2
    adapter._capture_once()
    assert adapter.latest_gaze().rejected_by == "no_frame"


def test_a_face_that_cannot_be_cropped_does_not_keep_the_last_emotion(monkeypatch):
    """A face at the frame's edge can be found and still fail every crop."""
    adapter = _emotion_adapter(FakeSource(), FakeLocator())
    adapter._capture_once()
    assert adapter.latest_emotion().label == "sad"

    def no_crop(frame, box):
        raise ValueError("face box runs off the frame")

    monkeypatch.setattr("src.app.services.face_emotion.to_gray64", no_crop)
    adapter._capture_once()

    assert adapter.latest_emotion() is None


def test_degraded_names_which_of_three_causes_it_was():
    cases = {
        "no frames from the camera": (FakeSource(fail_after=0), FakeLocator()),
        "no face detected": (FakeSource(), type("N", (), {"locate": lambda s, g: None})()),
        "too little usable skin (lighting)": (
            FakeSource(frame=np.zeros((FRAME_H, FRAME_W, 3), dtype=np.uint8)),
            FakeLocator(),
        ),
    }
    for expected, (source, locator) in cases.items():
        adapter, _, _ = _adapter(source=source, locator=locator, fps=2000.0)
        adapter.connect()
        try:
            assert _wait_for(lambda: adapter.get_ingestion_meta()["face_degraded"])
            assert adapter.get_ingestion_meta()["face_degraded_reason"] == expected
        finally:
            adapter.disconnect()


def test_the_buffer_carries_quality_alongside_colour():
    """So the gate applies to the scored window, not the current tick."""
    adapter, _, _ = _adapter(fps=200.0)
    adapter.connect()
    try:
        assert _wait_for(lambda: len(adapter.rgb_buffer()) > 5)
        rgb, measured, quality, _ = adapter.rgb_window(1.0)
        assert len(rgb) > 5
        assert measured is not None and measured > 0
        assert quality == pytest.approx(1.0, abs=0.01)
    finally:
        adapter.disconnect()


def test_the_measured_rate_reflects_reality_not_the_setting():
    """A nominal fps is a request; timer resolution and dropped frames lower it."""
    adapter, _, _ = _adapter(fps=500.0)
    adapter.connect()
    try:
        assert _wait_for(lambda: len(adapter.rgb_buffer()) > 20)
        _, measured, _, _ = adapter.rgb_window(5.0)
    finally:
        adapter.disconnect()

    assert measured is not None
    assert measured < 500.0, "the measured rate just echoed the configured one"


def test_buffered_seconds_reports_measured_time_not_a_frame_count():
    """Real elapsed time, not `len(buffer) / nominal_fps`."""
    held = 2.0          # the floor: buffer_seconds must cover one POS window
    adapter, _, _ = _adapter(fps=20.0, buffer_seconds=held)
    adapter.connect()
    try:
        assert _wait_for(lambda: len(adapter.rgb_buffer()) > 20, timeout=5.0)
        time.sleep(held + 0.3)

        reported = adapter.get_ingestion_meta()["buffered_seconds"]
        _, _, _, stamps = adapter.rgb_window(float("inf"))
        assert reported == pytest.approx(stamps[-1] - stamps[0], abs=0.05)
        assert reported == pytest.approx(held, abs=0.3)
    finally:
        adapter.disconnect()


def test_buffered_seconds_is_zero_before_anything_is_captured():
    adapter, _, _ = _adapter()
    assert adapter.get_ingestion_meta()["buffered_seconds"] == 0.0


# -- the exposure warm-up --

def test_the_exposure_ramp_is_discarded_before_anything_is_buffered():
    """The auto-exposure ramp dwarfs the pulse and cannot be detected, so it is discarded."""
    source = FakeSource()
    adapter, _, _ = _adapter(source=source, warmup_seconds=0.4)
    adapter.connect()
    try:
        time.sleep(0.2)
        assert len(adapter.rgb_buffer()) == 0, "a warm-up frame reached the buffer"
        assert source.reads > 0, "warm-up did not read from the camera"
        assert adapter.get_ingestion_meta()["warmup_remaining_s"] > 0

        assert _wait_for(lambda: len(adapter.rgb_buffer()) > 3)
        meta = adapter.get_ingestion_meta()
        assert meta["warmup_remaining_s"] == 0.0
        assert meta["warmup_frames_discarded"] > 0
    finally:
        adapter.disconnect()


def test_discarded_frames_are_not_counted_as_captured():
    """frames_read and faces_found drive the degraded ratio."""
    adapter, _, _ = _adapter(warmup_seconds=0.3)
    adapter.connect()
    try:
        time.sleep(0.15)
        meta = adapter.get_ingestion_meta()
        assert meta["frames_read"] == 0
        assert meta["faces_found"] == 0
        assert meta["warmup_frames_discarded"] > 0
    finally:
        adapter.disconnect()


def test_warming_up_is_distinguishable_from_a_camera_that_cannot_see():
    """Both produce an empty buffer; only one is a fault."""
    warming, _, _ = _adapter(warmup_seconds=5.0)
    blind, _, _ = _adapter(source=FakeSource(fail_after=0))
    warming.connect()
    blind.connect()
    try:
        time.sleep(0.3)
        assert warming.get_ingestion_meta()["warmup_remaining_s"] > 0
        assert blind.get_ingestion_meta()["warmup_remaining_s"] == 0.0
    finally:
        warming.disconnect()
        blind.disconnect()


def test_disconnecting_during_warm_up_is_prompt():
    """The warm-up loop must watch the stop event, not just the clock."""
    adapter, _, _ = _adapter(warmup_seconds=30.0)
    adapter.connect()
    started = time.perf_counter()
    adapter.disconnect()
    assert time.perf_counter() - started < 2.0, "disconnect waited for the warm-up"


# -- the heart channel is dormant --

def test_the_heart_channel_is_off_by_default():
    """Failed ECG validation (tests/fixtures/FACE_RPPG_ECG.md); must not come back by drift."""
    from src.app.config import Settings

    settings = Settings(API_TOKEN="x", ADMIN_TOKEN="y", EEG_DEVICES="camera:face@0")
    assert settings.face_heart_enabled is False
    assert settings.face_emotion_enabled is True, "the camera ships emotion-only"


def test_enabling_the_heart_channel_is_never_quiet(caplog):
    """Warned, not refused: the confidence gate cannot catch a confident wrong rate here."""
    import logging

    with caplog.at_level(logging.WARNING):
        _adapter(fps=30.0)

    assert any("FACE_RPPG_ECG.md" in r.message for r in caplog.records), (
        "enabling camera heart rate produced no warning"
    )


# -- gaze --

class FakeLandmarker:
    """Stands in for MediaPipe."""

    def __init__(self, named=None, raises=False):
        self.named = named if named is not None else {}
        self.raises = raises
        self.calls = 0

    def locate(self, frame, width, height):
        self.calls += 1
        if self.raises:
            raise RuntimeError("mediapipe exploded")
        return self.named


def _eyes_looking(dx: float) -> dict:
    named = {}
    for side, (lo, hi) in (("right", (40.0, 60.0)), ("left", (90.0, 110.0))):
        mid = (lo + hi) / 2
        named[f"{side}_eye_outer"] = (lo if side == "right" else hi, 50.0)
        named[f"{side}_eye_inner"] = (hi if side == "right" else lo, 50.0)
        named[f"{side}_eye_upper"] = (mid, 45.0)
        named[f"{side}_eye_lower"] = (mid, 55.0)
        named[f"{side}_iris"] = (mid + dx, 50.0)
    return named


def _gaze_adapter(landmarker, locator=None, gaze_interval_s=0.0):
    src = FakeSource()
    return FaceCaptureAdapter(
        lambda: src, lambda: locator or FakeLocator(), fps=500.0,
        buffer_seconds=2.0, error_backoff=0.0, warmup_seconds=0.0,
        gaze_enabled=True, landmarker_factory=lambda: landmarker,
        gaze_interval_s=gaze_interval_s), src


def test_gaze_enabled_without_a_landmarker_is_refused_at_construction():
    with pytest.raises(ValueError, match="landmarker_factory"):
        FaceCaptureAdapter(FakeSource, FakeLocator, gaze_enabled=True)


def test_gaze_is_sampled_from_the_capture_loop():
    lm = FakeLandmarker(_eyes_looking(+6.0))
    adapter, _ = _gaze_adapter(lm)
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.latest_gaze() is not None)
        reading = adapter.latest_gaze()
    finally:
        adapter.disconnect()

    assert reading.x is not None and reading.x > 0


def test_a_haar_miss_does_not_suppress_gaze():
    """The landmarker detects on the full frame, so gaze must not depend on the Haar box."""
    class NeverFinds:
        def locate(self, gray):
            return None

    lm = FakeLandmarker(_eyes_looking(+6.0))
    adapter, _ = _gaze_adapter(lm, locator=NeverFinds())
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.latest_gaze() is not None), \
            "no gaze was ever sampled while Haar found nothing"
        reading = adapter.latest_gaze()
    finally:
        adapter.disconnect()

    # Read inside the try: `disconnect()` clears the reading.
    assert reading.x is not None and reading.x > 0


def test_a_landmarker_that_raises_costs_only_gaze():
    """Gaze runs before the colour sample, so an escaping exception would cost the heart channel."""
    lm = FakeLandmarker(raises=True)
    adapter, _ = _gaze_adapter(lm)
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.get_ingestion_meta()["frames_read"] > 5)
        assert _wait_for(lambda: len(adapter.rgb_buffer()) > 0), \
            "the colour buffer stopped filling when gaze raised"
        contained = adapter.latest_gaze()
    finally:
        adapter.disconnect()

    # A refusal on this channel rather than escaping.
    assert contained is not None, "a raising landmarker produced no state at all"
    assert contained.x is None


def test_a_refusal_is_kept_rather_than_discarded():
    """`rejected_by` tells a closed eye from a channel that has produced nothing yet."""
    lm = FakeLandmarker({})            # no eyes at all
    adapter, _ = _gaze_adapter(lm)
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.latest_gaze() is not None)
        reading = adapter.latest_gaze()
    finally:
        adapter.disconnect()

    assert reading.x is None and reading.rejected_by == "no_eye"


def test_gaze_runs_on_its_own_interval_not_every_frame():
    lm = FakeLandmarker(_eyes_looking(+6.0))
    adapter, _ = _gaze_adapter(lm, gaze_interval_s=10.0)
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.get_ingestion_meta()["frames_read"] > 20)
    finally:
        adapter.disconnect()

    assert lm.calls == 1, f"ran {lm.calls} times over 20+ frames"


def test_disconnect_drops_the_reading_but_keeps_the_model():
    """The landmarker is kept, since MediaPipe takes seconds to build a model."""
    lm = FakeLandmarker(_eyes_looking(+6.0))
    adapter, _ = _gaze_adapter(lm)
    adapter.connect()
    assert _wait_for(lambda: adapter.latest_gaze() is not None)
    adapter.disconnect()

    assert adapter.latest_gaze() is None
    assert adapter._landmarker is lm


def test_a_landmarker_that_always_fails_says_so_rather_than_warming_up():
    """None reports as `no_reading` (warming up); broken and not-yet-started differ."""
    lm = FakeLandmarker(raises=True)
    adapter, _ = _gaze_adapter(lm)
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.latest_gaze() is not None), \
            "a failing landmarker left the channel indistinguishable from warm-up"
        reading = adapter.latest_gaze()
    finally:
        adapter.disconnect()

    assert reading.x is None
    assert reading.rejected_by == "landmarker_failed"


def test_a_missing_landmark_model_costs_gaze_and_not_the_camera():
    """An off-by-default channel must not take the camera down; it stays on and says why."""
    def explode():
        raise FileNotFoundError("no face landmark model at models/…")

    src = FakeSource()
    adapter = FaceCaptureAdapter(
        lambda: src, lambda: FakeLocator(), fps=500.0, buffer_seconds=2.0,
        error_backoff=0.0, warmup_seconds=0.0,
        gaze_enabled=True, landmarker_factory=explode, gaze_interval_s=0.0)

    adapter.connect()                      # must not raise
    try:
        assert _wait_for(lambda: len(adapter.rgb_buffer()) > 0), \
            "a missing landmark model stopped the colour channel"
        reading = adapter.latest_gaze()
    finally:
        adapter.disconnect()

    assert adapter.gaze_enabled is True, \
        "the channel reported itself off, which is a claim about configuration"
    assert reading is not None and reading.x is None
    assert reading.rejected_by == "landmarker_unavailable"


def test_head_pose_is_stored_alongside_gaze():
    named = _eyes_looking(0.0)
    named.update(nose_tip=(75.0, 70.0), chin=(75.0, 110.0),
                 mouth_left=(88.0, 95.0), mouth_right=(62.0, 95.0))
    adapter, _ = _gaze_adapter(FakeLandmarker(named))
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.latest_pose() is not None)
        pose = adapter.latest_pose()
    finally:
        adapter.disconnect()

    assert pose is not None
    # A reading or a named refusal; the fit may refuse a synthetic face.
    assert pose.yaw is not None or pose.rejected_by


def test_disconnect_drops_the_pose_too():
    adapter, _ = _gaze_adapter(FakeLandmarker(_eyes_looking(+6.0)))
    adapter.connect()
    assert _wait_for(lambda: adapter.latest_pose() is not None)
    adapter.disconnect()

    assert adapter.latest_pose() is None


def test_a_missing_landmark_model_names_the_pose_refusal_too():
    def explode():
        raise FileNotFoundError("no model")

    adapter = FaceCaptureAdapter(
        lambda: FakeSource(), lambda: FakeLocator(), fps=500.0,
        buffer_seconds=2.0, error_backoff=0.0, warmup_seconds=0.0,
        gaze_enabled=True, landmarker_factory=explode, gaze_interval_s=0.0)
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.latest_pose() is not None)
        pose = adapter.latest_pose()
    finally:
        adapter.disconnect()

    assert pose.yaw is None
    assert pose.rejected_by == "landmarker_unavailable"


def test_a_failure_in_one_derivation_keeps_the_other():
    """Marking both dead when only one raised would discard a good reading."""
    class HalfBroken:
        """Landmarks that gaze can read and that make head_pose explode."""
        def locate(self, frame, w, h):
            return _eyes_looking(+6.0)

    import src.app.services.face_ingestion as fi
    real = fi.__dict__.get("head_pose")
    adapter, _ = _gaze_adapter(HalfBroken())

    import src.app.services.face_geometry as fg
    original = fg.head_pose
    fg.head_pose = lambda _named: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        adapter.connect()
        assert _wait_for(lambda: adapter.latest_pose() is not None)
        gaze_reading, pose = adapter.latest_gaze(), adapter.latest_pose()
        adapter.disconnect()
    finally:
        fg.head_pose = original

    assert pose.rejected_by == "landmarker_failed"
    assert gaze_reading.x is not None and gaze_reading.x > 0, (
        "a usable gaze was discarded because head_pose raised")
