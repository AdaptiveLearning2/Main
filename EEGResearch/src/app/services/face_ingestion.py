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
        self._make_source = frame_source_factory
        self._make_locator = locator_factory
        self.fps = fps

        self._source: FrameSource | None = None
        self._locator: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

        self._queue: queue.Queue[FaceSample] = queue.Queue(maxsize=queue_max)
        self._buffer: deque[tuple[float, float, float]] = deque(
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
            return

        with self._lock:
            self._counters.frames_read += 1

        gray = frame.mean(axis=2).astype(np.uint8)
        box = self._locator.locate(gray)
        if box is None:
            with self._lock:
                self._counters.consecutive_missing += 1
            return

        sample = mean_rgb(frame, box)
        # `frame` goes out of scope here and is never referenced again. Every
        # path below deals only in the three numbers.
        with self._lock:
            self._counters.faces_found += 1
            if not sample.ok:
                self._counters.consecutive_missing += 1
                return
            self._counters.consecutive_missing = 0
            self._buffer.append(sample.rgb)

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
        with self._lock:
            data = list(self._buffer)
        return np.array(data, dtype=float) if data else np.empty((0, 3))

    def rgb_window(self, seconds: float) -> np.ndarray:
        """The most recent `seconds` of colour, as (n, 3), for POS.

        Returns a copy: the buffer is mutated by the capture thread, and handing
        out a view would let a consumer read a half-written window.
        """
        want = int(seconds * self.fps)
        with self._lock:
            data = list(self._buffer)[-want:]
        return np.array(data, dtype=float) if data else np.empty((0, 3))

    def has_full_window(self) -> bool:
        """Whether enough colour history exists for POS to produce anything."""
        with self._lock:
            return len(self._buffer) >= int(WINDOW_SECONDS * self.fps)

    # ── reporting ────────────────────────────────────────────────────────────

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
                "face_degraded_reason": "no usable face" if degraded else None,
                "last_error": c.last_error,
            }
