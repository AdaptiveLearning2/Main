"""Background pollers -- VERBOSE diagnostic version."""
from __future__ import annotations

import os
import concurrent.futures
import threading
import time
from typing import Dict

import eeg_client
import signal_mapping

POLL_INTERVAL = 1.0 / max(0.5, float(os.getenv("EEG_POLL_HZ", "1")))

# Backoff cap (s) on empty reads; small because the consent re-check shares this loop.
POLL_BACKOFF_MAX_S = 5.0


def _poll_wait(consecutive_misses: int) -> float:
    """Seconds to wait before the next read after this many empty reads in a row."""
    if consecutive_misses <= 1:
        return POLL_INTERVAL
    return min(POLL_BACKOFF_MAX_S, POLL_INTERVAL * (2 ** (consecutive_misses - 1)))

# `pull` = this poller reads the sidecar; `push` = sidecar POSTs to /api/signals/*.
# Binds the poller only (start() refuses under push); ingest endpoints stay open.
INGEST_MODE = (os.getenv("INGEST_MODE", "pull") or "pull").strip().lower()
_VALID_MODES = ("pull", "push")
if INGEST_MODE not in _VALID_MODES:
    print(f"[eeg-poller] INGEST_MODE={INGEST_MODE!r} is not one of {_VALID_MODES}; "
          f"falling back to 'pull'", flush=True)
    INGEST_MODE = "pull"


class ConsentError(PermissionError):
    """Raised when EEG recording is not consented for this student (HTTP 403, not PushModeError's 409)."""


