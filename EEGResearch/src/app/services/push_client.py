"""Posts derived signals from this sidecar to the website backend (`INGEST_MODE=push`).

The student's token arrives from the browser, lives in memory only, is never
logged, and `stop()` clears it. No arithmetic here: `signal_mapping` on the
backend converts for both ingest paths. See docs/signals.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Below the backend's INGEST_MAX_BATCH, not equal to it: an oversized batch is refused whole.
MAX_BATCH = 50

# Samples per channel; drops oldest on overflow.
MAX_QUEUE = 600

# Seconds per flush cycle.
FLUSH_SECONDS = 5.0

# Batches the shutdown flush may try: the whole backlog, capped.
MAX_SHUTDOWN_FLUSHES = MAX_QUEUE // MAX_BATCH

# Seconds; doubles to the ceiling, resets on the first success.
BACKOFF_START = 5.0
BACKOFF_MAX = 120.0

# Seconds; shorter than FLUSH_SECONDS so a hung request cannot stack flushes.
REQUEST_TIMEOUT = 4.0

# Seconds of wall clock for all of `stop()`; an attempt cap alone is not a bound.
SHUTDOWN_BUDGET = 10.0

_CHANNELS = ("cognitive", "heart", "face")


class PushClient:
    """Buffers samples per channel and flushes them to the backend.

    Before `start()` supplies a session and token, `enqueue` is a no-op.
    """

    def __init__(self, backend_url: str) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._session_id: str | None = None
        self._token: str | None = None
        self._queues: dict[str, deque[dict[str, Any]]] = {
            channel: deque(maxlen=MAX_QUEUE) for channel in _CHANNELS
        }
        # Last heart reading enqueued per (device, source): one held headband block becomes one row.
        self._last_heart_ts: dict[tuple[str | None, str | None], str | None] = {}
        self._dropped: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        self._sent: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        # Committed but with an unreadable receipt: neither recorded nor lost.
        self._unaccounted: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        # Rows the backend already had from an earlier attempt.
        self._duplicates: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        # Samples the backend refused one by one as unreadable.
        self._malformed: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        self._task: asyncio.Task | None = None
        # Serialises start/stop: interleaved starts could leave a running loop with no token.
        self._lifecycle = asyncio.Lock()
        self._wake = asyncio.Event()
        # Asks the loop to finish at a tick boundary, so no committed request is aborted.
        self._stopping = asyncio.Event()
        self._backoff = 0.0
        # Monotonic deadline; the wake event can skip the sleep, so this enforces the backoff.
        self._retry_at = 0.0
        self._last_error: str | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def session_id(self) -> str | None:
        """The session being pushed for, or None."""
        return self._session_id

    async def start(self, session_id: str, token: str) -> bool:
        """Begin pushing for one session, with that student's bearer token.

        Same session id: token refresh only, queue untouched. Different id: the
        old queue is discarded unsent. Returns whether the session is new, decided
        under the lifecycle lock so a concurrent start cannot restart the baseline.
        """
        async with self._lifecycle:
            new_session = session_id != self._session_id
            if new_session:
                await self._stop_locked(flush=False)
            self._session_id = session_id
            self._token = token
            self._stopping.clear()
            self._backoff = 0.0
            self._retry_at = 0.0
            self._last_error = None
            if not self.running:
                self._task = asyncio.create_task(self._loop())
            return new_session

    async def stop(self, *, flush: bool = True) -> None:
        """Stop pushing and forget the token; one bounded final flush by default."""
        async with self._lifecycle:
            await self._stop_locked(flush=flush)

    async def _stop_locked(self, *, flush: bool) -> None:
        """The body of `stop()`. Assumes `_lifecycle` is held (it is not reentrant)."""
        deadline = time.monotonic() + SHUTDOWN_BUDGET
        task, self._task = self._task, None
        if task is not None:
            # Asked to finish, not cancelled: a mid-POST cancel leaves the batch's fate unknown.
            self._stopping.set()
            self._wake.set()
            try:
                await asyncio.wait_for(task, timeout=max(0.5, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
        if flush and self._token:
            # Loop until empty: `_flush_once` takes at most MAX_BATCH per channel.
            for _ in range(MAX_SHUTDOWN_FLUSHES):
                if not any(self._queues[c] for c in _CHANNELS):
                    break
                if time.monotonic() >= deadline:
                    logger.warning("push: shutdown budget spent, %d sample(s) not sent",
                                   sum(len(self._queues[c]) for c in _CHANNELS))
                    break
                try:
                    await self._flush_once()
                except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                    logger.warning("push: final flush failed, %d sample(s) lost: %s",
                                   sum(len(self._queues[c]) for c in _CHANNELS), exc)
                    break
        self._session_id = None
        # Cleared, not merely unused: the token must not outlive the session.
        self._token = None
        for channel in _CHANNELS:
            self._queues[channel].clear()
        # Stamps and counters are per session.
        self._last_heart_ts.clear()
        self._sent = {channel: 0 for channel in _CHANNELS}
        self._dropped = {channel: 0 for channel in _CHANNELS}
        self._unaccounted = {channel: 0 for channel in _CHANNELS}
        self._duplicates = {channel: 0 for channel in _CHANNELS}
        self._malformed = {channel: 0 for channel in _CHANNELS}

    # ── producing ────────────────────────────────────────────────────────────

    def enqueue(self, channel: str, sample: dict[str, Any]) -> None:
        """Queue one derived sample. Never blocks; a backend outage becomes a drop count."""
        if channel not in self._queues:
            raise ValueError(f"unknown push channel: {channel!r}")
        if not self._token:
            # No session: nothing is being recorded and nothing was lost.
            return
        if not self.running:
            # Session open but loop ended: a real loss.
            self._dropped[channel] += 1
            return
        queue = self._queues[channel]
        if len(queue) == queue.maxlen:
            # deque(maxlen=...) evicts silently.
            self._dropped[channel] += 1
        queue.append(sample)
        if len(queue) >= MAX_BATCH:
            self._wake.set()

    def submit_payload(self, payload: dict[str, Any]) -> None:
        """Split one sidecar tick into the channels the backend accepts.

        Shaping only, no number converted. An absent camera block means
        "switched off" and must not become a row.
        """
        # `enqueue` decides between "no session" and "loop ended (a loss)".
        if not self._token:
            return
        ts = payload.get("timestamp")
        device_id = payload.get("device_id")

        if payload.get("kind") != "camera":
            self.enqueue("cognitive", {
                "ts": ts,
                "features": payload.get("features") or {},
                "bands": payload.get("bands") or {},
                # Merged into `raw` by the mapper.
                "raw": {"device_id": device_id,
                        "channels": payload.get("channels"),
                        "state": payload.get("state"),
                        "ingestion": payload.get("ingestion")},
            })

        face = payload.get("face")
        # The face block is always present; a row needs an emotion, gaze or pose reading.
        if face and (face.get("emotion") is not None
                     or face.get("gaze_x") is not None
                     or face.get("head_yaw") is not None):
            self.enqueue("face", {
                "ts": ts,
                "emotion": face.get("emotion"),
                "emotion_confidence": face.get("emotion_confidence"),
                # Renamed so a face `trusted` cannot be read as the heart one.
                "emotion_trusted": face.get("trusted"),
                "attention": face.get("attention"),
                "gaze_x": face.get("gaze_x"),
                "gaze_y": face.get("gaze_y"),
                "head_yaw": face.get("head_yaw"),
                "head_pitch": face.get("head_pitch"),
                "head_roll": face.get("head_roll"),
                "raw": {"device_id": device_id,
                        "rejected_by": face.get("rejected_by"),
                        "gaze_rejected_by": face.get("gaze_rejected_by"),
                        "pose_rejected_by": face.get("pose_rejected_by"),
                        "degraded": face.get("degraded"),
                        "ingestion": payload.get("ingestion")},
            })

        heart = payload.get("heart")
        # Needs a bpm (source is set even on rejects) and a source (consent is per sensor).
        # The headband's held block is deduped on its own `ts`, keyed per (device, source).
        heart_ts = (heart or {}).get("ts") or ts
        heart_key = (device_id, (heart or {}).get("source"))
        if (heart and heart.get("source") and heart.get("bpm") is not None
                and self._last_heart_ts.get(heart_key) != heart_ts):
            self._last_heart_ts[heart_key] = heart_ts
            self.enqueue("heart", {
                "ts": heart_ts,
                "source": heart.get("source"),
                "heart_rate_bpm": heart.get("bpm"),
                "rmssd_ms": heart.get("rmssd_ms"),
                # RMSSD's own gates, apart from the rate's `rejected_by`.
                "beat_coverage": heart.get("beat_coverage"),
                "rmssd_rejected_by": heart.get("rmssd_rejected_by"),
                "sqi": heart.get("sqi"),
                "stress_score": heart.get("stress_score"),
                "stress_category": heart.get("stress_category"),
                "trusted": heart.get("trusted"),
                # Top-level, not in `raw`, where the endpoint strips client-posted keys.
                "synthetic": heart.get("synthetic"),
                "raw": {"device_id": device_id,
                        "confidence": heart.get("confidence"),
                        "rejected_by": heart.get("rejected_by"),
                        "measured_fps": heart.get("measured_fps"),
                        "window_coverage": heart.get("window_coverage"),
                        # Headband sample rate, kept apart from the camera's measured_fps.
                        "sample_rate_hz": heart.get("sample_rate_hz"),
                        "largest_gap_s": heart.get("largest_gap_s"),
                        "channel_count": heart.get("channel_count"),
                        "ingestion": payload.get("ingestion")},
            })

    # ── flushing ─────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        while not self._stopping.is_set():
            delay = self._backoff or FLUSH_SECONDS
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self._stopping.is_set():
                return
            # The deadline, not the wake event, is the authority: a full batch may flush early, never before backoff.
            if self._retry_at and time.monotonic() < self._retry_at:
                continue
            try:
                await self._flush_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop outlives failures
                self._note_failure(exc)

    def _note_failure(self, exc: Exception) -> None:
        self._last_error = str(exc)
        self._backoff = min(BACKOFF_MAX, max(BACKOFF_START, self._backoff * 2))
        self._retry_at = time.monotonic() + self._backoff
        logger.warning("push: flush failed (%s), backing off %.0fs",
                       exc, self._backoff)

    async def _flush_once(self) -> None:
        """One pass over the channels. A failure in one does not cost the others.

        Each channel is drained just before its own POST, so a failure restores only that batch.
        """
        session_id, token = self._session_id, self._token
        if not session_id or not token:
            return
        first_error: Exception | None = None
        delivered = False
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            for channel in _CHANNELS:
                samples = self._take(channel)
                if not samples:
                    continue
                try:
                    await self._post(client, channel, session_id, token, samples)
                    delivered = True
                except asyncio.CancelledError:
                    # Not restored: the server may already have committed it.
                    self._unaccounted[channel] += len(samples)
                    logger.warning("push: %s batch cancelled in flight; %d sample(s) "
                                   "unaccounted", channel, len(samples))
                    raise
                except Exception as exc:  # noqa: BLE001 - re-raised below
                    self._restore(channel, samples)
                    if first_error is None:
                        first_error = exc
        if first_error is not None:
            raise first_error
        if not delivered:
            # Sending nothing proves nothing; do not reset the backoff.
            return
        self._backoff = 0.0
        self._retry_at = 0.0
        self._last_error = None

    def _take(self, channel: str) -> list[dict[str, Any]]:
        queue = self._queues[channel]
        return [queue.popleft() for _ in range(min(MAX_BATCH, len(queue)))]

    def _restore(self, channel: str, samples: list[dict[str, Any]]) -> None:
        """Return a failed batch to the front of its queue, counting the loss.

        `extendleft` on a full `deque(maxlen=...)` silently evicts the newest, so overflow is counted.
        """
        queue = self._queues[channel]
        overflow = max(0, len(queue) + len(samples) - (queue.maxlen or 0))
        if overflow:
            self._dropped[channel] += overflow
        queue.extendleft(reversed(samples))

    async def _post(self, client: httpx.AsyncClient, channel: str,
                    session_id: str, token: str,
                    samples: list[dict[str, Any]]) -> None:
        response = await client.post(
            f"{self._backend_url}/api/signals/{channel}",
            json={"session_id": session_id, "samples": samples},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code == 429:
            # A failure, so samples are restored and backoff widens.
            raise RuntimeError("rate limited by backend (429)")
        response.raise_for_status()
        # Past here the rows are committed, so nothing below may raise and trigger a re-post.
        try:
            body = response.json() if response.content else {}
            inserted = int(body.get("inserted", 0))
            dropped = int(body.get("dropped", 0))
            # Default 0 for an older backend that does not report these.
            duplicates = int(body.get("duplicates", 0))
            malformed = int(body.get("malformed", 0))
            reason = body.get("reason", "unspecified")
        except Exception as exc:  # noqa: BLE001 - see above
            # Delivered-but-unknown: the write happened.
            logger.warning("push: %s batch committed but its receipt was "
                           "unreadable (%s); %d sample(s) unaccounted",
                           channel, exc, len(samples))
            self._unaccounted[channel] += len(samples)
            return
        # The server's count, not len(samples): it drops unconsented samples.
        self._sent[channel] += inserted
        # Not added to `_sent`: recorded by an earlier attempt.
        self._duplicates[channel] += duplicates
        self._malformed[channel] += malformed
        if malformed:
            logger.warning("push: backend could not read %d %s sample(s); "
                           "they are lost, not retried", malformed, channel)
        if dropped:
            logger.info("push: backend dropped %d %s sample(s): %s",
                        dropped, channel, reason)
        if duplicates:
            logger.info("push: backend already had %d %s sample(s); a replay "
                        "is a no-op, not a loss", duplicates, channel)

    # ── introspection ────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """What this client is doing, for `/api/v1/push/status`. Carries no token."""
        return {
            "running": self.running,
            "session_id": self._session_id,
            "queued": {c: len(self._queues[c]) for c in _CHANNELS},
            "recorded": dict(self._sent),
            "dropped_locally": dict(self._dropped),
            # Written, amount unknown: neither recorded nor lost.
            "unaccounted": dict(self._unaccounted),
            # Recorded by an earlier attempt; not a sensor that measured nothing.
            "duplicates": dict(self._duplicates),
            # Refused by the backend as unreadable; lost.
            "malformed": dict(self._malformed),
            "backoff_seconds": self._backoff,
            "last_error": self._last_error,
        }
