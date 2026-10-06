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
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple

import httpx

logger = logging.getLogger(__name__)

# Below the backend's INGEST_MAX_BATCH, not equal to it: an oversized batch is refused whole.
MAX_BATCH = 50

# Samples per channel; drops oldest on overflow.
MAX_QUEUE = 600

# Seconds per flush cycle.
FLUSH_SECONDS = 5.0

# A last result older than this claims nothing: a channel that stopped sending (a camera off) has none.
RESULT_FRESH_SECONDS = 30.0

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

# Statuses that refuse the batch itself: resent unchanged it is refused again, so retrying it
# holds the head of its queue for ever and widens every channel's backoff.
_REFUSED_WHOLE = frozenset({400, 413, 422})

# Halving stops here: below it a size refusal is final, so no cap turns each reading into a request.
MIN_BATCH = 5

# Seconds between reads of `/api/recording/me` while a session runs; the lesson page polls at this pace too.
PERMIT_CHECK_SECONDS = 30.0
# How long readings wait for a device start's fresh answer before the last one decides; held longer they
# would overflow the queue and read as lost rather than declined.
PERMIT_HOLD_SECONDS = 3 * FLUSH_SECONDS

# The consent channel each push channel is recorded under; heart goes by its sensor, from the answer.
_CHANNEL_CONSENT = {"cognitive": "eeg", "face": "camera"}
_CONSENT_CHANNELS = ("eeg", "headband_optical", "camera")
_SENSORS = ("camera", "headband")
# (refused sensor, still the refusing session?) -> stops that sensor's running devices.
_RefusalHandler = Callable[[set[str], Callable[[], bool]], Awaitable[None]]


class _Permits(NamedTuple):
    """One landed `/api/recording/me` answer. The rules behind it are the backend's; nothing is re-derived here."""
    states: dict[str, str]
    reasons: dict[str, str | None]
    sensors: dict[str, dict[str, bool]]
    heart_sources: dict[str, str]
    at: float  # monotonic

    def answer(self) -> dict[str, Any]:
        """As the backend gave it, for the lesson page, which reads the same fields."""
        return {**{c: {"state": self.states[c], "reason": self.reasons[c]} for c in _CONSENT_CHANNELS},
                "sensors": self.sensors, "heart_sources": self.heart_sources}


class _BatchRefused(Exception):
    """The backend refused this batch, not the request; see `_REFUSED_WHOLE`."""

    def __init__(self, message: str, sized: bool) -> None:
        super().__init__(message)
        self.sized = sized


