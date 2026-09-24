"""INGEST_MODE (push or pull): the wrong mode refuses loudly instead of recording nothing."""

import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import eeg_poller  # noqa: E402
import main  # noqa: E402
import signal_mapping  # noqa: E402


@pytest.fixture
def push_mode(monkeypatch):
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "push")


def test_the_poller_refuses_rather_than_returning_not_running(push_mode):
    """A falsy return would look like a sidecar that isn't up yet."""
    with pytest.raises(eeg_poller.PushModeError) as exc:
        eeg_poller.start(None, "user-1", "session-1", "station1")

    assert "INGEST_MODE" in str(exc.value), "the refusal does not name the setting"


def test_the_endpoint_reports_configuration_not_a_broken_headband(push_mode, monkeypatch):
    """Under push this answers before the liveness check, which would read as a fault."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "user-1"})
    monkeypatch.setattr(main, "eeg_client",
                        type("C", (), {"is_alive": staticmethod(lambda: False)}))

    class _Res:
        data = {"user_id": "user-1", "ended_at": None}

    class _Q:
        def select(self, *_a): return self
        def eq(self, *_a): return self
        def single(self): return self
        def execute(self): return _Res()

    monkeypatch.setattr(main, "supabase", type("S", (), {"table": lambda _s, _n: _Q()})())

    with pytest.raises(main.HTTPException) as exc:
        main.eeg_start(type("P", (), {"session_id": "s1", "device_id": None})(), None)

    assert exc.value.status_code == 409
    assert "push ingestion" in exc.value.detail
    assert "Nothing is wrong with the headband" in exc.value.detail


def test_pull_mode_is_the_default_so_existing_deployments_are_unchanged():
    assert eeg_poller.INGEST_MODE == "pull"


def test_an_unrecognised_mode_falls_back_rather_than_crashing_the_backend(monkeypatch):
    monkeypatch.setenv("INGEST_MODE", "shove")
    import importlib
    reloaded = importlib.reload(eeg_poller)
    try:
        assert reloaded.INGEST_MODE == "pull"
    finally:
        monkeypatch.delenv("INGEST_MODE", raising=False)
        importlib.reload(eeg_poller)


# ── the mapping both paths share ────────────────────────────────────────────

def test_the_eeg_mapping_is_importable_without_an_http_client():
    """Both paths share one mapping, so neither stores a different unit."""
    row = signal_mapping.map_eeg_to_cognitive(
        {"features": {"focus_score": 72.0, "calm_score": 60.0, "confidence": 90.0},
         "timestamp": "2026-08-09T10:00:00Z"},
        "session-1", "user-1")

    assert row["focus"] == pytest.approx(0.72)
    assert row["engagement"] == pytest.approx(0.72), (
        "engagement is the focus index, not the confidence -- confidence is a "
        "signal-quality number, and an Engagement tile fed by it showed strap fit")
    assert row["stress"] == pytest.approx(0.40), "stress is 1 - calm, not a measurement"


def test_a_low_score_is_not_rescued_into_a_high_one():
    """No scale-sniffing: a genuine 1.2% focus must not be stored as 100%."""
    row = signal_mapping.map_eeg_to_cognitive(
        {"features": {"focus_score": 1.2}}, "s", "u")
    assert row["focus"] == pytest.approx(0.012)


def test_eeg_client_still_re_exports_the_mapping():
    import eeg_client
    assert eeg_client.map_eeg_to_cognitive is signal_mapping.map_eeg_to_cognitive


def test_an_absent_channel_maps_to_no_row_rather_than_a_row_of_nulls():
    """Every aggregate would count a row of nulls as a sample."""
    payload = {"timestamp": "2026-08-09T10:00:00Z", "device_id": "camera",
               "face": {"emotion": "happy", "emotion_confidence": 0.9}}

    assert signal_mapping.map_heart_to_heart_signal(payload, "s", "u") is None
    assert signal_mapping.map_face_to_face_signal(payload, "s", "u") is not None


def test_a_heart_reading_without_a_source_is_dropped():
    """Consent is per sensor, so a row without `source` cannot be consent-checked."""
    payload = {"heart": {"bpm": 72.0, "trusted": True}}
    assert signal_mapping.map_heart_to_heart_signal(payload, "s", "u") is None


def test_heart_values_are_carried_in_absolute_units():
    """No rescaling, unlike the EEG path: bpm, ms and 0..100."""
    row = signal_mapping.map_heart_to_heart_signal({
        "timestamp": "2026-08-09T10:00:00Z",
        "heart": {"source": "muse_optics", "bpm": 72.4, "rmssd_ms": 41.8,
                  "stress_score": 34.0, "stress_category": "low", "trusted": True},
    }, "s", "u")

    assert row["heart_rate_bpm"] == 72.4
    assert row["rmssd_ms"] == 41.8
    assert row["stress_score"] == 34.0
    assert row["trusted"] is True


def test_a_row_can_carry_a_heart_rate_and_no_rmssd():
    """RMSSD is an enrichment; its refusal reason is carried under its own name."""
    row = signal_mapping.map_heart_to_heart_signal({
        "timestamp": "2026-08-09T10:00:00Z",
        "heart": {"source": "muse_optics", "bpm": 70.2, "trusted": True,
                  "rejected_by": None, "rmssd_ms": None,
                  "beat_coverage": 0.91, "rmssd_rejected_by": "coverage"},
    }, "s", "u")

    assert row["heart_rate_bpm"] == 70.2
    assert row["trusted"] is True
    assert row["rmssd_ms"] is None
    # Distinct from the rate's own `rejected_by`.
    assert row["raw"]["rmssd_rejected_by"] == "coverage"
    assert row["raw"]["beat_coverage"] == 0.91
    assert row["raw"].get("rejected_by") is None


def test_a_heart_row_is_stamped_by_the_reading_not_the_tick():
    """The block rides ~40 ticks; its own stamp makes repeats no-ops on (session_id, source, ts)."""
    row = signal_mapping.map_heart_to_heart_signal({
        "timestamp": "2026-08-09T10:00:07Z",
        "heart": {"source": "muse_optics", "bpm": 68.2,
                  "ts": "2026-08-09T10:00:00+00:00", "sample_rate_hz": 64.2,
                  "largest_gap_s": 0.03, "channel_count": 4},
    }, "s", "u")

    assert row["ts"] == "2026-08-09T10:00:00+00:00"
    # The headband's counterparts to measured_fps, under their own names.
    assert row["raw"]["sample_rate_hz"] == 64.2
    assert row["raw"]["channel_count"] == 4


def test_a_camera_heart_row_still_takes_the_ticks_stamp():
    """The camera's block has no `ts` of its own and must be unaffected."""
    row = signal_mapping.map_heart_to_heart_signal({
        "timestamp": "2026-08-09T10:00:07Z",
        "heart": {"source": "rppg", "bpm": 71.0},
    }, "s", "u")

    assert row["ts"] == "2026-08-09T10:00:07Z"
    assert "sample_rate_hz" not in row["raw"]


