"""Which ingestion path is live, and why it must be stated rather than inferred.

`eeg_poller` runs inside this backend and polls the sidecar over HTTP. That only
works because `start.ps1` puts both on one machine. With the camera on each
student's own device the sidecar is a local per-student process and a hosted
backend has no route to it -- so ingestion flips to the sidecar POSTing here.

The failure this guards is silent. A poller that cannot reach a sidecar produces
no rows, raises nothing, and leaves a session looking live: identical, from
every surface, to a headband that was never put on. Deploy the backend anywhere
but the student's machine and *every* session degrades that way with nothing to
read anywhere. So the mode is explicit and the wrong one refuses loudly.
"""

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
    """A falsy return would be indistinguishable from a sidecar that is merely
    not up yet -- the exact confusion this setting exists to remove."""
    with pytest.raises(eeg_poller.PushModeError) as exc:
        eeg_poller.start(None, "user-1", "session-1", "station1")

    assert "INGEST_MODE" in str(exc.value), "the refusal does not name the setting"


def test_the_endpoint_reports_configuration_not_a_broken_headband(push_mode, monkeypatch):
    """And answers before the liveness check.

    Under push ingestion this backend never talks to a sidecar, so "EEG service
    is not running on port 8001" would be true and completely misleading -- it
    reads as a fault when the deployment simply does not work that way.
    """
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
    """start.ps1, dev and a single-machine classroom all keep working without
    setting anything."""
    assert eeg_poller.INGEST_MODE == "pull"


def test_an_unrecognised_mode_falls_back_rather_than_crashing_the_backend(monkeypatch):
    """A typo in one optional setting must not take down every endpoint. Same
    reasoning as `_env_number`'s floors."""
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
    """It used to live in `eeg_client`, the pull transport. The push path would
    have had to import an HTTP client it never calls to reach a pure function,
    or keep a second copy -- and a second copy of a unit conversion is how one
    path ends up storing percentages while the other stores ratios."""
    row = signal_mapping.map_eeg_to_cognitive(
        {"features": {"focus_score": 72.0, "calm_score": 60.0, "confidence": 90.0},
         "timestamp": "2026-08-09T10:00:00Z"},
        "session-1", "user-1")

    assert row["focus"] == pytest.approx(0.72)
    assert row["engagement"] == pytest.approx(0.90)
    assert row["stress"] == pytest.approx(0.40), "stress is 1 - calm, not a measurement"


def test_a_low_score_is_not_rescued_into_a_high_one():
    """The regression the old scale-sniffing caused: `if v > 1.5: v /= 100` left
    a genuine 1.2% focus undivided, clamped it to 1.0, and stored *100%* --
    precisely the disengaged region that should drive difficulty down."""
    row = signal_mapping.map_eeg_to_cognitive(
        {"features": {"focus_score": 1.2}}, "s", "u")
    assert row["focus"] == pytest.approx(0.012)


def test_eeg_client_still_re_exports_the_mapping():
    """Existing importers keep working; only the home moved."""
    import eeg_client
    assert eeg_client.map_eeg_to_cognitive is signal_mapping.map_eeg_to_cognitive


def test_an_absent_channel_maps_to_no_row_rather_than_a_row_of_nulls():
    """A row would be counted as a sample by every aggregate downstream, so an
    emotion-only camera would report heart readings it never took."""
    payload = {"timestamp": "2026-08-09T10:00:00Z", "device_id": "camera",
               "face": {"emotion": "happy", "emotion_confidence": 0.9}}

    assert signal_mapping.map_heart_to_heart_signal(payload, "s", "u") is None
    assert signal_mapping.map_face_to_face_signal(payload, "s", "u") is not None


def test_a_heart_reading_without_a_source_is_dropped():
    """The table constrains `source` and consent is decided per sensor, so a row
    that cannot say which sensor produced it cannot be consent-checked."""
    payload = {"heart": {"bpm": 72.0, "trusted": True}}
    assert signal_mapping.map_heart_to_heart_signal(payload, "s", "u") is None


