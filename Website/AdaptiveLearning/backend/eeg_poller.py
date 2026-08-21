"""Background pollers -- VERBOSE diagnostic version."""
from __future__ import annotations

import os
import threading
import time
from typing import Dict

import eeg_client
import signal_mapping

POLL_INTERVAL = 1.0 / max(0.5, float(os.getenv("EEG_POLL_HZ", "1")))

# Which ingestion path is live, stated rather than inferred.
#
# `pull` is this poller: it runs inside the backend and polls the sidecar over
# HTTP, which only works when both are on one machine (start.ps1). `push` is
# the sidecar POSTing to /api/signals/* with the student's own token -- what
# the camera forces, since a hosted backend has no route to a student's laptop.
#
# Explicit because the failure is silent otherwise: a poller that can't reach
# the sidecar looks exactly like a headband that never connected (no rows, no
# error, a live-looking session). So `push` makes start() refuse loudly instead
# of starting a thread that will never succeed.
#
# **It binds the poller only. The ingest endpoints are always open**, in both
# modes -- rejecting push under `pull` would break local dev, where a developer
# runs the poller and also posts a batch by hand.
#
# One consequence: a deployment left on `pull` whose sidecar also pushes writes
# `cognitive_signals` twice per sample. Rows are valid, counts are silently
# wrong -- unlike the heart path, there's no dedupe key to catch it here.
# `main.ingest_cognitive` warns when this happens.
INGEST_MODE = (os.getenv("INGEST_MODE", "pull") or "pull").strip().lower()
_VALID_MODES = ("pull", "push")
if INGEST_MODE not in _VALID_MODES:
    print(f"[eeg-poller] INGEST_MODE={INGEST_MODE!r} is not one of {_VALID_MODES}; "
          f"falling back to 'pull'", flush=True)
    INGEST_MODE = "pull"


