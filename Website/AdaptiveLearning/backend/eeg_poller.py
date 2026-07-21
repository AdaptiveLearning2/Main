"""Background pollers — VERBOSE diagnostic version."""
from __future__ import annotations

import os
import threading
import time
from typing import Dict

import eeg_client

POLL_INTERVAL = 1.0 / max(0.5, float(os.getenv("EEG_POLL_HZ", "1")))


class _Poller(threading.Thread):
    def __init__(self, supabase, user_id: str, session_id: str):
        super().__init__(daemon=True)
        self.supabase   = supabase
        self.user_id    = user_id
        self.session_id = session_id
        self._stop      = threading.Event()
        self.last_ts    = None
        self.samples    = 0
        self.errors     = 0

    def run(self):
        print(f"\n>>> [eeg-poller] STARTING user={self.user_id[:8]} session={self.session_id[:8]}", flush=True)
        try:
            r = eeg_client.start_session()
            print(f">>> [eeg-poller] sidecar session/start -> {r}", flush=True)
        except Exception as e:
            print(f"!!! [eeg-poller] could not start eeg session: {e}", flush=True)

        loops = 0
        while not self._stop.is_set():
            loops += 1
            data = eeg_client.get_state()
            if loops <= 3 or loops % 10 == 0:
                print(f">>> [eeg-poller] loop={loops} got_data={bool(data)} ts={data.get('timestamp') if data else None}", flush=True)

            if data and data.get("timestamp") and data["timestamp"] != self.last_ts:
                self.last_ts = data["timestamp"]
                signal_quality = (data.get("features") or {}).get("signal_quality")
                if signal_quality in ("no_signal", "poor"):
                    # no_signal: the EEG service has no data at all this cycle
                    #   (headset disconnected), and reports zeroed scores.
                    # poor: data is flowing but libMuse reports the electrode fit
                    #   or the data itself as bad. focus/calm are still computed
                    #   from it and look like confident numbers -- a headband with
                    #   two dead electrodes reports "84.8% focus" -- so persisting
                    #   them would quietly corrupt the teacher/parent analytics
                    #   built on this table.
                    # Either way, don't write measurements we know are wrong.
                    if loops <= 3 or loops % 10 == 0:
                        print(f">>> [eeg-poller] loop={loops} {signal_quality}, skipping insert", flush=True)
                else:
                    row = eeg_client.map_eeg_to_cognitive(data, self.session_id, self.user_id)
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
            # The EEGResearch sidecar is a single shared stream, not one per
            # session. Hold _lock across the whole check-then-stop sequence
            # (not just the _active read) so start() -- which also holds
            # _lock while registering a new poller and spawning its thread
            # -- can't register a replacement in the gap between our check
            # and the actual stop_session() call. Without this, a rapid
            # disconnect+reconnect could have this thread kill the stream
            # right after a new poller started depending on it.
            with _lock:
                stream_still_needed = any(p.is_alive() for p in _active.values())
                if stream_still_needed:
                    print(">>> [eeg-poller] another poller is active, leaving sidecar stream running", flush=True)
                else:
                    r = eeg_client.stop_session()
                    print(f">>> [eeg-poller] sidecar session/stop -> {r}", flush=True)
        except Exception as e:
            print(f"!!! [eeg-poller] could not stop eeg session: {e}", flush=True)

        print(f"<<< [eeg-poller] STOPPED user={self.user_id[:8]} session={self.session_id[:8]} samples={self.samples} errors={self.errors}", flush=True)

    def stop(self):
        self._stop.set()


_active: Dict[str, _Poller] = {}
_lock = threading.Lock()


def start(supabase, user_id: str, session_id: str) -> dict:
    print(f"\n=== eeg_poller.start() called user={user_id[:8]} session={session_id[:8]}", flush=True)
    with _lock:
        if session_id in _active and _active[session_id].is_alive():
            print(f"=== already running for this session", flush=True)
            return {"running": True, "already": True}
        for sid, p in list(_active.items()):
            if p.user_id == user_id:
                print(f"=== stopping previous poller for same user (session={sid[:8]})", flush=True)
                p.stop()
                _active.pop(sid, None)
        p = _Poller(supabase, user_id, session_id)
        p.start()
        _active[session_id] = p
        return {"running": True, "already": False}


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
                    "samples":    p.samples,
                    "errors":     p.errors,
                    "last_ts":    p.last_ts,
                }
    return {"running": False}