def test_the_mapper_does_not_carry_a_retired_identity_confidence():
    """An older sidecar may still send `identity_confidence`; it must not reach the row."""
    row = signal_mapping.map_face_to_face_signal({
        "face": {"emotion": "sad", "emotion_confidence": 0.81, "trusted": True,
                 "identity_confidence": 0.42, "attention": 0.6},
    }, "s", "u")

    assert row["emotion_confidence"] == 0.81
    assert row["emotion_trusted"] is True
    assert "identity_confidence" not in row


def test_the_gaze_refusal_rides_in_raw_beside_the_emotion_one():
    """Two measurements in one block need two refusal fields."""
    row = signal_mapping.map_face_to_face_signal({
        "face": {"emotion": "sad", "emotion_confidence": 0.81, "trusted": True,
                 "rejected_by": None, "gaze_x": None, "gaze_y": None,
                 "gaze_rejected_by": "no_eye"},
    }, "s", "u")

    assert row["gaze_x"] is None
    assert row["raw"]["gaze_rejected_by"] == "no_eye"
    # `_raw` drops nulls, so the emotion refusal is missing, not null.
    assert "rejected_by" not in row["raw"]


def test_the_three_unproduced_face_columns_are_kept_on_purpose():
    """Gaze is produced; `attention` is unproduced until there is a labelled reference."""
    row = signal_mapping.map_face_to_face_signal({
        "face": {"emotion": "sad", "attention": 0.6, "gaze_x": -0.2,
                 "gaze_y": 0.1},
    }, "s", "u")

    for column in ("attention", "gaze_x", "gaze_y"):
        assert column in row, (
            f"{column} was dropped. It has no producer yet, but it is planned, "
            "not dead weight. If it is genuinely being retired, that needs a "
            "deliberate scope decision, and this test should be deleted "
            "deliberately rather than made to pass."
        )
    assert row["attention"] == 0.6