class ConsentError(PermissionError):
    """Raised when EEG recording is not consented for this student.

    Distinct from PushModeError so the endpoint can answer 403 rather than 409:
    one says this deployment does not work that way, the other says this student
    said no. Rendering them the same would let a refusal read as a
    misconfiguration.
    """


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
        # Named _stop_event, not _stop: threading.Thread has its own private
        # _stop() method, called internally once the thread finishes. An
        # attribute named _stop would shadow it and break is_alive()/join().
        self._stop_event = threading.Event()
        self.last_ts    = None
        self.samples    = 0
        self.errors     = 0
        # Not 0.0: `start()` just read consent, so the first re-check should
        # be a full interval away, not immediately on loop 1.
        self._consent_checked_at = 0.0
        # Heart readings are tracked separately from EEG ones: they arrive on
        # a different cadence (one per ~10s window, held between recomputes),
        # so the EEG `last_ts` can't say whether a heart reading is new.
        self.last_heart_ts   = None
        self.heart_samples   = 0
        # Its own counter: folding heart failures into `errors` would make a
        # session with fine EEG writes but failing heart writes look like
        # general trouble.
        self.heart_errors    = 0
        self._heart_consent  = False
        self._heart_source   = None
        # None, not 0.0: time.monotonic()'s reference point is undefined, so
        # 0.0 doesn't mean "never checked" -- it could look like "checked at
        # boot" on a freshly started host.
        self._heart_checked_at = None

    def _may_record_heart(self, source: str) -> bool:
        """Whether this student consents to heart data from `source`.

        The poller writes with the **service-role** client, so neither RLS nor
        `/api/signals/heart`'s per-sample check applies to what it inserts:
        this call is the only thing standing between a withdrawal and a heart
        row under `INGEST_MODE=pull`.

        Per source, not per channel: one channel arrives from two sensors
        under two separate permissions, so a student who allowed the headband
        and refused the camera consented to `muse_optics` but not `rppg`.

        Re-read on a slow cadence, not per sample, so a mid-lesson withdrawal
        takes effect instead of being recorded against until the session ends.
        Unwired, it denies -- otherwise an unwired deployment looks the same
        as a wired one where the student said yes.
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
            # Fails closed, like `_consent` itself and unlike the reporting
            # helpers. A failed read must never be the reason a refusal stops
            # being enforced -- recording looks identical either way.
            self._heart_consent = False
            print(f"!!! [eeg-poller] heart consent check failed, not recording: {e}", flush=True)
        return self._heart_consent

    def _record_heart(self, data: dict, loops: int) -> None:
        """Write one heart reading, if the payload carries a new one.

        A rejected window is not a reading: the sidecar's block always has a
        `source` and reports refusal as `bpm: None` with a `rejected_by`, so
        gating on the block's mere presence would write a null row every tick.
        """
        heart = data.get("heart")
        if not heart or heart.get("bpm") is None:
            return
        source = heart.get("source")
        if not source:
            # Same rule as the mapper: consent is per sensor, so a reading
            # that can't name its sensor can't be checked at all.
            return
        # Falls back to the tick's stamp exactly as the mapper does, so the
        # value compared here matches what ends up in the row.
        heart_ts = heart.get("ts") or data.get("timestamp")
        if heart_ts == self.last_heart_ts:
            return

        # Claim the stamp only once this reading is finished with. A refusal
        # or an unmappable block is final, so claiming it stops re-logging the
        # same refusal every tick. A failed *write* is different: leaving the
        # stamp unclaimed lets the next tick retry it for free, instead of
        # turning one transient insert error into a permanently lost reading.
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
            # Upsert, matching `/api/signals/heart`. `heart_session_source_ts_key`
            # makes a repeat a no-op, so a deployment left on `pull` whose
            # sidecar also pushes doesn't double-count this channel.
            self.supabase.table("heart_signals").upsert(
                row, on_conflict="session_id,source,ts", ignore_duplicates=True
            ).execute()
            # Only now is the reading accounted for. See the note above.
            self.last_heart_ts = heart_ts
            self.heart_samples += 1
            if self.heart_samples <= 3 or self.heart_samples % 10 == 0:
                print(f"+++ [eeg-poller] HEART #{self.heart_samples} session={self.session_id[:8]} "
                      f"source={source} bpm={row.get('heart_rate_bpm')}", flush=True)
        except Exception as e:
            self.heart_errors += 1
            # Throttled like the success lines: the block is held for ~10
            # ticks, so an unreachable table would otherwise print ten
            # identical failure lines per reading.
            if self.heart_errors <= 3 or self.heart_errors % 10 == 0:
                print(f"!!! [eeg-poller] HEART INSERT FAILED #{self.heart_errors}: "
                      f"{type(e).__name__}: {e}", flush=True)

    def run(self):
        # Not 0.0 at construction: `start()` read consent moments ago, so
        # starting the clock here makes the first re-check a full interval
        # away rather than immediate.
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
            # Re-read consent on a slow cadence. A student may withdraw
            # mid-lesson, and a poller that only checked at start would keep
            # recording against that refusal until the session ended.
            now = time.monotonic()
            if now - self._consent_checked_at >= CONSENT_RECHECK_SECONDS:
                self._consent_checked_at = now
                try:
                    still_consented = _consent_check(self.user_id) if _consent_check else False
                except Exception as e:
                    # Fails closed, like `_consent` itself: a read error must
                    # not be the reason recording continues.
                    still_consented = False
                    print(f"!!! [eeg-poller] consent re-check failed, stopping: {e}", flush=True)
                if not still_consented:
                    # **This stops the heart channel too, deliberately.**
                    # `_record_heart` runs in this loop, so stopping the
                    # poller ends headband heart recording even though
                    # `headband_optical` is a separate consent -- a student
                    # who allows the headband but declines EEG records no
                    # heart rate under pull. Accepted because it errs safe:
                    # this records *less* than consent allows, never more.
                    # The push path is unaffected, since `/api/signals/heart`
                    # checks per source and never consults EEG consent.
                    print(f"<<< [eeg-poller] stopping session={self.session_id[:8]}: "
                          "recording no longer permitted", flush=True)
                    self._stop_event.set()
                    break

            data = eeg_client.get_state(self.device_id)
            if loops <= 3 or loops % 10 == 0:
                print(f">>> [eeg-poller] loop={loops} got_data={bool(data)} ts={data.get('timestamp') if data else None}", flush=True)

            # Outside the EEG freshness check below, deliberately: a heart
            # reading has its own cadence and stamp. Nesting it under "the EEG
            # timestamp moved" would drop it whenever EEG stalled.
            if data:
                self._record_heart(data, loops)

            if data and data.get("timestamp") and data["timestamp"] != self.last_ts:
                self.last_ts = data["timestamp"]
                # Lives in `signal_mapping.eeg_quality` so both ingestion
                # paths agree on it, rather than each having its own copy.
                verdict = signal_mapping.eeg_quality(data)
                row = eeg_client.map_eeg_to_cognitive(data, self.session_id, self.user_id)

                if row is None:
                    # no_signal: the headset is disconnected and the service
                    # reports zeroed scores. Nothing to record.
                    if loops <= 3 or loops % 10 == 0:
                        print(f">>> [eeg-poller] loop={loops} no_signal, skipping insert", flush=True)
                else:
                    if verdict == "contact_poor" and (loops <= 3 or loops % 10 == 0):
                        # The mapper already nulled the measurements; this is
                        # just the log. Keeping the row keeps the session's
                        # timeline intact for class_live's staleness check.
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
            # instead of waiting out the rest of POLL_INTERVAL.
            self._stop_event.wait(POLL_INTERVAL)

        try:
            # Each device_id (station) is its own sidecar stream; only another
            # poller *on the same device* keeps it alive. Hold _lock across
            # the whole check-then-stop sequence, not just the _active read,
            # so start() can't register a replacement in the gap between our
            # check and the actual stop_session() call -- otherwise a rapid
            # disconnect+reconnect could have this thread kill the stream
            # right after a new poller started depending on it.
            with _lock:
                # Deregister here: this is the one stop path nothing outside
                # this thread drives (a poller ending itself on withdrawn
                # consent has no external caller to do it). Inside the lock
                # and before the liveness scan below, so a dead self can't
                # count as a reason to keep the sidecar stream open.
                #
                # **`is self`, not just the key.** A disconnect/reconnect on
                # the same session id can leave this thread finishing a
                # `get_state` after `start()` already registered a replacement
                # under the same key. An unconditional pop would then
                # deregister the *live* replacement instead -- the exact race
                # the lock above exists to prevent.
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


# The heart channel re-reads consent on the same cadence as EEG
# (CONSENT_RECHECK_SECONDS, below). One constant, since both answer the same
# question: how long a withdrawal may keep being recorded against.

# `_consent` lives in `main`; importing it here would be a cycle, so `main`
# hands it in at import. Takes the sensor as well as the student, since one
# channel arrives from two sensors under two separate permissions -- a single
# boolean would let a headband's consent authorise a camera.
#
# No default, and None denies. An unwired deployment that assumed yes would
# record against refusals while looking like a wired one doing its job.
_heart_consent_check = None


def set_heart_consent_check(fn) -> None:
    """Register how this module asks whether a student consents to a heart sensor.

    `fn(user_id, source) -> bool`. Wired from `main` at import, against the
    same source map `/api/signals/heart` enforces, so the two ingestion paths
    can't disagree about one student.
    """
    global _heart_consent_check
    _heart_consent_check = fn


_active: Dict[str, _Poller] = {}
# Sessions already warned about for double-writing. Lives here, not in `main`,
# because `stop()` is what bounds it -- next to `_active`, under the same
# lock, discarded by the same call that ends the poller.
_warned_double_write: set[str] = set()
_lock = threading.Lock()

# Pre-claim pairing window. A station with no live poller is open to anyone,
# but a user has to scan and connect before their own poller can exist -- so
# without this, two users targeting the same unclaimed station could both read
# its live snapshot or issue control commands until one won the eventual
# eeg_poller.start() claim. Ownership has to begin at the first
# scan/connect/disconnect on an unclaimed device, not at that later claim.
#
# TTL'd rather than held indefinitely: an abandoned scan (tab closed mid-way)
# would otherwise brick a physical station for anyone else. 30s is a reasoned
# default, not a measured one -- long enough for a ~12s Bluetooth scan plus a
# connect attempt, short enough to reopen well within a lesson. It slides
# (refreshed on every reserve_device call from the same user), so an
# in-progress pairing doesn't expire out from under the user driving it.
RESERVATION_TTL_SECONDS = 30.0

# device_id -> (user_id, reserved_at, session_id | None). Same _lock as
# _active: a reservation and a poller-claim are two stages of one ownership
# question, and checking them under different locks would let one race past
# the other.
#
# session_id scopes a release to the pairing attempt that created it, and
# stays optional: the pairing flow can run before a session exists. None
# means "unattributable", and release_reservation treats that as
# release-everything for that user rather than as a reason to keep the entry
# -- an entry nobody can attribute must still be releasable, or an abandoned
# pairing outlives every way of clearing it early.
_reservations: Dict[str, tuple] = {}


def _reservation_owner(device_id: str) -> str | None:
    """The user_id currently holding device_id's reservation, or None.

    Caller holds _lock. An expired entry is dropped here, on read, rather than
    by a background sweep -- the dict is small and read often enough that
    lazy pruning is enough, and a timer thread would be one more thing
    stop_all has to join.
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
    """The user_id of the live poller currently holding device_id, or None.

    Caller holds _lock. Shared by start(), reserve_device() and
    can_use_device() -- all three answer the same question ("who, if anyone,
    owns this device via a live poller"), so they can't quietly disagree.
    """
    for p in _active.values():
        if p.device_id == device_id and p.is_alive():
            return p.user_id
    return None