def _is_size_refusal(response: Any) -> bool:
    """A 413, or a 422 whose every error is the samples list being too long.

    Anything else (a field the two versions disagree on) is not cured by a smaller batch."""
    if response.status_code == 413:
        return True
    if response.status_code != 422:
        return False
    try:
        detail = response.json().get("detail")
    except Exception:  # noqa: BLE001 - an unreadable body is not evidence of size
        return False
    return isinstance(detail, list) and bool(detail) and all(
        isinstance(d, dict) and d.get("type") == "too_long"
        and list(d.get("loc") or [])[-1:] == ["samples"] for d in detail)


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
        # Refused for other than size, or for size at MIN_BATCH: lost, and never retried.
        self._rejected: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        # Accepted, then not recorded (consent, school year, a switch, a reading with nothing in it), and why.
        self._declined: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        self._declined_reason: dict[str, str | None] = {channel: None for channel in _CHANNELS}
        # "recorded" or "declined": what the channel's latest receipt did; None before one.
        self._last_result: dict[str, str | None] = {channel: None for channel in _CHANNELS}
        self._last_result_at: dict[str, float] = {channel: 0.0 for channel in _CHANNELS}
        # Whether the channel's latest receipt was a refusal, so a run of them asks once.
        self._refusing: dict[str, bool] = {channel: False for channel in _CHANNELS}
        # Per channel; only shrinks, on a size refusal, and resets with the session.
        self._batch_limit: dict[str, int] = {channel: MAX_BATCH for channel in _CHANNELS}
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
        # The latest landed answer, or None: send everything and let the backend decide.
        self._permits: _Permits | None = None
        self._permits_due = 0.0
        # Set by `recheck()` until a check begun after it lands; see `_held_back`. Counted per recheck.
        self._permits_stale = False
        self._permits_generation = 0
        # Monotonic end of the hold a recheck starts; see PERMIT_HOLD_SECONDS.
        self._hold_until = 0.0
        # Stops the running devices of a refused sensor ("camera", "headband"), while the second argument
        # says the refusing session is still current; the page may be gone.
        self._on_refused: _RefusalHandler | None = None
        # Per sensor, each its own task: a slow Bluetooth disconnect holds neither the loop nor a `stop()`.
        self._refusal_tasks: dict[str, asyncio.Task] = {}

    def set_refusal_handler(self, handler: _RefusalHandler | None) -> None:
        """What to call with a refused sensor after each check that refuses one."""
        self._on_refused = handler

    def recheck(self) -> None:
        """Ask again before the next send: a device just started, and must not be judged by an older answer.

        Until the new one lands, readings the old answer refuses are held, not dropped and not sent."""
        self._permits_stale = True
        self._permits_generation += 1
        self._hold_until = time.monotonic() + PERMIT_HOLD_SECONDS
        self._permits_due = 0.0
        self._wake.set()

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

    async def stop(self, *, flush: bool = True, only_session: str | None = None) -> bool | None:
        """Stop pushing and forget the token; one bounded final flush by default. Returns whether it stopped.

        SHUTDOWN_BUDGET includes the wait for a start or stop holding the lock; spent there, this changes nothing.
        With `only_session`, None and nothing changed unless that session is the one pushing, decided under the lock."""
        deadline = time.monotonic() + SHUTDOWN_BUDGET
        try:
            await asyncio.wait_for(self._lifecycle.acquire(), timeout=SHUTDOWN_BUDGET)
        except TimeoutError:
            logger.warning("push: shutdown budget spent waiting for another start or stop; not stopped")
            return False
        try:
            if only_session is not None and only_session != self._session_id:
                return None
            await self._stop_locked(flush=flush, deadline=deadline)
        finally:
            self._lifecycle.release()
        return True

    async def _stop_locked(self, *, flush: bool, deadline: float | None = None) -> None:
        """The body of `stop()`. Assumes `_lifecycle` is held (it is not reentrant).

        Ends by `deadline`, SHUTDOWN_BUDGET from now by default. A cancelled flushing stop keeps the queue
        and token for the next stop; any other end forgets the session."""
        if deadline is None:
            deadline = time.monotonic() + SHUTDOWN_BUDGET
        kept = False
        try:
            await self._wind_down(flush=flush, deadline=deadline)
        except asyncio.CancelledError:
            # Only shutdown cancels a stop, and its own stop then flushes what this one kept.
            kept = flush and self._token is not None
            if kept:
                logger.warning("push: stop cancelled; %d sample(s) kept for the next stop",
                               sum(len(self._queues[c]) for c in _CHANNELS))
            raise
        finally:
            if not kept:
                self._forget_session()

    async def _wind_down(self, *, flush: bool, deadline: float) -> None:
        task, self._task = self._task, None
        if task is not None:
            # Asked to finish, not cancelled: a mid-POST cancel leaves the batch's fate unknown.
            self._stopping.set()
            self._wake.set()
            try:
                try:
                    await asyncio.wait_for(task, timeout=max(0.5, deadline - time.monotonic()))
                except TimeoutError:
                    task.cancel()
                    await task
            except asyncio.CancelledError:
                # The loop's own end is swallowed; one aimed at the caller, a cancelled request's, is not.
                if asyncio.current_task().cancelling():
                    raise
        if not (flush and self._token):
            return
        if self._permits_stale:
            # Held readings get the fresh answer if the budget allows; the final flush then holds nothing.
            self._permits_due = 0.0
            try:
                async with asyncio.timeout(max(0.0, deadline - time.monotonic())):
                    await self._check_permits_if_due()
            except TimeoutError:
                pass
        # Loop until empty: `_flush_once` takes at most MAX_BATCH per channel.
        for _ in range(MAX_SHUTDOWN_FLUSHES):
            if not any(self._queues[c] for c in _CHANNELS):
                return
            if time.monotonic() >= deadline:
                logger.warning("push: shutdown budget spent, %d sample(s) not sent",
                               sum(len(self._queues[c]) for c in _CHANNELS))
                return
            budget = asyncio.timeout(deadline - time.monotonic())
            try:
                # Cancelled at the deadline, as the loop is: the kit ends a sidecar that overruns it.
                async with budget:
                    await self._flush_once(final=True)
            except Exception as exc:  # noqa: BLE001 - shutdown must not raise
                # A TimeoutError the flush raised itself is a failure like any other, not the budget.
                if isinstance(exc, TimeoutError) and budget.expired():
                    logger.warning("push: shutdown budget spent mid-flush, %d sample(s) not sent",
                                   sum(len(self._queues[c]) for c in _CHANNELS))
                else:
                    logger.warning("push: final flush failed, %d sample(s) lost: %s",
                                   sum(len(self._queues[c]) for c in _CHANNELS), exc)
                return

    def _forget_session(self) -> None:
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
        self._rejected = {channel: 0 for channel in _CHANNELS}
        self._declined = {channel: 0 for channel in _CHANNELS}
        self._declined_reason = {channel: None for channel in _CHANNELS}
        self._last_result = {channel: None for channel in _CHANNELS}
        self._last_result_at = {channel: 0.0 for channel in _CHANNELS}
        self._refusing = {channel: False for channel in _CHANNELS}
        self._batch_limit = {channel: MAX_BATCH for channel in _CHANNELS}
        # The answer was about this student; the next session asks afresh.
        self._permits = None
        self._permits_due = 0.0
        self._permits_stale = False

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
                # What `stress_score` is relative to; lands in `raw`.
                "stress_baseline_bpm": heart.get("stress_baseline_bpm"),
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
            # Before the backoff gate, and the wait ends when it is next due: a backoff must not delay a withdrawal.
            await self._check_permits_if_due()
            delay = min(self._backoff or FLUSH_SECONDS, max(0.0, self._permits_due - time.monotonic()))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            if self._stopping.is_set():
                return
            # Again before sending: `recheck()` wants this batch judged by a fresh answer.
            await self._check_permits_if_due()
            # The deadline, not the wake event, is the authority: a full batch may flush early, never before backoff.
            if self._retry_at and time.monotonic() < self._retry_at:
                continue
            try:
                await self._flush_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop outlives failures
                self._note_failure(exc)

    async def _check_permits_if_due(self) -> None:
        """Re-read what this student may record, and stop the sensors it refuses.

        A failed read keeps the last answer: it says nothing new, and never switches anything off."""
        if time.monotonic() < self._permits_due:
            return
        # Advanced even with no token, or the loop's wait, capped by it, would spin.
        self._permits_due = time.monotonic() + PERMIT_CHECK_SECONDS
        if not self._token:
            return
        # Only a check begun after the latest `recheck()` counts.
        generation = self._permits_generation
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(f"{self._backend_url}/api/recording/me",
                                            headers={"Authorization": f"Bearer {self._token}"})
            response.raise_for_status()
            body = response.json()
            permits = _Permits(
                states={c: str(body[c]["state"]) for c in _CONSENT_CHANNELS},
                reasons={c: body[c]["reason"] for c in _CONSENT_CHANNELS},
                sensors={s: {"allowed": body["sensors"][s]["allowed"] is True,
                             "refused": body["sensors"][s]["refused"] is True} for s in _SENSORS},
                heart_sources={str(k): str(v) for k, v in body["heart_sources"].items()},
                at=time.monotonic())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - an older backend 404s; the backend still gates
            logger.info("push: could not check what may be recorded (%s); keeping the last answer", exc)
            if self._holding():
                # Still held, not dropped on the old answer; asked again soon rather than in a full interval.
                self._permits_due = time.monotonic() + FLUSH_SECONDS
            return
        if generation != self._permits_generation:
            # Asked before a device started: not kept, passed on or acted on, so it cannot stop that device.
            return
        self._permits = permits
        self._permits_stale = False
        for sensor in (s for s, verdict in permits.sensors.items() if verdict["refused"]):
            task = self._refusal_tasks.get(sensor)
            # One at a time per sensor, and per sensor: a slow camera stop must not hold up the headband's.
            if self._on_refused is not None and (task is None or task.done()):
                self._refusal_tasks[sensor] = asyncio.create_task(self._stop_refused(sensor, self._session_id))

    async def _stop_refused(self, sensor: str, session_id: str | None) -> None:
        """Stops one sensor for the session that refused it; once that session ends it stops nothing more."""
        try:
            await self._on_refused({sensor}, lambda: self._session_id == session_id)
        except Exception as exc:  # noqa: BLE001 - the next check retries
            logger.warning("push: could not stop the %s, which may not record: %s", sensor, exc)

    async def cancel_refusals(self) -> None:
        """For shutdown: cancels device stops still under way; a cancelled stop still runs its own cleanup."""
        tasks = [task for task in self._refusal_tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._refusal_tasks.clear()

    def _refused(self, consent: str | None) -> bool:
        """Refused by the latest answer; `unknown` and no answer are not refusals."""
        state = self._permits.states.get(consent, "unknown") if self._permits else "unknown"
        return state not in ("permitted", "unknown")

    def _consent_of(self, channel: str, sample: dict[str, Any]) -> str | None:
        """The consent channel a sample is recorded under; heart goes by its sensor, from the answer."""
        if channel != "heart":
            return _CHANNEL_CONSENT[channel]
        return self._permits.heart_sources.get(sample.get("source")) if self._permits else None

    def _held_back(self, channel: str, samples: list[dict[str, Any]]) -> bool:
        """A fresh answer is on its way and the old one refuses some of these: they wait for it."""
        return self._holding() and any(self._refused(self._consent_of(channel, s)) for s in samples)

    def _holding(self) -> bool:
        """A recheck's answer is still awaited and its hold has not run out."""
        return self._permits_stale and time.monotonic() < self._hold_until

    def _withhold(self, channel: str, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop the samples the latest answer refuses, counted as declined with the backend's reason."""
        if self._permits is None:
            return samples
        kept = []
        for sample in samples:
            consent = self._consent_of(channel, sample)
            if not self._refused(consent):
                kept.append(sample)
                continue
            self._declined[channel] += 1
            self._declined_reason[channel] = self._permits.reasons.get(consent) or "not permitted"
            self._last_result[channel] = "declined"
            self._last_result_at[channel] = time.monotonic()
        return kept

    def _note_failure(self, exc: Exception) -> None:
        self._last_error = str(exc)
        self._backoff = min(BACKOFF_MAX, max(BACKOFF_START, self._backoff * 2))
        self._retry_at = time.monotonic() + self._backoff
        logger.warning("push: flush failed (%s), backing off %.0fs",
                       exc, self._backoff)

    async def _flush_once(self, *, final: bool = False) -> None:
        """One pass over the channels. A failure in one does not cost the others.

        Each channel is drained just before its own POST, so a failure restores only that batch.
        `final` (shutdown) holds nothing back: there is no later flush to judge it."""
        session_id, token = self._session_id, self._token
        if not session_id or not token:
            return
        first_error: Exception | None = None
        delivered = False
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            for channel in _CHANNELS:
                samples = self._take(channel)
                if not final and self._held_back(channel, samples):
                    # Neither dropped on the old answer nor sent: the next flush judges them by the new one.
                    self._restore(channel, samples)
                    continue
                samples = self._withhold(channel, samples)
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
                except _BatchRefused as exc:
                    # No backoff either way: the other channels are not at fault.
                    if exc.sized and len(samples) // 2 >= MIN_BATCH:
                        self._batch_limit[channel] = len(samples) // 2
                        self._restore(channel, samples)
                        logger.warning("push: %s; resending in batches of %d",
                                       exc, self._batch_limit[channel])
                        continue
                    self._rejected[channel] += len(samples)
                    self._last_error = str(exc)
                    logger.warning("push: %s; %d sample(s) lost, not retried",
                                   exc, len(samples))
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
        return [queue.popleft() for _ in range(min(self._batch_limit[channel], len(queue)))]

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
        if response.status_code in _REFUSED_WHOLE:
            raise _BatchRefused(f"backend refused the {channel} batch ({response.status_code})",
                                _is_size_refusal(response))
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
            # Dropped for want of permission; an older backend sends no `refused`, so a wholly declined batch stands in.
            refused = dropped > 0 and bool(body.get("refused", not inserted))
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
        self._declined[channel] += dropped
        if dropped:
            self._declined_reason[channel] = reason
        # A receipt of only duplicates or unreadable samples says nothing about recording now.
        if inserted or dropped:
            # Refused despite the latest answer, which is then stale (a withdrawal): ask now, once per run.
            if refused and not self._refusing[channel]:
                self._permits_due = 0.0
            self._refusing[channel] = refused
            self._last_result[channel] = "recorded" if inserted else "declined"
            self._last_result_at[channel] = time.monotonic()
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
            # Refused, and no smaller batch would pass; lost, not retried.
            "rejected": dict(self._rejected),
            # Accepted but not recorded; every drop carries the backend's reason, which the page shows.
            "declined": dict(self._declined),
            "declined_reason": dict(self._declined_reason),
            "last_result": {c: r if time.monotonic() - self._last_result_at[c] <= RESULT_FRESH_SECONDS else None
                            for c, r in self._last_result.items()},
            "batch_limit": dict(self._batch_limit),
            # The states this session sends by, and their age, which the lesson page uses in place of its own poll.
            "permits": self._permits.answer() if self._permits else None,
            "permits_age_seconds": round(time.monotonic() - self._permits.at, 1) if self._permits else None,
            # How often it asks, so the page can tell a current answer from one the sidecar stopped renewing.
            "permits_check_seconds": PERMIT_CHECK_SECONDS,
            # This client re-asks on a refused batch itself, so the page need not; an older one sends nothing.
            "checks_on_refusal": True,
            "backoff_seconds": self._backoff,
            "last_error": self._last_error,
        }