def test_the_status_endpoint_does_not_contradict_the_409(push_mode, monkeypatch):
    """A flat False here would render as "EEG service is down" after /start's careful 409."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "user-1"})
    monkeypatch.setattr(main, "eeg_client",
                        type("C", (), {"is_alive": staticmethod(lambda: False),
                                       "DEFAULT_DEVICE_ID": "station1",
                                       "get_muse_status": staticmethod(lambda *_a, **_k: {})}))
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {})
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda *_a: True)

    out = main.eeg_status(None, device_id="station1")

    # None, not False: "not probed here" differs from "probed and down".
    assert out["service"] is None
    assert out["ingest_mode"] == "push"


def test_status_still_reports_liveness_under_pull(monkeypatch):
    """Otherwise the test above passes for the wrong reason."""
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "user-1"})
    monkeypatch.setattr(main, "eeg_client",
                        type("C", (), {"is_alive": staticmethod(lambda: True),
                                       "DEFAULT_DEVICE_ID": "station1",
                                       "get_muse_status": staticmethod(lambda *_a, **_k: {})}))
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {})
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda *_a: True)

    out = main.eeg_status(None, device_id="station1")
    assert out["service"] is True
    assert out["ingest_mode"] == "pull"


def test_health_does_not_report_an_outage_under_push(push_mode):
    """This is the poll that runs from page load, before /status does."""
    out = main.eeg_health()

    assert out["available"] is None, "'not probed here' rendered as 'probed and down'"
    assert out["ingest_mode"] == "push"


def test_health_still_reports_a_real_outage_under_pull(monkeypatch):
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main, "eeg_client",
                        type("C", (), {"is_alive": staticmethod(lambda: False),
                                       "EEG_API_URL": "http://127.0.0.1:8001"}))

    out = main.eeg_health()
    assert out["available"] is False
    assert out["ingest_mode"] == "pull"


def test_the_double_write_warning_fires_on_the_real_condition(monkeypatch, capsys):
    """The condition is a live poller for this session, not `INGEST_MODE == "pull"`."""
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(main, "supabase",
                        type("S", (), {"table": lambda _s, _n: type("Q", (), {
                            "insert": lambda _q, _r: type("E", (), {
                                "execute": lambda _e: None})()})()})())

    batch = main.CognitiveBatch(session_id="sess-1", samples=[])

    # Captured: the claim is per session, so the id passed must be the session's.
    asked = []

    # No poller for this session: the benign case, must stay quiet.
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning",
                        lambda s: (asked.append(s), False)[1])
    main.ingest_cognitive(batch, None)
    assert "twice" not in capsys.readouterr().out

    # A live poller for it: the actual double-write.
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning",
                        lambda s: (asked.append(s), True)[1])
    main.ingest_cognitive(batch, None)
    assert "twice" in capsys.readouterr().out

    assert asked == ["sess-1", "sess-1"], f"claimed against the wrong id: {asked}"


def test_the_claim_is_once_per_session_and_only_while_polling(monkeypatch):
    """Check-and-claim under one lock, so two concurrent batches cannot both log."""
    class _LivePoller:
        def is_alive(self):
            return True

    monkeypatch.setattr(eeg_poller, "_active", {"s1": _LivePoller()})
    monkeypatch.setattr(eeg_poller, "_warned_double_write", set())

    assert eeg_poller.claim_double_write_warning("s1") is True
    assert eeg_poller.claim_double_write_warning("s1") is False, "warned twice"
    # A session with no live poller is the dev case, not a double write.
    assert eeg_poller.claim_double_write_warning("s2") is False


def test_stopping_a_poller_evicts_its_warning_record(monkeypatch):
    """Keeps the set bounded by concurrent sessions, not process uptime."""
    class _LivePoller:
        def is_alive(self):
            return True
        def stop(self):
            pass
        samples = 0
        user_id = "u1"

    monkeypatch.setattr(eeg_poller, "_active", {"s1": _LivePoller()})
    monkeypatch.setattr(eeg_poller, "_warned_double_write", set())

    eeg_poller.claim_double_write_warning("s1")
    assert "s1" in eeg_poller._warned_double_write

    eeg_poller.stop("s1")
    assert "s1" not in eeg_poller._warned_double_write, "the record outlived the poller"


# A down sidecar. Method names must match the real module exactly.
class _StubClient:
    DEFAULT_DEVICE_ID = "station1"
    EEG_API_URL = "http://127.0.0.1:8001"

    @staticmethod
    def is_alive():
        return False

    @staticmethod
    def get_state(*_a, **_k):
        return None

    @staticmethod
    def get_muse_status(*_a, **_k):
        # Raises like the real one does without EEG_API_TOKEN, the normal push state.
        raise RuntimeError("Missing EEG_API_TOKEN environment variable")

    @staticmethod
    def list_devices():
        return []

    @staticmethod
    def muse_refresh(*_a, **_k):
        return {}

    @staticmethod
    def muse_connect(*_a, **_k):
        return {}

    @staticmethod
    def muse_disconnect(*_a, **_k):
        return {}


class _StubClientConfigured(_StubClient):
    """A pull deployment: EEG_API_TOKEN is set, so the probe runs and answers."""

    @staticmethod
    def get_muse_status(*_a, **_k):
        return {}


# Mode-aware endpoints that return a payload -> the key carrying the liveness claim.
_MODE_AWARE = {
    "eeg_health":  (lambda: main.eeg_health(),       "available"),
    "eeg_debug":   (lambda: main.eeg_debug(None),    "available"),
    "eeg_devices": (lambda: main.eeg_devices(None),  "available"),
    "eeg_status":  (lambda: main.eeg_status(None),   "service"),
}


@pytest.mark.parametrize("endpoint", sorted(_MODE_AWARE))
def test_every_endpoint_in_the_family_knows_the_mode(endpoint, push_mode, monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda *_a: True)
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {})
    monkeypatch.setattr(main, "eeg_client", _StubClient)

    call, key = _MODE_AWARE[endpoint]
    out = call()

    assert out[key] is None, f"{endpoint} reports an outage under push"
    assert out["ingest_mode"] == "push"


@pytest.mark.parametrize("endpoint", sorted(_MODE_AWARE))
def test_the_same_endpoints_still_report_a_real_outage_under_pull(endpoint, monkeypatch):
    """Otherwise a hardcoded push answer would pass the test above."""
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda *_a: True)
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {})
    monkeypatch.setattr(main, "eeg_client", _StubClientConfigured)

    call, key = _MODE_AWARE[endpoint]
    out = call()

    assert out[key] is False, f"{endpoint} hides a genuine outage under pull"
    assert out["ingest_mode"] == "pull"


# Mode-aware endpoints that raise: a 409 naming the configuration, not a 503.
_MODE_AWARE_RAISING = {
    "eeg_muse_refresh":    lambda: main.eeg_muse_refresh(None, body={}),
    "eeg_muse_connect":    lambda: main.eeg_muse_connect(None, body={"name": "Muse-1234"}),
    "eeg_muse_disconnect": lambda: main.eeg_muse_disconnect(None, body={}),
    "eeg_start":           lambda: main.eeg_start(
        type("P", (), {"session_id": "s1", "device_id": None})(), None),
}


class _OwnedSession:
    """The caller's live session, so /start reaches the mode check, not a 404/403."""
    data = {"user_id": "u", "ended_at": None}

    def select(self, *_a): return self
    def eq(self, *_a): return self
    def single(self): return self
    def execute(self): return self


