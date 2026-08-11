"""`get_session_signal_state` — the database half of fusion.

The rule itself is tested exhaustively in `test_signal_fusion.py` without a
database. What is tested here is what gets *read*, which is the part consent
governs: a revoked channel must never be queried, not merely ignored after the
fact. An empty result cannot distinguish "asked and got nothing" from "never
asked", so these assert on `table_calls`.
"""

from __future__ import annotations

import pytest

import LLM_topic_decider as decider
from tests.test_access_control import _FakeSupabase

SESSION = "session-1"
USER = "user-1"

CONSENT_ALL = {"eeg_enabled": True, "headband_optical_enabled": True,
               "camera_enabled": True, "user_id": USER}
EEG_CALM = [{"session_id": SESSION, "focus": 0.8, "stress": 0.3, "engagement": 0.9}]
HEART_HIGH = [{"session_id": SESSION, "stress_category": "high",
               "trusted": True, "source": "muse_optics"}]


def _install(monkeypatch, consent, **tables):
    fake = _FakeSupabase({
        "signal_consent": [consent] if consent else [],
        "cognitive_signals": tables.get("eeg", []),
        "heart_signals": tables.get("heart", []),
        "face_signals": tables.get("face", []),
    })
    monkeypatch.setattr(decider, "supabase", fake)
    return fake


def test_a_revoked_channel_is_never_queried(monkeypatch):
    """Not read-then-discard. The read itself is what consent governs, and a
    row that was never fetched cannot be acted on by a later edit."""
    fake = _install(
        monkeypatch,
        {"eeg_enabled": True, "headband_optical_enabled": False,
         "camera_enabled": False, "user_id": USER},
        eeg=EEG_CALM, heart=HEART_HIGH,
    )
    state = decider.get_session_signal_state(SESSION, USER)

    assert "cognitive_signals" in fake.table_calls
    assert "heart_signals" not in fake.table_calls, "read a revoked channel"
    assert "face_signals" not in fake.table_calls, "read a revoked channel"
    assert state.channels["heart"] == "heart revoked"


def test_a_revoked_heart_channel_cannot_change_the_difficulty(monkeypatch):
    """The row exists and says 'high'. Consent means it does not count."""
    _install(monkeypatch,
             {"eeg_enabled": True, "headband_optical_enabled": False,
              "camera_enabled": False, "user_id": USER},
             eeg=EEG_CALM, heart=HEART_HIGH)

    assert decider.get_session_signal_state(SESSION, USER).label == "focused"


def test_a_consented_heart_channel_does_change_it(monkeypatch):
    """The same rows, with consent, must reach the opposite conclusion --
    otherwise the test above proves nothing about consent."""
    _install(monkeypatch, CONSENT_ALL, eeg=EEG_CALM, heart=HEART_HIGH)
    state = decider.get_session_signal_state(SESSION, USER)

    assert state.label == "stressed"
    assert "muse_optics" in state.reason


def test_consent_fails_closed_when_it_cannot_be_read(monkeypatch):
    """A database problem must suppress signals, never enable ones the student
    may have refused. Same direction as main's `_consent()`, opposite to the
    reporting helpers, and deliberately so."""
    fake = _FakeSupabase({}, table_raises={"signal_consent"})
    monkeypatch.setattr(decider, "supabase", fake)

    state = decider.get_session_signal_state(SESSION, USER)

    assert "cognitive_signals" not in fake.table_calls
    assert state.label == "no_eeg"
    assert not state.adjusted


def test_an_absent_consent_row_means_the_same_as_all_false(monkeypatch):
    """No backfill: an unconfigured student records and acts on nothing."""
    fake = _install(monkeypatch, None, eeg=EEG_CALM)

    assert decider.get_session_signal_state(SESSION, USER).label == "no_eeg"
    assert "cognitive_signals" not in fake.table_calls


def test_heart_consent_follows_the_sensor_that_produced_the_reading(monkeypatch):
    """One flag per sensor, and the heart channel can arrive from either.
    Camera consent alone still permits a heart reading -- from the camera."""
    fake = _install(monkeypatch,
                    {"eeg_enabled": False, "headband_optical_enabled": False,
                     "camera_enabled": True, "user_id": USER},
                    heart=[{"session_id": SESSION, "stress_category": "high",
                            "trusted": True, "source": "rppg"}])
    state = decider.get_session_signal_state(SESSION, USER)

    assert "heart_signals" in fake.table_calls
    assert state.label == "stressed"


