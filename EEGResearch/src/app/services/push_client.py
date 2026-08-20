"""Posts derived signals from this sidecar to the website backend.

The other half of the ingestion split: `eeg_poller` in the website backend
polls a sidecar over HTTP, which only works when both run on one machine (as
`start.ps1` does). With a camera on each student's own device the sidecar is a
local per-student process a hosted backend can't reach, so this module POSTs
the other direction, to `/api/signals/{cognitive,heart,face}`.

Three things shape the design.

The token is the student's own and doesn't live here: it arrives from the
browser at session start, stays in memory only, is never logged or written to
disk, and `stop()` clears it.

The receiving endpoints are a trust boundary -- they rate-limit and bound batch
size because this client is untrusted code on someone's machine. This side
stays inside those bounds by construction (batches capped below the server's
limit, queue bounded) instead of discovering them as 429s.

No arithmetic happens here. The sidecar's payload goes up whole and the backend
maps it with `signal_mapping` -- a /100 conversion on this side would duplicate
that logic and risk one path storing percentages while the other stores ratios.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Below the backend's INGEST_MAX_BATCH, not equal to it: the server rejects an
# oversized batch outright, so sitting exactly on the limit risks dropped data
# if what a sample contains ever grows.
MAX_BATCH = 50

# What a stalled backend is allowed to cost. Minutes of buffer at a few Hz;
# drops oldest on overflow since recent samples matter most for a live session.
MAX_QUEUE = 600

# One flush cycle. Long enough that a 4 Hz stream batches rather than trickling,
# short enough that a teacher's live view isn't minutes behind.
FLUSH_SECONDS = 5.0

# How many batches the shutdown flush tries to clear (the whole backlog), capped
# so a backend refusing everything can't turn a Ctrl-C into a long wait.
MAX_SHUTDOWN_FLUSHES = MAX_QUEUE // MAX_BATCH

# A down backend must not be retried at the flush rate for a whole lesson.
# Doubles to this ceiling and resets on the first success.
BACKOFF_START = 5.0
BACKOFF_MAX = 120.0

# Per-request timeout, deliberately shorter than the flush interval so a hung
# request can't stack flushes on top of each other.
REQUEST_TIMEOUT = 4.0

# Wall-clock budget for everything `stop()` does. An attempt cap alone isn't a
# real bound: 12 attempts x 3 channels x a 4s timeout is ~144s, and this runs
# on a Ctrl-C and on page teardown.
SHUTDOWN_BUDGET = 10.0

_CHANNELS = ("cognitive", "heart", "face")


class PushClient:
    """Buffers samples per channel and flushes them to the backend.

    One instance per sidecar process. `start()` supplies the session and token;
    before that, `enqueue` is a no-op, so streaming for a local dashboard with
    no session open sends nothing anywhere.
    """

    def __init__(self, backend_url: str) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._session_id: str | None = None
        self._token: str | None = None
        self._queues: dict[str, deque[dict[str, Any]]] = {
            channel: deque(maxlen=MAX_QUEUE) for channel in _CHANNELS
        }
        # The timestamp of the last heart reading enqueued, per (device, source).
        # The headband's block is held on the payload between recomputes (see
        # `submit_payload`), so this turns ~40 arrivals of one measurement back
        # into one row.
        self._last_heart_ts: dict[tuple[str | None, str | None], str | None] = {}
        self._dropped: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        self._sent: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        # Committed by the backend but with an unreadable receipt -- neither
        # recorded nor lost, so it gets its own bucket instead of a guess.
        self._unaccounted: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        self._task: asyncio.Task | None = None
        # Serialises start/stop: both await, so two starts for the same new
        # session could interleave and leave a running loop with no token.
        self._lifecycle = asyncio.Lock()
        self._wake = asyncio.Event()
        # Set by `stop()` to ask the loop to finish at a tick boundary, so no
        # request is aborted after the server has committed it.
        self._stopping = asyncio.Event()
        self._backoff = 0.0
        # Monotonic deadline the backoff is enforced against -- the backoff
        # value alone is just a sleep length, and the wake event can skip the
        # sleep (see `_loop`).
        self._retry_at = 0.0
        self._last_error: str | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, session_id: str, token: str) -> None:
        """Begin pushing for one session, with that student's bearer token.

        Called again with the same session id, this only replaces the token --
        the queue is untouched. That's the token-refresh path: Supabase access
        tokens expire roughly hourly and a lesson can outlast one.

        Called with a different session id, the old queue is discarded without
        being sent -- those samples belong to a session the new token may not
        own, so posting them would misfile readings under the wrong session.
        """
        async with self._lifecycle:
            if session_id != self._session_id:
                await self._stop_locked(flush=False)
            self._session_id = session_id
            self._token = token
            self._stopping.clear()
            self._backoff = 0.0
            self._retry_at = 0.0
            self._last_error = None
            if not self.running:
                self._task = asyncio.create_task(self._loop())

    async def stop(self, *, flush: bool = True) -> None:
        """Stop pushing and forget the token.

        One last flush by default, so the tail of a session isn't lost to the
        gap before the next tick. Bounded by the same request timeout, so a
        dead backend delays shutdown by seconds rather than hanging it.
        """
        async with self._lifecycle:
            await self._stop_locked(flush=flush)

    async def _stop_locked(self, *, flush: bool) -> None:
        """The body of `stop()`. Assumes `_lifecycle` is held.

        Split out because `start()` needs to stop a previous session within its
        own critical section -- calling the public `stop()` there would deadlock
        on a non-reentrant lock.
        """
        deadline = time.monotonic() + SHUTDOWN_BUDGET
        task, self._task = self._task, None
        if task is not None:
            # Asked to finish, not cancelled outright: cancelling mid-POST could
            # abort a request the server already committed, and the restored
            # batch would be re-sent as duplicates into tables with no dedupe
            # key. Letting the in-flight request land costs at most one timeout.
            self._stopping.set()
            self._wake.set()
            try:
                await asyncio.wait_for(task, timeout=max(0.5, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                # Didn't return inside the budget, so cancel it now -- whatever
                # it held is genuinely unknown and recorded as such.
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
        if flush and self._token:
            # Loop until empty, not a single call: `_flush_once` takes at most
            # MAX_BATCH per channel. Also bounded by attempts, so a backend
            # accepting nothing can't hold up exit.
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
        # Cleared, not merely unused: the token is only meant to live in memory
        # for one session, and keeping it past `stop()` breaks that guarantee.
        self._token = None
        for channel in _CHANNELS:
            self._queues[channel].clear()
        # Per session, like the queues. A stamp held across sessions could
        # suppress the first reading of the next one.
        self._last_heart_ts.clear()
        # Counters are per session too. Carried over, a fresh session would
        # open showing readings recorded before any were taken.
        self._sent = {channel: 0 for channel in _CHANNELS}
        self._dropped = {channel: 0 for channel in _CHANNELS}
        self._unaccounted = {channel: 0 for channel in _CHANNELS}

    # ── producing ────────────────────────────────────────────────────────────

    def enqueue(self, channel: str, sample: dict[str, Any]) -> None:
        """Queue one derived sample. Never raises, never blocks.

        Called from the sampling loops, which must not be slowed or killed by
        anything network-related. A backend outage degrades to dropped samples
        and a count, not a stalled capture.
        """
        if channel not in self._queues:
            raise ValueError(f"unknown push channel: {channel!r}")
        if not self._token:
            # No session: nothing is being recorded and nothing was lost.
            return
        if not self.running:
            # A session is open but the loop has ended (the shutdown-flush
            # window). These samples were produced for a live session and will
            # never be sent, so it's a real loss and must be counted.
            self._dropped[channel] += 1
            return
        queue = self._queues[channel]
        if len(queue) == queue.maxlen:
            # deque(maxlen=...) evicts silently, so count it here or the loss
            # is invisible.
            self._dropped[channel] += 1
        queue.append(sample)
        if len(queue) >= MAX_BATCH:
            self._wake.set()

    def submit_payload(self, payload: dict[str, Any]) -> None:
        """Split one sidecar tick into the channels the backend accepts.

        Shaping only -- fields are renamed and re-nested to match the ingest
        models, no number is converted. `features`/`bands` go up exactly as the
        sidecar produced them, on its 0..100 scale, and `signal_mapping` on the
        backend converts for both this path and the poller.

        A camera tick can produce a face sample, a heart sample, both or
        neither: `build_camera_payload` omits a disabled channel rather than
        nulling it, so an absent block here means "switched off" and must not
        become a row.
        """
        # No guard on `running` here: `enqueue` decides, distinguishing "no
        # session" (not a loss) from "session open but loop ended" (a loss,
        # counted).
        if not self._token:
            return
        ts = payload.get("timestamp")
        device_id = payload.get("device_id")

        if payload.get("kind") != "camera":
            self.enqueue("cognitive", {
                "ts": ts,
                "features": payload.get("features") or {},
                "bands": payload.get("bands") or {},
                # Merged into `raw` by the mapper, where fields explaining a
                # nulled measurement live.
                "raw": {"device_id": device_id,
                        "channels": payload.get("channels"),
                        "state": payload.get("state"),
                        "ingestion": payload.get("ingestion")},
            })

        face = payload.get("face")
        # `build_face_record` always returns a dict -- a rejected window reports
        # `emotion: None, rejected_by: "no_face"`, not an absent block. Gating
        # on the block's presence would write a row every tick (~14k all-null
        # face_signals rows an hour, all counted by the aggregates).
        #
        # A reading is an emotion (untrusted ones still count, since
        # `emotion_trusted` is a column fusion gates on) -- a rejection isn't a
        # measurement and belongs only in the sidecar's own state.
        # A reading is also an emotion OR a gaze OR a head pose: gating on
        # emotion alone would drop windows where FER+ refused but the
        # landmarks/pose didn't (e.g. eyes closed refuses gaze, pose is fine).
        if face and (face.get("emotion") is not None
                     or face.get("gaze_x") is not None
                     or face.get("head_yaw") is not None):
            self.enqueue("face", {
                "ts": ts,
                "emotion": face.get("emotion"),
                "emotion_confidence": face.get("emotion_confidence"),
                # The endpoint calls this `emotion_trusted`; the sidecar block
                # calls it `trusted`. Renamed here since the backend has two
                # `trusted` fields (one per channel) and a shared name would
                # let a face confidence get read as a heart confidence.
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
        # `source` alone isn't enough as a gate: `build_heart_record` sets
        # `source: "rppg"` unconditionally, even on rejects, so gating on it
        # alone would write a null-bpm row every tick. `source` is still
        # required, though, since consent is per sensor and a reading that
        # can't name its sensor can't be consent-checked.
        #
        # A third condition, for the headband: its block is a 25s window
        # recomputed every 10s and held on the payload in between, so one
        # measurement arrives on ~40 consecutive ticks. Keyed on the tick's
        # timestamp, each would be a distinct row -- forty copies of one
        # 25-second window -- so the block carries its own `ts` instead. The
        # camera's block has none and falls back to the tick's.
        #
        # Keyed per (device, source), not one field: a laptop running both a
        # headband and a camera feeds two sessions into this one client, and a
        # single slot would let each suppress the other's readings.
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
                # RMSSD's own gates, kept apart from `rejected_by` below (which
                # says whether there's a heart rate at all): a row can carry a
                # good bpm with no RMSSD, and these say which was refused.
                "beat_coverage": heart.get("beat_coverage"),
                "rmssd_rejected_by": heart.get("rmssd_rejected_by"),
                "sqi": heart.get("sqi"),
                "stress_score": heart.get("stress_score"),
                "stress_category": heart.get("stress_category"),
                "trusted": heart.get("trusted"),
                "raw": {"device_id": device_id,
                        "confidence": heart.get("confidence"),
                        "rejected_by": heart.get("rejected_by"),
                        "measured_fps": heart.get("measured_fps"),
                        "window_coverage": heart.get("window_coverage"),
                        # Headband-side equivalents of measured_fps, kept under
                        # their own names: one is a camera frame rate, one is a
                        # BLE sample rate, and conflating them would hide which
                        # sensor was struggling.
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
                # Asked to finish between ticks. Returning here rather than
                # starting another flush lets `stop()` await this task instead
                # of cancelling it mid-request.
                return
            # The wake event fires on a full batch, which during an outage is
            # every few samples -- flushing on it immediately would make the
            # backoff decorative and retry a dead backend at the sample rate.
            # The deadline is the real authority: the event can make a flush
            # happen sooner than the idle interval, never sooner than the
            # backoff says.
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

        Each channel is drained inside the loop, immediately before its own
        POST -- draining all three up front and re-raising on the first failure
        would discard the other two batches outright with nothing to restore
        them or count the loss.
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
                    # Not restored: the request was in flight when cancelled, so
                    # the server may have already committed it, and re-posting
                    # into a table with no dedupe key would duplicate every row.
                    # Unknown is recorded as unknown.
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
            # Nothing was sent, so nothing was proved. Clearing the backoff here
            # would let a lull in sampling (paused lesson, student between
            # questions) reset a 120s backoff to 0 and report "recovered" on the
            # strength of having sent zero bytes to a backend that's still down.
            return
        self._backoff = 0.0
        self._retry_at = 0.0
        self._last_error = None

    def _take(self, channel: str) -> list[dict[str, Any]]:
        queue = self._queues[channel]
        return [queue.popleft() for _ in range(min(MAX_BATCH, len(queue)))]

    def _restore(self, channel: str, samples: list[dict[str, Any]]) -> None:
        """Return a failed batch to the front of its queue, counting the loss.

        Front, because these are the oldest and must not be reordered relative
        to whatever arrived while the request was in flight.

        The count matters: `extendleft` on a `deque(maxlen=...)` silently evicts
        from the other end, so restoring an old batch into a now-full queue
        would throw away the newest samples -- the reverse of drop-oldest, and
        uncounted.
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
            # The backend's rate limit, which this client is meant to stay
            # inside. Treated as a failure so samples go back on the queue and
            # backoff widens, rather than being counted as delivered.
            raise RuntimeError("rate limited by backend (429)")
        response.raise_for_status()
        # Past this line the server has committed the rows, so nothing below may
        # raise: the caller restores a failed batch and re-posts it, and
        # `cognitive_signals`/`face_signals` have no dedupe key, so a throw here
        # would duplicate every row just written. Reading the response body is
        # exactly the kind of thing that fails late (short read, truncated body,
        # a proxy returning HTML).
        try:
            body = response.json() if response.content else {}
            inserted = int(body.get("inserted", 0))
            dropped = int(body.get("dropped", 0))
            reason = body.get("reason", "unspecified")
        except Exception as exc:  # noqa: BLE001 - see above
            # Counted as delivered-but-unknown, not not-delivered: the write
            # happened, only our knowledge of how much landed didn't.
            logger.warning("push: %s batch committed but its receipt was "
                           "unreadable (%s); %d sample(s) unaccounted",
                           channel, exc, len(samples))
            self._unaccounted[channel] += len(samples)
            return
        # The server's own count, not len(samples): it drops samples for an
        # unconsented sensor and reports how many, so counting sent instead
        # would report success for a batch that recorded nothing.
        self._sent[channel] += inserted
        if dropped:
            logger.info("push: backend dropped %d %s sample(s): %s",
                        dropped, channel, reason)

    # ── introspection ────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """What this client is doing, for `/api/v1/push/status`.

        Deliberately carries no token and no session-scoped secret: this is
        readable with the learner token, which is shipped in client code and is
        not a secret either.
        """
        return {
            "running": self.running,
            "session_id": self._session_id,
            "queued": {c: len(self._queues[c]) for c in _CHANNELS},
            "recorded": dict(self._sent),
            # Samples produced by this sidecar that never reached the backend.
            "dropped_locally": dict(self._dropped),
            # Written, but we couldn't read how much. Kept separate from
            # `recorded` (would overstate) and `dropped_locally` (would claim a
            # loss that didn't happen).
            "unaccounted": dict(self._unaccounted),
            "backoff_seconds": self._backoff,
            "last_error": self._last_error,
        }
