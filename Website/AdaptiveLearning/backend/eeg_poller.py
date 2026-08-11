"""Background pollers — VERBOSE diagnostic version."""
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
        # First re-check is one interval away, not immediate: `start()` has
        # just read consent, and initialising this to 0.0 made loop 1 read it
        # again milliseconds later.
        self._consent_checked_at = 0.0
        # Heart readings are tracked separately from EEG ones. They arrive on a
        # different cadence -- one per ~10s window, held on the payload in
        # between -- so the EEG `last_ts`, which changes every tick, cannot say
        # whether a heart reading has already been recorded.
        self.last_heart_ts   = None
        self.heart_samples   = 0
        # Its own counter, for the same reason `heart_samples` is separate from
        # `samples`: folding heart failures into `errors` makes a session whose
        # EEG writes are fine but whose heart writes all fail look like general
        # trouble, which is precisely the distinction splitting the counts was
        # meant to preserve.
        self.heart_errors    = 0
        self._heart_consent  = False
        self._heart_source   = None
        # None, not 0.0. time.monotonic()'s reference point is undefined, so
        # 0.0 is not a "never checked" sentinel -- on a host that has been up
        # for less than the interval it is a claim that we checked at boot.
        self._heart_checked_at = None

    def _may_record_heart(self, source: str) -> bool:
        """Whether this student consents to heart data from `source`.

        The poller writes with the **service-role** client, so neither RLS nor
        `/api/signals/heart`'s per-sample check applies to anything it inserts:
        this call is the only thing standing between a withdrawal and a heart
        row under `INGEST_MODE=pull`.

        Per source, not per channel, because one channel arrives from two
        sensors under two separate permissions -- a student who allowed the
        headband and refused the camera has consented to `muse_optics` and not
        to `rppg`, and a heart-rate failover must never open a webcam they
        declined.

        Re-read on a slow cadence rather than per sample: a lesson outlives a
        change of mind, and a poller that only asked at the start would keep
        recording against a mid-lesson withdrawal until the session ended.
        Unwired, it denies -- a deployment nobody wired the check into is
        otherwise indistinguishable from one whose student said yes.
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

        The headband is the primary heart source and nothing wrote
        `heart_signals` from this path at all, so a pull deployment recorded no
        heart rate while an identical push one recorded it fully -- the same
        two-deployments-diverging failure the shared mapper exists to prevent,
        one layer above it.

        A rejected window is not a reading: the sidecar's block always has a
        `source` and reports refusal as `bpm: None` with a `rejected_by`, so
        gating on the block's presence would write a null row per tick.
        """
        heart = data.get("heart")
        if not heart or heart.get("bpm") is None:
            return
        source = heart.get("source")
        if not source:
            # Same rule as the mapper's: consent is decided per sensor, so a
            # reading that cannot name its sensor cannot be checked at all.
            return
        # Falls back to the tick's stamp exactly as the mapper does, so the
        # value compared here is the one that ends up in the row.
        heart_ts = heart.get("ts") or data.get("timestamp")
        if heart_ts == self.last_heart_ts:
            return

        # The stamp is claimed at each point where this reading is *finished
        # with*, and deliberately not before. A refusal and an unmappable block
        # are both final -- nothing about the next tick would change them, and
        # re-deciding them 40 times over would re-log the refusal every tick.
        # A failed write is the opposite: the block sits on the payload for
        # ~40 more ticks, so leaving the stamp unclaimed retries it for free on
        # the next one. Claiming it up front turned one transient insert error
        # into a permanently lost reading.
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
            # makes a repeat a no-op, which is what keeps a deployment left on
            # `pull` while its sidecar also pushes from double-counting this
            # channel -- the protection `cognitive_signals` has no key for.
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
            # Throttled like the success lines, because the retry that makes a
            # transient failure survivable also makes a *persistent* one repeat:
            # the block is held for ~10 ticks, so an unreachable table would
            # otherwise print ten identical lines per reading, for the session.
            if self.heart_errors <= 3 or self.heart_errors % 10 == 0:
                print(f"!!! [eeg-poller] HEART INSERT FAILED #{self.heart_errors}: "
                      f"{type(e).__name__}: {e}", flush=True)

    def run(self):
        # Not 0.0 at construction: `start()` read consent moments ago, and
        # starting the clock here means the first re-check is a full interval
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
                    # Fails closed, like `_consent` itself. A read error must
                    # not be the reason recording continues.
                    still_consented = False
                    print(f"!!! [eeg-poller] consent re-check failed, stopping: {e}", flush=True)
                if not still_consented:
                    # **This stops the heart channel too, and that is a
                    # decision rather than an oversight.** `_record_heart` runs
                    # in this loop, so killing the poller ends headband heart
                    # recording even though `headband_optical` is consented
                    # separately -- and `start()` refuses without EEG consent,
                    # so on the pull path a student who allows the headband and
                    # declines EEG records no heart rate at all.
                    #
                    # Accepted because it errs the safe way: the pull path
                    # records *less* than consent allows, never more. Undoing it
                    # means a poller that keeps running with the cognitive write
                    # switched off, which is a session that reports EEG stopped
                    # while still holding the device -- a feature, not a fix.
                    # The push path is unaffected: `/api/signals/heart` checks
                    # per source and never consults EEG consent.
                    print(f"<<< [eeg-poller] consent withdrawn for session={self.session_id[:8]}, stopping", flush=True)
                    self._stop_event.set()
                    break

            data = eeg_client.get_state(self.device_id)
            if loops <= 3 or loops % 10 == 0:
                print(f">>> [eeg-poller] loop={loops} got_data={bool(data)} ts={data.get('timestamp') if data else None}", flush=True)

            # Outside the EEG freshness check below, deliberately. A heart
            # reading covers a 25s window and is held on the payload between
            # recomputes, so it has its own cadence and its own stamp; nesting
            # it under "the EEG timestamp moved" would tie it to a channel it
            # does not come from and drop it whenever EEG stalled.
            if data:
                self._record_heart(data, loops)

            if data and data.get("timestamp") and data["timestamp"] != self.last_ts:
                self.last_ts = data["timestamp"]
                # Both rules now live in `signal_mapping.eeg_quality`, which
                # the push path reads too. Inline here, they were pull-only: the
                # same unworn headband wrote nothing through this loop and a
                # zeroed row per tick through /api/signals/cognitive.
                verdict = signal_mapping.eeg_quality(data)
                row = eeg_client.map_eeg_to_cognitive(data, self.session_id, self.user_id)

                if row is None:
                    # no_signal: the headset is disconnected and the service
                    # reports zeroed scores. Nothing to record.
                    if loops <= 3 or loops % 10 == 0:
                        print(f">>> [eeg-poller] loop={loops} no_signal, skipping insert", flush=True)
                else:
                    if verdict == "contact_poor" and (loops <= 3 or loops % 10 == 0):
                        # The mapper has already nulled the measurements; this is
                        # only the log. Keeping the row keeps the session's
                        # timeline intact -- see the mapper for why that matters
                        # to class_live's staleness check.
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
                # Deregister *here*, in the one stop path that nothing outside
                # this thread drives. `stop`, `stop_for_user`, `start`'s
                # same-user replacement and `stop_all` all pop and forget; a
                # poller that ends itself -- consent withdrawn -- had no caller
                # to do it, so the entry and the warning record outlived it.
                # That is the fifth stop path, and `_forget_warning`'s docstring
                # says every one of them. Inside the lock this block already
                # takes, and before the liveness scan below, so a dead self
                # cannot count as a reason to keep the sidecar stream open.
                #
                # **`is self`, not just the key.** A disconnect/reconnect on the
                # same session id leaves this thread finishing a `get_state`
                # while `start()` has already registered a replacement under the
                # same key -- the normal case, since that read is real HTTP. An
                # unconditional pop then deregisters the *live* poller: it keeps
                # writing, `stop()` can no longer reach it, `can_use_device`
                # hands its device to another user, and the scan below stops the
                # sidecar stream out from under it. That is the very race the
                # lock three lines up exists to prevent, reintroduced by the fix
                # for the self-termination path.
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
# (CONSENT_RECHECK_SECONDS, below). One constant, because the question it
# answers is the same one -- how long may a withdrawal keep being recorded
# against -- and two numbers would imply a difference in that answer that
# nothing here intends.

