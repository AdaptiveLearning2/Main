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
