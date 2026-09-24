"""Admin dashboard: who can reach it, flags that only say no, and a self-expiring consent bypass."""

import os
from datetime import timedelta

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import main  # noqa: E402

ADMIN = {"id": "admin-1"}
TEACHER = {"id": "teacher-1"}
STUDENT = "student-1"


class _Fake:
    """A PostgREST stand-in for the handful of tables the admin paths touch."""

    def __init__(self, admins=(), flags=(), consent=None, raises=()):
        self.admins = list(admins)
        self.flags = {f["key"]: dict(f) for f in flags}
        self.consent = consent
        self.raises = set(raises)
        self.upserts = []
        self.inserts = []

    def table(self, name):
        client, table = self, name

        class _Q:
            def __init__(self):
                self._filters = {}

            def select(self, *_a):  return self
            def order(self, *_a, **_k): return self
            def limit(self, *_a):   return self
            def is_(self, *_a):     return self
            def or_(self, *_a):     return self

            def eq(self, col, value):
                self._filters[col] = value
                return self

            def in_(self, _col, _vals):
                return self

            def single(self):
                self._single = True
                return self

            def upsert(self, row, **kw):
                self._write = ("upsert", row, kw)
                return self

            def insert(self, row):
                self._write = ("insert", row, {})
                return self

            def execute(self):
                if table in client.raises:
                    raise RuntimeError(f"{table} unavailable")

                write = getattr(self, "_write", None)
                if write:
                    kind, row, kw = write
                    if kind == "upsert":
                        client.upserts.append((table, row))
                        if table == "feature_flags":
                            client.flags[row["key"]] = dict(row)
                    else:
                        client.inserts.append((table, row))
                    return type("R", (), {"data": []})()

                if table == "profiles":
                    uid = self._filters.get("id")
                    if uid is not None:
                        # Admin status comes from `profiles.role`, same as production.
                        rows = [{"id": uid, "display_name": "Ada",
                                 "email": "ada@example.com",
                                 "role": ("admin" if uid in client.admins
                                          else "student")}]
                    else:
                        rows = [{"id": STUDENT, "display_name": "Ada",
                                 "email": "ada@example.com", "role": "student"}]
                elif table == "feature_flags":
                    rows = list(client.flags.values())
                elif table == "signal_consent":
                    rows = [client.consent] if client.consent else []
                else:
                    rows = []
                data = (rows[0] if rows else None) if getattr(self, "_single", False) else rows
                return type("R", (), {"data": data})()

        return _Q()


def _flag_rows(**overrides):
    """The seeded flag table, with any values a test wants changed."""
    rows = [{"key": k, "enabled": v, "bypass_until": None}
            for k, v in main._FEATURE_FLAG_DEFAULTS.items()]
    for row in rows:
        if row["key"] in overrides:
            row.update(overrides[row["key"]])
    return rows


# Saved before conftest's autouse fixture replaces it; `real_flags` restores it.
_REAL_FEATURE_FLAGS = main._feature_flags


@pytest.fixture
def real_flags(monkeypatch):
    """Undo conftest's default-pinning so a test drives the real reader."""
    monkeypatch.setattr(main, "_feature_flags", _REAL_FEATURE_FLAGS)
    main._feature_flags_cache_clear()
    yield
    main._feature_flags_cache_clear()


# ── access ──────────────────────────────────────────────────────────────────

ADMIN_GETS = [
    ("admin_me", lambda r: main.admin_me(r)),
    ("admin_flags", lambda r: main.admin_flags(r)),
    ("admin_env_flags", lambda r: main.admin_env_flags(r)),
    ("admin_health", lambda r: main.admin_health(r)),
    ("admin_consent_summary", lambda r: main.admin_consent_summary(r)),
    ("admin_live_signals", lambda r: main.admin_live_signals(r)),
    ("admin_get_retention_window", lambda r: main.admin_get_retention_window(r)),
    ("admin_student_search", lambda r: main.admin_student_search(r, q="ada")),
    ("admin_flag_history",
     lambda r: main.admin_flag_history("strategy_llm_enabled", r)),
    ("admin_security_events", lambda r: main.admin_security_events(r)),
]


@pytest.mark.parametrize("name,call", ADMIN_GETS, ids=[n for n, _ in ADMIN_GETS])
def test_a_non_admin_is_refused_by_every_admin_endpoint(monkeypatch, name, call):
    monkeypatch.setattr(main, "supabase", _Fake(admins=[], flags=_flag_rows()))
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)

    with pytest.raises(main.HTTPException) as e:
        call(None)
    assert e.value.status_code == 403


