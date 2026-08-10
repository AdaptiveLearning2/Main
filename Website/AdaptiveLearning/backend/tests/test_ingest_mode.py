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


def test_a_row_can_carry_a_heart_rate_and_no_rmssd():
    """RMSSD is an enrichment, and about one window in five is gated out even on
    a good seated recording. So a null `rmssd_ms` beside a trusted
    `heart_rate_bpm` is the normal case, not a broken one -- the cause is
    carried under its own name so the row can be read back and explained."""
    row = signal_mapping.map_heart_to_heart_signal({
        "timestamp": "2026-08-09T10:00:00Z",
        "heart": {"source": "muse_optics", "bpm": 70.2, "trusted": True,
                  "rejected_by": None, "rmssd_ms": None,
                  "beat_coverage": 0.91, "rmssd_rejected_by": "coverage"},
    }, "s", "u")

    assert row["heart_rate_bpm"] == 70.2
    assert row["trusted"] is True
    assert row["rmssd_ms"] is None
    # Distinct from the rate's own `rejected_by`, which is what says whether
    # there is a reading at all.
    assert row["raw"]["rmssd_rejected_by"] == "coverage"
    assert row["raw"]["beat_coverage"] == 0.91
    assert row["raw"].get("rejected_by") is None


def test_a_heart_row_is_stamped_by_the_reading_not_the_tick():
    """The headband's block covers 25s, is recomputed every 10s and is held on
    the payload in between -- so it rides ~40 consecutive ticks. Stamped from
    the tick, one measurement becomes forty rows; stamped from itself, the
    unique (session_id, source, ts) makes the repeats no-ops."""
    row = signal_mapping.map_heart_to_heart_signal({
        "timestamp": "2026-08-09T10:00:07Z",
        "heart": {"source": "muse_optics", "bpm": 68.2,
                  "ts": "2026-08-09T10:00:00+00:00", "sample_rate_hz": 64.2,
                  "largest_gap_s": 0.03, "channel_count": 4},
    }, "s", "u")

    assert row["ts"] == "2026-08-09T10:00:00+00:00"
    # The headband's counterparts to measured_fps, kept under their own names:
    # one is a camera's frame rate, the other a BLE link's sample rate.
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
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
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
        # Raises, like the real one. `eeg_client._learner_headers()` throws when
        # EEG_API_TOKEN is unset -- the normal state of a hosted push
        # deployment -- and it throws outside the request try block, so the
        # endpoint 500s. A stub returning `{}` made `/api/eeg/status` look
        # mode-aware while the probe in front of the check was crashing it, and
        # the parametrised test that exists to catch exactly that passed.
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
    """A pull deployment: EEG_API_TOKEN is set, so the probe runs and answers.

    Separate from `_StubClient` because the two deployments differ in exactly
    the way that mattered -- under push there is no token and the probe throws,
    which is what the base stub models.
    """

    @staticmethod
    def get_muse_status(*_a, **_k):
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
    monkeypatch.setattr(main, "eeg_client", _StubClientConfigured)

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
    monkeypatch.setattr(main, "_consent", lambda _u: {"eeg_enabled": True, "retrieved": True})
    monkeypatch.setattr(eeg_poller, "claim_double_write_warning", lambda _s: False)
    _capture_inserts(monkeypatch)
    monkeypatch.setattr(main, "_ingest_hits", {})

    batch = main.CognitiveBatch(session_id="s1", samples=[])
    for _ in range(main._INGEST_RATE_LIMIT):
        main.ingest_cognitive(batch, None)

    with pytest.raises(main.HTTPException) as exc:
        main.ingest_cognitive(batch, None)
    assert exc.value.status_code == 429


def test_eeg_samples_are_dropped_when_the_student_has_not_consented(monkeypatch):
    """The last line of defence, and the one this endpoint was missing.

    The sidecar gates on consent too, but a stale one that kept sending after a
    withdrawal would otherwise keep recording, and the withdrawal would look
    respected from every surface that reads."""
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
    """`_consent` fails closed, unlike the reporting helpers. A dashboard
    degrading to empty is fine; a consent check degrading to *enabled* records
    data against a refusal."""
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
    """`map_eeg_to_cognitive` built its `raw` inline and discarded whatever the
    caller sent -- invisible while the poller, which sends none, was the only
    caller."""
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
    # Derived keys win the collision: a client must not overwrite what was
    # actually observed by choosing a key name. Only where there *is* a derived
    # value -- `_raw` drops Nones so an absent field cannot read as a recorded
    # null, which means a key the sidecar did not populate stays the client's.
    # That is not a hole to close here: on this path the client *is* the
    # sidecar, and the endpoint's defences are session ownership and consent.
    assert written[0]["raw"]["signal_quality"] == "good"


