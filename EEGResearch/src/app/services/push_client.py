"""Posts derived signals from this sidecar to the website backend.

The other half of the ingestion split. `eeg_poller` inside the website backend
polls a sidecar over HTTP, which works only when `start.ps1` puts both on one
machine. With the camera on each student's own device the sidecar is a local
per-student process and a hosted backend has no route to it, so the direction
reverses: this module POSTs to `/api/signals/{cognitive,heart,face}`.

Three things shape the design.

**The token is the student's own, and it does not live here.** It arrives from
the browser at session start, is held in memory for the life of the session, and
is never logged or written to disk. This process runs on a student's laptop; a
long-lived credential on that disk is a worse thing to own than a short-lived
one in RAM. `stop()` clears it.

**The receiving endpoints are a trust boundary.** They rate-limit and bound
batch size because this client is, from the backend's point of view, untrusted
code on someone's machine. So this side stays inside those bounds by
construction rather than discovering them as 429s: batches are capped below the
server's limit and the queue is bounded, dropping oldest.

**No arithmetic happens here.** The sidecar's own payload goes up whole and the
backend maps it with `signal_mapping`. A /100 conversion on this side would be a
second copy of one the poller path already has, which is how one path ends up
storing percentages while the other stores ratios.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Below the backend's `INGEST_MAX_BATCH` rather than equal to it. The server
# rejects an oversized batch outright, so a client sitting exactly on the limit
# turns any later loosening of what a sample contains into dropped data.
MAX_BATCH = 50

# What a stalled backend is allowed to cost. The queue holds derived samples at
# a few Hz, so this is minutes of buffer, and it drops **oldest** on overflow:
# for a live session the recent samples are the ones worth keeping, and an
# unbounded queue on a student's laptop is a memory leak with a friendly name.
MAX_QUEUE = 600

# One flush cycle. Long enough that a 4 Hz stream batches rather than trickling,
# short enough that a teacher's live view is not minutes behind.
FLUSH_SECONDS = 5.0

# How many batches the shutdown flush will try to clear. MAX_QUEUE / MAX_BATCH
# is the whole backlog; the cap exists so a backend refusing everything cannot
# turn a Ctrl-C into a long wait.
MAX_SHUTDOWN_FLUSHES = MAX_QUEUE // MAX_BATCH

# A backend that is down must not be retried at the flush rate for a whole
# lesson. Doubles to this ceiling and resets on the first success.
BACKOFF_START = 5.0
BACKOFF_MAX = 120.0

# Per-request timeout. Deliberately shorter than the flush interval so a hung
# request cannot stack flushes on top of each other.
REQUEST_TIMEOUT = 4.0

# Wall-clock budget for everything `stop()` does. The attempt cap alone was not
# a bound anyone would recognise from the docstring: 12 attempts x 3 channels x
# a 4 s timeout is ~144 s, and this runs on a Ctrl-C and on a page teardown.
SHUTDOWN_BUDGET = 10.0

_CHANNELS = ("cognitive", "heart", "face")


class PushClient:
    """Buffers samples per channel and flushes them to the backend.

    One instance per sidecar process. `start()` is what supplies the session and
    the token; before that call `enqueue` is a no-op, so a sidecar streaming for
    a local dashboard with no session open sends nothing anywhere.
    """

    def __init__(self, backend_url: str) -> None:
        self._backend_url = backend_url.rstrip("/")
        self._session_id: str | None = None
        self._token: str | None = None
        self._queues: dict[str, deque[dict[str, Any]]] = {
            channel: deque(maxlen=MAX_QUEUE) for channel in _CHANNELS
        }
        self._dropped: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        self._sent: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        # Committed by the backend but with an unreadable receipt. Neither
        # recorded nor lost, and lumping it into either would make that number
        # a guess -- the same three-state discipline the reporting surfaces use.
        self._unaccounted: dict[str, int] = {channel: 0 for channel in _CHANNELS}
        self._task: asyncio.Task | None = None
        # Serialises `start` and `stop`. Both await, so two starts for the same
        # new session could interleave: the slower one resumes inside its own
        # `stop()` and clears the session and token the faster one had already
        # installed, leaving a running loop with no token -- `status()` says
        # running, nothing records, and the drops are not even counted because
        # `enqueue` sees no token and treats it as "no session".
        self._lifecycle = asyncio.Lock()
        self._wake = asyncio.Event()
        # Set by `stop()` to ask the loop to finish at a tick boundary, so no
        # request is aborted after the server has committed it.
        self._stopping = asyncio.Event()
        self._backoff = 0.0
        # Monotonic deadline the backoff is actually enforced against. The
        # backoff alone is only a sleep length, and the wake event can skip the
        # sleep -- see `_loop`.
        self._retry_at = 0.0
        self._last_error: str | None = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, session_id: str, token: str) -> None:
        """Begin pushing for one session, with that student's bearer token.

        Called again with the **same** session id, this only replaces the token
        -- the queue is untouched. That is the token-refresh path: Supabase
        access tokens expire roughly hourly and a lesson can outlast one.

        Called with a **different** session id, the old queue is discarded
        without being sent. Those samples belong to a session the new token may
        not own; posting them is either rejected by `_verify_session_owner` or,
        for the same student, files one session's readings under another.

        Two things that were wrong here. The docstring said "drops", and the
        code called `stop()`, which flushes -- so the samples were *posted*, and
        the guard `if self.running` meant a client whose loop had already ended
        skipped the stop entirely and carried the old queue into the new session
        to be posted under the new id. Both are now `stop(flush=False)`, taken
        unconditionally.
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

        One last flush by default, so the tail of a session is not lost to the
        gap between the final sample and the next tick. It is bounded by the
        same request timeout, so a dead backend delays shutdown by seconds
        rather than hanging it.
        """
        async with self._lifecycle:
            await self._stop_locked(flush=flush)

    async def _stop_locked(self, *, flush: bool) -> None:
        """The body of `stop()`. Assumes `_lifecycle` is held.

        Split out because `start()` needs to stop a previous session *within*
        its own critical section -- calling the public `stop()` there would
        deadlock on a non-reentrant lock.
        """
        deadline = time.monotonic() + SHUTDOWN_BUDGET
        task, self._task = self._task, None
        if task is not None:
            # Asked to finish, not cancelled outright. Cancelling mid-POST
            # aborts a request the server may already have committed, and
            # `_flush_once` then restores the batch for the shutdown flush to
            # send again -- up to 50 duplicate rows per channel, into tables
            # with no dedupe key. Letting the in-flight request land instead
            # costs at most one request timeout, and it is accounted for.
            self._stopping.set()
            self._wake.set()
            try:
                await asyncio.wait_for(task, timeout=max(0.5, deadline - time.monotonic()))
            except asyncio.TimeoutError:
                # It did not come back inside the budget, so now it is cancelled
                # and whatever it held is genuinely unknown -- `_flush_once`
                # records that rather than guessing either way.
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            except asyncio.CancelledError:
                pass
        if flush and self._token:
            # Until empty, not once: `_flush_once` takes at most MAX_BATCH per
            # channel, so a single call at shutdown delivered 50 samples and
            # discarded whatever else had backed up. Bounded by attempts as well
            # as by emptiness, so a backend accepting nothing cannot hold up
            # exit -- each attempt is capped by the request timeout.
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
        # Cleared, not merely unused. The whole argument for accepting a
        # student's bearer token in this process is that it lives in memory for
        # one session; keeping it past `stop()` quietly withdraws that.
        self._token = None
        for channel in _CHANNELS:
            self._queues[channel].clear()
        # Counters are per session, like the queues. Carried over, a fresh
        # session opens showing readings recorded before any were taken -- the
        # panel would say "412 readings recorded from this computer" about a
        # lesson that has not started, which is a worse lie than a blank tile.
        self._sent = {channel: 0 for channel in _CHANNELS}
        self._dropped = {channel: 0 for channel in _CHANNELS}
        self._unaccounted = {channel: 0 for channel in _CHANNELS}

    # ── producing ────────────────────────────────────────────────────────────

    def enqueue(self, channel: str, sample: dict[str, Any]) -> None:
        """Queue one derived sample. Never raises, never blocks.

        Called from the sampling loops, which must not be slowed or killed by
        anything to do with the network. A backend outage degrades to dropped
        samples and a count, not to a stalled capture.
        """
        if channel not in self._queues:
            raise ValueError(f"unknown push channel: {channel!r}")
        if not self._token:
            # No session: nothing is being recorded and nothing was lost. A
            # sidecar streaming for its local dashboard is not dropping samples,
            # it simply has nowhere to send them.
            return
        if not self.running:
            # A session *is* open but the loop has ended -- the shutdown-flush
            # window. These samples were produced for a live session and will
            # never be sent, which is a loss and has to be counted like any
            # other. Returning silently here was the one uncounted path left.
            self._dropped[channel] += 1
            return
        queue = self._queues[channel]
        if len(queue) == queue.maxlen:
            # `deque(maxlen=...)` evicts silently, so count it here or the loss
            # is invisible -- which is the same failure as a poller producing no
            # rows and raising nothing.
            self._dropped[channel] += 1
        queue.append(sample)
        if len(queue) >= MAX_BATCH:
            self._wake.set()

    def submit_payload(self, payload: dict[str, Any]) -> None:
        """Split one sidecar tick into the channels the backend accepts.

        Shaping only -- fields are renamed and re-nested to match the ingest
        models, and not one number is converted. The `features`/`bands` blocks
        go up exactly as the sidecar produced them, on the sidecar's 0..100
        scale, and `signal_mapping` on the backend does the conversion for both
        this path and the poller.

        A camera tick can produce a face sample, a heart sample, both or
        neither: `build_camera_payload` omits a disabled channel rather than
        nulling it, so an absent block here means "switched off", which must not
        become a row.
        """
        # No guard on `running` here: `enqueue` decides, and it distinguishes
        # the two cases this cannot. No session at all is not a loss (nothing to
        # send it to); a session whose loop has ended is, and gets counted.
        # Returning early here skipped that accounting for exactly the samples
        # `enqueue` was fixed to count.
        if not self._token:
            return
        ts = payload.get("timestamp")
        device_id = payload.get("device_id")

        if payload.get("kind") != "camera":
            self.enqueue("cognitive", {
                "ts": ts,
                "features": payload.get("features") or {},
                "bands": payload.get("bands") or {},
                # Merged into `raw` by the mapper, which is where the fields
                # that explain a nulled measurement live.
                "raw": {"device_id": device_id,
                        "channels": payload.get("channels"),
                        "state": payload.get("state"),
                        "ingestion": payload.get("ingestion")},
            })

        face = payload.get("face")
        # `build_face_record` always returns a dict -- a rejected window is
        # reported as `emotion: None, rejected_by: "no_face"`, not as an absent
        # block. Enqueuing on the block's presence therefore wrote a row every
        # tick: ~14k all-null `face_signals` rows an hour, every one of them
        # counted as a sample by the aggregates and by `dominant_emotion`.
        #
        # A reading is an emotion. Untrusted ones still go -- `emotion_trusted`
        # is a column and fusion gates on it -- but a rejection is not a
        # measurement of anything and belongs in the sidecar's own state, which
        # is where `/api/v1/state` already reports it.
        if face and face.get("emotion") is not None:
            self.enqueue("face", {
                "ts": ts,
                "emotion": face.get("emotion"),
                "emotion_confidence": face.get("emotion_confidence"),
                # The endpoint calls this `emotion_trusted`; the sidecar block
                # calls it `trusted`. Renamed here rather than on the backend
                # because the backend has two `trusted` fields -- one per
                # channel -- and one flat name for both is how a face
                # confidence gets read as a heart confidence.
                "emotion_trusted": face.get("trusted"),
                "attention": face.get("attention"),
                "gaze_x": face.get("gaze_x"),
                "gaze_y": face.get("gaze_y"),
                "identity_confidence": face.get("identity_confidence"),
                "raw": {"device_id": device_id,
                        "rejected_by": face.get("rejected_by"),
                        "degraded": face.get("degraded"),
                        "ingestion": payload.get("ingestion")},
            })

        heart = payload.get("heart")
        # Two conditions, and `source` alone was not enough. `build_heart_record`
        # sets `source: "rppg"` unconditionally, including on `warming_up` and
        # `no_face` rejects, so every rejected window became a null-bpm row --
        # the same all-null flood as the face channel, latent only because
        # FACE_HEART_ENABLED is off. `source` is still required: consent is per
        # sensor, and a reading that cannot name its sensor cannot be
        # consent-checked.
        if heart and heart.get("source") and heart.get("bpm") is not None:
            self.enqueue("heart", {
                "ts": ts,
                "source": heart.get("source"),
                "heart_rate_bpm": heart.get("bpm"),
                "rmssd_ms": heart.get("rmssd_ms"),
                "sqi": heart.get("sqi"),
                "stress_score": heart.get("stress_score"),
                "stress_category": heart.get("stress_category"),
                "trusted": heart.get("trusted"),
                "raw": {"device_id": device_id,
                        "confidence": heart.get("confidence"),
                        "rejected_by": heart.get("rejected_by"),
                        "measured_fps": heart.get("measured_fps"),
                        "window_coverage": heart.get("window_coverage"),
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
                # starting another flush is what lets `stop()` await this task
                # instead of cancelling it mid-request.
                return
            # The wake event fires on a full batch, which during an outage is
            # every few samples -- so waking on it and flushing straight away
            # made the backoff decorative and retried a dead backend at the
            # sample rate. The deadline is the authority: the event may make a
            # flush happen sooner than the *idle interval*, never sooner than
            # the backoff says.
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

        Each channel is drained *inside* the loop, immediately before its own
        POST. Draining all three up front and re-raising on the first failure
        discarded the other two batches outright -- already popped, nothing put
        them back, and no counter anywhere recorded it. That is the silent loss
        this module exists to prevent, sitting in its own error path.
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
                    # Not restored. The request was in flight when it was
                    # cancelled, so the server may well have committed it, and
                    # re-posting into a table with no dedupe key duplicates
                    # every row. Unknown is recorded as unknown.
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
            # meant a lull in sampling during an outage -- a paused lesson, a
            # student between questions -- reset a 120 s backoff to 0 and
            # reported `last_error: null`, i.e. "recovered", on the strength of
            # having delivered no bytes to a backend that is still down.
            return
        self._backoff = 0.0
        self._retry_at = 0.0
        self._last_error = None

    def _take(self, channel: str) -> list[dict[str, Any]]:
        queue = self._queues[channel]
        return [queue.popleft() for _ in range(min(MAX_BATCH, len(queue)))]

    def _restore(self, channel: str, samples: list[dict[str, Any]]) -> None:
        """Return a failed batch to the front of its queue, counting the loss.

        Front, because these are the oldest and a session's samples must not be
        reordered relative to whatever arrived while the request was in flight.

        The count is the point: `extendleft` on a `deque(maxlen=...)` silently
        evicts from the *other* end, so restoring an old batch into a queue that
        filled during the request throws away the newest samples -- the reverse
        of the drop-oldest rule stated at the top of this module, and uncounted,
        which is worse than either.
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
            # inside. Treated as a failure so the samples go back on the queue
            # and the backoff widens, rather than being counted as delivered.
            raise RuntimeError("rate limited by backend (429)")
        response.raise_for_status()
        # Past this line the server has committed the rows, so nothing below may
        # raise: the caller restores a failed batch to the queue and re-posts it,
        # and `cognitive_signals` and `face_signals` have no dedupe key, so a
        # throw here would duplicate every row the request just wrote. Reading a
        # response body is exactly the sort of thing that fails late -- a short
        # read, a truncated body, a proxy returning HTML.
        try:
            body = response.json() if response.content else {}
            inserted = int(body.get("inserted", 0))
            dropped = int(body.get("dropped", 0))
            reason = body.get("reason", "unspecified")
        except Exception as exc:  # noqa: BLE001 - see above
            # Counted as delivered-but-unknown rather than not delivered. The
            # write happened; only our knowledge of how much of it landed did
            # not, and pretending otherwise is what causes the duplicate.
            logger.warning("push: %s batch committed but its receipt was "
                           "unreadable (%s); %d sample(s) unaccounted",
                           channel, exc, len(samples))
            self._unaccounted[channel] += len(samples)
            return
        # The server's own count, not `len(samples)`. It drops samples whose
        # sensor the student has not consented to, and reports how many -- a
        # client that counted what it *sent* would report success for a batch
        # that recorded nothing, which is the write-side version of a dashboard
        # that cannot tell "no data" from "zero".
        self._sent[channel] += inserted
        if dropped:
            logger.info("push: backend dropped %d %s sample(s): %s",
                        dropped, channel, reason)

    # ── introspection ────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        """What this client is doing, for `/api/v1/push/status`.

        Deliberately carries no token and no session-scoped secret: this is
        readable with the learner token, which in this deployment is shipped in
        client code and is not a secret either.
        """
        return {
            "running": self.running,
            "session_id": self._session_id,
            "queued": {c: len(self._queues[c]) for c in _CHANNELS},
            "recorded": dict(self._sent),
            # Named for what happened rather than for the queue: these were
            # produced by this sidecar and never reached the backend.
            "dropped_locally": dict(self._dropped),
            # Written, but we could not read how much. Not folded into
            # `recorded`, which would overstate it, nor into `dropped_locally`,
            # which would claim a loss that did not happen.
            "unaccounted": dict(self._unaccounted),
            "backoff_seconds": self._backoff,
            "last_error": self._last_error,
        }