class PushModeError(RuntimeError):
    """Raised when something asks the poller to run under push ingestion (a config state, not a hardware failure)."""


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
        # Not `_stop`: that would shadow Thread._stop() and break is_alive()/join().
        self._stop_event = threading.Event()
        # Whether ticks are written. Off, it keeps the device stream up but stores nothing.
        self.recording  = True
        self.last_ts    = None
        self.samples    = 0
        self.errors     = 0
        # Empty reads in a row; drives the backoff and is exposed on status().
        self.consecutive_misses = 0
        self._consent_checked_at = 0.0  # reset in run()
        # Heart arrives on its own ~10 s cadence, so it gets its own stamp and counters.
        self.last_heart_ts   = None
        self.heart_samples   = 0
        self.heart_errors    = 0
        self._heart_consent  = False
        self._heart_source   = None
        # None, not 0.0: monotonic()'s reference point is undefined.
        self._heart_checked_at = None

    def _may_record_heart(self, source: str) -> bool:
        """Whether this student consents to heart data from `source` (per sensor, not per channel).

        The only consent gate on service-role heart writes under pull. Re-read every
        CONSENT_RECHECK_SECONDS; unwired or on error, it denies.
        """
        now = time.monotonic()
        if (self._heart_checked_at is not None and source == self._heart_source
                and now - self._heart_checked_at < CONSENT_RECHECK_SECONDS):
            return self._heart_consent
        self._heart_checked_at = now
        self._heart_source = source
        try:
            self._heart_consent = bool(
                _heart_consent_check(self.user_id, source)) if _heart_consent_check else False
        except Exception as e:
            # Fails closed.
            self._heart_consent = False
            print(f"!!! [eeg-poller] heart consent check failed, not recording: {e}", flush=True)
        return self._heart_consent

    def _record_heart(self, data: dict, loops: int) -> None:
        """Write one heart reading, if the payload carries a new one.

        A rejected window arrives as `bpm: None`, so gate on bpm, not the block.
        """
        heart = data.get("heart")
        if not heart or heart.get("bpm") is None:
            return
        source = heart.get("source")
        if not source:
            # Consent is per sensor; an unnamed sensor can't be checked.
            return
        # Same fallback as the mapper, so this matches the row's ts.
        heart_ts = heart.get("ts") or data.get("timestamp")
        if heart_ts == self.last_heart_ts:
            return

        # Claim the stamp on refusal or success; a failed write leaves it so the next tick retries.
        if not self._may_record_heart(source):
            self.last_heart_ts = heart_ts
            if loops <= 3 or loops % 10 == 0:
                print(f">>> [eeg-poller] loop={loops} heart {source} not consented, skipping", flush=True)
            return

        row = signal_mapping.map_heart_to_heart_signal(data, self.session_id, self.user_id)
        if row is None:
            self.last_heart_ts = heart_ts
            return
        try:
            # Upsert on heart_session_source_ts_key, so pull + push can't double-count.
            self.supabase.table("heart_signals").upsert(
                row, on_conflict="session_id,source,ts", ignore_duplicates=True
            ).execute()
            self.last_heart_ts = heart_ts
            self.heart_samples += 1
            if self.heart_samples <= 3 or self.heart_samples % 10 == 0:
                print(f"+++ [eeg-poller] HEART #{self.heart_samples} session={self.session_id[:8]} "
                      f"source={source} bpm={row.get('heart_rate_bpm')}", flush=True)
        except Exception as e:
            self.heart_errors += 1
            if self.heart_errors <= 3 or self.heart_errors % 10 == 0:
                print(f"!!! [eeg-poller] HEART INSERT FAILED #{self.heart_errors}: "
                      f"{type(e).__name__}: {e}", flush=True)

    def run(self):
        # start() just read consent, so the first re-check is a full interval away.
        self._consent_checked_at = time.monotonic()
        print(f"\n>>> [eeg-poller] STARTING user={self.user_id[:8]} session={self.session_id[:8]} device={self.device_id}", flush=True)
        try:
            r = eeg_client.start_session(self.device_id)
            print(f">>> [eeg-poller] sidecar session/start -> {r}", flush=True)
        except Exception as e:
            print(f"!!! [eeg-poller] could not start eeg session: {e}", flush=True)

        loops = 0
        while not self._stop_event.is_set():
            loops += 1
            # Re-read consent so a mid-lesson withdrawal takes effect.
            now = time.monotonic()
            if now - self._consent_checked_at >= CONSENT_RECHECK_SECONDS:
                self._consent_checked_at = now
                try:
                    still_consented = _consent_check(self.user_id) if _consent_check else False
                except Exception as e:
                    # Fails closed.
                    still_consented = False
                    print(f"!!! [eeg-poller] consent re-check failed, stopping: {e}", flush=True)
                if not still_consented:
                    # Stops pull-mode heart recording too, deliberately: errs toward recording less.
                    print(f"<<< [eeg-poller] stopping session={self.session_id[:8]}: "
                          "recording no longer permitted", flush=True)
                    self._stop_event.set()
                    break

            data = eeg_client.get_state(self.device_id)
            # None covers both unreachable and idle; the first real payload resets the backoff.
            self.consecutive_misses = 0 if data else self.consecutive_misses + 1
            if loops <= 3 or loops % 10 == 0:
                print(f">>> [eeg-poller] loop={loops} got_data={bool(data)} ts={data.get('timestamp') if data else None}", flush=True)

            if data and not self.recording:
                # Not recording: advance last_ts so arming starts from the live tick.
                if data.get("timestamp"):
                    self.last_ts = data["timestamp"]
                if loops <= 3 or loops % 10 == 0:
                    print(f">>> [eeg-poller] loop={loops} not recording (no question started), skipping", flush=True)
                self._stop_event.wait(_poll_wait(self.consecutive_misses))
                continue

            # Outside the EEG freshness check: heart has its own stamp and must not stall with EEG.
            if data:
                self._record_heart(data, loops)

            if data and data.get("timestamp") and data["timestamp"] != self.last_ts:
                self.last_ts = data["timestamp"]
                verdict = signal_mapping.eeg_quality(data)
                row = eeg_client.map_eeg_to_cognitive(data, self.session_id, self.user_id)

                if row is None:
                    # no_signal: headset disconnected, scores zeroed; nothing to record.
                    if loops <= 3 or loops % 10 == 0:
                        print(f">>> [eeg-poller] loop={loops} no_signal, skipping insert", flush=True)
                else:
                    if verdict == "contact_poor" and (loops <= 3 or loops % 10 == 0):
                        # Mapper nulled the measurements; the row keeps class_live's timeline intact.
                        print(f">>> [eeg-poller] loop={loops} poor contact, inserting null measurements", flush=True)
                    try:
                        # Upsert on cog_session_ts_key, so pull + push can't double-write.
                        res = self.supabase.table("cognitive_signals").upsert(
                            row, on_conflict="session_id,ts",
                            ignore_duplicates=True
                        ).execute()
                        # Count rows written; a deduped repeat writes none.
                        self.samples += len(res.data or [])
                        if self.samples <= 3 or self.samples % 10 == 0:
                            print(f"+++ [eeg-poller] INSERTED #{self.samples} session={self.session_id[:8]} focus={row.get('focus')}", flush=True)
                    except Exception as e:
                        self.errors += 1
                        print(f"!!! [eeg-poller] INSERT FAILED #{self.errors}: {type(e).__name__}: {e}", flush=True)
                        print(f"!!! [eeg-poller] row was: {row}", flush=True)
            # wait(), not sleep(), so stop() takes effect immediately.
            self._stop_event.wait(_poll_wait(self.consecutive_misses))

        try:
            # Hold _lock across check-then-stop so start() can't register a
            # replacement on this device in the gap.
            with _lock:
                # Deregister before the liveness scan; `is self` so a replacement
                # registered under the same session id isn't popped.
                if _active.get(self.session_id) is self:
                    _active.pop(self.session_id, None)
                    _forget_warning(self.session_id)
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