# `_consent` lives in `main`, and importing it here would be a cycle, so `main`
# hands it in at import. Takes the sensor as well as the student: one channel
# arrives from two sensors under two separate permissions, and a single boolean
# would let a headband's consent authorise a camera.
#
# No default, and None denies. An unwired deployment that assumed yes would
# record against refusals and look exactly like a wired one doing its job.
_heart_consent_check = None


def set_heart_consent_check(fn) -> None:
    """Register how this module asks whether a student consents to a heart sensor.

    `fn(user_id, source) -> bool`. Wired from `main` at import, against the same
    `_consent` read and the same source map `/api/signals/heart` enforces, so
    the two ingestion paths cannot come to different answers about one student.
    """
    global _heart_consent_check
    _heart_consent_check = fn


_active: Dict[str, _Poller] = {}
# Sessions already warned about for double-writing. Lives here rather than in
# `main` because `stop()` is what bounds it: a set kept over there had nothing
# to evict it and grew with every session ever double-written for the life of
# the process -- "bounded by concurrent sessions" was what the comment claimed
# and the opposite of what it did. Next to `_active`, under the same lock, and
# discarded by the same call that ends the poller.
_warned_double_write: set[str] = set()
_lock = threading.Lock()

# Pre-claim pairing window (#34). can_use_device's own docstring used to say a
# station with no live poller is "open to anyone" -- true and necessary, since
# a user has to scan and connect before their own poller can exist, but it left
# a gap: two users targeting the same unclaimed station could each read its
# live snapshot or issue control commands (scan/connect/disconnect) until one
# of them won the eeg_poller.start() claim. That claim is the *end* of
# pairing, not the start of it, so ownership has to begin earlier -- at the
# first scan/connect/disconnect a user makes on an unclaimed device.
#
# TTL'd rather than held indefinitely: an abandoned scan (tab closed mid-way,
# /start never called) would otherwise brick a physical station for anyone
# else, which is exactly the contention pairing exists to resolve. 30s is a
# reasoned default, not a measured one -- long enough to cover a ~12s
# Bluetooth scan (PR #8) plus a subsequent connect attempt, short enough that
# an abandoned pairing reopens the station well within a lesson. It is
# sliding (refreshed on every reserve_device call from the same user), so an
# active pairing flow that runs longer than one window doesn't expire out
# from under the user still driving it.
RESERVATION_TTL_SECONDS = 30.0

