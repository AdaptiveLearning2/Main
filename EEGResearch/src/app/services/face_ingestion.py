"""Headless camera capture: frames in, colour samples out, nothing stored.

The adapter duck-types the same shape as the EEG ones — `connect`,
`disconnect`, `drain_samples`, `get_ingestion_meta` — because `stream_manager`
probes with `hasattr` and has no base class to inherit.

Three constraints shape the whole design, and each is a defect in the original
CLI this is ported from:

**The capture thread never blocks on I/O.** In the original, the backend POST
ran synchronously inside the capture loop, so a 10 s HTTP timeout stalled frame
grabbing — which does not merely lose frames, it corrupts the time series,
because POS and the rate derivation both assume a roughly uniform sample
interval. Here the capture thread only ever appends to a bounded queue.

**Nothing accumulates.** The original re-scanned every snapshot taken so far on
every tick to rebuild summaries, which is O(n²) in session length. Nothing here
keeps a growing structure: the RGB buffer is a fixed-length deque and the queue
is bounded, so an eight-hour session costs the same per frame as the first
minute.

**No image is retained.** Frames are read, reduced to three numbers, and
dropped in the same iteration. There is no path from this module to disk, to a
payload, or to a second frame's memory. That is the product constraint the
facial pipeline exists under, and honouring it here means no later layer *can*
violate it by accident.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from itertools import islice
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

import numpy as np

from src.app.services.face_roi import mean_rgb
from src.app.services.pos_rppg import WINDOW_SECONDS

logger = logging.getLogger(__name__)

# Bounded so a stalled consumer costs a fixed amount of memory and drops the
# oldest samples rather than growing until the process dies. Two minutes at
# 30 fps is far more than any consumer should be behind.
QUEUE_MAX = 3600

# How much colour history to keep for POS. It needs one window (1.6 s); the rate
# derivation downstream wants 25-30 s. 40 s at 30 fps gives that with headroom
# and is a fixed cost.
BUFFER_SECONDS = 40.0

# Consecutive frames without a usable face before the adapter reports degraded.
# One missed frame is a blink or a turn of the head; a second of them is the
# student having left or the lighting having failed.
MISSING_FACE_TOLERANCE = 30

# ITU-R BT.601 luma. The eye is far more sensitive to green than to blue, and
# both Haar and FER+ were trained on images converted this way; a flat RGB mean
# is a measurably different picture.
LUMA_WEIGHTS = np.array([0.299, 0.587, 0.114], dtype=np.float32)

# How often the emotion classifier runs, in seconds. Far below the frame rate on
# purpose: expression changes on a timescale of seconds, so classifying every
# frame would spend roughly thirty times the CPU to produce the same answer --
# on a device that is also running a browser, a maths lesson and the EEG stack.
EMOTION_INTERVAL_S = 0.25


class FrameSource(Protocol):
    """Anything that yields frames. A webcam in production, a list in tests.

    Injected rather than constructed internally so the adapter's threading,
    buffering, quality gating and teardown are all testable without OpenCV --
    which is absent from CI by design.
    """

    def read(self) -> np.ndarray | None:
        """Next frame as (h, w, 3) RGB, or None when unavailable."""

    def release(self) -> None:
        ...


@dataclass
class FaceSample:
    """One frame's contribution. Three numbers and a quality figure."""
    monotonic_ts: float
    rgb: tuple[float, float, float]
    usable_fraction: float


@dataclass
class _Counters:
    frames_read: int = 0
    faces_found: int = 0
    samples_emitted: int = 0
    dropped_full_queue: int = 0
    consecutive_missing: int = 0
    # Why the last frame produced nothing: "camera" (no frame at all),
    # "no_face" (nothing detected) or "quality" (face found, too little usable
    # skin). Three different problems -- a disconnected webcam, a student who
    # left, and bad lighting -- and collapsing them into one string is the same
    # conflation this module argues against elsewhere.
    missing_reason: str | None = None
    last_error: str | None = None
    # Counters rather than lists. A list of per-frame records would be the same
    # unbounded-growth mistake the docstring above exists to prevent.