def test_the_write_endpoints_refuse_a_non_admin_too(monkeypatch):
    monkeypatch.setattr(main, "supabase", _Fake(admins=[], flags=_flag_rows()))
    monkeypatch.setattr(main, "get_user", lambda _r: TEACHER)

    with pytest.raises(main.HTTPException) as e:
        main.admin_set_flag("strategy_llm_enabled", None,
                            main.FeatureFlagUpdate(enabled=True))
    assert e.value.status_code == 403

    with pytest.raises(main.HTTPException) as e:
        main.admin_set_retention_window(
            None, main.RetentionWindowUpdate(enforced=False))
    assert e.value.status_code == 403


def test_every_admin_route_is_covered_by_the_refusal_test():
    """The list above stays exhaustive only if it's kept in sync with the router."""
    routed = {r.name for r in main.app.routes
              if getattr(r, "path", "").startswith("/api/admin")}
    covered = {n for n, _ in ADMIN_GETS} | {"admin_set_flag",
                                            "admin_set_retention_window"}
    assert not routed - covered, (
        f"admin endpoints with no refusal test: {sorted(routed - covered)}")


def test_an_unreadable_admin_table_denies(monkeypatch):
    """Fails closed: this guards the switch that turns off consent enforcement."""
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"],
                                                raises=["profiles"]))
    assert main._is_admin("admin-1") is False


def test_an_admin_may_view_any_student(monkeypatch):
    """Admin is a fourth relationship on the shared helper."""
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"]))
    assert main._can_view_student(ADMIN, STUDENT) is True


# ── the flags ───────────────────────────────────────────────────────────────

def test_an_unknown_flag_is_refused_rather_than_created(monkeypatch):
    """An unrecognised row is inert, so writing one would be a switch that controls nothing."""
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"], flags=_flag_rows()))
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)

    with pytest.raises(main.HTTPException) as e:
        main.admin_set_flag("recording_telepathy_enabled", None,
                            main.FeatureFlagUpdate(enabled=True))
    assert e.value.status_code == 404


def test_a_flag_write_is_audited(monkeypatch):
    monkeypatch.setattr(main, "supabase",
                        c := _Fake(admins=["admin-1"], flags=_flag_rows()))
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)

    main.admin_set_flag("strategy_llm_enabled", None,
                        main.FeatureFlagUpdate(enabled=True))

    audits = [row for table, row in c.inserts if table == "feature_flag_changes"]
    assert len(audits) == 1
    assert audits[0]["key"] == "strategy_llm_enabled"
    assert audits[0]["new_enabled"] is True
    assert audits[0]["changed_by"] == "admin-1"


def test_a_failed_audit_does_not_undo_the_flag(monkeypatch):
    """The flag is already written; raising would invite a retry that audits nothing."""
    monkeypatch.setattr(main, "supabase",
                        c := _Fake(admins=["admin-1"], flags=_flag_rows(),
                                   raises=["feature_flag_changes"]))
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)

    main.admin_set_flag("strategy_llm_enabled", None,
                        main.FeatureFlagUpdate(enabled=True))  # must not raise

    assert c.flags["strategy_llm_enabled"]["enabled"] is True


@pytest.mark.parametrize("flag,channel", [
    ("recording_eeg_enabled", "record_eeg"),
    ("recording_heart_enabled", "record_headband_optical"),
    ("recording_camera_enabled", "record_camera"),
])
def test_a_recording_switch_denies_its_channel_for_a_consenting_student(
        monkeypatch, set_flag, flag, channel):
    monkeypatch.setattr(main, "supabase", _Fake(consent={
        "user_id": STUDENT, "eeg_enabled": True,
        "headband_optical_enabled": True, "camera_enabled": True}))
    set_flag(flag, False)

    out = main._may_record(STUDENT)
    assert out[channel] is False


def test_a_recording_switch_cannot_grant_what_consent_refused(monkeypatch, set_flag):
    """ANDed, never ORed."""
    monkeypatch.setattr(main, "supabase", _Fake(consent={
        "user_id": STUDENT, "eeg_enabled": False,
        "headband_optical_enabled": False, "camera_enabled": False}))
    set_flag("recording_eeg_enabled", True)

    out = main._may_record(STUDENT)
    assert out["record_eeg"] is False
    assert out["record_headband_optical"] is False
    assert out["record_camera"] is False


# ── the consent bypass ──────────────────────────────────────────────────────

