"""Background pollers — VERBOSE diagnostic version."""
from __future__ import annotations

import os
import threading
import time
from typing import Dict

import eeg_client

POLL_INTERVAL = 1.0 / max(0.5, float(os.getenv("EEG_POLL_HZ", "1")))

# Which ingestion path is live, stated rather than inferred.
#
# `pull` is this poller: it runs inside the backend and polls the sidecar over
# HTTP, which works only because start.ps1 puts both on one machine. `push` is
# the sidecar POSTing to /api/signals/* with the student's own token, which is
# what the camera forces -- a hosted backend has no route to a student's laptop.
#
# Explicit because the failure is silent otherwise. A poller that cannot reach
# the sidecar looks exactly like a headband that never connected: no rows, no
# error, a live-looking session. Deploy the backend anywhere but the student's
# machine and every session degrades that way with nothing to read. So `push`
# makes start() refuse loudly instead of starting a thread that will never
# succeed, and the refusal names the setting.
#
# **It binds the poller only. The ingest endpoints are always open.**
# `/api/signals/*` accepts in either mode, deliberately -- rejecting the push
# endpoints under `pull` would break the mixed local-dev case where a developer
# runs the poller and posts a batch by hand.
#
# The consequence is worth stating rather than discovering: a deployment left on
# `pull` whose sidecar *also* pushes writes `cognitive_signals` twice for every
# sample. The rows are valid and the counts are wrong, silently -- and unlike
# the heart path there is no dedupe key to catch it, because
# `heart_session_source_ts_key` has no equivalent on a table that already holds
# production rows. `main.ingest_cognitive` warns when it is used under `pull`
# for that reason; `face_signals` and `heart_signals` are unexposed to this,
# since the poller never writes them.
INGEST_MODE = (os.getenv("INGEST_MODE", "pull") or "pull").strip().lower()
_VALID_MODES = ("pull", "push")
if INGEST_MODE not in _VALID_MODES:
    print(f"[eeg-poller] INGEST_MODE={INGEST_MODE!r} is not one of {_VALID_MODES}; "
          f"falling back to 'pull'", flush=True)
    INGEST_MODE = "pull"


class PushModeError(RuntimeError):
    """Raised when something asks the poller to run under push ingestion.

    A distinct type rather than a bare RuntimeError so the endpoint can answer
    with a specific message: this is a configuration statement, not a failure to
    reach hardware, and the two would otherwise reach a student identically.
    """


class DeviceClaimedError(Exception):
    """Raised when a device is already claimed by another user's live poller."""

    def __init__(self, device_id: str):
        self.device_id = device_id
        super().__init__(f"Device {device_id!r} is already in use by another user")


