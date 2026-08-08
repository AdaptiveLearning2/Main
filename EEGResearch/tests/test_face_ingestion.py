"""Headless capture, with the camera faked.

The frame source and the face locator are both injected, so the adapter's
threading, bounded buffering, quality gating and teardown are exercised without
OpenCV — which is absent from CI by design and would make these silently skip.

Fakes are inline and duck-typed, matching the existing convention for adapter
tests (`FailingAdapter` in test_app.py) rather than introducing a base class the
production code does not have either.
"""

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

# Small frames on purpose: mean_rgb is O(pixels), and a test that fed 640x480
# could not fill a buffer inside a sane timeout. The box is scaled to match.
# Big enough to carry a face crop of at least 64x64, which is the model's own
# input size -- anything smaller would have to be upsampled, and to_gray64
# refuses that rather than invent detail.
FRAME_H, FRAME_W = 120, 160
FACE_BOX = (20, 10, 100, 100)


class FakeSource:
    """Yields a fixed frame, optionally failing or ending.

    `read()` sleeps for a beat, because a real one blocks until the sensor has a
    frame -- and since the capture loop is now paced by that block rather than
    by a sleep of its own, a source returning instantly is not a fast camera but
    an impossible one. Returning with no delay put many samples on the same
    `time.monotonic()` value, which made the median frame interval zero and the
    measured rate unmeasurable: a real duplicate-timestamp failure, reachable
    only through a fake.
    """

    READ_SECONDS = 0.002

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
    """Always finds a face, unless told otherwise."""

    def __init__(self, box=FACE_BOX):
        self.box = box

    def locate(self, gray):
        return self.box


def _flat_frame(colour=(180, 120, 110)):
    return np.tile(np.array(colour, dtype=np.uint8), (FRAME_H, FRAME_W, 1))


def _adapter(source=None, locator=None, fps=500.0, buffer_seconds=2.0,
             queue_max=QUEUE_MAX, error_backoff=0.0):
    """fps is deliberately high and the buffer deliberately small so tests run
    in milliseconds; nothing under test depends on the wall-clock rate.

    `error_backoff` is zero here and 0.1 s in production. The backoff exists so
    a camera that is gone cannot spin a core; in a test the only effect is to
    multiply the tolerance for consecutive failures by a tenth of a second
    apiece, which turns a millisecond assertion into a three-second one."""
    src = source or FakeSource()
    loc = locator or FakeLocator()
    adapter = FaceCaptureAdapter(lambda: src, lambda: loc, fps=fps,
                                 buffer_seconds=buffer_seconds,
                                 queue_max=queue_max,
                                 error_backoff=error_backoff)
    return adapter, src, loc


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


# ── lifecycle ────────────────────────────────────────────────────────────────

def test_captures_and_emits_samples():
    adapter, _, _ = _adapter()
    adapter.connect()
    try:
        assert _wait_for(lambda: len(adapter.drain_samples(10)) > 0)
    finally:
        adapter.disconnect()


def test_disconnect_joins_the_thread_and_releases_the_camera():
    """A daemon thread that logs during interpreter shutdown, while the stdout
    lock is held, is a fatal abort that reads as unrelated flake -- the failure
    `eeg_poller.stop_all()` exists to prevent. Joining is not optional."""
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
    """A second session must not inherit the first one's colour history -- POS
    would splice two unrelated recordings across the join."""
    # fps=20 so one POS window is 32 frames rather than 800. The loop paces on
    # Event.wait, whose resolution on Windows is ~15 ms, so a nominal 500 fps
    # actually runs near 65 and a full window would take 12 s to fill.
    adapter, _, _ = _adapter(fps=20.0, buffer_seconds=2.0)
    adapter.connect()
    assert _wait_for(adapter.has_full_window, timeout=6.0)
    adapter.disconnect()

    assert adapter.rgb_window(10.0)[0].shape == (0, 3)
    assert adapter.drain_samples(100) == []


# ── the capture thread never blocks and never grows ──────────────────────────

def test_the_buffer_is_bounded():
    """An eight-hour session must cost the same per frame as the first minute.
    The original re-scanned every snapshot on every tick, which is O(n^2) in
    session length."""
    adapter, _, _ = _adapter(fps=20.0, buffer_seconds=2.5)
    cap = 50
    adapter.connect()
    try:
        assert _wait_for(lambda: len(adapter.rgb_buffer()) >= cap, timeout=5.0)
        time.sleep(0.2)
        assert len(adapter.rgb_buffer()) == cap, "the buffer grew past its bound"
    finally:
        adapter.disconnect()
    assert BUFFER_SECONDS == 40.0, "production default changed; check the comment"


def test_a_stalled_consumer_drops_samples_rather_than_stalling_capture():
    """Blocking on a full queue would stall frame grabbing, which does not
    merely lose frames -- it corrupts the time series, because POS and the rate
    derivation both assume a roughly uniform sample interval."""
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
    """Found by a test that expected has_full_window() to become true and waited
    forever. Without the guard, such an adapter connects, reports healthy and
    counts frames while never producing a reading -- indistinguishable from a
    student who is not there."""
    with pytest.raises(ValueError, match="shorter than one POS window"):
        FaceCaptureAdapter(FakeSource, FakeLocator,
                           buffer_seconds=WINDOW_SECONDS / 2)


# ── failure handling ─────────────────────────────────────────────────────────