# device_id -> (user_id, reserved_at). Same _lock as _active: a reservation
# and a poller-claim are two stages of the same ownership question, and
# checking them under different locks would let one race past the other.
_reservations: Dict[str, tuple] = {}


def _reservation_owner(device_id: str) -> str | None:
    """The user_id currently holding device_id's reservation, or None.

    Caller holds _lock. An expired entry is dropped here rather than by a
    background sweep -- the registry has one entry per physical station and
    is read on every can_use_device call, so pruning lazily on read is
    enough; a timer thread would be one more thing stop_all has to join, for
    a dict this small.
    """
    entry = _reservations.get(device_id)
    if entry is None:
        return None
    user_id, reserved_at = entry
    if time.monotonic() - reserved_at >= RESERVATION_TTL_SECONDS:
        del _reservations[device_id]
        return None
    return user_id


def _live_poller_owner(device_id: str) -> str | None:
    """The user_id of the live poller currently holding device_id, or None.

    Caller holds _lock. Shared by start(), reserve_device() and
    can_use_device() -- all three answer the same question, "who, if anyone,
    currently owns this device via a live poller", and previously each
    carried its own copy of the scan. Three copies is how can_use_device and
    start() could have quietly disagreed about a claim; a fourth copy for
    reserve_device would only have made that more likely, not less.
    """
    for p in _active.values():
        if p.device_id == device_id and p.is_alive():
            return p.user_id
    return None