@pytest.mark.parametrize("endpoint", sorted(_MODE_AWARE_RAISING))
def test_the_raising_endpoints_name_the_configuration(endpoint, push_mode, monkeypatch):
    """Unmocked reserve_device on purpose: `_refuse_under_push` must run before it."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "eeg_client", _StubClient)
    monkeypatch.setattr(main, "supabase",
                        type("S", (), {"table": lambda _s, _n: _OwnedSession()})())

    with pytest.raises(main.HTTPException) as exc:
        _MODE_AWARE_RAISING[endpoint]()

    assert exc.value.status_code == 409, f"{endpoint} still answers 503"
    assert "port 8001" not in str(exc.value.detail)
    assert "push ingestion" in str(exc.value.detail)
    # No reservation was claimed that push could never release.
    assert eeg_poller._reservations == {}


@pytest.mark.parametrize("endpoint", sorted(_MODE_AWARE_RAISING))
def test_the_raising_endpoints_still_report_a_real_outage_under_pull(endpoint, monkeypatch):
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    # Real reserve_device; state is clean per test via conftest's stop_all().
    monkeypatch.setattr(main, "eeg_client", _StubClient)
    monkeypatch.setattr(main, "_consent",
                        lambda _s: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(main, "supabase",
                        type("S", (), {"table": lambda _s, _n: _OwnedSession()})())

    with pytest.raises(main.HTTPException) as exc:
        _MODE_AWARE_RAISING[endpoint]()

    assert exc.value.status_code == 503, f"{endpoint} refuses a legitimate call"


# ── the cognitive endpoint as a push target ─────────────────────────────────

def _capture_inserts(monkeypatch):
    """Stub supabase, returning the list the endpoint inserts into."""
    written = []

    class _Write:
        def __init__(self, rows): self.rows = rows

        def execute(self):
            written.extend(self.rows)
            # The endpoints count `inserted` from `.data`.
            return type("R", (), {"data": list(self.rows)})

    class _Tbl:
        def insert(self, rows, **_k): return _Write(rows)

        # Conflict semantics are modelled in `test_signal_ingest.py`.
        def upsert(self, rows, **_k): return _Write(rows)

    monkeypatch.setattr(main, "supabase", type("S", (), {"table": lambda _s, _n: _Tbl()})())
    return written


def test_sensor_shaped_samples_are_converted_by_the_shared_mapper(monkeypatch):
    """The push client sends the sidecar's own payload and does no arithmetic."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)

    main.ingest_cognitive(main.CognitiveBatch(session_id="s1", samples=[
        {"ts": "2026-08-10T10:00:00Z",
         "features": {"focus_score": 72.0, "calm_score": 60.0, "confidence": 90.0}},
    ]), None)

    assert written[0]["focus"] == pytest.approx(0.72), "stored on the sidecar's scale"
    assert written[0]["stress"] == pytest.approx(0.40), "stress is 1 - calm"