def test_heart_values_are_carried_in_absolute_units():
    """No rescaling, unlike the EEG path. bpm, ms and 0..100 all the way down."""
    row = signal_mapping.map_heart_to_heart_signal({
        "timestamp": "2026-08-09T10:00:00Z",
        "heart": {"source": "muse_optics", "bpm": 72.4, "rmssd_ms": 41.8,
                  "stress_score": 34.0, "stress_category": "low", "trusted": True},
    }, "s", "u")

    assert row["heart_rate_bpm"] == 72.4
    assert row["rmssd_ms"] == 41.8
    assert row["stress_score"] == 34.0
    assert row["trusted"] is True


def test_the_two_face_confidences_stay_separate():
    """One is how sure we are of the expression, the other whose face it is. The
    fusion rule reads only the first, and read the second by mistake once."""
    row = signal_mapping.map_face_to_face_signal({
        "face": {"emotion": "sad", "emotion_confidence": 0.81, "trusted": True,
                 "identity_confidence": 0.42, "attention": 0.6},
    }, "s", "u")

    assert row["emotion_confidence"] == 0.81
    assert row["identity_confidence"] == 0.42
    assert row["emotion_trusted"] is True


def test_the_status_endpoint_does_not_contradict_the_409(push_mode, monkeypatch):
    """The 409 from /start says nothing is wrong with the headband. This is
    polled every 3 seconds and rendered as "EEG service is down", so returning
    a flat False under push contradicted it continuously -- the student got the
    careful sentence once and the opposite reading forever after."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "user-1"})
    monkeypatch.setattr(main, "eeg_client",
                        type("C", (), {"is_alive": staticmethod(lambda: False),
                                       "DEFAULT_DEVICE_ID": "station1",
                                       "get_muse_status": staticmethod(lambda *_a, **_k: {})}))
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {})
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda *_a: True)

    out = main.eeg_status(None, device_id="station1")

    # None, not False: "we do not probe a sidecar here" is a different claim
    # from "we probed and it is down".
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
    """This is the poll that runs from page load -- /status is gated on a
    session existing. Reporting `available: False` here put "EEG service not
    reachable on port 8001" on the first screen a student sees, which is the
    sentence the mode check exists to stop showing. Fixing /start and /status
    alone only moved it."""
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
    """A live poller for *this session*, not `INGEST_MODE == "pull"`. The mode
    was a proxy and wrong in both directions: it fired on the hand-posted dev
    batch the endpoint's openness exists for, and -- with a once-per-process
    flag -- that benign post spent the warning so a genuine double-write later
    was never reported."""
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(main, "supabase",
                        type("S", (), {"table": lambda _s, _n: type("Q", (), {
                            "insert": lambda _q, _r: type("E", (), {
                                "execute": lambda _e: None})()})()})())

    batch = main.CognitiveBatch(session_id="sess-1", samples=[])

    # Captured, not discarded. The claim is per *session*, so passing the wrong
    # identifier -- the user id, say -- would warn once per user and then go
    # quiet for every later session they run. A stub ignoring its argument
    # cannot tell that apart from correct wiring.
    asked = []

    # No poller for this session: the benign case, and it must stay quiet.
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
    """Check-and-claim in one call under one lock, so two concurrent batches
    cannot both pass the check and log."""
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
    """What makes the set bounded by concurrent sessions rather than by uptime.

    It previously lived in `main` with nothing to evict it, so it grew with
    every session ever double-written for the life of the process -- while the
    comment beside it claimed the opposite, three lines below a note saying an
    un-evicted set was "the wrong way round".
    """
    class _LivePoller:
        def is_alive(self):
            return True
        def stop(self):
            pass
        samples = 0

    monkeypatch.setattr(eeg_poller, "_active", {"s1": _LivePoller()})
    monkeypatch.setattr(eeg_poller, "_warned_double_write", set())

    eeg_poller.claim_double_write_warning("s1")
    assert "s1" in eeg_poller._warned_double_write

    eeg_poller.stop("s1")
    assert "s1" not in eeg_poller._warned_double_write, "the record outlived the poller"


# A down sidecar, so the pull half of each pair below sees a genuine outage and
# the push half never reaches it. Only names that exist on the real module: an
# earlier version declared `get_debug_snapshot`, which does not (the real call is
# `get_state`) -- harmless, because `eeg_debug` returns before touching the
# client under push, but a stub is a claim about an API and whoever copies it
# next inherits the claim.
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
        return {}

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


# The family is eight, and it splits by how an endpoint answers rather than by
# anything about the mode. Four return a payload carrying a liveness claim; four
# raise. Both halves are tables because the signatures differ (`eeg_health`
# takes nothing) and so does the key carrying the claim (`eeg_status` calls it
# `service`) -- an earlier version listed the two that happened to share a
# signature while the docstring claimed five, which is how three more went
# unnoticed.
#
# Endpoints that return a payload:
_MODE_AWARE = {
    "eeg_health":  (lambda: main.eeg_health(),       "available"),
    "eeg_debug":   (lambda: main.eeg_debug(None),    "available"),
    "eeg_devices": (lambda: main.eeg_devices(None),  "available"),
    "eeg_status":  (lambda: main.eeg_status(None),   "service"),
}


@pytest.mark.parametrize("endpoint", sorted(_MODE_AWARE))
def test_every_endpoint_in_the_family_knows_the_mode(endpoint, push_mode, monkeypatch):
    """Checked together rather than one per review round.

    /start, /status, /health, /debug and /devices were each fixed separately for
    the same reason, in four rounds; the table above is what stops a sixth from
    being found the same way."""
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
    """The half that stops the one above passing for the wrong reason.

    An unconditional `{"available": None, "ingest_mode": "push"}` in any of these
    satisfies the push assertions completely. Only the pull side can tell a mode
    check apart from a hardcoded answer."""
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda *_a: True)
    monkeypatch.setattr(eeg_poller, "status", lambda _u: {})
    monkeypatch.setattr(main, "eeg_client", _StubClient)

    call, key = _MODE_AWARE[endpoint]
    out = call()

    assert out[key] is False, f"{endpoint} hides a genuine outage under pull"
    assert out["ingest_mode"] == "pull"


# Endpoints that raise instead of returning a payload. Same reason, same check,
# different shape of answer -- a 409 naming the configuration rather than the
# 503 the liveness probe underneath would give.
#
# /start is in here too, though it has its own older test above. Left out, it
# was the one endpoint of eight whose refusal was neither produced by the helper
# nor asserted by these tests, so a change to the shared wording would have
# diverged from it silently -- which is the failure the helper exists to stop,
# reintroduced by the commit that added the helper.
#
# The three muse handlers are reachable only from `connectHeadband`, behind a
# Connect button disabled under push. That was equally true of /start, which got
# the 409 anyway: a true-and-misleading message should not exist in the codepath
# at all.
_MODE_AWARE_RAISING = {
    "eeg_muse_refresh":    lambda: main.eeg_muse_refresh(None, body={}),
    "eeg_muse_connect":    lambda: main.eeg_muse_connect(None, body={"name": "Muse-1234"}),
    "eeg_muse_disconnect": lambda: main.eeg_muse_disconnect(None, body={}),
    "eeg_start":           lambda: main.eeg_start(
        type("P", (), {"session_id": "s1", "device_id": None})(), None),
}


class _OwnedSession:
    """A live session belonging to the stubbed caller, so /start reaches the
    mode check rather than being turned away as 404/403 first."""
    data = {"user_id": "u", "ended_at": None}

    def select(self, *_a): return self
    def eq(self, *_a): return self
    def single(self): return self
    def execute(self): return self


@pytest.mark.parametrize("endpoint", sorted(_MODE_AWARE_RAISING))
def test_the_raising_endpoints_name_the_configuration(endpoint, push_mode, monkeypatch):
    """Not "EEG service not running on port 8001", which is what all three said.

    Under push the sidecar owns the headband and lives on the student's own
    device, so there is no bridge here to scan, connect or disconnect with."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda *_a: True)
    monkeypatch.setattr(main, "eeg_client", _StubClient)
    monkeypatch.setattr(main, "supabase",
                        type("S", (), {"table": lambda _s, _n: _OwnedSession()})())

    with pytest.raises(main.HTTPException) as exc:
        _MODE_AWARE_RAISING[endpoint]()

    assert exc.value.status_code == 409, f"{endpoint} still answers 503"
    assert "port 8001" not in str(exc.value.detail)
    assert "push ingestion" in str(exc.value.detail)