def reserve_device(user_id: str, device_id: str) -> bool:
    """Claim or refresh device_id's pre-claim reservation for user_id.

    Called from the three control endpoints -- refresh/connect/disconnect --
    the actions that mean "I am now pairing this station", rather than from
    read-only status/debug polling. A bystander's periodic status check must
    not itself claim a station out from under someone about to pair it.

    Returns False when a *live poller* already owns the device (the
    stronger, later-stage claim start() enforces) or another user's
    reservation on it hasn't expired. The check and the claim happen under
    one lock so two users calling this for the same unclaimed device can't
    both observe "free" before either commits -- the exact race #34 exists to
    close.
    """
    with _lock:
        owner = _live_poller_owner(device_id)
        if owner is not None:
            return owner == user_id
        owner = _reservation_owner(device_id)
        if owner is not None and owner != user_id:
            return False
        _reservations[device_id] = (user_id, time.monotonic())
        return True


def release_reservation(user_id: str) -> None:
    """Drop every reservation user_id currently holds.

    Called from /api/eeg/stop regardless of whether a live poller existed for
    the session -- a user who scanned/connected but never reached /start
    still holds a pre-claim reservation, and /stop is the signal they're done
    with the station. Scoped by user_id rather than a device_id the caller may
    not know (EegSessionRequest carries session_id, not device_id): one call
    clears whatever this user is holding, matching stop_for_user's scoping.
    """
    with _lock:
        for device_id, entry in list(_reservations.items()):
            if entry[0] == user_id:
                del _reservations[device_id]


def is_polling(session_id: str) -> bool:
    """Whether this backend has a live poller for that session.

    The actual condition, rather than `INGEST_MODE == "pull"`, which is only a
    proxy for it. A developer hand-posting a batch while no poller runs is not
    double-writing, and treating the mode as the answer reports them anyway.
    """
    with _lock:
        p = _active.get(session_id)
        return bool(p and p.is_alive())


def _forget_warning(session_id: str) -> None:
    """Evict one session's warning record. Call from **every** stop path.

    This is what makes the set bounded by concurrent sessions rather than by
    uptime, which is what the comment beside it claims. `stop()` had it and the
    other two did not, so the claim was true only for the path someone had
    thought about -- the same shape as the bug that moved this set out of `main`
    in the first place.

    Caller holds `_lock`; `stop_all` is the exception and says why.
    """
    _warned_double_write.discard(session_id)


def claim_double_write_warning(session_id: str) -> bool:
    """True once per live-polled session, for logging a double write.

    Answers "is this session being written by both paths, and have we not said
    so yet" in one step under one lock, so the check and the claim cannot race
    into two log lines. Returns False for a session with no live poller, which
    is the hand-posted dev batch the ingest endpoint stays open for.
    """
    with _lock:
        p = _active.get(session_id)
        if not (p and p.is_alive()) or session_id in _warned_double_write:
            return False
        _warned_double_write.add(session_id)
        return True


# How often a running poller re-reads consent. Not per tick: at 4 Hz that is a
# database read four times a second per student, to answer a question that
# changes at most a few times a session. Not never, either -- a withdrawal that
# only takes effect at the next session means recording against a refusal for as
# long as the current one runs, which is the thing consent exists to prevent.
CONSENT_RECHECK_SECONDS = 20.0

# Injected by `main` at start(). A callable rather than an import, because
# `_consent` lives in `main` and importing it here would be a cycle -- and
# because it keeps the dependency pointing one way, `main` -> `eeg_poller`.
_consent_check = None