# Wired from `main` (import cycle otherwise); per (user, sensor). None denies.
_heart_consent_check = None


def set_heart_consent_check(fn) -> None:
    """Register `fn(user_id, source) -> bool`, the heart-sensor consent check."""
    global _heart_consent_check
    _heart_consent_check = fn


_active: Dict[str, _Poller] = {}
# Sessions already warned about double-writing; bounded by stop(), under _lock.
_warned_double_write: set[str] = set()
_lock = threading.Lock()

# Pre-claim pairing reservation, so ownership starts at the first scan/connect,
# not at start(). Sliding TTL (s): covers a ~12 s scan plus connect; unmeasured.
RESERVATION_TTL_SECONDS = 30.0

# device_id -> (user_id, reserved_at, session_id | None), under _lock like _active.
# session_id None = unattributable; release_reservation then releases it for that user.
_reservations: Dict[str, tuple] = {}


def _reservation_owner(device_id: str) -> str | None:
    """The user_id currently holding device_id's reservation, or None.

    Caller holds _lock. Expired entries are pruned here, on read.
    """
    entry = _reservations.get(device_id)
    if entry is None:
        return None
    user_id, reserved_at = entry[0], entry[1]
    if time.monotonic() - reserved_at >= RESERVATION_TTL_SECONDS:
        del _reservations[device_id]
        return None
    return user_id


def _live_poller_owner(device_id: str) -> str | None:
    """The user_id of the live poller currently holding device_id, or None. Caller holds _lock."""
    for p in _active.values():
        if p.device_id == device_id and p.is_alive():
            return p.user_id
    return None


def reserve_device(user_id: str, device_id: str,
                   session_id: str | None = None) -> bool:
    """Claim or refresh device_id's pre-claim reservation for user_id.

    Called from the control endpoints only, never from read-only status polling.
    False when another user's live poller or unexpired reservation holds it.
    """
    with _lock:
        owner = _live_poller_owner(device_id)
        if owner is not None:
            return owner == user_id
        owner = _reservation_owner(device_id)
        if owner is not None and owner != user_id:
            return False
        # A refresh with no session_id keeps the existing one rather than widening it to None.
        previous = _reservations.get(device_id)
        if session_id is None and previous is not None and previous[0] == user_id:
            session_id = previous[2] if len(previous) > 2 else None
        _reservations[device_id] = (user_id, time.monotonic(), session_id)
        return True


def release_reservation(user_id: str, device_id: str | None = None,
                        session_id: str | None = None) -> None:
    """Drop user_id's reservation, scoped as narrowly as the caller can manage.

    device_id: only that entry, if still user_id's. session_id: that session's
    entries plus unattributed ones (else they could never be cleared early).
    Neither: all of user_id's; releasing too much only frees a station sooner.
    """
    with _lock:
        if device_id is not None:
            entry = _reservations.get(device_id)
            if entry is not None and entry[0] == user_id:
                del _reservations[device_id]
            return
        for did, entry in list(_reservations.items()):
            if entry[0] != user_id:
                continue
            if session_id is not None:
                owner_session = entry[2] if len(entry) > 2 else None
                if owner_session is not None and owner_session != session_id:
                    continue
            del _reservations[did]


def is_polling(session_id: str) -> bool:
    """Whether this backend has a live poller for that session (not merely INGEST_MODE == "pull")."""
    with _lock:
        p = _active.get(session_id)
        return bool(p and p.is_alive())


def _forget_warning(session_id: str) -> None:
    """Evict one session's warning record. Call from every stop path, or the set grows unbounded.

    Caller holds `_lock`.
    """
    _warned_double_write.discard(session_id)


def claim_double_write_warning(session_id: str) -> bool:
    """True once per live-polled session, for logging a double write; False with no live poller.

    Check and claim share one lock so they can't race into two log lines.
    """
    with _lock:
        p = _active.get(session_id)
        if not (p and p.is_alive()) or session_id in _warned_double_write:
            return False
        _warned_double_write.add(session_id)
        return True


# Seconds between consent re-reads in a running poller (EEG and heart alike).
CONSENT_RECHECK_SECONDS = 20.0