def reserve_device(user_id: str, device_id: str,
                   session_id: str | None = None) -> bool:
    """Claim or refresh device_id's pre-claim reservation for user_id.

    session_id records *which* pairing attempt this is, so ending one session
    releases only the reservations it created. Optional, since the pairing
    endpoints are reachable without one.

    Called from the three control endpoints -- refresh/connect/disconnect --
    meaning "I am now pairing this station", not from read-only status/debug
    polling. A bystander's status check must not itself claim a station out
    from under someone about to pair it.

    Returns False when a *live poller* already owns the device, or another
    user's reservation on it hasn't expired. Check and claim happen under one
    lock, so two users can't both observe "free" before either commits.
    """
    with _lock:
        owner = _live_poller_owner(device_id)
        if owner is not None:
            return owner == user_id
        owner = _reservation_owner(device_id)
        if owner is not None and owner != user_id:
            return False
        # Refreshing our own entry keeps whatever session it was already
        # attributed to when this call cannot name one. Overwriting with None
        # would silently widen a scoped reservation back to the old
        # release-everything behaviour, and it would do so on the *refresh*
        # path -- the one that runs repeatedly during a pairing flow, so the
        # scoping would decay rather than fail, which is worse to notice.
        previous = _reservations.get(device_id)
        if session_id is None and previous is not None and previous[0] == user_id:
            session_id = previous[2] if len(previous) > 2 else None
        _reservations[device_id] = (user_id, time.monotonic(), session_id)
        return True


