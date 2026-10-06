"""`GET /api/recording/me`: what the lesson page may switch on, per channel, and why not."""
import os
from datetime import timedelta

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

STUDENT = "student-1"
ALL_ON = {"eeg_enabled": True, "headband_optical_enabled": True, "camera_enabled": True}
SWITCHES = [("recording_eeg_enabled", "eeg"),
            ("recording_heart_enabled", "headband_optical"),
            ("recording_camera_enabled", "camera")]


class _Consent:
    """`signal_consent` alone, recording the filter each read applied."""

    def __init__(self, row=None, raises=False):
        self.row, self.raises, self.reads = row, raises, []

    def table(self, name):
        assert name == "signal_consent", name
        return self

    def select(self, *_a):
        return self

    def eq(self, col, val):
        self.reads.append((col, val))
        return self

    def limit(self, *_a):
        return self

    def execute(self):
        if self.raises:
            raise RuntimeError("signal_consent unavailable")
        return type("R", (), {"data": [{**self.row, "user_id": STUDENT}] if self.row else []})()


def _ask(monkeypatch, row=None, raises=False):
    fake = _Consent(row, raises)
    monkeypatch.setattr(main, "supabase", fake)
    monkeypatch.setattr(main, "get_user", lambda _r: {"id": STUDENT})
    res = TestClient(main.app).get("/api/recording/me")
    assert res.status_code == 200, res.text
    return res, fake


def _states(res):
    return {c: res.json()[c]["state"] for c in main.CONSENT_CHANNELS}


def _year(monkeypatch, state):
    monkeypatch.setattr(main, "_retention_window", lambda: {
        "state": state, "starts_on": "2026-09-01", "ends_on": "2027-06-30", "timezone": "UTC"})


def test_consent_in_an_open_year_permits_every_channel(monkeypatch):
    res, fake = _ask(monkeypatch, ALL_ON)
    assert _states(res) == {"eeg": "permitted", "headband_optical": "permitted", "camera": "permitted"}
    # The caller's own row, and nobody else's.
    assert fake.reads == [("user_id", STUDENT)]


@pytest.mark.parametrize("channel", main.CONSENT_CHANNELS)
def test_a_declined_channel_is_declined_and_the_others_are_not(monkeypatch, channel):
    res, _ = _ask(monkeypatch, {**ALL_ON, f"{channel}_enabled": False})
    assert _states(res) == {c: "declined" if c == channel else "permitted"
                            for c in main.CONSENT_CHANNELS}


def test_no_consent_row_declines_everything(monkeypatch):
    res, _ = _ask(monkeypatch, None)
    assert set(_states(res).values()) == {"declined"}


@pytest.mark.parametrize("flag,channel", SWITCHES)
def test_a_switch_outranks_consent(monkeypatch, set_flag, flag, channel):
    set_flag(flag, False)
    # Declined as well, so only the order decides which reason is given.
    res, _ = _ask(monkeypatch, {**ALL_ON, f"{channel}_enabled": False})
    assert _states(res) == {c: "switched_off" if c == channel else "permitted"
                            for c in main.CONSENT_CHANNELS}


@pytest.mark.parametrize("state,expected", [
    (main.WINDOW_BEFORE, "school_year_not_started"),
    (main.WINDOW_AFTER, "school_year_ended"),
    (main.WINDOW_UNCONFIGURED, "school_year_unconfigured"),
])
def test_a_closed_year_outranks_the_switch_and_consent(monkeypatch, set_flag, state, expected):
    _year(monkeypatch, state)
    set_flag("recording_camera_enabled", False)
    res, _ = _ask(monkeypatch, {**ALL_ON, "eeg_enabled": False})
    assert set(_states(res).values()) == {expected}


def test_an_unreadable_year_is_unknown_not_a_refusal(monkeypatch):
    _year(monkeypatch, main.WINDOW_UNREADABLE)
    res, _ = _ask(monkeypatch, ALL_ON)
    assert set(_states(res).values()) == {"unknown"}


def test_a_definite_refusal_outranks_an_unreadable_year(monkeypatch, set_flag):
    """The page stops a sensor only on a refusal, so `unknown` must not hide one."""
    _year(monkeypatch, main.WINDOW_UNREADABLE)
    set_flag("recording_camera_enabled", False)
    res, _ = _ask(monkeypatch, {**ALL_ON, "eeg_enabled": False})
    assert _states(res) == {"eeg": "declined", "headband_optical": "unknown",
                            "camera": "switched_off"}


def test_unreadable_consent_is_unknown_but_a_switch_still_names_itself(monkeypatch, set_flag):
    set_flag("recording_camera_enabled", False)
    res, fake = _ask(monkeypatch, raises=True)
    assert _states(res) == {"eeg": "unknown", "headband_optical": "unknown",
                            "camera": "switched_off"}
    assert fake.reads == [("user_id", STUDENT)]