# Wired from `main` (a direct import would be a cycle).
_consent_check = None

# Optional: says *why* recording is refused, for start()'s error message.
_consent_reason_check = None


def set_consent_check(fn) -> None:
    """Register the EEG consent check. Required: unwired, `start()` refuses."""
    global _consent_check
    _consent_check = fn


def set_consent_reason_check(fn) -> None:
    """Register `fn(user_id) -> str` explaining why recording is not permitted. Optional."""
    global _consent_reason_check
    _consent_reason_check = fn


def _arm_sidecar_baseline(device_id: str) -> None:
    """Tell the sidecar recording started, so its baseline skips the strap-adjusting period.

    Best effort: logs, never raises.
    """
    try:
        eeg_client.arm_session(device_id)
    except Exception as e:  # noqa: BLE001 -- the recording must not depend on this
        print(f"!!! [eeg-poller] could not arm the sidecar baseline (device={device_id}): "
              f"{type(e).__name__}: {e} -- scores will be relative to stream start", flush=True)


# Answer notifications run off the request thread on one worker with bounded
# pending, so a stalled sidecar drops notifications rather than holding threads.
NOTIFY_MAX_PENDING = 8
_notify_pool: concurrent.futures.ThreadPoolExecutor | None = None
_notify_pending = 0


def _deliver_answer(device_id: str, correct: bool, difficulty: str | None) -> bool:
    """The sidecar call, on the notify worker. Best effort: logs, never raises."""
    global _notify_pending
    try:
        eeg_client.report_answer(device_id, correct=correct, difficulty=difficulty)
    except Exception as e:  # noqa: BLE001 -- the recording must not depend on this
        print(f"[eeg-poller] could not report the answer to the sidecar "
              f"(device={device_id}): {type(e).__name__}: {e}", flush=True)
        return False
    finally:
        with _lock:
            _notify_pending -= 1
    return True


def notify_answer(session_id: str, correct: bool,
                  difficulty: str | None = None) -> "concurrent.futures.Future[bool] | None":
    """Tell the sidecar behind this session's poller that an answer was recorded.

    Returns the delivery's future, or None when nothing was sent (push, no
    poller, or queue full). Request-path callers must not wait on it.
    """
    global _notify_pool, _notify_pending
    if INGEST_MODE == "push":
        return None
    with _lock:
        poller = _active.get(session_id)
        if poller is None:
            return None
        if _notify_pending >= NOTIFY_MAX_PENDING:
            print(f"[eeg-poller] answer notification dropped: {_notify_pending} pending "
                  f"(device={poller.device_id})", flush=True)
            return None
        _notify_pending += 1
        if _notify_pool is None:
            _notify_pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="eeg-notify")
        pool = _notify_pool
        device_id = poller.device_id
    try:
        return pool.submit(_deliver_answer, device_id, correct, difficulty)
    except RuntimeError:
        # Pool shut down mid-request; undo the count.
        with _lock:
            _notify_pending -= 1
        return None


def start(supabase, user_id: str, session_id: str, device_id: str,
          record: bool = True) -> dict:
    """Start this session's poller, or arm/disarm one already running.

    `record=False` (Connect) streams without writing; `record=True` on the first
    question flips the live poller in place.
    """
    print(f"\n=== eeg_poller.start() called user={user_id[:8]} session={session_id[:8]} device={device_id} record={record}", flush=True)
    if INGEST_MODE == "push":
        # Raise, not {"running": False}, which would read as a sidecar not up yet.
        raise PushModeError(
            "INGEST_MODE=push: the sidecar posts to /api/signals/* itself, so "
            "this backend does not poll it. Set INGEST_MODE=pull for a "
            "co-located deployment (start.ps1, dev, single-machine classroom)."
        )
    # Service-role writes bypass RLS, so under pull this is the only consent check.
    if _consent_check is None:
        raise ConsentError(
            "eeg_poller has no consent check wired. Refusing to poll rather "
            "than assume consent -- see set_consent_check()."
        )
    if not _consent_check(user_id):
        reason = _consent_reason_check(user_id) if _consent_reason_check else None
        raise ConsentError(reason or (
            "EEG recording is switched off for this student. A parent can turn "
            "it back on in Settings."
        ))
    # The arm is a blocking POST, so it runs after _lock is released.
    result = _start_locked(supabase, user_id, session_id, device_id, record)
    if result.pop("_arm", False):
        _arm_sidecar_baseline(device_id)
    return result