def release_reservation(user_id: str, device_id: str | None = None,
                        session_id: str | None = None) -> None:
    """Drop user_id's reservation, scoped as narrowly as the caller can manage.

    With device_id: drop only that device's entry, and only if it's still
    user_id's. Needed so a bridge error scanning station B doesn't release a
    *different* reservation the same user legitimately holds on station A.

    With session_id (no device_id): drop the reservations *this session*
    created, plus any the same user holds not attributed to any session. Used
    by stop() -- a user can genuinely hold more than one reservation, so
    closing one session must not release a different, live session's station
    early.

    **Unattributed entries are still dropped, deliberately.** A reservation
    with no session_id can never be named by a session close, so sparing it
    would mean an abandoned pairing outlives every way of clearing it early --
    a station held against everyone, which is worse than releasing one
    person's slightly too soon. It's also the compatibility path: an
    unupdated frontend sends no session_id and gets the old behaviour.

    Without either: drop every reservation user_id holds. Stays the default
    so a caller with no session in scope releases too much rather than too
    little -- releasing early can only free a station sooner, never deny one
    to its rightful holder.
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
    """Whether this backend has a live poller for that session.

    The actual condition, not `INGEST_MODE == "pull"`, which is only a proxy:
    a developer hand-posting a batch while no poller runs isn't
    double-writing, but the mode check alone would report it as such.
    """
    with _lock:
        p = _active.get(session_id)
        return bool(p and p.is_alive())


def _forget_warning(session_id: str) -> None:
    """Evict one session's warning record. Call from **every** stop path.

    This is what keeps the set bounded by concurrent sessions instead of by
    uptime -- miss a stop path and it grows for the life of the process.

    Caller holds `_lock`; `stop_all` is the exception and says why.
    """
    _warned_double_write.discard(session_id)


def claim_double_write_warning(session_id: str) -> bool:
    """True once per live-polled session, for logging a double write.

    Answers "is this being written by both paths, and have we not said so
    yet" in one step under one lock, so the check and the claim can't race
    into two log lines. False for a session with no live poller -- the
    hand-posted dev batch the ingest endpoint stays open for.
    """
    with _lock:
        p = _active.get(session_id)
        if not (p and p.is_alive()) or session_id in _warned_double_write:
            return False
        _warned_double_write.add(session_id)
        return True


# How often a running poller re-reads consent. Not per tick: at 4 Hz that's a
# database read four times a second per student for a question that changes
# at most a few times a session. Not never, either, or a withdrawal wouldn't
# take effect until the next session -- recording against a refusal the whole
# time, which is what consent exists to prevent.
CONSENT_RECHECK_SECONDS = 20.0

# Injected by `main` at start(). A callable, not an import: `_consent` lives
# in `main`, and importing it here would be a cycle.
_consent_check = None

# Optional. `_consent_check` only returns a bool, which can't say *why* --
# withdrawn consent and a closed school year both read as False. When wired,
# start() uses this for the message it raises instead of a fixed sentence
# that always blames consent.
_consent_reason_check = None


def set_consent_check(fn) -> None:
    """Register how this module asks whether a student still consents to EEG.

    Required before `start()` will run. No default, deliberately: "assume
    yes" would make an unwired deployment record against a refusal and look
    identical to a wired one.
    """
    global _consent_check
    _consent_check = fn


def set_consent_reason_check(fn) -> None:
    """Register how this module asks *why* recording is not permitted.

    Optional -- unwired, start() falls back to a consent-only message. `fn`
    takes a student id and returns a human-readable sentence.
    """
    global _consent_reason_check
    _consent_reason_check = fn


def start(supabase, user_id: str, session_id: str, device_id: str) -> dict:
    print(f"\n=== eeg_poller.start() called user={user_id[:8]} session={session_id[:8]} device={device_id}", flush=True)
    if INGEST_MODE == "push":
        # Not a silent no-op: {"running": False} would be indistinguishable
        # from a sidecar that's simply not up yet.
        raise PushModeError(
            "INGEST_MODE=push: the sidecar posts to /api/signals/* itself, so "
            "this backend does not poll it. Set INGEST_MODE=pull for a "
            "co-located deployment (start.ps1, dev, single-machine classroom)."
        )
    # Consent, before anything starts polling. The poller writes
    # `cognitive_signals` directly with the service-role client, which
    # bypasses RLS and the ingest endpoint, so under pull nothing else checks.
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
    with _lock:
        if session_id in _active and _active[session_id].is_alive():
            print(f"=== already running for this session", flush=True)
            return {"running": True, "already": True}
        # The sidecar is a single shared stream: two different users polling
        # it concurrently would each attribute the same physical device's
        # readings to their own session. Same-user restarts are fine, but a
        # live poller some other user owns must block us instead of quietly
        # sharing the stream.
        live_owner = _live_poller_owner(device_id)
        if live_owner is not None and live_owner != user_id:
            print(f"=== device claimed by another user (device={device_id})", flush=True)
            raise DeviceClaimedError(device_id)
        # A caller that skips the scan/connect UI and hits /start directly
        # with a known device_id must not be able to walk around someone
        # else's in-progress pairing on that station.
        owner = _reservation_owner(device_id)
        if owner is not None and owner != user_id:
            print(f"=== device reserved by another user, not yet polling (device={device_id})", flush=True)
            raise DeviceClaimedError(device_id)
        for sid, p in list(_active.items()):
            if p.user_id == user_id:
                print(f"=== stopping previous poller for same user (session={sid[:8]})", flush=True)
                p.stop()
                _active.pop(sid, None)
                # Every way a poller ends has to clear its warning record too,
                # or the set grows unbounded -- this is the path a student
                # hits just by starting a second session.
                _forget_warning(sid)
        p = _Poller(supabase, user_id, session_id, device_id)
        p.start()
        _active[session_id] = p
        # The reservation's job ends here: ownership has moved to something
        # stronger, so leaving the entry around just wastes a dict slot until
        # it expires.
        _reservations.pop(device_id, None)
        return {"running": True, "already": False}


def can_use_device(user_id: str, device_id: str) -> bool:
    """Whether user_id may read/control device_id's (station's) live stream.

    A station with a *live* poller belongs to that poller's user -- its
    stream is that student's in-progress biometric data, not shared classroom
    data -- so only the owner may touch it. A dead-but-not-yet-reaped poller
    doesn't count (`is_alive()`).

    Below that, a station someone else has *reserved* (mid-scan/connect, no
    poller yet) is theirs until the reservation expires. Only once neither a
    live poller nor an unexpired reservation names an owner is a station open
    to anyone -- what lets a user scan/pair a genuinely free station.
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
    """Stop this session's poller if one exists, and release its user's
    pre-claim reservation regardless of whether one did.

    user_id matters exactly when there's no poller to pop: a user who
    scanned or connected but gave up before reaching /start holds a
    reservation with nothing in _active for it, so releasing only a *popped
    poller's* user_id would silently skip that case. Every caller here
    already has the right user_id in scope -- the student ending their own
    session, or, for the teacher-facing stale-session sweep, the student the
    session belongs to.

    Reservations are released outside the lock this function otherwise
    holds, since release_reservation takes its own; p.stop() is fine to
    leave inside it, since _Poller.stop() only sets an Event and does no I/O.
    """
    with _lock:
        _forget_warning(session_id)
        p = _active.pop(session_id, None)
        if p:
            p.stop()
    release_for = user_id or (p.user_id if p else None)
    if release_for is not None:
        # Scoped to this session: a stale-session sweep must not release the
        # station a *different*, still-live session of the same user is
        # mid-pairing with.
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
    """Every poller thread still running, registered or not.

    threading.enumerate() rather than _active, deliberately: start() and
    stop_for_user() pop a poller from the registry the moment they signal it,
    while its thread runs on for one more loop check. So the registry doesn't
    name every thread that's still alive -- which is what matters when the
    question is "has anything got output left to print".
    """
    # Matched by class name, not isinstance: reloading this module rebinds
    # _Poller to a new class object, so threads started by the old one would
    # fail an isinstance check and report "nothing running" while still
    # running -- the one wrong answer this must never give.
    return [t for t in threading.enumerate() if type(t).__name__ == "_Poller"]