def test_disabling_consent_enforcement_requires_a_duration(monkeypatch):
    """No default: how long consent goes unenforced is not the code's choice."""
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"], flags=_flag_rows()))
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)

    with pytest.raises(main.HTTPException) as e:
        main.admin_set_flag(main.CONSENT_ENFORCEMENT_FLAG, None,
                            main.FeatureFlagUpdate(enabled=False))
    assert e.value.status_code == 422


@pytest.mark.parametrize("minutes", [0, -1, main._MAX_BYPASS_MINUTES + 1])
def test_the_bypass_duration_is_bounded(monkeypatch, minutes):
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"], flags=_flag_rows()))
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)

    with pytest.raises(main.HTTPException) as e:
        main.admin_set_flag(main.CONSENT_ENFORCEMENT_FLAG, None,
                            main.FeatureFlagUpdate(enabled=False,
                                                   bypass_minutes=minutes))
    assert e.value.status_code == 422


def test_a_live_bypass_records_for_a_student_who_never_consented(monkeypatch, set_flag):
    """The bypass's intended effect, not a bug."""
    monkeypatch.setattr(main, "supabase", _Fake(consent=None))
    set_flag(main.CONSENT_ENFORCEMENT_FLAG, False,
             bypass_until=(main._utc_now() + timedelta(minutes=30)).isoformat())

    out = main._may_record(STUDENT)
    assert out["record_eeg"] is True
    assert out["record_headband_optical"] is True
    assert out["record_camera"] is True
    # The payload never claims the student agreed.
    assert out["consent_bypassed"] is True


def test_an_expired_bypass_denies_again_with_no_write(monkeypatch, set_flag):
    """Expiry is checked on read, so no job has to run for enforcement to resume."""
    monkeypatch.setattr(main, "supabase", _Fake(consent=None))
    set_flag(main.CONSENT_ENFORCEMENT_FLAG, False,
             bypass_until=(main._utc_now() - timedelta(seconds=1)).isoformat())

    out = main._may_record(STUDENT)
    assert out["record_eeg"] is False
    assert out["consent_bypassed"] is False


def test_a_bypass_with_no_expiry_is_treated_as_expired(monkeypatch, set_flag):
    """A row with no expiry resumes enforcement rather than running forever."""
    monkeypatch.setattr(main, "supabase", _Fake(consent=None))
    set_flag(main.CONSENT_ENFORCEMENT_FLAG, False, bypass_until=None)

    assert main._consent_enforcement_active() is True
    assert main._may_record(STUDENT)["record_eeg"] is False


def test_the_bypass_does_not_reach_the_consent_screen(monkeypatch, set_flag):
    """The bypass skips asking; it must not change what `_consent` reports as agreed."""
    monkeypatch.setattr(main, "supabase", _Fake(consent=None))
    set_flag(main.CONSENT_ENFORCEMENT_FLAG, False,
             bypass_until=(main._utc_now() + timedelta(minutes=30)).isoformat())

    assert main._consent(STUDENT)["eeg_enabled"] is False


def test_the_bypass_does_not_override_the_school_year(monkeypatch, set_flag):
    monkeypatch.setattr(main, "supabase", _Fake(consent=None))
    monkeypatch.setattr(main, "_retention_window",
                        lambda: {"state": main.WINDOW_AFTER, "starts_on": None,
                                 "ends_on": None, "timezone": "UTC"})
    set_flag(main.CONSENT_ENFORCEMENT_FLAG, False,
             bypass_until=(main._utc_now() + timedelta(minutes=30)).isoformat())

    assert main._may_record(STUDENT)["record_eeg"] is False


# ── the reader ──────────────────────────────────────────────────────────────

def test_an_unreadable_flag_table_falls_back_to_the_declared_defaults(
        monkeypatch, real_flags):
    """A database blip must not be why consent stops being enforced."""
    monkeypatch.setattr(main, "supabase", _Fake(raises=["feature_flags"]))

    flags = main._feature_flags()
    assert flags == {k: {"enabled": v, "bypass_until": None}
                     for k, v in main._FEATURE_FLAG_DEFAULTS.items()}
    assert flags[main.CONSENT_ENFORCEMENT_FLAG]["enabled"] is True


def test_an_unknown_row_in_the_table_is_ignored(monkeypatch, real_flags):
    monkeypatch.setattr(main, "supabase", _Fake(flags=_flag_rows() + [
        {"key": "recording_telepathy_enabled", "enabled": True, "bypass_until": None}]))

    assert set(main._feature_flags()) == set(main._FEATURE_FLAG_DEFAULTS)