def _start_locked(supabase, user_id: str, session_id: str, device_id: str,
                  record: bool) -> dict:
    with _lock:
        if session_id in _active and _active[session_id].is_alive():
            p = _active[session_id]
            flipped = p.recording != record
            if flipped:
                print(f"=== already running for this session; recording -> {record}", flush=True)
                p.recording = record
            else:
                print(f"=== already running for this session", flush=True)
            # Arm only on the flip to recording.
            return {"running": True, "already": True, "recording": record,
                    "_arm": flipped and record}
        # One stream per device: another user's live poller blocks us.
        live_owner = _live_poller_owner(device_id)
        if live_owner is not None and live_owner != user_id:
            print(f"=== device claimed by another user (device={device_id})", flush=True)
            raise DeviceClaimedError(device_id)
        # A direct /start must not bypass another user's in-progress pairing.
        owner = _reservation_owner(device_id)
        if owner is not None and owner != user_id:
            print(f"=== device reserved by another user, not yet polling (device={device_id})", flush=True)
            raise DeviceClaimedError(device_id)
        for sid, p in list(_active.items()):
            if p.user_id == user_id:
                print(f"=== stopping previous poller for same user (session={sid[:8]})", flush=True)
                p.stop()
                _active.pop(sid, None)
                _forget_warning(sid)
        p = _Poller(supabase, user_id, session_id, device_id)
        p.recording = record
        p.start()
        _active[session_id] = p
        # The live poller now owns the device.
        _reservations.pop(device_id, None)
        return {"running": True, "already": False, "recording": record, "_arm": record}


def can_use_device(user_id: str, device_id: str) -> bool:
    """Whether user_id may read/control device_id's (station's) live stream.

    Owned by a live poller's user, else by an unexpired reservation's; otherwise open.
    """
    with _lock:
        owner = _live_poller_owner(device_id)
        if owner is not None:
            return owner == user_id
        owner = _reservation_owner(device_id)
        if owner is not None:
            return owner == user_id
        return True


def stop(session_id: str, user_id: str | None = None) -> dict:
    """Stop this session's poller if any, and release its user's reservation either way.

    user_id covers a user who reserved but never reached /start (no poller to pop).
    release_reservation runs outside _lock, since it takes its own.
    """
    with _lock:
        _forget_warning(session_id)
        p = _active.pop(session_id, None)
        if p:
            p.stop()
    release_for = user_id or (p.user_id if p else None)
    if release_for is not None:
        # Scoped, so another live session of this user keeps its station.
        release_reservation(release_for, session_id=session_id)
    if p:
        return {"running": False, "samples": p.samples}
    return {"running": False, "samples": 0}


def stop_for_user(user_id: str) -> int:
    stopped = 0
    with _lock:
        for sid, p in list(_active.items()):
            if p.user_id == user_id:
                p.stop()
                _active.pop(sid, None)
                _forget_warning(sid)
                stopped += 1
    return stopped


def live_pollers() -> list[_Poller]:
    """Every poller thread still running, registered or not (popped threads run one more loop)."""
    # By class name, not isinstance: a module reload rebinds _Poller.
    return [t for t in threading.enumerate() if type(t).__name__ == "_Poller"]


def stop_all(timeout: float = 5.0) -> int:
    """Stop and join every live poller; returns how many were signalled.

    Joined because a daemon printing during interpreter shutdown aborts (exit 134).
    `timeout` is one deadline for the whole join, not per thread.
    """
    global _notify_pool
    pollers = live_pollers()
    for p in pollers:
        p.stop()
    deadline = time.monotonic() + timeout
    for p in pollers:
        p.join(timeout=max(0.0, deadline - time.monotonic()))
    with _lock:
        pool, _notify_pool = _notify_pool, None
    if pool is not None:
        # Joined too: the worker prints on failure.
        pool.shutdown(wait=True)
    # Don't reset `_notify_pending`: each submit's `finally` balances it to 0.
    with _lock:
        # Not holding _lock across the joins: that deadlocks with run()'s exit path.
        _active.clear()
        _warned_double_write.clear()
        # Tests rely on this so reservations can't leak between files.
        _reservations.clear()
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
                    # Running is not recording (paired, no question yet).
                    "recording":  p.recording,
                    "session_id": sid,
                    "device_id":  p.device_id,
                    "samples":    p.samples,
                    "errors":     p.errors,
                    "last_ts":    p.last_ts,
                    # Climbing = sidecar stopped answering.
                    "consecutive_misses": p.consecutive_misses,
                    "heart_samples": p.heart_samples,
                    "heart_errors": p.heart_errors,
                    "last_heart_ts": p.last_heart_ts,
                }
    return {"running": False}