def test_flat_samples_are_still_stored_as_given(monkeypatch):
    """The hand-posted dev shape: already-mapped rows, in table units."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)

    main.ingest_cognitive(main.CognitiveBatch(session_id="s1", samples=[
        {"ts": "2026-08-10T10:00:00Z", "focus": 0.72, "stress": 0.40},
    ]), None)

    assert written[0]["focus"] == pytest.approx(0.72), "converted twice"


def test_the_cognitive_batch_is_length_bounded_like_the_others():
    """The writer is a process on a student's machine; this endpoint is the trust boundary."""
    with pytest.raises(Exception):
        main.CognitiveBatch(session_id="s1",
                            samples=[{} for _ in range(main._INGEST_MAX_BATCH + 1)])


def test_the_cognitive_endpoint_is_rate_limited(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "flooder"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    _capture_inserts(monkeypatch)
    monkeypatch.setattr(main._INGEST_LIMITER, "hits", {})

    batch = main.CognitiveBatch(session_id="s1", samples=[])
    for _ in range(main._INGEST_RATE_LIMIT):
        main.ingest_cognitive(batch, None)

    with pytest.raises(main.HTTPException) as exc:
        main.ingest_cognitive(batch, None)
    assert exc.value.status_code == 429


def test_eeg_samples_are_dropped_when_the_student_has_not_consented(monkeypatch):
    """The last line of defence against a stale sidecar sending after a withdrawal."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)
    monkeypatch.setattr(main, "_consent",
                        lambda _u: {"eeg_enabled": False, "retrieved": True})

    out = main.ingest_cognitive(main.CognitiveBatch(
        session_id="s1", samples=[{"focus": 0.5}]), None)

    assert written == []
    assert out["dropped"] == 1
    assert out["reason"] == "eeg not consented"


def test_an_unreadable_consent_row_records_nothing(monkeypatch):
    """`_consent` fails closed, unlike the reporting helpers."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)
    monkeypatch.setattr(main, "_consent", lambda _u: {"retrieved": False})

    out = main.ingest_cognitive(main.CognitiveBatch(
        session_id="s1", samples=[{"focus": 0.5}]), None)

    assert written == []
    assert out["reason"] == "consent unavailable"


def test_a_client_supplied_raw_survives_the_mapping(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)

    main.ingest_cognitive(main.CognitiveBatch(session_id="s1", samples=[
        {"features": {"focus_score": 50.0, "signal_quality": "good"},
         "raw": {"note": "kept", "signal_quality": "spoofed"}},
    ]), None)

    assert written[0]["raw"]["note"] == "kept"
    # Derived keys win a collision, where there is a derived value (`_raw` drops Nones).
    assert written[0]["raw"]["signal_quality"] == "good"


@pytest.mark.parametrize("stopper", ["stop", "stop_for_user", "stop_all"])
def test_every_stop_path_evicts_the_warning_record(stopper, monkeypatch):
    class _P:
        user_id = "u1"
        session_id = "s1"
        samples = 0
        def is_alive(self): return True
        def stop(self): pass
        def join(self, *_a, **_k): pass

    poller = _P()
    monkeypatch.setattr(eeg_poller, "_active", {"s1": poller})
    monkeypatch.setattr(eeg_poller, "_warned_double_write", set())
    monkeypatch.setattr(eeg_poller, "live_pollers", lambda: [poller])
    eeg_poller.claim_double_write_warning("s1")
    assert "s1" in eeg_poller._warned_double_write

    if stopper == "stop":
        eeg_poller.stop("s1")
    elif stopper == "stop_for_user":
        eeg_poller.stop_for_user("u1")
    else:
        eeg_poller.stop_all(timeout=0.01)

    assert "s1" not in eeg_poller._warned_double_write, \
        f"{stopper} left the record behind"


def test_derived_keys_win_on_the_push_path_too(monkeypatch):
    """The push client nests the envelope under `raw`; the mapper must read it from there."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)

    main.ingest_cognitive(main.CognitiveBatch(session_id="s1", samples=[
        {"features": {"focus_score": 50.0, "signal_quality": "good"},
         "raw": {"device_id": "station1", "state": {"label": "focused"},
                 "signal_quality": "spoofed", "note": "kept"}},
    ]), None)

    raw = written[0]["raw"]
    assert raw["device_id"] == "station1"
    assert raw["state"] == {"label": "focused"}
    # Derived beats client-supplied.
    assert raw["signal_quality"] == "good"
    # Anything outside the envelope survives.
    assert raw["note"] == "kept"


def test_the_pull_path_mapping_is_unchanged(monkeypatch):
    """The poller passes the envelope at the top level."""
    row = signal_mapping.map_eeg_to_cognitive(
        {"timestamp": "t", "device_id": "station1",
         "features": {"focus_score": 50.0, "signal_quality": "good"},
         "state": {"label": "focused"}},
        "s", "u")

    assert row["raw"]["device_id"] == "station1"
    assert row["raw"]["signal_quality"] == "good"


def test_the_same_user_restart_path_evicts_too(monkeypatch):
    """The stop path a student hits by starting a second session."""
    class _P:
        def __init__(self, *a):
            # eeg_poller.start builds it as _Poller(supabase, user, session, device)
            self.session_id = a[2] if len(a) > 2 else "s1"
            self.user_id = a[1] if len(a) > 1 else "u1"
        device_id = "station1"
        samples = 0
        def is_alive(self): return True
        def start(self): pass
        def stop(self): pass

    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(eeg_poller, "_active", {"s1": _P("s1")})
    monkeypatch.setattr(eeg_poller, "_warned_double_write", set())
    monkeypatch.setattr(eeg_poller, "_Poller", _P)
    eeg_poller.claim_double_write_warning("s1")
    assert "s1" in eeg_poller._warned_double_write

    eeg_poller.start(None, "u1", "s2", "station1")

    assert "s1" not in eeg_poller._warned_double_write, \
        "the replaced session's record outlived its poller"


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), "oops", [1]])
def test_unstorable_band_values_are_rejected_at_the_boundary(bad):
    """Checked per sample, so one bad sample is dropped rather than failing the batch."""
    with pytest.raises(Exception):
        main.CognitiveSample.model_validate({"bands": {"alpha": bad}})


def test_non_column_keys_are_still_free_form():
    """Only keys that become columns are checked; the rest goes to `raw`."""
    sample = main.CognitiveSample.model_validate(
        {"features": {"focus_score": 50.0, "signal_quality": "good",
                      "quality_basis": "contact", "batch_size": 3}})
    assert sample.features["signal_quality"] == "good"


def test_status_does_not_touch_the_sidecar_under_push(push_mode, monkeypatch):
    """The mode check must precede the probe, which 500s without EEG_API_TOKEN."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {})

    probed = []

    class _Exploding(_StubClient):
        @staticmethod
        def get_muse_status(*_a, **_k):
            probed.append(1)
            raise RuntimeError("Missing EEG_API_TOKEN environment variable")

    monkeypatch.setattr(main, "eeg_client", _Exploding)

    out = main.eeg_status(None)

    assert probed == [], "the sidecar was probed under push"
    assert out["service"] is None
    assert out["muse"]["available"] is None, "'not probed' rendered as 'absent'"
    assert out["muse"]["reason"] == "push_ingestion"


def test_a_device_claimed_by_another_user_still_reads_that_way_under_pull(monkeypatch):
    """The push branch must not have swallowed the in-use case."""
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {})
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda *_a: False)
    monkeypatch.setattr(main, "eeg_client", _StubClientConfigured)

    out = main.eeg_status(None)

    assert out["muse"] == {"available": False, "reason": "in_use_by_other"}