class FaceCaptureAdapter:
    """Reads frames on a thread, emits colour samples, retains no images."""

    def __init__(
        self,
        frame_source_factory: Callable[[], FrameSource],
        locator_factory: Callable[[], Any],
        *,
        fps: float = 30.0,
        buffer_seconds: float = BUFFER_SECONDS,
        queue_max: int = QUEUE_MAX,
        heart_enabled: bool = True,
        emotion_enabled: bool = False,
        emotion_classifier_factory: Callable[[], Any] | None = None,
        emotion_interval_s: float = EMOTION_INTERVAL_S,
    ) -> None:
        # buffer_seconds and queue_max are injectable so the bounded-growth and
        # queue-full behaviours can be tested at a scale that runs in
        # milliseconds. Defaults are the production values; a test that had to
        # fill 40 s of buffer at 30 fps to prove a bound would take 40 s, so in
        # practice the bound would go untested.
        if buffer_seconds < WINDOW_SECONDS:
            # Guarded rather than left to produce nothing. A buffer shorter than
            # one POS window can never yield a pulse, and the symptom would be a
            # camera that connects, reports healthy, counts frames, and silently
            # never produces a reading -- indistinguishable from a student who
            # is simply not there.
            raise ValueError(
                f"buffer_seconds={buffer_seconds} is shorter than one POS window "
                f"({WINDOW_SECONDS}s); no pulse could ever be produced"
            )
        if not heart_enabled and not emotion_enabled:
            # Opening a camera to compute nothing is never what was meant, and
            # the failure would be silent: frames read, nothing produced,
            # indistinguishable from a student out of shot. The plan's rule is
            # that emotion off with a healthy headband means the camera is not
            # open at all -- this makes that unrepresentable rather than
            # merely intended.
            raise ValueError(
                "refusing to open a camera with both heart and emotion disabled"
            )
        if emotion_enabled and emotion_classifier_factory is None:
            raise ValueError("emotion_enabled requires an emotion_classifier_factory")

        self._make_source = frame_source_factory
        self._make_locator = locator_factory
        self.fps = fps
        self.heart_enabled = heart_enabled
        self.emotion_enabled = emotion_enabled
        # A factory, like the frame source and locator, and for the same reason:
        # constructing the classifier loads and verifies a 35 MB model, so doing
        # it here would mean a device registry could not *name* a camera on a
        # machine that has not provisioned one. Built at connect(), where the
        # error names the real problem.
        self._make_emotion = emotion_classifier_factory
        self._emotion: Any = None
        self._emotion_interval = emotion_interval_s
        self._last_emotion_at = 0.0
        self._latest_emotion: Any = None

        self._source: FrameSource | None = None
        self._locator: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._queue: queue.Queue[FaceSample] = queue.Queue(maxsize=queue_max)
        # (monotonic_ts, r, g, b, usable_fraction). The timestamp is kept because the configured
        # fps is a request, not a measurement: a webcam asked for 30 routinely
        # delivers 22 under load or poor light. Deriving the time base from the
        # nominal rate would scale every bpm by nominal/actual -- 30/22 is +36%,
        # a confidently wrong heart rate with nothing to indicate it.
        # usable_fraction rides along so quality gating is per *window* rather
        # than per tick. Deriving it from the samples drained since the last tick
        # made it intermittent: a tick that drained nothing left quality unknown
        # while the 25 s colour buffer was still full and scored, so the gate
        # silently did not run. Ticks faster than the frame rate hit that
        # routinely.
        self._buffer: deque[tuple[float, float, float, float, float]] = deque(
            maxlen=max(1, int(buffer_seconds * fps))
        )
        self._lock = threading.Lock()
        self._counters = _Counters()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the camera and start capturing.

        Raises if the camera cannot be opened. Unlike the Muse bridge adapter,
        which retries because a headband may legitimately be switched on later,
        a camera that will not open is a configuration or permission problem
        and silently retrying would hide it.
        """
        if self._thread is not None:
            return
        self._source = self._make_source()
        self._locator = self._make_locator()
        if self.emotion_enabled and self._emotion is None:
            self._emotion = self._make_emotion()
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, name="face-capture", daemon=True
        )
        self._thread.start()

    def disconnect(self) -> None:
        """Stop capturing and release the camera.

        Joined rather than left to daemon teardown. A daemon thread that logs
        during interpreter shutdown, while the stdout lock is held, is a fatal
        `_enter_buffered_busy` abort that reads as unrelated flake -- the same
        failure `eeg_poller.stop_all()` exists to prevent.
        """
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            if self._thread.is_alive():
                logger.warning("face capture thread did not stop within 3s")
            self._thread = None
        if self._source is not None:
            self._source.release()
            self._source = None
        self._locator = None
        self._latest_emotion = None
        self._last_emotion_at = 0.0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self._buffer.clear()

    # ── capture ──────────────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        interval = 1.0 / self.fps if self.fps > 0 else 0.0
        while not self._stop.is_set():
            started = time.monotonic()
            try:
                self._capture_once()
            except Exception as exc:                      # noqa: BLE001
                # Caught at the thread boundary rather than by installing a
                # process-wide threading.excepthook, which is what the original
                # did -- that silences every other thread's errors in the same
                # process, including ones that have nothing to do with the
                # camera.
                with self._lock:
                    self._counters.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("face capture iteration failed")

            # Wait on the stop event rather than sleeping, so disconnect() takes
            # effect immediately instead of up to one frame interval later.
            remaining = interval - (time.monotonic() - started)
            if remaining > 0:
                self._stop.wait(remaining)

    def _capture_once(self) -> None:
        frame = self._source.read() if self._source else None
        if frame is None:
            with self._lock:
                self._counters.consecutive_missing += 1
                self._counters.missing_reason = "camera"
            return

        with self._lock:
            self._counters.frames_read += 1

        # Luma-weighted, not a flat mean. Haar cascades are trained on
        # ITU-R BT.601 luma, and a flat RGB average is a different image --
        # notably brighter in the red channel, which is most of a face.
        gray = frame.astype(np.float32) @ LUMA_WEIGHTS
        box = self._locator.locate(gray)
        if box is None:
            with self._lock:
                self._counters.consecutive_missing += 1
                self._counters.missing_reason = "no_face"
            return

        now = time.monotonic()
        if (self.emotion_enabled
                and now - self._last_emotion_at >= self._emotion_interval):
            self._last_emotion_at = now
            from src.app.services.face_emotion import to_gray64  # noqa: PLC0415

            try:
                crop = to_gray64(frame, box)
                result = self._emotion.classify(crop)
            except Exception as exc:                      # noqa: BLE001
                # Guarded separately from classify(), which has its own handler.
                # to_gray64 did not, so a geometry it could not handle aborted
                # the whole iteration and took the colour sample with it -- one
                # channel's failure silently stopping the other, when the two
                # are meant to be independent.
                logger.exception("emotion crop failed")
                with self._lock:
                    self._counters.last_error = f"{type(exc).__name__}: {exc}"
            else:
                with self._lock:
                    self._latest_emotion = result

        if not self.heart_enabled:
            # Emotion-only. The face was found and classified; there is no
            # colour sample to make, and the buffer stays empty so nothing
            # downstream mistakes an idle heart channel for a stalled one.
            with self._lock:
                self._counters.faces_found += 1
                self._counters.consecutive_missing = 0
            return

        sample = mean_rgb(frame, box)
        # `frame` goes out of scope here and is never referenced again. Every
        # path below deals only in the three numbers.
        with self._lock:
            self._counters.faces_found += 1
            if not sample.ok:
                self._counters.consecutive_missing += 1
                self._counters.missing_reason = "quality"
                return
            self._counters.consecutive_missing = 0
            self._counters.missing_reason = None
            self._buffer.append((now, *sample.rgb, sample.usable_fraction))

        item = FaceSample(time.monotonic(), sample.rgb, sample.usable_fraction)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # Drop the newest rather than block. Blocking here would stall
            # capture, which is the defect this whole structure exists to avoid,
            # and a consumer 2 minutes behind has bigger problems than one lost
            # frame.
            with self._lock:
                self._counters.dropped_full_queue += 1
            return
        with self._lock:
            self._counters.samples_emitted += 1

    # ── consumption ──────────────────────────────────────────────────────────

    def drain_samples(self, max_batch: int) -> list[FaceSample]:
        """Every queued sample, up to max_batch. Never blocks.

        Deliberately unlike the Muse adapter, which blocks briefly and raises on
        timeout. A camera that has produced nothing yet is the normal state for
        the first second of a session and during any moment the student looks
        away; it is not an error, and raising would turn an ordinary gap into a
        stream restart.
        """
        out: list[FaceSample] = []
        while len(out) < max_batch:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    def rgb_buffer(self) -> np.ndarray:
        """Everything currently buffered, as (n, 3)."""
        return self.rgb_window(float("inf"))[0]

    def window_quality(self, seconds: float) -> float | None:
        """Mean usable-pixel fraction over the same window as the colour."""
        return self.rgb_window(seconds)[2]

    def rgb_window(
        self, seconds: float
    ) -> tuple[np.ndarray, float | None, float | None, np.ndarray]:
        """The most recent `seconds` of colour, the rate it was *actually*
        sampled at, its mean quality, and the timestamp of every sample.

        Returns (rgb, measured_fps, mean_usable_fraction, timestamps).
        `measured_fps` is None when there are too few samples to measure one; a
        caller must treat that as no window rather than falling back to the
        nominal rate, which is the assumption this return value exists to
        remove. Quality comes from the same window as the colour, so the gate
        applies to what is being scored rather than to whatever happened to
        arrive this tick.

        `measured_fps` is the **median** interval's rate, not samples-over-span.

        Both were computed on a real webcam and they disagree. Asked for 30 fps
        it delivered intervals that were bimodal -- 78% at 31 ms, 21% at 47 ms,
        with occasional stalls past 100 ms. Span-based said 28.6 Hz; the median
        said 32.3 Hz, which is the rate the camera was genuinely running at.
        A mean is dragged by the tail of stalls and reports a rate no interval
        in the recording actually had.

        (This is the opposite of the call made for the headband's optical
        packets, where the median was wrong because ~9% of timestamps were
        exact duplicates from the SDK's batching. Here every stamp is a distinct
        `time.monotonic()` read at the moment of capture, so the median is the
        robust statistic it is normally assumed to be. The difference is in the
        clock, not the preference.)

        Copies rather than views: the buffer is mutated by the capture thread.
        `islice` over the tail rather than `list(...)[-n:]`, which materialised
        the whole deque under the capture thread's lock on every tick to keep
        the last 60% of it.
        """
        with self._lock:
            n = len(self._buffer)
            want = n if seconds == float("inf") else min(n, int(seconds * self.fps))
            data = list(islice(self._buffer, n - want, n)) if want else []

        if not data:
            return np.empty((0, 3)), None, None, np.empty(0)

        rgb = np.array([row[1:4] for row in data], dtype=float)
        timestamps = np.array([row[0] for row in data], dtype=float)
        quality = float(np.mean([row[4] for row in data]))
        if len(data) < 2:
            return rgb, None, quality, timestamps
        median_interval = float(np.median(np.diff(timestamps)))
        measured = (1.0 / median_interval) if median_interval > 0 else None
        return rgb, measured, quality, timestamps

    def has_full_window(self) -> bool:
        """Whether enough colour history exists for POS to produce anything."""
        with self._lock:
            return len(self._buffer) >= int(WINDOW_SECONDS * self.fps)

    def measured_fps(self) -> float | None:
        """The rate the buffer was actually filled at, or None if unmeasurable."""
        return self.rgb_window(float("inf"))[1]

    # ── reporting ────────────────────────────────────────────────────────────

    def latest_emotion(self) -> Any:
        """The most recent classification, or None if emotion is off or nothing
        has been classified yet."""
        with self._lock:
            return self._latest_emotion

    def get_ingestion_meta(self) -> dict[str, Any]:
        """Camera state for the API.

        `face_quality` is named for what it is -- the fraction of pixels that
        survived the luminance mask -- and is deliberately *not* called
        confidence. In the original, the SQI was surfaced under
        `quality.confidence` while downstream code read `features.confidence` as
        a generic confidence in the reading, so a well-lit face and a trusted
        heart rate became the same field.
        """
        with self._lock:
            c = self._counters
            degraded = c.consecutive_missing >= MISSING_FACE_TOLERANCE
            return {
                "camera_connected": self._thread is not None and self._thread.is_alive(),
                "frames_read": c.frames_read,
                "faces_found": c.faces_found,
                "face_found_ratio": (c.faces_found / c.frames_read) if c.frames_read else None,
                "samples_emitted": c.samples_emitted,
                "samples_dropped": c.dropped_full_queue,
                "buffered_seconds": len(self._buffer) / self.fps if self.fps else 0.0,
                "face_degraded": degraded,
                "face_degraded_reason": (
                    {
                        "camera": "no frames from the camera",
                        "no_face": "no face detected",
                        "quality": "too little usable skin (lighting)",
                    }.get(c.missing_reason, "no usable face")
                    if degraded else None
                ),
                "last_error": c.last_error,
                "heart_enabled": self.heart_enabled,
                "emotion_enabled": self.emotion_enabled,
                **(self._emotion.get_meta() if self._emotion is not None else {}),
            }


class OpenCvFrameSource:
    """A webcam, behind the FrameSource protocol.

    OpenCV is imported here rather than at module scope so `face_ingestion` can
    be imported -- and everything above it tested -- on a machine with no camera
    dependencies. That is the state of CI and of any deployment that never
    enables the camera.

    Converts BGR to RGB at this boundary. OpenCV hands out BGR and every layer
    above assumes RGB; converting once here means POS's projection matrix can
    never silently be fed reversed channels, which would not error, just quietly
    halve the pulse.
    """

    def __init__(self, camera_index: int = 0, width: int = 640, height: int = 480,
                 fps: float = 30.0) -> None:
        import cv2                                   # noqa: PLC0415 -- lazy by design

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(camera_index)
        if not self._cap.isOpened():
            raise RuntimeError(f"could not open camera index {camera_index}")

        # Ask for a fixed exposure and white balance. Auto-exposure is the
        # single worst thing for rPPG: it reacts to the scene on roughly the
        # timescale of a heartbeat, so the camera's own gain control writes a
        # signal into the pulse band that looks exactly like what we are trying
        # to measure. Not all drivers honour these; failure to set them is
        # reported rather than silently accepted.
        self.locked = {
            "auto_exposure": bool(self._cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)),
            "auto_wb": bool(self._cap.set(cv2.CAP_PROP_AUTO_WB, 0)),
            "fps": bool(self._cap.set(cv2.CAP_PROP_FPS, fps)),
            "width": bool(self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)),
            "height": bool(self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)),
        }
        for name, ok in self.locked.items():
            if not ok:
                logger.warning(
                    "camera driver did not accept %s; rPPG accuracy may suffer", name
                )

    def read(self) -> np.ndarray | None:
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return None
        return self._cv2.cvtColor(frame, self._cv2.COLOR_BGR2RGB)

    def release(self) -> None:
        self._cap.release()


def build_face_adapter(
    camera_index: int,
    fps: float,
    *,
    heart_enabled: bool = True,
    emotion_enabled: bool = False,
    emotion_model_path: Any = None,
) -> FaceCaptureAdapter:
    """A camera-backed adapter. Nothing is opened, loaded or verified until
    connect() is called -- so a registry can name a camera on a machine that has
    neither the extra nor the model."""
    def make_locator():
        from src.app.services.face_roi import FaceLocator   # noqa: PLC0415

        return FaceLocator()

    def make_classifier():
        from src.app.services.face_emotion import EmotionClassifier  # noqa: PLC0415

        return EmotionClassifier(emotion_model_path)

    return FaceCaptureAdapter(
        lambda: OpenCvFrameSource(camera_index=camera_index, fps=fps),
        make_locator,
        fps=fps,
        heart_enabled=heart_enabled,
        emotion_enabled=emotion_enabled,
        emotion_classifier_factory=make_classifier if emotion_enabled else None,
    )