@pytest.mark.parametrize("stopper", ["stop", "stop_for_user", "stop_all"])
def test_every_stop_path_evicts_the_warning_record(stopper, monkeypatch):
    """"Bounded by concurrent sessions" is only true if *every* way a poller
    ends clears its record. `stop()` did it and the other two did not -- the
    same shape as the bug that moved this set out of `main`, one layer along."""
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
    """"Derived keys win a collision" held on the pull path and silently did not
    here. The push client nests device_id/channels/state/ingestion under `raw`,
    so the mapper's top-level lookups all found None, `_raw` dropped the Nones,
    and the client's values were stored -- one path differing from the other,
    which is the thing `signal_mapping` exists to prevent."""
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
    # The envelope is read as the envelope, not left buried under `raw`.
    assert raw["device_id"] == "station1"
    assert raw["state"] == {"label": "focused"}
    # Derived beats client-supplied, the same way it does on the pull path.
    assert raw["signal_quality"] == "good"
    # And anything outside the envelope still survives.
    assert raw["note"] == "kept"


def test_the_pull_path_mapping_is_unchanged(monkeypatch):
    """The un-nesting must not have moved the poller's goalposts: it passes the
    envelope at the top level already."""
    row = signal_mapping.map_eeg_to_cognitive(
        {"timestamp": "t", "device_id": "station1",
         "features": {"focus_score": 50.0, "signal_quality": "good"},
         "state": {"label": "focused"}},
        "s", "u")

    assert row["raw"]["device_id"] == "station1"
    assert row["raw"]["signal_quality"] == "good"


def test_the_same_user_restart_path_evicts_too(monkeypatch):
    """The fourth stop path, missed when the helper was introduced for the other
    three -- and the one a student hits just by starting a second session."""
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
    """The flat fields are typed and Pydantic rejects these; `features`/`bands`
    were free-form dicts, so the same values reached the numeric columns and
    failed at PostgREST -- taking the *whole batch* with them, valid samples
    included, and reporting it as a 500 the client will retry."""
    with pytest.raises(Exception):
        main.CognitiveBatch(session_id="s", samples=[{"bands": {"alpha": bad}}])


def test_non_column_keys_are_still_free_form():
    """Only the keys that become columns are checked. The rest is metadata bound
    for `raw`, which is jsonb and can hold it -- a sidecar gaining a feature
    must not need this model changed in lockstep to keep posting."""
    batch = main.CognitiveBatch(session_id="s", samples=[
        {"features": {"focus_score": 50.0, "signal_quality": "good",
                      "quality_basis": "contact", "batch_size": 3}},
    ])
    assert batch.samples[0].features["signal_quality"] == "good"


def test_status_does_not_touch_the_sidecar_under_push(push_mode, monkeypatch):
    """The probe ran *before* the mode check, so the rule this endpoint was
    fixed to follow was defeated by statement order. With EEG_API_TOKEN unset --
    the normal state of a hosted push deployment -- it 500s, and the frontend
    polls it every 3 s, so the headband block never updates all lesson."""
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


# ── the quality rules, which used to exist on one path only ─────────────────

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
    """Zeroed scores from a disconnected headset are not a reading of zero.

    Worse than nulls: aggregates average zeros as real readings and exclude
    nulls, so a headband on the desk read as sustained zero focus rather than as
    no data -- the can't-tell-no-data-from-zero failure arriving through the
    write side, where none of the reporting rules can see it."""
    assert signal_mapping.map_eeg_to_cognitive(_UNWORN, "s", "u") is None


def test_bad_contact_keeps_the_row_and_nulls_the_measurements():
    """"We were recording but could not measure" is not "no session happened",
    and `class_live` derives staleness from the newest row's ts -- dropping
    these would age out a session the student is still working in."""
    row = signal_mapping.map_eeg_to_cognitive(_BAD_CONTACT, "s", "u")

    assert row is not None
    assert all(row[c] is None for c in signal_mapping._MEASUREMENT_COLUMNS)
    # Still explicable after the fact.
    assert row["raw"]["signal_quality"] == "poor"
    assert row["raw"]["quality_basis"] == "contact"


def test_the_legacy_poor_heuristic_is_not_treated_as_bad_contact():
    """It reports "poor" for any focused student and says nothing about
    electrodes. Treating it as bad contact would silently disable collection for
    a whole session."""
    row = signal_mapping.map_eeg_to_cognitive(_LEGACY_POOR, "s", "u")

    assert row["focus"] == pytest.approx(0.848)


@pytest.mark.parametrize("payload,expected", [(_UNWORN, "no_signal"),
                                              (_BAD_CONTACT, "contact_poor"),
                                              (_LEGACY_POOR, "ok")])
def test_both_paths_read_one_verdict(payload, expected):
    """The rules were inline in `eeg_poller` and absent from the push path, so
    the same headband behaved differently depending on the deployment. One
    function, both callers."""
    assert signal_mapping.eeg_quality(payload) == expected


def test_the_push_endpoint_drops_no_signal_ticks(monkeypatch):
    """The half that was missing entirely."""
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
