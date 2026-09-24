"""Headless camera capture: frames in, colour samples out, nothing stored.

Duck-types the EEG adapters (`stream_manager` probes with `hasattr`). Rules:
the capture thread never blocks on I/O (it would skew sample intervals); nothing
accumulates (bounded deque and queue); no image is retained -- frames are reduced
to three numbers and dropped in the same iteration.
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

# Bounded so a stalled consumer drops old samples; two minutes at 30 fps.
QUEUE_MAX = 3600

# Seconds to wait after an empty read, so a vanished camera doesn't spin a core.
ERROR_BACKOFF_SECONDS = 0.1

# Memory backstop on the buffer, not an expected rate; the real bound is buffer_seconds.
MAX_BURST_FPS = 240.0

# Seconds discarded after open while auto-exposure converges (it can't be disabled on
# Windows); reported as `warmup_remaining_s`.
WARMUP_SECONDS = 8.0

# perf_counter, not monotonic: monotonic is 15.6 ms on Windows and quantises frame intervals.
now_seconds = time.perf_counter

# Colour history for POS; the downstream rate wants 25-30 s.
BUFFER_SECONDS = 40.0

# Consecutive frames without a usable face before the adapter reports degraded.
MISSING_FACE_TOLERANCE = 30

# ITU-R BT.601 luma, which Haar and FER+ were trained on.
LUMA_WEIGHTS = np.array([0.299, 0.587, 0.114], dtype=np.float32)

# Emotion cadence (s); expression changes over seconds.
EMOTION_INTERVAL_S = 0.25

# Landmarker cadence (s); independent of EMOTION_INTERVAL_S on purpose.
GAZE_INTERVAL_S = 0.2


class FrameSource(Protocol):
    """Anything that yields frames; injected so the adapter is testable without OpenCV."""

    def read(self) -> np.ndarray | None:
        """Next frame as (h, w, 3) RGB, or None when unavailable."""

    def release(self) -> None:
        ...


@dataclass
class FaceSample:
    """One frame's contribution. Three numbers and a quality figure."""
    capture_ts: float
    rgb: tuple[float, float, float]
    usable_fraction: float


