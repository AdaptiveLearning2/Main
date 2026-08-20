"""Headless camera capture: frames in, colour samples out, nothing stored.

Duck-types the EEG adapters' shape (`connect`, `disconnect`, `drain_samples`,
`get_ingestion_meta`) since `stream_manager` probes with `hasattr` and there's
no base class.

Three rules shape the design:

- **The capture thread never blocks on I/O.** It only ever appends to a
  bounded queue. A blocking call here would stall frame grabbing, which
  corrupts the time series since POS and the rate derivation assume roughly
  uniform sample intervals.
- **Nothing accumulates.** The RGB buffer is a fixed-length deque and the
  queue is bounded, so a long session costs the same per frame as the first
  minute.
- **No image is retained.** Frames are read, reduced to three numbers, and
  dropped in the same iteration. Nothing here can leak a frame to disk or a
  payload.
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

# Bounded so a stalled consumer costs a fixed amount of memory and old samples
# get dropped instead of the queue growing forever. Two minutes at 30 fps is
# far more than any consumer should be behind.
QUEUE_MAX = 3600

# How long the capture thread waits after a read that produced nothing.
# Normally the loop is paced by `read()` blocking on the sensor. If the sensor
# is gone (unplugged, permission revoked), read() returns instantly and
# nothing limits the rate, so without this the thread spins a core. Short
# enough that a camera coming back is picked up almost immediately.
ERROR_BACKOFF_SECONDS = 0.1

# Ceiling on the buffer's fixed size, not an expected rate. The real bound is
# `buffer_seconds` of elapsed time; this only stops a runaway source from
# growing the buffer without limit. Set well above any real burst (measured
# ~160 Hz instantaneous between paired frames).
MAX_BURST_FPS = 240.0

# Seconds of frames discarded after the camera opens, before buffering starts.
#
# Auto-exposure converges over the first few seconds, and the ramp is huge
# relative to the pulse signal: measured mean green climbing 17% over ~5s
# against a pulse under 1%. Same recording scored confidence 0.05 with the
# ramp in the window vs 0.81 after it.
#
# The ramp can't be prevented in software: `CAP_PROP_AUTO_EXPOSURE` reads back
# -1.0 on this Windows backend no matter what it's set to. Discarding frames
# is the only available fix.
#
# 8s for margin. This delays the first reading, so `warmup_remaining_s` is
# reported in the meta so warm-up isn't mistaken for a camera that can't see.
WARMUP_SECONDS = 8.0

# The clock samples are stamped with: `perf_counter`, not `monotonic`.
# On Windows, `time.monotonic()` has 15.625ms resolution, so it quantises
# frame intervals instead of measuring them (e.g. 31ms and 47ms just become 2
# and 3 ticks), which can look like jitter between the loop and the camera
# when it's really just clock rounding. `perf_counter` resolves 100ns and
# costs the same. It's unsuitable for wall-clock time, but nothing here needs
# that (the capture's absolute start is recorded separately).
now_seconds = time.perf_counter

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

# How often the emotion classifier runs. Well below the frame rate on
# purpose: expression changes over seconds, so classifying every frame would
# burn far more CPU for the same answer, on a laptop already running a
# browser, a maths lesson and the EEG stack.
EMOTION_INTERVAL_S = 0.25

# How often the face-mesh landmarker runs. Slower than emotion because it's a
# second detector doing its own face detection on the full frame (it can't
# reuse the Haar box, which has no landmarks). 5 Hz is fast enough to catch a
# glance away across several samples while staying a fraction of one core.
#
# Not derived from EMOTION_INTERVAL_S: expression and gaze change on
# different timescales, so tying the two together would silently retune one
# whenever someone tuned the other.
GAZE_INTERVAL_S = 0.2


class FrameSource(Protocol):
    """Anything that yields frames. A webcam in production, a list in tests.

    Injected so the adapter's threading, buffering, quality gating and
    teardown are testable without OpenCV, which CI doesn't have.
    """

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
    # Why the last frame produced nothing: "camera" (no frame at all),
    # "no_face" (nothing detected) or "quality" (face found, too little usable
    # skin). Kept distinct since a disconnected webcam, a student leaving, and
    # bad lighting are different problems.
    missing_reason: str | None = None
    last_error: str | None = None
    # Counters, not lists of per-frame records, to avoid unbounded growth.


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
        # buffer_seconds and queue_max are injectable so tests can hit the
        # bounded-growth and queue-full behaviour quickly, instead of needing
        # to fill a real 40s buffer.
        if buffer_seconds < WINDOW_SECONDS:
            # A buffer shorter than one POS window can never yield a pulse.
            # Refuse at construction rather than let it silently connect,
            # report healthy, and never produce a reading.
            raise ValueError(
                f"buffer_seconds={buffer_seconds} is shorter than one POS window "
                f"({WINDOW_SECONDS}s); no pulse could ever be produced"
            )
        if heart_enabled:
            # Not refused outright (a future experiment might need it), but
            # never enabled silently. Validated against a simultaneous ECG:
            # 47.7 bpm at confidence 0.74 against a true 88, with the face
            # found in every frame. The pulse just isn't in the recording --
            # autocorrelation peak 0.02 vs 0.3-0.7 for a real pulse -- and the
            # confidence gate can't catch it, since its terms were built for
            # four contact channels and read as "clear pulse" on a single
            # noisy waveform. So this ships a confident wrong number, not a
            # noisy one. See tests/fixtures/FACE_RPPG_ECG.md.
            logger.warning(
                "FACE_HEART_ENABLED is on: camera heart rate failed ECG "
                "validation (47.7 bpm reported at confidence 0.74 against 88) "
                "and its confidence gate does not apply to a single-channel "
                "waveform. See tests/fixtures/FACE_RPPG_ECG.md. Readings from "
                "this channel must not be recorded or shown to a user."
            )

        if not heart_enabled and not emotion_enabled and not gaze_enabled:
            # Opening a camera to compute nothing would fail silently: frames
            # read, nothing produced, indistinguishable from a student out of
            # shot. Refuse at construction instead.
            #
            # gaze_enabled counts too -- a gaze-only camera (emotion off, no
            # FER+ model needed) is a valid deployment on its own.
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
        # A factory, like the frame source and locator: constructing the
        # classifier loads and verifies a 35 MB model, so building it here
        # would stop a device registry from naming a camera on a machine
        # without the model. Built at connect() instead, where a failure
        # names the real problem.
        self._make_emotion = emotion_classifier_factory
        self._emotion: Any = None
        self._emotion_interval = emotion_interval_s
        self._error_backoff = error_backoff
        self._warmup_seconds = warmup_seconds
        self._warmup_started_at: float | None = None
        self._last_emotion_at = 0.0
        self._latest_emotion: Any = None

        # Same factory treatment as the classifier: the landmarker loads a
        # model file that may not exist on the machine at all.
        self.gaze_enabled = gaze_enabled
        self._make_landmarker = landmarker_factory
        self._landmarker: Any = None
        self._gaze_interval = gaze_interval_s
        self._last_gaze_at = 0.0
        self._latest_gaze: Any = None
        # Head pose comes from the same landmark call as gaze but is stored
        # separately since the two refuse independently: near profile, pose
        # refuses while the eyes stay readable; a closed eye refuses gaze
        # while pose is fine.
        self._latest_pose: Any = None

        self._source: FrameSource | None = None
        self._locator: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._queue: queue.Queue[FaceSample] = queue.Queue(maxsize=queue_max)
        # (capture_ts, r, g, b, usable_fraction). The timestamp is kept
        # because configured fps is a request, not a measurement -- a webcam
        # asked for 30 can deliver 22 under load, and scaling by the nominal
        # rate would produce a confidently wrong bpm.
        # usable_fraction rides along so quality gating covers a whole
        # window rather than one tick, since a tick that drained nothing
        # would otherwise leave quality unknown even with a full buffer.
        # Bounded by *time* in `_trim_buffer`; the maxlen below is only a
        # memory backstop, not the real bound (deriving it from nominal fps
        # would cap the buffer under the window length once actual frame
        # rate ran ahead of nominal, stalling the heart channel forever in
        # `warming_up`). Kept generous so a runaway source can't grow the
        # buffer without limit.
        self._buffer_seconds = buffer_seconds
        self._buffer: deque[tuple[float, float, float, float, float]] = deque(
            maxlen=max(1, int(buffer_seconds * MAX_BURST_FPS))
        )
        self._lock = threading.Lock()
        self._counters = _Counters()

    # ── lifecycle ────────────────────────────────────────────────────────────

    def connect(self) -> None:
        """Open the camera and start capturing.

        Raises if the camera can't be opened, unlike the Muse adapter (which
        retries, since a headband can legitimately be turned on later) -- a
        camera that won't open is a config or permission problem, and
        retrying would hide it.
        """
        if self._thread is not None:
            return
        self._source = self._make_source()
        self._locator = self._make_locator()
        if self.emotion_enabled and self._emotion is None:
            self._emotion = self._make_emotion()
        if self.gaze_enabled and self._landmarker is None:
            # Tolerated, unlike the classifier above -- deliberately.
            # Building the landmarker can fail because the model file isn't
            # provisioned (`start.ps1 -Gaze` fetches it; a hand-edited `.env`
            # doesn't), MediaPipe is missing, its API moved, or the bundle is
            # corrupt. None of that is a reason to take heart and emotion
            # down with it, since gaze is off by default and nothing renders
            # it yet. So the channel stays enabled and reports a named
            # refusal rather than silently going off. `logger.exception`
            # because CI has no camera dependencies to test this path, so a
            # traceback here is the only diagnostic available.
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

        Joined rather than left to daemon teardown -- a daemon thread that
        logs during interpreter shutdown while the stdout lock is held is a
        fatal abort that reads as unrelated flake.
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
        # The landmarker itself is kept (it holds a loaded model, and
        # MediaPipe takes seconds to build one) but the reading is cleared,
        # since a gaze from before release isn't a gaze now.
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

        No pacing here on purpose. `read()` already blocks until the sensor
        has a frame, so the camera is the clock -- adding a sleep-based clock
        on top just creates a beat between the two. Measured: pacing to 30fps
        against a camera running at ~32 produced bimodal intervals (78% at
        31ms, 21% at 47ms), discarding a fifth of the signal to enforce a
        rate the camera was already exceeding. Downstream resampling handles
        the uneven result fine.
        """
        # Discard the exposure ramp before anything reaches the buffer. Done
        # in the loop, not connect(), so the caller isn't blocked for 8s, and
        # before `_capture_once` so a discarded frame is never counted,
        # stamped or classified.
        warmup_until = now_seconds() + self._warmup_seconds
        with self._lock:
            self._warmup_started_at = now_seconds()
        while not self._stop.is_set() and now_seconds() < warmup_until:
            try:
                if self._source is not None:
                    self._source.read()
            except Exception:                             # noqa: BLE001
                # A source failing during warm-up isn't warm-up's problem --
                # let the normal loop handle and report it.
                break
            with self._lock:
                self._counters.warmup_frames_discarded += 1
        with self._lock:
            self._counters.warmup_done = True

        while not self._stop.is_set():
            try:
                got_frame = self._capture_once()
            except Exception as exc:                      # noqa: BLE001
                # Caught at the thread boundary, not via a process-wide
                # excepthook, so other threads' errors aren't silenced too.
                with self._lock:
                    self._counters.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("face capture iteration failed")
                # Wait rather than spin: a source failing immediately (an
                # unplugged camera returning None with no blocking read)
                # would otherwise spin this thread at full speed. Waiting on
                # the stop event instead of sleeping keeps disconnect() prompt.
                self._stop.wait(self._error_backoff)
                continue

            if not got_frame:
                # Same reasoning: a source yielding nothing isn't blocking on
                # hardware, so nothing else limits the rate.
                self._stop.wait(self._error_backoff)

    def _capture_once(self) -> bool:
        """One frame. Returns whether the camera handed one over at all.

        This is about the *source*, not the face: a frame with no face still
        means the camera is alive and pacing the loop.
        """
        frame = self._source.read() if self._source else None
        if frame is None:
            with self._lock:
                self._counters.consecutive_missing += 1
                self._counters.missing_reason = "camera"
            return False

        with self._lock:
            self._counters.frames_read += 1

        now = now_seconds()
        # Sampled before the Haar box on purpose: the landmarker runs its own
        # detection on the full frame, so a Haar miss says nothing about
        # whether a mesh is available. Returning early on a Haar miss would
        # make gaze silently depend on a detector it doesn't use.
        if (self.gaze_enabled
                and now - self._last_gaze_at >= self._gaze_interval):
            self._last_gaze_at = now
            self._sample_gaze(frame)

        # Luma-weighted, not a flat mean: Haar cascades are trained on
        # ITU-R BT.601 luma, and a flat RGB average is a noticeably different
        # (redder) image for skin tones.
        gray = frame.astype(np.float32) @ LUMA_WEIGHTS
        box = self._locator.locate(gray)
        if box is None:
            with self._lock:
                self._counters.consecutive_missing += 1
                self._counters.missing_reason = "no_face"
            return True

        if (self.emotion_enabled
                and now - self._last_emotion_at >= self._emotion_interval):
            self._last_emotion_at = now
            from src.app.services.face_emotion import to_gray64  # noqa: PLC0415

            try:
                crop = to_gray64(frame, box)
                result = self._emotion.classify(crop)
            except Exception as exc:                      # noqa: BLE001
                # Guarded separately from classify() (which has its own
                # handler) so a crop failure can't abort the iteration and
                # take the colour sample down with it -- the two channels
                # must stay independent.
                logger.exception("emotion crop failed")
                with self._lock:
                    self._counters.last_error = f"{type(exc).__name__}: {exc}"
            else:
                with self._lock:
                    self._latest_emotion = result

        if not self.heart_enabled:
            # Emotion-only: no colour sample to take, and the buffer stays
            # empty so nothing downstream mistakes an idle heart channel for
            # a stalled one.
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
            # Drop rather than block -- blocking here would stall capture,
            # and a consumer 2 minutes behind has bigger problems than one
            # lost frame.
            with self._lock:
                self._counters.dropped_full_queue += 1
            return True
        with self._lock:
            self._counters.samples_emitted += 1
        return True

    # ── consumption ──────────────────────────────────────────────────────────

    def drain_samples(self, max_batch: int) -> list[FaceSample]:
        """Every queued sample, up to max_batch. Never blocks.

        Unlike the Muse adapter, which blocks briefly and raises on timeout:
        a camera with nothing queued (session start, student looked away) is
        normal, not an error, and raising would turn an ordinary gap into a
        stream restart.
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

        If the count cap ends up doing the bounding instead of the time cap,
        that's recorded. Shouldn't happen (the cap is provisioned for
        240fps), but if it does the buffer holds less time than asked and
        the heart channel silently stalls in `warming_up` -- the counter
        makes that diagnosable instead of a mystery.
        """
        cutoff = self._buffer[-1][0] - self._buffer_seconds
        while len(self._buffer) > 1 and self._buffer[0][0] < cutoff:
            self._buffer.popleft()
        if len(self._buffer) == self._buffer.maxlen:
            self._counters.buffer_capped += 1

    def _sample_gaze(self, frame: np.ndarray) -> None:
        """One gaze reading from a full frame. Never raises.

        Wrapped like the emotion crop, for the same reason: a failure in one
        channel must not stop the others. This runs *before* the colour
        sample, so an escaping exception would cost the heart channel every
        frame, not just blank gaze.

        A refusal is stored, not discarded -- `Gaze.rejected_by` is what lets
        the record layer tell a closed eye apart from a channel that hasn't
        produced anything yet.

        A failure is stored too, as its own refusal. Leaving `_latest_gaze`
        at None would report as `no_reading` (the warming-up state), so a
        landmarker that raises on every frame would spend the whole session
        claiming to be warming up instead of reporting broken.
        """
        from src.app.services.face_geometry import (  # noqa: PLC0415
            Gaze, HeadPose, gaze, head_pose,
        )

        if self._landmarker is None:
            # connect() couldn't build one. Named refusal instead of silence,
            # or the channel would report `no_reading` (warming-up) for the
            # whole session, which a missing model is not.
            with self._lock:
                self._latest_gaze = Gaze(None, None, 0, "landmarker_unavailable")
                self._latest_pose = HeadPose(None, None, None, 0,
                                             "landmarker_unavailable")
            return

        reading = pose = None
        try:
            height, width = frame.shape[0], frame.shape[1]
            named = self._landmarker.locate(frame, width, height)
            # One detector call, two derivations (both cheap pure numpy over
            # the named points).
            reading = gaze(named)
            pose = head_pose(named)
        except Exception as exc:                          # noqa: BLE001
            logger.exception("landmark sampling failed")
            # Only overwrite what didn't survive -- gaze() and head_pose()
            # normally return named refusals rather than raising, so if one
            # already succeeded, don't discard it for the other's failure.
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
        """The most recent `seconds` of colour, the rate it was *actually*
        sampled at, its mean quality, and the timestamp of every sample.

        Returns (rgb, measured_fps, mean_usable_fraction, timestamps).
        `measured_fps` is None when there are too few samples to measure one;
        callers must treat that as no window rather than falling back to a
        nominal rate. Quality comes from the same window as the colour, so
        the gate applies to what's actually being scored.

        `measured_fps` uses the **median** interval, not samples-over-span.
        Measured on a real webcam asked for 30fps: intervals were bimodal
        (78% at 31ms, 21% at 47ms, occasional stalls past 100ms). Span-based
        gave 28.6 Hz; median gave 32.3 Hz, the camera's true rate -- a mean
        gets dragged down by stalls.

        (Opposite call to the headband's optical packets, where the median
        was wrong because ~9% of timestamps were exact duplicates from SDK
        batching. Here every stamp is a distinct `perf_counter()` read at
        capture time, so median is the right statistic.)

        Copies, not views, since the buffer is mutated by the capture thread.
        Uses `islice` over the tail rather than `list(...)[-n:]`, which would
        materialize the whole deque under the lock on every tick.
        """
        # Sliced by the clock, not by a count against the nominal rate --
        # `int(seconds * self.fps)` undercounts whenever the real rate runs
        # ahead of nominal (e.g. 750 samples at a measured 32.26 Hz is 23.25s,
        # not the 25s asked for), which would report a full buffer as only
        # 93% covered.
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
        """Whether enough colour history exists for POS to produce anything.

        Measured in seconds held, not samples counted -- POS needs a window
        of elapsed time, not a frame count.
        """
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
        """The most recent head pose, or None if gaze is off or nothing has
        been measured yet.

        A `HeadPose` whose `yaw` is None is a *refusal* (`implausible_pose`
        past +/-90 degrees, or too few landmarks) and is returned as such,
        like `latest_gaze`'s refusals.
        """
        with self._lock:
            return self._latest_pose

    def latest_gaze(self) -> Any:
        """The most recent gaze reading, or None if gaze is off or nothing has
        been measured yet.

        A `Gaze` whose `x` is None is a *refusal* and is returned as such --
        callers need `rejected_by` to tell a closed eye apart from a channel
        that hasn't produced anything.
        """
        with self._lock:
            return self._latest_gaze

    def get_ingestion_meta(self) -> dict[str, Any]:
        """Camera state for the API.

        `face_quality` is named for what it is -- the fraction of pixels that
        survived the luminance mask -- deliberately not called "confidence",
        to avoid conflating a well-lit face with a trusted heart rate.
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
                # Non-zero means the colour buffer is bounded by its size cap
                # rather than time, so it holds less history than asked for.
                # Surfaced since the symptom is otherwise an unexplained
                # heart channel stuck in warming_up.
                "buffer_capped": c.buffer_capped,
                # Measured span, not count over nominal fps -- the latter
                # would misreport actual buffered time whenever real fps
                # diverges from nominal, which is exactly what's being
                # diagnosed here.
                "buffered_seconds": (
                    self._buffer[-1][0] - self._buffer[0][0]
                    if len(self._buffer) > 1 else 0.0
                ),
                # Warm-up isn't a fault and must not read as one -- otherwise
                # the first 8s of every session look like a camera that can't
                # see a face.
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

    OpenCV is imported here, not at module scope, so `face_ingestion` can be
    imported and tested on a machine with no camera dependencies -- which is
    the state of CI and any deployment that never enables the camera.

    Converts BGR to RGB at this boundary, since OpenCV hands out BGR and
    every layer above assumes RGB. Converting once here means POS's
    projection matrix can never silently get reversed channels, which
    wouldn't error, just quietly halve the pulse.
    """

    def __init__(self, camera_index: int = 0, width: int = 640, height: int = 480,
                 fps: float = 30.0) -> None:
        import cv2                                   # noqa: PLC0415 -- lazy by design

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(camera_index)
        if not self._cap.isOpened():
            raise RuntimeError(f"could not open camera index {camera_index}")

        # Ask for a fixed exposure and white balance. Auto-exposure is the
        # worst thing for rPPG: it reacts on roughly the timescale of a
        # heartbeat, writing a signal into the pulse band that looks like
        # what's being measured.
        #
        # `set()` returning True doesn't mean the property took. Measured on
        # this Windows backend, `CAP_PROP_AUTO_EXPOSURE` returns True for
        # every value and then reads back -1.0 regardless. Each entry below
        # instead reports what the driver *reads back*, so a lock that
        # silently did nothing shows up as one. This is why WARMUP_SECONDS
        # exists -- the exposure ramp can't be prevented here, only waited out.
        self.locked = {
            # One frame of buffer, so read() returns the frame being exposed
            # now, not the oldest one queued.
            #
            # This fixes the time base, not latency. A deeper queue drains
            # several frames back to back, each stamped at *read* time, not
            # *exposure* time -- measured as intervals alternating ~6ms/~41ms
            # for a camera running evenly at ~24 Hz. rPPG only has the timing
            # of the light, so those stamps would be wrong.
            #
            # Requested, not guaranteed: 28% of intervals are still under
            # 15ms against a 40ms median even with this set. It removes the
            # worst outlier for free, but frame stamps are still read-times,
            # not exposure-times. Resampling absorbs the rest.
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

        `set()` returning True only means the call was accepted, not that
        anything changed -- `CAP_PROP_AUTO_EXPOSURE` on this Windows backend
        returns True for every value and reads back -1.0 regardless.

        A driver that doesn't implement a property typically reports -1; one
        that does reports the value back. Compared with a tolerance since
        these are floats round-tripped through a driver.
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
    """A camera-backed adapter. Nothing is opened, loaded or verified until
    connect() is called, so a registry can name a camera on a machine that
    has neither the extra nor the model."""
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