@pytest.mark.parametrize("endpoint", sorted(_MODE_AWARE_RAISING))
def test_the_raising_endpoints_still_report_a_real_outage_under_pull(endpoint, monkeypatch):
    monkeypatch.setattr(eeg_poller, "INGEST_MODE", "pull")
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(eeg_poller, "can_use_device", lambda *_a: True)
    monkeypatch.setattr(main, "eeg_client", _StubClient)
    monkeypatch.setattr(main, "supabase",
                        type("S", (), {"table": lambda _s, _n: _OwnedSession()})())

    with pytest.raises(main.HTTPException) as exc:
        _MODE_AWARE_RAISING[endpoint]()

    assert exc.value.status_code == 503, f"{endpoint} refuses a legitimate call"


# ── the cognitive endpoint as a push target ─────────────────────────────────

def _capture_inserts(monkeypatch):
    """Stub supabase, returning the list the endpoint inserts into."""
    written = []

    class _Ins:
        def __init__(self, rows): self.rows = rows
        def execute(self): written.extend(self.rows)

    class _Tbl:
        def insert(self, rows): return _Ins(rows)

    monkeypatch.setattr(main, "supabase", type("S", (), {"table": lambda _s, _n: _Tbl()})())
    return written


def test_sensor_shaped_samples_are_converted_by_the_shared_mapper(monkeypatch):
    """The push client sends the sidecar's own payload and does no arithmetic.

    A /100 conversion on the sidecar would be a second copy of the poller's,
    which is how one path ends up storing percentages and the other ratios."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "u"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
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
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    written = _capture_inserts(monkeypatch)

    main.ingest_cognitive(main.CognitiveBatch(session_id="s1", samples=[
        {"ts": "2026-08-10T10:00:00Z", "focus": 0.72, "stress": 0.40},
    ]), None)

    assert written[0]["focus"] == pytest.approx(0.72), "converted twice"


def test_the_cognitive_batch_is_length_bounded_like_the_others():
    """It was the only ingest batch without a cap. Survivable while the sole
    writer was the in-process poller; not once the writer is a process on a
    student's machine and this endpoint is the trust boundary."""
    with pytest.raises(Exception):
        main.CognitiveBatch(session_id="s1",
                            samples=[{} for _ in range(main._INGEST_MAX_BATCH + 1)])


def test_the_cognitive_endpoint_is_rate_limited(monkeypatch):
    """Also the only one of the three without a limit, and for the same reason
    it now needs one."""
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": "flooder"})
    monkeypatch.setattr(main, "_verify_session_owner", lambda *_a: None)
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    _capture_inserts(monkeypatch)
    monkeypatch.setattr(main, "_ingest_hits", {})

    batch = main.CognitiveBatch(session_id="s1", samples=[])
    for _ in range(main._INGEST_RATE_LIMIT):
        main.ingest_cognitive(batch, None)

    with pytest.raises(main.HTTPException) as exc:
        main.ingest_cognitive(batch, None)
    assert exc.value.status_code == 429
