"""Background pollers — VERBOSE diagnostic version."""
from __future__ import annotations

import os
import threading
import time
from typing import Dict

import eeg_client

POLL_INTERVAL = 1.0 / max(0.5, float(os.getenv("EEG_POLL_HZ", "1")))


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
                        # LLM_topic_decider.get_session_eeg_state filters
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
            time.sleep(POLL_INTERVAL)

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