# ── the quality rules, shared by both paths ─────────────────────────────────

_UNWORN = {"timestamp": "t", "features": {"signal_quality": "no_signal",
                                          "focus_score": 0.0, "calm_score": 0.0,
                                          "confidence": 0.0}}
_BAD_CONTACT = {"timestamp": "t", "bands": {"alpha": 0.3},
                "features": {"signal_quality": "poor", "quality_basis": "contact",
                             "focus_score": 84.8, "calm_score": 20.0}}
_LEGACY_POOR = {"timestamp": "t",
                "features": {"signal_quality": "poor", "quality_basis": "heuristic",
                             "focus_score": 84.8, "calm_score": 20.0}}


def test_an_unworn_headband_produces_no_row():
    """Zeroed scores from a disconnected headset are not a reading of zero."""
    assert signal_mapping.map_eeg_to_cognitive(_UNWORN, "s", "u") is None


def test_bad_contact_keeps_the_row_and_nulls_the_measurements():
    """`class_live` derives staleness from the newest row, so the row must stay."""
    row = signal_mapping.map_eeg_to_cognitive(_BAD_CONTACT, "s", "u")

    assert row is not None
    assert all(row[c] is None for c in signal_mapping._MEASUREMENT_COLUMNS)
    # Still explicable after the fact.
    assert row["raw"]["signal_quality"] == "poor"
    assert row["raw"]["quality_basis"] == "contact"