@dataclass
class _Counters:
    frames_read: int = 0
    faces_found: int = 0
    samples_emitted: int = 0
    dropped_full_queue: int = 0
    buffer_capped: int = 0
    warmup_frames_discarded: int = 0
    warmup_done: bool = False
    consecutive_missing: int = 0
    # "camera" (no frame), "no_face", or "quality" (too little usable skin).
    missing_reason: str | None = None
    last_error: str | None = None


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
        gaze_enabled: bool = False,
        landmarker_factory: Callable[[], Any] | None = None,
        gaze_interval_s: float = GAZE_INTERVAL_S,
        error_backoff: float = ERROR_BACKOFF_SECONDS,
        warmup_seconds: float = WARMUP_SECONDS,
    ) -> None:
        if buffer_seconds < WINDOW_SECONDS:
            # Could never yield a pulse; refuse rather than report healthy forever.
            raise ValueError(
                f"buffer_seconds={buffer_seconds} is shorter than one POS window "
                f"({WINDOW_SECONDS}s); no pulse could ever be produced"
            )
        if heart_enabled:
            # Failed ECG validation with a confident wrong number; never enabled silently.
            logger.warning(
                "FACE_HEART_ENABLED is on: camera heart rate failed ECG "
                "validation (47.7 bpm reported at confidence 0.74 against 88) "
                "and its confidence gate does not apply to a single-channel "
                "waveform. See tests/fixtures/FACE_RPPG_ECG.md. Readings from "
                "this channel must not be recorded or shown to a user."
            )

        if not heart_enabled and not emotion_enabled and not gaze_enabled:
            # A camera computing nothing would look like a student out of shot.
            raise ValueError(
                "refusing to open a camera with heart, emotion and gaze all disabled"
            )
        if emotion_enabled and emotion_classifier_factory is None:
            raise ValueError("emotion_enabled requires an emotion_classifier_factory")
        if gaze_enabled and landmarker_factory is None:
            raise ValueError("gaze_enabled requires a landmarker_factory")

        self._make_source = frame_source_factory
        self._make_locator = locator_factory
        self.fps = fps
        self.heart_enabled = heart_enabled
        self.emotion_enabled = emotion_enabled
        # Built at connect(), since it loads a 35 MB model.
        self._make_emotion = emotion_classifier_factory
        self._emotion: Any = None
        self._emotion_interval = emotion_interval_s
        self._error_backoff = error_backoff
        self._warmup_seconds = warmup_seconds
        self._warmup_started_at: float | None = None
        self._last_emotion_at = 0.0
        self._latest_emotion: Any = None

        self.gaze_enabled = gaze_enabled
        self._make_landmarker = landmarker_factory
        self._landmarker: Any = None
        self._gaze_interval = gaze_interval_s
        self._last_gaze_at = 0.0
        self._latest_gaze: Any = None
        # Separate from gaze: the two refuse independently.
        self._latest_pose: Any = None
        # For `_forget_readings`; None until first seen.
        self._last_face_at: float | None = None
        self._last_frame_at: float | None = None

        self._source: FrameSource | None = None
        self._locator: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._queue: queue.Queue[FaceSample] = queue.Queue(maxsize=queue_max)
        # (capture_ts, r, g, b, usable_fraction): stamped because configured fps is a
        # request, not a measurement. Bounded by time in `_trim_buffer`; maxlen is a backstop.
        self._buffer_seconds = buffer_seconds
        self._buffer: deque[tuple[float, float, float, float, float]] = deque(
            maxlen=max(1, int(buffer_seconds * MAX_BURST_FPS))
        )
        self._lock = threading.Lock()
        self._counters = _Counters()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the camera and start capturing.

        Raises if the camera can't be opened (a config or permission problem), unlike Muse.
        """
        if self._thread is not None:
            return
        self._source = self._make_source()
        self._locator = self._make_locator()
        if self.emotion_enabled and self._emotion is None:
            self._emotion = self._make_emotion()
        if self.gaze_enabled and self._landmarker is None:
            # Tolerated, unlike the classifier: a gaze failure must not take heart and
            # emotion down; the channel reports a named refusal instead.
            try:
                self._landmarker = self._make_landmarker()
            except Exception as exc:                  # noqa: BLE001
                logger.exception("gaze is enabled but the landmarker could not "
                                 "be built, so this session records no gaze")
                with self._lock:
                    self._counters.last_error = f"{type(exc).__name__}: {exc}"
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._capture_loop, name="face-capture", daemon=True
        )
        self._thread.start()

    def disconnect(self) -> None:
        """Stop capturing and release the camera.

        Joined: a daemon thread logging during interpreter shutdown is a fatal abort.
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
        self._last_face_at = self._last_frame_at = None
        # Keep the landmarker (slow to build); clear its readings.
        self._latest_gaze = None
        self._latest_pose = None
        self._last_gaze_at = 0.0
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        with self._lock:
            self._buffer.clear()

    # ── capture ──────────────────────────────────────────────────────────────

    def _capture_loop(self) -> None:
        """Read frames as fast as the camera hands them over, and no faster.

        No pacing: `read()` blocks on the sensor, so the camera is the clock; a sleep
        on top beats against it. Downstream resampling handles uneven intervals.
        """
        # Discard the exposure ramp here, not in connect(), so the caller isn't blocked.
        warmup_until = now_seconds() + self._warmup_seconds
        with self._lock:
            self._warmup_started_at = now_seconds()
        while not self._stop.is_set() and now_seconds() < warmup_until:
            try:
                if self._source is not None:
                    self._source.read()
            except Exception:                             # noqa: BLE001
                # Let the normal loop handle and report it.
                break
            with self._lock:
                self._counters.warmup_frames_discarded += 1
        with self._lock:
            self._counters.warmup_done = True

        while not self._stop.is_set():
            try:
                got_frame = self._capture_once()
            except Exception as exc:                      # noqa: BLE001
                with self._lock:
                    self._counters.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("face capture iteration failed")
                # Wait on the stop event rather than spin, so disconnect() stays prompt.
                self._stop.wait(self._error_backoff)
                continue

            if not got_frame:
                self._stop.wait(self._error_backoff)

    def _capture_once(self) -> bool:
        """One frame. Returns whether the camera handed one over at all.

        About the source, not the face: a faceless frame still means the camera is alive.
        """
        frame = self._source.read() if self._source else None
        if frame is None:
            with self._lock:
                self._counters.consecutive_missing += 1
                self._counters.missing_reason = "camera"
            self._forget_readings(now_seconds(), camera_gone=True)
            return False

        with self._lock:
            self._counters.frames_read += 1

        now = now_seconds()
        self._last_frame_at = now
        # Before the Haar box: the landmarker detects on its own, so a Haar miss must not skip it.
        if (self.gaze_enabled
                and now - self._last_gaze_at >= self._gaze_interval):
            self._last_gaze_at = now
            self._sample_gaze(frame)

        gray = frame.astype(np.float32) @ LUMA_WEIGHTS
        box = self._locator.locate(gray)
        if box is None:
            with self._lock:
                self._counters.consecutive_missing += 1
                self._counters.missing_reason = "no_face"
            self._forget_readings(now, camera_gone=False)
            return True
        self._last_face_at = now

        if (self.emotion_enabled
                and now - self._last_emotion_at >= self._emotion_interval):
            self._last_emotion_at = now
            from src.app.services.face_emotion import to_gray64  # noqa: PLC0415

            try:
                crop = to_gray64(frame, box)
                result = self._emotion.classify(crop)
            except Exception as exc:                      # noqa: BLE001
                # A crop failure must not take the colour sample down with it.
                logger.exception("emotion crop failed")
                with self._lock:
                    self._counters.last_error = f"{type(exc).__name__}: {exc}"
                    # Else the stale reading is re-stored at 4 Hz as trusted.
                    self._latest_emotion = None
            else:
                with self._lock:
                    self._latest_emotion = result

        if not self.heart_enabled:
            # No colour sample; the buffer stays empty.
            with self._lock:
                self._counters.faces_found += 1
                self._counters.consecutive_missing = 0
            return True

        sample = mean_rgb(frame, box)
        # `frame` is never referenced again below -- only the three numbers.
        with self._lock:
            self._counters.faces_found += 1
            if not sample.ok:
                self._counters.consecutive_missing += 1
                self._counters.missing_reason = "quality"
                return
            self._counters.consecutive_missing = 0
            self._counters.missing_reason = None
            self._buffer.append((now, *sample.rgb, sample.usable_fraction))
            self._trim_buffer()

        item = FaceSample(now_seconds(), sample.rgb, sample.usable_fraction)
        try:
            self._queue.put_nowait(item)
        except queue.Full:
            # Drop rather than block capture.
            with self._lock:
                self._counters.dropped_full_queue += 1
            return True
        with self._lock:
            self._counters.samples_emitted += 1
        return True

    def _forget_readings(self, now: float, *, camera_gone: bool) -> None:
        """Drop readings that no longer describe the current frame.

        Emotion clears once the face is gone for an emotion interval; gaze/pose get a
        named `no_frame` refusal (not None, which means warming up) once frames stop
        for a gaze interval. Time, not frame count, so one flicker clears nothing.
        """
        def gone_for(since, interval):
            return since is None or now - since >= interval

        if gone_for(self._last_face_at, self._emotion_interval):
            with self._lock:
                self._latest_emotion = None
        if (not camera_gone or not self.gaze_enabled
                or not gone_for(self._last_frame_at, self._gaze_interval)):
            return
        from src.app.services.face_geometry import Gaze, HeadPose  # noqa: PLC0415

        with self._lock:
            self._latest_gaze = Gaze(None, None, 0, "no_frame")
            self._latest_pose = HeadPose(None, None, None, 0, "no_frame")

    # ── consumption ──────────────────────────────────────────────────────────

    def drain_samples(self, max_batch: int) -> list[FaceSample]:
        """Every queued sample, up to max_batch. Never blocks.

        Unlike Muse: an empty camera queue is normal, and raising would restart the stream.
        """
        out: list[FaceSample] = []
        while len(out) < max_batch:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    def _trim_buffer(self) -> None:
        """Drop samples older than `buffer_seconds`. Caller holds the lock.

        `buffer_capped` counts ticks where the count backstop, not time, did the bounding.
        """
        cutoff = self._buffer[-1][0] - self._buffer_seconds
        while len(self._buffer) > 1 and self._buffer[0][0] < cutoff:
            self._buffer.popleft()
        if len(self._buffer) == self._buffer.maxlen:
            self._counters.buffer_capped += 1

    def _sample_gaze(self, frame: np.ndarray) -> None:
        """One gaze reading from a full frame. Never raises.

        Runs before the colour sample, so it must not raise. Refusals and failures are
        stored as named refusals; None would read as `no_reading` (warming up).
        """
        from src.app.services.face_geometry import (  # noqa: PLC0415
            Gaze, HeadPose, gaze, head_pose,
        )

        if self._landmarker is None:
            # connect() couldn't build one.
            with self._lock:
                self._latest_gaze = Gaze(None, None, 0, "landmarker_unavailable")
                self._latest_pose = HeadPose(None, None, None, 0,
                                             "landmarker_unavailable")
            return

        reading = pose = None
        try:
            height, width = frame.shape[0], frame.shape[1]
            named = self._landmarker.locate(frame, width, height)
            reading = gaze(named)
            pose = head_pose(named)
        except Exception as exc:                          # noqa: BLE001
            logger.exception("landmark sampling failed")
            # Keep whichever derivation already succeeded.
            if not isinstance(reading, Gaze):
                reading = Gaze(None, None, 0, "landmarker_failed")
            if not isinstance(pose, HeadPose):
                pose = HeadPose(None, None, None, 0, "landmarker_failed")
            with self._lock:
                self._counters.last_error = f"{type(exc).__name__}: {exc}"
                self._latest_gaze = reading
                self._latest_pose = pose
            return
        with self._lock:
            self._latest_gaze = reading
            self._latest_pose = pose

    def rgb_buffer(self) -> np.ndarray:
        """Everything currently buffered, as (n, 3)."""
        return self.rgb_window(float("inf"))[0]

    def window_quality(self, seconds: float) -> float | None:
        """Mean usable-pixel fraction over the same window as the colour."""
        return self.rgb_window(seconds)[2]

    def rgb_window(
        self, seconds: float
    ) -> tuple[np.ndarray, float | None, float | None, np.ndarray]:
        """The last `seconds` of colour: (rgb, measured_fps, mean_usable_fraction, timestamps).

        None fps means no window -- never fall back to nominal. fps is the median
        interval (stalls drag a mean down). Returns copies.
        """
        # Sliced by the clock, not a count against the nominal rate.
        with self._lock:
            if seconds == float("inf") or not self._buffer:
                data = list(self._buffer)
            else:
                cutoff = self._buffer[-1][0] - seconds
                data = []
                for row in reversed(self._buffer):
                    if row[0] < cutoff:
                        break
                    data.append(row)
                data.reverse()

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
        """Whether enough colour history (seconds held, not frames) exists for POS."""
        with self._lock:
            if len(self._buffer) < 2:
                return False
            return (self._buffer[-1][0] - self._buffer[0][0]) >= WINDOW_SECONDS

    def measured_fps(self) -> float | None:
        """The rate the buffer was actually filled at, or None if unmeasurable."""
        return self.rgb_window(float("inf"))[1]

    # ── reporting ────────────────────────────────────────────────────────────

    def latest_emotion(self) -> Any:
        """The most recent classification, or None if emotion is off or nothing
        has been classified yet."""
        with self._lock:
            return self._latest_emotion

    def latest_pose(self) -> Any:
        """The most recent head pose, or None if gaze is off or nothing measured yet.

        A `HeadPose` with `yaw` None is a refusal, returned as such.
        """
        with self._lock:
            return self._latest_pose

    def latest_gaze(self) -> Any:
        """The most recent gaze reading, or None if gaze is off or nothing measured yet.

        A `Gaze` with `x` None is a refusal; `rejected_by` says why.
        """
        with self._lock:
            return self._latest_gaze

    def get_ingestion_meta(self) -> dict[str, Any]:
        """Camera state for the API.

        `face_quality` is the luminance-mask pixel fraction, deliberately not "confidence".
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
                # Non-zero: the size cap, not time, bounds the buffer (heart stuck warming_up).
                "buffer_capped": c.buffer_capped,
                # Measured span, not count over nominal fps.
                "buffered_seconds": (
                    self._buffer[-1][0] - self._buffer[0][0]
                    if len(self._buffer) > 1 else 0.0
                ),
                # So warm-up doesn't read as a camera that can't see a face.
                "warmup_remaining_s": round(max(0.0, (
                    (self._warmup_started_at + self._warmup_seconds) - now_seconds()
                    if self._warmup_started_at is not None and not c.warmup_done
                    else 0.0
                )), 2),
                "warmup_frames_discarded": c.warmup_frames_discarded,
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
                "gaze_enabled": self.gaze_enabled,
                **(self._emotion.get_meta() if self._emotion is not None else {}),
            }


class OpenCvFrameSource:
    """A webcam, behind the FrameSource protocol.

    OpenCV is imported lazily so this module works without camera dependencies.
    Converts BGR to RGB here, once: reversed channels would silently halve the pulse.
    """

    def __init__(self, camera_index: int = 0, width: int = 640, height: int = 480,
                 fps: float = 30.0) -> None:
        import cv2                                   # noqa: PLC0415 -- lazy by design

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(camera_index)
        if not self._cap.isOpened():
            raise RuntimeError(f"could not open camera index {camera_index}")

        # Request fixed exposure and white balance (auto-exposure writes into the pulse
        # band). Each entry reports what the driver reads back, not what set() returned.
        self.locked = {
            # One-frame buffer, so read-time stamps sit closer to exposure time. Requested only.
            "buffer_size": self._applied(cv2.CAP_PROP_BUFFERSIZE, 1),
            "auto_exposure": self._applied(cv2.CAP_PROP_AUTO_EXPOSURE, 1),
            "auto_wb": self._applied(cv2.CAP_PROP_AUTO_WB, 0),
            "fps": bool(self._cap.set(cv2.CAP_PROP_FPS, fps)),
            "width": bool(self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)),
            "height": bool(self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)),
        }
        for name, ok in self.locked.items():
            if not ok:
                logger.warning(
                    "camera driver did not accept %s; rPPG accuracy may suffer", name
                )

    def _applied(self, prop: int, wanted: float) -> bool:
        """Whether the driver actually took a property, by reading it back.

        `set()` returning True only means the call was accepted. Float tolerance.
        """
        self._cap.set(prop, wanted)
        return abs(self._cap.get(prop) - wanted) < 0.01

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
    gaze_enabled: bool = False,
    landmark_model_path: Any = None,
) -> FaceCaptureAdapter:
    """A camera-backed adapter. Nothing is opened, loaded or verified until connect()."""
    def make_locator():
        from src.app.services.face_roi import FaceLocator   # noqa: PLC0415

        return FaceLocator()

    def make_classifier():
        from src.app.services.face_emotion import EmotionClassifier  # noqa: PLC0415

        return EmotionClassifier(emotion_model_path)

    def make_landmarker():
        from src.app.services.face_landmarks import FaceMeshLandmarker  # noqa: PLC0415

        return FaceMeshLandmarker(model_path=landmark_model_path)

    return FaceCaptureAdapter(
        lambda: OpenCvFrameSource(camera_index=camera_index, fps=fps),
        make_locator,
        fps=fps,
        heart_enabled=heart_enabled,
        emotion_enabled=emotion_enabled,
        emotion_classifier_factory=make_classifier if emotion_enabled else None,
        gaze_enabled=gaze_enabled,
        landmarker_factory=make_landmarker if gaze_enabled else None,
    )