def test_a_camera_that_stops_yielding_frames_does_not_kill_the_thread():
    adapter, source, _ = _adapter(source=FakeSource(fail_after=3))
    adapter.connect()
    try:
        assert _wait_for(lambda: source.reads > 20)
        assert adapter.get_ingestion_meta()["camera_connected"]
    finally:
        adapter.disconnect()


def test_an_exception_is_caught_at_the_thread_boundary_and_surfaced():
    """Not by installing a process-wide threading.excepthook, which is what the
    original did -- that silences every other thread's errors in the process,
    including ones unrelated to the camera."""
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
    """A zero would enter POS as a real measurement and put a step into the
    waveform. The contract at every layer is a missing sample."""
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
    """A blink or a turn of the head is not a fault. Only a sustained absence
    is, or the state would flap on every ordinary movement."""
    adapter, _, _ = _adapter()
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.get_ingestion_meta()["frames_read"] > 2)
        assert not adapter.get_ingestion_meta()["face_degraded"]
        assert MISSING_FACE_TOLERANCE > 1
    finally:
        adapter.disconnect()


# ── the contract with the rest of the pipeline ───────────────────────────────

def test_rgb_window_returns_a_copy_not_a_view():
    """The capture thread mutates the buffer. Handing out a view would let a
    consumer read a half-written window."""
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
    """In the original, SQI was surfaced under `quality.confidence` while
    downstream read `features.confidence` as a generic confidence in the
    reading, so a well-lit face and a trusted heart rate became one field."""
    adapter, _, _ = _adapter()
    adapter.connect()
    try:
        meta = adapter.get_ingestion_meta()
        assert "confidence" not in meta
        assert "face_found_ratio" in meta
    finally:
        adapter.disconnect()


def test_a_pulse_survives_the_full_capture_chain():
    """Frames in, heart rate out, through every layer this phase adds."""
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

    # Driven synchronously rather than by the capture thread. 25 s of samples at
    # a true 30 fps is 25 s of wall clock, and pacing the thread faster would
    # decouple the frame index from the fps the pulse is generated at. The
    # threading is covered by every other test in this file; this one is about
    # the signal path.
    adapter = FaceCaptureAdapter(PulsingSource, FakeLocator, fps=fps,
                                 buffer_seconds=30.0)
    adapter._source = PulsingSource()
    adapter._locator = FakeLocator()
    for _ in range(int(25 * fps)):
        adapter._capture_once()
    rgb = adapter.rgb_buffer()

    assert len(rgb) >= int(24 * fps)
    assert estimate_window(pos_pulse(rgb, fps), fps).bpm == pytest.approx(bpm, abs=3.0)


# ── the two channels, independently switchable ───────────────────────────────

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


def test_opening_a_camera_with_both_channels_off_is_refused():
    """Opening a camera to compute nothing is never what was meant, and the
    failure would be silent: frames read, nothing produced, indistinguishable
    from a student out of shot."""
    with pytest.raises(ValueError, match="both heart and emotion disabled"):
        FaceCaptureAdapter(FakeSource, FakeLocator,
                           heart_enabled=False, emotion_enabled=False)


def test_emotion_enabled_without_a_classifier_is_refused():
    with pytest.raises(ValueError, match="requires an emotion_classifier_factory"):
        FaceCaptureAdapter(FakeSource, FakeLocator, emotion_enabled=True)


def test_emotion_runs_far_less_often_than_frames_are_captured():
    """Expression changes on a timescale of seconds. Classifying every frame
    would spend roughly thirty times the CPU for the same answer, on a device
    also running a browser, a maths lesson and the EEG stack."""
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
    """Warm standby with a healthy headband: the camera is open and FER+ runs,
    while POS is idle. The colour buffer must stay empty so nothing downstream
    mistakes an idle heart channel for a stalled one."""
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
    """Before connect() the classifier does not exist yet -- it is built there,
    so a registry can name a camera on a machine with no model provisioned --
    so its own meta is absent rather than fabricated."""
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
    """A second session must not open showing the previous student's
    expression."""
    clf = FakeClassifier()
    adapter = FaceCaptureAdapter(
        lambda: FakeSource(), lambda: FakeLocator(), fps=30.0,
        buffer_seconds=2.0, emotion_enabled=True, emotion_classifier_factory=lambda: clf,
        emotion_interval_s=0.0,
    )
    adapter.connect()
    try:
        assert _wait_for(lambda: adapter.latest_emotion() is not None)
    finally:
        adapter.disconnect()
    assert adapter.latest_emotion() is None


def test_degraded_names_which_of_three_causes_it_was():
    """A disconnected webcam, a student who left, and bad lighting are three
    different problems. One string for all of them is the conflation this module
    argues against elsewhere."""
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
    """So the gate downstream applies to the window being scored rather than to
    whatever arrived on the current tick."""
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
    """A nominal fps is a request. Event.wait resolution alone means a loop asked
    for 500 fps runs nearer 65, and a real camera drops frames under load."""
    adapter, _, _ = _adapter(fps=500.0)
    adapter.connect()
    try:
        assert _wait_for(lambda: len(adapter.rgb_buffer()) > 20)
        _, measured, _, _ = adapter.rgb_window(5.0)
    finally:
        adapter.disconnect()

    assert measured is not None
    assert measured < 500.0, "the measured rate just echoed the configured one"