def test_the_legacy_poor_heuristic_is_not_treated_as_bad_contact():
    """The legacy heuristic reports "poor" for any focused student; it says nothing about electrodes."""
    row = signal_mapping.map_eeg_to_cognitive(_LEGACY_POOR, "s", "u")

    assert row["focus"] == pytest.approx(0.848)


@pytest.mark.parametrize("payload,expected", [(_UNWORN, "no_signal"),
                                              (_BAD_CONTACT, "contact_poor"),
                                              (_LEGACY_POOR, "ok")])
def test_both_paths_read_one_verdict(payload, expected):
    assert signal_mapping.eeg_quality(payload) == expected


def test_the_push_endpoint_drops_no_signal_ticks(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)

    out = main.ingest_cognitive(main.CognitiveBatch(session_id="s1", samples=[
        {"features": _UNWORN["features"]},
        {"features": {"signal_quality": "good", "focus_score": 60.0}},
    ]), None)

    assert len(written) == 1, "a zeroed row was stored"
    assert out["inserted"] == 1
    # Counted, so a caller can tell "sent 2, recorded 1" from "sent 1".
    assert out["dropped"] == 1


def test_the_push_endpoint_nulls_measurements_on_bad_contact(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)

    main.ingest_cognitive(main.CognitiveBatch(session_id="s1", samples=[
        {"features": _BAD_CONTACT["features"], "bands": _BAD_CONTACT["bands"]},
    ]), None)

    assert written[0]["focus"] is None
    assert written[0]["alpha"] is None, "a band computed from bad electrodes was stored"