class _Poller(threading.Thread):
    def __init__(self, supabase, user_id: str, session_id: str, device_id: str):
        super().__init__(daemon=True)
        self.supabase   = supabase
        self.user_id    = user_id
        self.session_id = session_id
        self.device_id  = device_id
        # Named _stop_event, not _stop: threading.Thread itself uses a private
        # method named _stop() internally (called from _wait_for_tstate_lock,
        # which is_alive()/join() hit once the thread has actually finished).
        # An attribute here named _stop shadows that method, so the internal
        # call becomes "call this Event instance" -> TypeError: 'Event' object
        # is not callable, raised from is_alive()/join() on a finished thread.
        self._stop_event = threading.Event()
        self.last_ts    = None
        self.samples    = 0
        self.errors     = 0

    def run(self):
        print(f"\n>>> [eeg-poller] STARTING user={self.user_id[:8]} session={self.session_id[:8]} device={self.device_id}", flush=True)
        try:
            r = eeg_client.start_session(self.device_id)
            print(f">>> [eeg-poller] sidecar session/start -> {r}", flush=True)
        except Exception as e:
            print(f"!!! [eeg-poller] could not start eeg session: {e}", flush=True)

        loops = 0
        while not self._stop_event.is_set():
            loops += 1
            data = eeg_client.get_state(self.device_id)
            if loops <= 3 or loops % 10 == 0:
                print(f">>> [eeg-poller] loop={loops} got_data={bool(data)} ts={data.get('timestamp') if data else None}", flush=True)

            if data and data.get("timestamp") and data["timestamp"] != self.last_ts:
                self.last_ts = data["timestamp"]
                features = data.get("features") or {}
                signal_quality = features.get("signal_quality")
                # Only "poor" backed by the headband's own electrode data means
                # the electrodes are actually bad. A "poor" from the legacy
                # calm-based heuristic (older bridge with no HSI/IS_GOOD) says
                # nothing about contact -- it reports poor for any focused
                # student -- so treating it as bad contact would silently
                # disable data collection for an entire session.
                contact_poor = (
                    signal_quality == "poor"
                    and features.get("quality_basis") == "contact"
                )

                if signal_quality == "no_signal":
                    # No data at all this cycle (headset disconnected); the
                    # service reports zeroed scores. Nothing to record.
                    if loops <= 3 or loops % 10 == 0:
                        print(f">>> [eeg-poller] loop={loops} no_signal, skipping insert", flush=True)
                else:
                    row = eeg_client.map_eeg_to_cognitive(data, self.session_id, self.user_id)
                    if contact_poor:
                        # Keep the row so the session's timeline stays intact --
                        # "we were recording but couldn't measure" is different
                        # from "no session happened" -- but null the measurements
                        # rather than persisting numbers computed from electrodes
                        # the headband says are bad.
                        #
                        # Every consumer of these columns must therefore handle
                        # null. Checked at the time of writing: the teacher live
                        # view gauges render "-" for null, and
                        # LLM_topic_decider.get_session_signal_state filters
                        # `is not None` and bails when nothing usable remains.
                        #
                        # Side effect worth knowing: class_live derives session
                        # staleness from the newest cognitive_signals row's ts.
                        # Skipping rows entirely would let a badly-seated
                        # headband age a session out after STALE_AFTER_SEC;
                        # writing null rows keeps it alive, which is the
                        # intended behaviour -- the student is still working,
                        # we just can't measure them -- but it is a change.
                        for k in ("focus", "stress", "engagement",
                                  "alpha", "beta", "theta", "delta", "gamma"):
                            row[k] = None
                        if loops <= 3 or loops % 10 == 0:
                            print(f">>> [eeg-poller] loop={loops} poor contact, inserting null measurements", flush=True)
                    try:
                        res = self.supabase.table("cognitive_signals").insert(row).execute()
                        self.samples += 1
                        if self.samples <= 3 or self.samples % 10 == 0:
                            print(f"+++ [eeg-poller] INSERTED #{self.samples} session={self.session_id[:8]} focus={row.get('focus')}", flush=True)
                    except Exception as e:
                        self.errors += 1
                        print(f"!!! [eeg-poller] INSERT FAILED #{self.errors}: {type(e).__name__}: {e}", flush=True)
                        print(f"!!! [eeg-poller] row was: {row}", flush=True)
            # wait(), not sleep(): stop() then returns within microseconds
            # instead of up to POLL_INTERVAL later. The lines printed below run
            # after the loop, so a sleeping poller is one that still has output
            # to emit -- and if the interpreter starts shutting down first, that
            # print can hit an already-held stdout lock and abort the process.
            self._stop_event.wait(POLL_INTERVAL)

        try:
            # Each device_id (station) is its own independent sidecar stream now, so
            # only another poller *on the same device* keeps it alive. Hold _lock
            # across the whole check-then-stop sequence (not just the _active read)
            # so start() -- which also holds _lock while registering a new poller
            # and spawning its thread -- can't register a replacement in the gap
            # between our check and the actual stop_session() call. Without this, a
            # rapid disconnect+reconnect could have this thread kill the stream
            # right after a new poller started depending on it.
            with _lock:
                stream_still_needed = any(
                    p.is_alive() for p in _active.values() if p.device_id == self.device_id
                )
                if stream_still_needed:
                    print(f">>> [eeg-poller] another poller is active on device={self.device_id}, leaving sidecar stream running", flush=True)
                else:
                    r = eeg_client.stop_session(self.device_id)
                    print(f">>> [eeg-poller] sidecar session/stop -> {r}", flush=True)
        except Exception as e:
            print(f"!!! [eeg-poller] could not stop eeg session: {e}", flush=True)

        print(f"<<< [eeg-poller] STOPPED user={self.user_id[:8]} session={self.session_id[:8]} samples={self.samples} errors={self.errors}", flush=True)

    def stop(self):
        self._stop_event.set()


_active: Dict[str, _Poller] = {}
_lock = threading.Lock()


def start(supabase, user_id: str, session_id: str, device_id: str) -> dict:
    print(f"\n=== eeg_poller.start() called user={user_id[:8]} session={session_id[:8]} device={device_id}", flush=True)
    if INGEST_MODE == "push":
        # Not a silent no-op. Returning {"running": False} here would be
        # indistinguishable from a sidecar that is simply not up yet, which is
        # the confusion this mode setting exists to remove.
        raise PushModeError(
            "INGEST_MODE=push: the sidecar posts to /api/signals/* itself, so "
            "this backend does not poll it. Set INGEST_MODE=pull for a "
            "co-located deployment (start.ps1, dev, single-machine classroom)."
        )
    with _lock:
        if session_id in _active and _active[session_id].is_alive():
            print(f"=== already running for this session", flush=True)
            return {"running": True, "already": True}
        # The sidecar is a single shared stream: two different users polling
        # it concurrently would each attribute the *same* physical device's
        # readings to their own session. Same-user restarts (below) are fine
        # -- that's just the user switching sessions/devices -- but a live
        # poller some other user still owns must block us instead of quietly
        # sharing the stream.
        for sid, p in _active.items():
            if p.device_id == device_id and p.user_id != user_id and p.is_alive():
                print(f"=== device claimed by another user (session={sid[:8]})", flush=True)
                raise DeviceClaimedError(device_id)
        for sid, p in list(_active.items()):
            if p.user_id == user_id:
                print(f"=== stopping previous poller for same user (session={sid[:8]})", flush=True)
                p.stop()
                _active.pop(sid, None)
        p = _Poller(supabase, user_id, session_id, device_id)
        p.start()
        _active[session_id] = p
        return {"running": True, "already": False}