def test_a_missing_row_still_has_its_default(monkeypatch, real_flags):
    """The declared defaults are the contract for an absent key."""
    monkeypatch.setattr(main, "supabase", _Fake(flags=[]))

    assert main._feature_flags()["recording_eeg_enabled"]["enabled"] is True


# ── the read-only surfaces ──────────────────────────────────────────────────

_SIGNAL_CONTENT = {"alpha", "beta", "theta", "delta", "gamma", "focus_score",
                   "calm_score", "stress", "emotion", "emotion_confidence",
                   "bpm", "rmssd_ms", "gaze_x", "gaze_y", "attention"}


class _LiveFake(_Fake):
    """`_Fake` plus open sessions, recording every select so tests can assert on the query."""

    def __init__(self, sessions=(), signals=None, **kw):
        super().__init__(**kw)
        self.sessions = list(sessions)
        self.signals = signals or {}
        self.selects = []

    def table(self, name):
        client, outer = self, super().table(name)

        class _Q:
            def __getattr__(self, item):
                return getattr(outer, item)

            def select(self, *cols):
                client.selects.append((name, cols))
                outer.select(*cols)
                return self

            def eq(self, col, value):
                outer.eq(col, value)
                return self

            def order(self, *a, **k):
                outer.order(*a, **k)
                return self

            def limit(self, *a):
                outer.limit(*a)
                return self

            def is_(self, *a):
                outer.is_(*a)
                return self

            def execute(self):
                if name == "sessions":
                    return type("R", (), {"data": list(client.sessions)})()
                if name in client.signals:
                    rows = client.signals[name]
                    return type("R", (), {"data": list(rows)})()
                return outer.execute()

        return _Q()


def test_live_signals_asks_the_database_for_no_readings(monkeypatch):
    """An admin is entitled to whether data arrives, not the values; they are never fetched."""
    ts = main._utc_now().isoformat()
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "_display_names", lambda _ids: {STUDENT: "Ada"})
    fake = _LiveFake(
        admins=["admin-1"],
        sessions=[{"id": "s-1", "user_id": STUDENT, "started_at": ts}],
        # Real content, so a `select *` would leak into the payload below.
        signals={"cognitive_signals": [{"ts": ts, "focus_score": 88,
                                        "alpha": 0.5, "stress": 0.2}],
                 "face_signals": [{"ts": ts, "emotion": "negative",
                                   "emotion_confidence": 0.97}]})
    monkeypatch.setattr(main, "supabase", fake)

    out = main.admin_live_signals(None)

    signal_selects = [cols for table, cols in fake.selects
                      if table in ("cognitive_signals", "face_signals")]
    assert signal_selects, "the endpoint never read a signal table"
    for cols in signal_selects:
        assert cols == ("ts",), (
            f"a signal table was queried for {cols} -- this endpoint must ask "
            "for `ts` alone, so a reading cannot reach it to be filtered out")

    flat = repr(out)
    for field in _SIGNAL_CONTENT:
        assert field not in flat, f"{field} reached an admin payload"
    # A value, not a field name; a string, since "88" could match the timestamp.
    assert "negative" not in flat
    session = out["sessions"][0]
    assert session["eeg"]["flowing"] is True
    # The exact key set pins the numbers out.
    assert set(session["eeg"]) == {"flowing", "stale", "seen", "last_ts"}
    assert set(session["camera"]) == {"flowing", "stale", "seen", "last_ts"}


def test_a_channel_that_never_reported_is_not_the_same_as_stale(monkeypatch):
    old = (main._utc_now() - timedelta(hours=2)).isoformat()
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "_display_names", lambda _ids: {STUDENT: "Ada"})
    monkeypatch.setattr(main, "supabase", _LiveFake(
        admins=["admin-1"],
        sessions=[{"id": "s-1", "user_id": STUDENT, "started_at": old}],
        signals={"cognitive_signals": [{"ts": old}], "face_signals": []}))

    s = main.admin_live_signals(None)["sessions"][0]
    assert s["eeg"] == {"flowing": False, "stale": True, "seen": True, "last_ts": old}
    assert s["camera"]["seen"] is False and s["camera"]["stale"] is False