def test_the_bypass_counts_as_consent_without_reading_it(monkeypatch, set_flag):
    set_flag("consent_enforcement_enabled", False,
             bypass_until=(main._utc_now() + timedelta(hours=1)).isoformat())
    res, fake = _ask(monkeypatch, None)
    assert set(_states(res).values()) == {"permitted"}
    assert fake.reads == []


def test_the_bypass_does_not_open_a_closed_year(monkeypatch, set_flag):
    set_flag("consent_enforcement_enabled", False,
             bypass_until=(main._utc_now() + timedelta(hours=1)).isoformat())
    _year(monkeypatch, main.WINDOW_AFTER)
    res, _ = _ask(monkeypatch, None)
    assert set(_states(res).values()) == {"school_year_ended"}


def test_a_gate_that_raises_is_a_200_with_every_channel_unknown(monkeypatch):
    def _boom(_student_id, consent=None):
        raise RuntimeError("feature_flags unavailable")
    monkeypatch.setattr(main, "_may_record", _boom)
    res, _ = _ask(monkeypatch, ALL_ON)
    assert _states(res) == {c: "unknown" for c in main.CONSENT_CHANNELS}


def test_the_answer_names_no_user(monkeypatch):
    res, _ = _ask(monkeypatch, {**ALL_ON, "user_id": STUDENT})
    body = res.json()
    assert set(body) == {*main.CONSENT_CHANNELS, "sensors", "heart_sources"}
    assert all(set(body[c]) == {"state", "reason"} for c in main.CONSENT_CHANNELS)
    assert STUDENT not in res.text


@pytest.mark.parametrize("state,expected", [
    (main.WINDOW_AFTER, "the school year has ended"),
    (main.WINDOW_UNCONFIGURED, "no school year is configured, so nothing is recorded"),
])
def test_each_refusal_carries_the_ingest_endpoints_reason(monkeypatch, set_flag, state, expected):
    res, _ = _ask(monkeypatch, {**ALL_ON, "eeg_enabled": False, "camera_enabled": False})
    reasons = {c: v["reason"] for c, v in res.json().items() if c in main.CONSENT_CHANNELS}
    assert reasons == {"eeg": "eeg not consented", "headband_optical": None, "camera": "camera not consented"}

    set_flag("recording_eeg_enabled", False)
    res, _ = _ask(monkeypatch, {**ALL_ON, "headband_optical_enabled": False})
    assert res.json()["eeg"]["reason"] == "recording is switched off by an administrator"
    assert res.json()["headband_optical"]["reason"] == "headband heart sensor not consented"

    _year(monkeypatch, state)
    res, _ = _ask(monkeypatch, ALL_ON)
    assert {res.json()[c]["reason"] for c in main.CONSENT_CHANNELS} == {expected}


def test_an_unknown_channel_gives_no_reason(monkeypatch):
    res, _ = _ask(monkeypatch, raises=True)
    assert {res.json()[c]["reason"] for c in main.CONSENT_CHANNELS} == {None}


@pytest.mark.parametrize("row,camera,headband", [
    (ALL_ON, (True, False), (True, False)),
    # Heart rate alone still allows the headband; nothing refuses it.
    ({**ALL_ON, "eeg_enabled": False, "camera_enabled": False}, (False, True), (True, False)),
    ({**ALL_ON, "eeg_enabled": False, "headband_optical_enabled": False}, (True, False), (False, True)),
])
def test_each_sensor_is_allowed_by_any_channel_and_refused_only_by_all(monkeypatch, row, camera, headband):
    sensors = _ask(monkeypatch, row)[0].json()["sensors"]
    assert (sensors["camera"]["allowed"], sensors["camera"]["refused"]) == camera
    assert (sensors["headband"]["allowed"], sensors["headband"]["refused"]) == headband


def test_unknown_neither_allows_nor_refuses_a_sensor(monkeypatch):
    sensors = _ask(monkeypatch, raises=True)[0].json()["sensors"]
    assert sensors == {"camera": {"allowed": False, "refused": False},
                       "headband": {"allowed": False, "refused": False}}


def test_heart_sources_map_to_the_channel_the_ingest_gate_records_them_under(monkeypatch):
    expected = {source: flag.removeprefix("record_")
                for flag, sources in main._HEART_SOURCES_BY_RECORD_FLAG.items() for source in sources}
    assert expected, "the ingest gate's table moved; this test is no longer reading it"
    assert _ask(monkeypatch, ALL_ON)[0].json()["heart_sources"] == expected