def can_use_device(user_id: str, device_id: str) -> bool:
    """Whether user_id may read/control device_id's (station's) live stream.

    A station with a *live* poller belongs to that poller's user -- its stream
    is that student's in-progress biometric data, not shared classroom data --
    so only the owner may touch it. An unclaimed station (no live poller) is
    open to anyone, which is what lets a user scan/pair a free station before
    their own poller has started. start()'s claim guard ensures at most one
    user ever holds a given device_id, so the first live match is decisive; a
    dead-but-not-yet-reaped poller doesn't count (is_alive()), matching the
    rest of this module.
    """
    with _lock:
        for p in _active.values():
            if p.device_id == device_id and p.is_alive():
                return p.user_id == user_id
    return True


def stop(session_id: str) -> dict:
    with _lock:
        p = _active.pop(session_id, None)
        if p:
            p.stop()
            return {"running": False, "samples": p.samples}
        return {"running": False, "samples": 0}


def stop_for_user(user_id: str) -> int:
    stopped = 0
    with _lock:
        for sid, p in list(_active.items()):
            if p.user_id == user_id:
                p.stop()
                _active.pop(sid, None)
                stopped += 1
    return stopped


def live_pollers() -> list[_Poller]:
    """Every poller thread still running, registered or not.

    threading.enumerate() rather than _active, deliberately: start() and
    stop_for_user() pop a poller out of the registry the moment they signal it,
    while its thread runs on for one more loop check. The registry therefore
    does not name every thread that is still alive, which is exactly the set
    that matters when the question is "has anything got output left to print".
    """
    # Matched by class name, not isinstance: reloading this module rebinds
    # _Poller to a new class object, and threads started by the old one would
    # then fail an isinstance check -- reporting "nothing running" while they
    # are still running, which is the one wrong answer this must never give.
    return [t for t in threading.enumerate() if type(t).__name__ == "_Poller"]


def stop_all(timeout: float = 5.0) -> int:
    """Stop and join every live poller. Called from main's lifespan shutdown.

    A poller is a daemon thread that prints, including a block of teardown
    lines after its loop ends. Left to interpreter shutdown, one of those
    prints can land while the stdout BufferedWriter lock is already held, which
    CPython treats as fatal ("_enter_buffered_busy: could not acquire lock
    for <_io.BufferedWriter name='<stdout>'>"), aborting the process with exit
    code 134 on a clean shutdown. Joining here means no poller outlives the
    server.

    Signals every poller before joining any, so the whole set costs one poll
    interval rather than one each. `timeout` is likewise the budget for the
    whole join, shared across the threads via a single deadline -- a per-thread
    timeout would make the worst case N x timeout, and this runs on a shutdown
    path where something is waiting on it.

    Returns how many pollers were signalled -- diagnostics for a caller that
    wants to log it; both call sites here ignore it.
    """
    pollers = live_pollers()
    for p in pollers:
        p.stop()
    deadline = time.monotonic() + timeout
    for p in pollers:
        p.join(timeout=max(0.0, deadline - time.monotonic()))
    with _lock:
        # Anything registered between live_pollers() above and this clear is
        # dropped while still running. That needs a request to start a poller
        # mid-shutdown, after the server stopped accepting them -- and the
        # alternative, holding _lock across the joins, would deadlock against
        # the run loop's own _lock use on the way out.
        _active.clear()
    still_running = [p.session_id[:8] for p in live_pollers()]
    if still_running:
        print(f"!!! [eeg-poller] did not stop within {timeout}s: {still_running}", flush=True)
    return len(pollers)


def status(user_id: str) -> dict:
    with _lock:
        for sid, p in _active.items():
            if p.user_id == user_id and p.is_alive():
                return {
                    "running":    True,
                    "session_id": sid,
                    "device_id":  p.device_id,
                    "samples":    p.samples,
                    "errors":     p.errors,
                    "last_ts":    p.last_ts,
                }
    return {"running": False}