def stop_all(timeout: float = 5.0) -> int:
    """Stop and join every live poller. Called from main's lifespan shutdown.

    A poller is a daemon thread that prints teardown lines after its loop
    ends. Left to interpreter shutdown, one of those prints can land while
    the stdout lock is already held, which CPython treats as fatal, aborting
    the process with exit code 134 on an otherwise clean shutdown. Joining
    here means no poller outlives the server.

    Signals every poller before joining any, so the whole set costs one poll
    interval rather than one each. `timeout` is the budget for the whole
    join, shared via a single deadline -- a per-thread timeout would make the
    worst case N x timeout on a shutdown path something is waiting on.

    Returns how many pollers were signalled, for a caller that wants to log
    it; both call sites here ignore it.
    """
    pollers = live_pollers()
    for p in pollers:
        p.stop()
    deadline = time.monotonic() + timeout
    for p in pollers:
        p.join(timeout=max(0.0, deadline - time.monotonic()))
    with _lock:
        # Anything registered between live_pollers() above and this clear is
        # dropped while still running -- that needs a request to start a
        # poller mid-shutdown. The alternative, holding _lock across the
        # joins, would deadlock against the run loop's own _lock use on exit.
        _active.clear()
        # The warned-set goes with them: every session just ended, so the
        # whole thing is spent, and leaving it populated across a reload
        # would carry one process's records into the next.
        _warned_double_write.clear()
        # And any pre-claim reservation. A restarting process holds nothing --
        # every physical station should come back open to pairing. Tests rely
        # on this too, so a reservation from one test file can't leak into
        # the next through the autouse stop_all() teardown.
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
                    "session_id": sid,
                    "device_id":  p.device_id,
                    "samples":    p.samples,
                    "errors":     p.errors,
                    "last_ts":    p.last_ts,
                    # Counted separately from `samples`: a session recording
                    # EEG fine while its heart channel is refused, declined,
                    # or unmeasurable is normal, and one combined number
                    # would hide that.
                    "heart_samples": p.heart_samples,
                    "heart_errors": p.heart_errors,
                    "last_heart_ts": p.last_heart_ts,
                }
    return {"running": False}