def set_consent_check(fn) -> None:
    """Register how this module asks whether a student still consents to EEG.

    Required before `start()` will run. There is deliberately no default: a
    fallback of "assume yes" would make an unwired deployment record against a
    refusal, and it would look identical to a wired one.
    """
    global _consent_check
    _consent_check = fn


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
    # Consent, before anything starts polling. The push path has had this at
    # `/api/signals/*` since it existed; the poller writes `cognitive_signals`
    # directly with the service-role client, which bypasses RLS *and* the ingest
    # endpoint, so under pull nothing checked at all. Same rule, both paths.
    if _consent_check is None:
        raise ConsentError(
            "eeg_poller has no consent check wired. Refusing to poll rather "
            "than assume consent -- see set_consent_check()."
        )
    if not _consent_check(user_id):
        raise ConsentError(
            "EEG recording is switched off for this student. A parent can turn "
            "it back on in Settings."
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
        live_owner = _live_poller_owner(device_id)
        if live_owner is not None and live_owner != user_id:
            print(f"=== device claimed by another user (device={device_id})", flush=True)
            raise DeviceClaimedError(device_id)
        # A caller that never went through reserve_device -- skipping the
        # scan/connect UI and hitting /start directly with a known device_id
        # -- must not be able to walk around someone else's in-progress
        # pairing on that station. Same exception as the live-poller case
        # above; main.py's handler already reads generically enough to cover
        # both (see its 409 message).
        owner = _reservation_owner(device_id)
        if owner is not None and owner != user_id:
            print(f"=== device reserved by another user, not yet polling (device={device_id})", flush=True)
            raise DeviceClaimedError(device_id)
        for sid, p in list(_active.items()):
            if p.user_id == user_id:
                print(f"=== stopping previous poller for same user (session={sid[:8]})", flush=True)
                p.stop()
                _active.pop(sid, None)
                # The fourth stop path, and the one the helper missed when it
                # was introduced to cover the other three. Every way a poller
                # ends has to clear its record or the set is bounded by uptime,
                # which is the claim its comment denies -- and this is the path
                # a student hits just by starting a second session.
                _forget_warning(sid)
        p = _Poller(supabase, user_id, session_id, device_id)
        p.start()
        _active[session_id] = p
        # The reservation's job ends here -- can_use_device checks live
        # pollers first, so a stale entry would never be consulted, but
        # leaving it costs a slot in the dict for no reason for up to
        # RESERVATION_TTL_SECONDS after ownership moved to something stronger.
        _reservations.pop(device_id, None)
        return {"running": True, "already": False}


def can_use_device(user_id: str, device_id: str) -> bool:
    """Whether user_id may read/control device_id's (station's) live stream.

    A station with a *live* poller belongs to that poller's user -- its stream
    is that student's in-progress biometric data, not shared classroom data --
    so only the owner may touch it. start()'s claim guard ensures at most one
    user ever holds a given device_id, so the first live match is decisive; a
    dead-but-not-yet-reaped poller doesn't count (is_alive()), matching the
    rest of this module.

    Below that, a station someone else has *reserved* (mid-scan/connect, no
    poller yet -- see reserve_device) is theirs until the reservation expires.
    Only once neither a live poller nor an unexpired reservation names an
    owner is a station open to anyone, which is what lets a user scan/pair a
    genuinely free station before their own poller has started.
    """
    with _lock:
        owner = _live_poller_owner(device_id)
        if owner is not None:
            return owner == user_id
        owner = _reservation_owner(device_id)
        if owner is not None:
            return owner == user_id
        return True


def stop(session_id: str) -> dict:
    with _lock:
        _forget_warning(session_id)
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
                _forget_warning(sid)
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
        # The warned-set goes with them. It is keyed by session and every
        # session just ended, so the whole thing is spent -- and leaving it
        # populated across a reload would carry one process's records into the
        # next, which is the unbounded growth the comment beside it denies.
        _warned_double_write.clear()
        # And any pre-claim reservation. A restarting process holds nothing --
        # every physical station should come back open to pairing rather than
        # locked to whoever last touched it before the restart. Tests rely on
        # this too: it's what stops a reservation from one test file leaking
        # into the next through the same autouse stop_all() every test's
        # teardown already calls.
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
                    # Counted separately from `samples`. A session recording
                    # EEG happily while its heart channel is refused, declined
                    # or unmeasurable is a normal state, and one combined
                    # number would report it as healthy.
                    "heart_samples": p.heart_samples,
                    "heart_errors": p.heart_errors,
                    "last_heart_ts": p.last_heart_ts,
                }
    return {"running": False}