def test_an_unreadable_channel_is_not_reported_as_never_reported(monkeypatch):
    """A failed read is a third state, not a hardware fault."""
    ts = main._utc_now().isoformat()
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "_display_names", lambda _ids: {STUDENT: "Ada"})
    monkeypatch.setattr(main, "supabase", _LiveFake(
        admins=["admin-1"], raises=["face_signals"],
        sessions=[{"id": "s-1", "user_id": STUDENT, "started_at": ts}],
        signals={"cognitive_signals": [{"ts": ts}]}))

    s = main.admin_live_signals(None)["sessions"][0]
    assert s["camera"]["seen"] is None, "an unreadable channel read as a fact"
    assert s["eeg"]["seen"] is True, "one bad channel took a good one with it"


def test_live_signals_submits_every_read_before_waiting_on_any():
    """A task waiting on another in the same fixed-size pool can deadlock it."""
    import inspect

    source = inspect.getsource(main._latest_signal_ts)
    last_submit = source.rindex(".submit(")
    first_wait = source.index(".result(")

    assert first_wait > last_submit, (
        "a read is awaited before the rest are submitted, so the pool runs them "
        "one at a time -- and a task that waits on a task behind it in the same "
        "queue does not run at all")


def test_consent_summary_returns_counts_and_no_identities(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"], consent={
        "user_id": STUDENT, "eeg_enabled": True,
        "headband_optical_enabled": False, "camera_enabled": False}))

    out = main.admin_consent_summary(None)

    assert out["eeg"] == 1 and out["camera"] == 0
    flat = repr(out)
    for leak in ("user_id", STUDENT, "display_name", "email"):
        assert leak not in flat, f"{leak} reached the consent summary"


def test_health_reports_unknown_rather_than_ok_when_a_check_cannot_run(monkeypatch):
    """A check that could not run has not earned a tick."""
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "supabase",
                        _Fake(admins=["admin-1"], raises=["signal_daily_rollup"]))
    monkeypatch.setattr(main.eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main.eeg_client, "is_alive",
                        lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    checks = {c["key"]: c["status"] for c in main.admin_health(None)["checks"]}
    assert checks["eeg_sidecar"] == "unknown"
    assert checks["last_rollup"] == "unknown"


def test_health_says_so_while_consent_is_bypassed(monkeypatch, set_flag):
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"]))
    set_flag(main.CONSENT_ENFORCEMENT_FLAG, False,
             bypass_until=(main._utc_now() + timedelta(minutes=5)).isoformat())

    checks = {c["key"]: c for c in main.admin_health(None)["checks"]}
    assert checks["consent_enforcement"]["status"] == "degraded"
    assert "BYPASSED" in checks["consent_enforcement"]["detail"]


def test_env_flags_are_marked_uneditable_and_never_enumerate_the_environment(
        monkeypatch):
    """Named, not enumerated: `os.environ` also holds the service-role key."""
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"]))
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "super-secret")

    out = main.admin_env_flags(None)

    assert out["flags"] and all(f["editable"] is False for f in out["flags"])
    assert "super-secret" not in repr(out)


# ── the school year ─────────────────────────────────────────────────────────

def test_an_unknown_timezone_is_refused(monkeypatch):
    """A typo here silently denies all recording."""
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"]))

    with pytest.raises(main.HTTPException) as e:
        main.admin_set_retention_window(None, main.RetentionWindowUpdate(
            enforced=True, starts_on="2026-09-01", ends_on="2027-06-30",
            timezone="America/Chicgao"))
    assert e.value.status_code == 422


def test_an_enforced_year_needs_both_dates(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"]))

    with pytest.raises(main.HTTPException) as e:
        main.admin_set_retention_window(
            None, main.RetentionWindowUpdate(enforced=True, timezone="UTC"))
    assert e.value.status_code == 422


def test_an_inverted_year_is_refused(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "supabase", _Fake(admins=["admin-1"]))

    with pytest.raises(main.HTTPException) as e:
        main.admin_set_retention_window(None, main.RetentionWindowUpdate(
            enforced=True, starts_on="2027-06-30", ends_on="2026-09-01",
            timezone="UTC"))
    assert e.value.status_code == 422


def test_an_unenforced_year_may_omit_the_dates(monkeypatch):
    """An unenforced row need not carry invented term dates."""
    monkeypatch.setattr(main, "get_user", lambda _r: ADMIN)
    monkeypatch.setattr(main, "supabase",
                        c := _Fake(admins=["admin-1"]))

    main.admin_set_retention_window(
        None, main.RetentionWindowUpdate(enforced=False, timezone="UTC"))

    written = [row for table, row in c.upserts if table == "retention_window"]
    assert written and written[0]["starts_on"] is None
