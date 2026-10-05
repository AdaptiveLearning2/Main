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
    return {c: v["state"] for c, v in res.json().items()}


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
    assert set(body) == set(main.CONSENT_CHANNELS)
    assert all(set(v) == {"state"} for v in body.values())
    assert STUDENT not in res.text