def test_no_signals_at_all_behaves_as_it_did_before_fusion(monkeypatch):
    """Phase 5 lands before heart and facial are fed by anything. With only EEG
    present the outcome must be identical to the EEG-only implementation."""
    _install(monkeypatch, CONSENT_ALL, eeg=EEG_CALM)
    assert decider.get_session_signal_state(SESSION, USER).label == "focused"

    _install(monkeypatch, CONSENT_ALL,
             eeg=[{"session_id": SESSION, "focus": 0.4, "stress": 0.8,
                   "engagement": 0.9}])
    assert decider.get_session_signal_state(SESSION, USER).label == "stressed"


def test_no_session_reads_nothing(monkeypatch):
    fake = _install(monkeypatch, CONSENT_ALL, eeg=EEG_CALM)
    assert decider.get_session_signal_state(None, USER) is None
    assert fake.table_calls == []


def test_a_broken_signals_table_does_not_retract_the_others(monkeypatch):
    """One failed query must degrade that channel only. The reporting rule,
    applied to the read side."""
    fake = _FakeSupabase(
        {"signal_consent": [CONSENT_ALL], "cognitive_signals": EEG_CALM},
        table_raises={"heart_signals"},
    )
    monkeypatch.setattr(decider, "supabase", fake)

    state = decider.get_session_signal_state(SESSION, USER)
    assert state.label == "focused", "a broken heart read suppressed the EEG"


def test_a_heart_row_from_a_declined_sensor_is_never_read(monkeypatch):
    """The mirror of the test above, and the one that was missing.

    Consent is per *sensor*. A student who allowed the headband and declined the
    camera must not have an rppg-sourced row acted on. The first version ORed the
    two flags into a single boolean and then never looked at `source` again, so
    it did. Latent, because nothing writes rppg today -- which is precisely how
    it would have survived to the point where something does.
    """
    _install(monkeypatch,
             {"eeg_enabled": False, "headband_optical_enabled": True,
              "camera_enabled": False, "user_id": USER},
             heart=[{"session_id": SESSION, "stress_category": "high",
                     "trusted": True, "source": "rppg"}])

    state = decider.get_session_signal_state(SESSION, USER)
    assert state.label != "stressed", "acted on a row from a declined sensor"
    assert state.channels["heart"] == "no heart samples"


def test_a_permitted_sensor_is_still_read_when_another_is_declined(monkeypatch):
    """The filter must narrow, not block. Same consent as above, headband row."""
    _install(monkeypatch,
             {"eeg_enabled": False, "headband_optical_enabled": True,
              "camera_enabled": False, "user_id": USER},
             heart=[{"session_id": SESSION, "stress_category": "high",
                     "trusted": True, "source": "muse_optics"}])

    assert decider.get_session_signal_state(SESSION, USER).label == "stressed"


def test_a_low_confidence_emotion_does_not_withhold_the_increase(monkeypatch):
    """The gate is `emotion_confidence`, and a bad FER+ label must not act.

    This began as a two-confidence test: `face_signals` also carried
    `identity_confidence` -- how sure we are whose face this is -- and reading it
    here let a clearly-identified face with a garbage label withhold an
    increase, while discarding a well-classified expression on a poorly
    identified face. Both silent. #86 retired that column, so the mix-up is no
    longer expressible and the fixture no longer sets it; what remains is the
    property the mix-up was violating, which is worth keeping on its own.
    """
    fake = _install(monkeypatch, CONSENT_ALL, eeg=EEG_CALM,
                    face=[{"session_id": SESSION, "emotion": "sad",
                           "emotion_confidence": 0.05, "emotion_trusted": True}])

    state = decider.get_session_signal_state(SESSION, USER)
    assert state.label == "focused", (
        "a low-confidence emotion withheld the increase"
    )
    assert "face_signals" in fake.table_calls


def test_an_untrusted_emotion_is_rejected_outright(monkeypatch):
    """Matching how the heart channel treats `trusted`. A classifier saying it
    does not stand behind a label is not answered by a confidence figure."""
    _install(monkeypatch, CONSENT_ALL, eeg=EEG_CALM,
             face=[{"session_id": SESSION, "emotion": "sad",
                    "emotion_confidence": 0.99, "emotion_trusted": False}])

    assert decider.get_session_signal_state(SESSION, USER).label == "focused"


def test_a_trusted_confident_negative_emotion_does_withhold(monkeypatch):
    """Otherwise the two tests above pass for the wrong reason."""
    _install(monkeypatch, CONSENT_ALL, eeg=EEG_CALM,
             face=[{"session_id": SESSION, "emotion": "sad",
                    "emotion_confidence": 0.9, "emotion_trusted": True}])

    state = decider.get_session_signal_state(SESSION, USER)
    assert state.label == "neutral"
    assert "withholding" in state.reason