def test_the_eeg_confidence_rides_in_raw_for_the_fusion_gate():
    """No column carries it; `engagement` is the focus index."""
    row = signal_mapping.map_eeg_to_cognitive(
        {"features": {"focus_score": 72.0, "calm_score": 60.0, "confidence": 90.0},
         "timestamp": "2026-08-09T10:00:00Z"},
        "session-1", "user-1")
    assert row["raw"]["confidence"] == pytest.approx(0.90)


def test_a_poor_contact_row_carries_no_confidence_either():
    """A surviving confidence on poor rows drags the average down and drops the EEG channel."""
    poor = signal_mapping.map_eeg_to_cognitive(
        {**_BAD_CONTACT, "features": {**_BAD_CONTACT["features"], "confidence": 30.0}}, "s", "u")
    assert poor["focus"] is None
    assert "confidence" not in poor["raw"]
    good = signal_mapping.map_eeg_to_cognitive(
        {**_LEGACY_POOR, "features": {**_LEGACY_POOR["features"], "confidence": 30.0}}, "s", "u")
    assert good["raw"]["confidence"] == pytest.approx(0.3)


def test_the_flat_ingest_shape_stores_engagement_as_focus(monkeypatch):
    """The flat branch bypasses the mapper; replay_into_backend uses it."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)
    main.ingest_cognitive(main.CognitiveBatch(session_id="s1", samples=[
        {"ts": "2026-08-10T10:00:00Z", "focus": 0.72, "stress": 0.40, "engagement": 0.11},
    ]), None)
    assert written[0]["engagement"] == pytest.approx(0.72)


def test_a_malformed_tick_stores_no_measurement_and_says_why():
    """Contact is fine on such a tick, so only the artifact reason can null it."""
    held = {"timestamp": "t", "bands": {"alpha": 0.3},
            "features": {"signal_quality": "good", "quality_basis": "contact",
                         "focus_score": 50.0, "calm_score": 50.0, "confidence": 20.0,
                         "artifact_reason": "malformed_bands"}}
    row = signal_mapping.map_eeg_to_cognitive(held, "s", "u")
    assert row is not None
    assert row["focus"] is None and row["stress"] is None
    assert row["raw"]["artifact_reason"] == "malformed_bands"
    assert "confidence" not in row["raw"]
    blink = signal_mapping.map_eeg_to_cognitive(
        {**held, "features": {**held["features"], "artifact_reason": "delta_jump"}}, "s", "u")
    assert blink["focus"] == pytest.approx(0.5), "a held-on-blink score is still stored"
    assert blink["raw"]["artifact_reason"] == "delta_jump"


def test_one_malformed_sample_does_not_fail_the_batch(monkeypatch):
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)
    out = main.ingest_cognitive(main.CognitiveBatch(session_id="s1", samples=[
        {"ts": "2026-08-10T10:00:00Z", "focus": 0.72, "stress": 0.40},
        {"ts": "2026-08-10T10:00:01Z", "focus": float("nan"), "stress": 0.40},
        "not even a dict",
        {"ts": "2026-08-10T10:00:02Z", "focus": 0.70, "stress": 0.41},
    ]), None)
    assert [w["ts"] for w in written] == ["2026-08-10T10:00:00Z", "2026-08-10T10:00:02Z"]
    assert out["malformed"] == 2 and out["inserted"] == 2


def test_a_synthesised_heart_reading_is_marked_in_raw_and_a_measured_one_is_not():
    """The simulator's pulse is stored as `muse_optics`; the mark is absent, not false, on hardware rows."""
    synthetic = signal_mapping.map_heart_to_heart_signal({
        "timestamp": "2026-08-09T10:00:00Z",
        "heart": {"source": "muse_optics", "bpm": 72.4, "trusted": True, "synthetic": True},
    }, "s", "u")
    assert synthetic["raw"]["synthetic"] is True
    measured = signal_mapping.map_heart_to_heart_signal({
        "timestamp": "2026-08-09T10:00:00Z",
        "heart": {"source": "muse_optics", "bpm": 72.4, "trusted": True},
    }, "s", "u")
    assert "synthetic" not in measured["raw"]
    # A client cannot mark a row synthetic via its own raw: derived wins, None removes.
    posted = signal_mapping.map_heart_to_heart_signal({
        "timestamp": "2026-08-09T10:00:00Z", "raw": {"synthetic": True},
        "heart": {"source": "muse_optics", "bpm": 72.4, "trusted": True, "synthetic": "yes"},
    }, "s", "u")
    assert "synthetic" not in posted["raw"]
