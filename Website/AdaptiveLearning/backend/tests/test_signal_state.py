"""`get_session_signal_state`: what fusion reads; consent gates the query, so tests check `table_calls`."""

from __future__ import annotations

import pytest

import LLM_topic_decider as decider
from tests.test_access_control import _FakeSupabase

SESSION = "session-1"
USER = "user-1"

CONSENT_ALL = {"eeg_enabled": True, "headband_optical_enabled": True,
               "camera_enabled": True, "user_id": USER}
# `raw.confidence` is the signal-quality number the fusion gate reads;
# `engagement` is the focus index and is not consulted by the gate.
EEG_CALM = [{"session_id": SESSION, "focus": 0.8, "stress": 0.3, "engagement": 0.8,
             "raw": {"confidence": 0.9}}]
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
    """Not read-then-discard: consent governs the read itself."""
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
    _install(monkeypatch,
             {"eeg_enabled": True, "headband_optical_enabled": False,
              "camera_enabled": False, "user_id": USER},
             eeg=EEG_CALM, heart=HEART_HIGH)

    assert decider.get_session_signal_state(SESSION, USER).label == "focused"


def test_a_consented_heart_channel_does_change_it(monkeypatch):
    """Same rows with consent reach the opposite label, so the test above is about consent."""
    _install(monkeypatch, CONSENT_ALL, eeg=EEG_CALM, heart=HEART_HIGH)
    state = decider.get_session_signal_state(SESSION, USER)

    assert state.label == "stressed"
    assert "muse_optics" in state.reason


def test_consent_fails_closed_when_it_cannot_be_read(monkeypatch):
    fake = _FakeSupabase({}, table_raises={"signal_consent"})
    monkeypatch.setattr(decider, "supabase", fake)

    state = decider.get_session_signal_state(SESSION, USER)

    assert "cognitive_signals" not in fake.table_calls
    assert state.label == "no_eeg"
    assert not state.adjusted


def test_an_absent_consent_row_means_the_same_as_all_false(monkeypatch):
    fake = _install(monkeypatch, None, eeg=EEG_CALM)

    assert decider.get_session_signal_state(SESSION, USER).label == "no_eeg"
    assert "cognitive_signals" not in fake.table_calls


def test_heart_consent_follows_the_sensor_that_produced_the_reading(monkeypatch):
    """Camera consent alone permits a camera-sourced heart reading."""
    fake = _install(monkeypatch,
                    {"eeg_enabled": False, "headband_optical_enabled": False,
                     "camera_enabled": True, "user_id": USER},
                    heart=[{"session_id": SESSION, "stress_category": "high",
                            "trusted": True, "source": "rppg"}])
    state = decider.get_session_signal_state(SESSION, USER)

    assert "heart_signals" in fake.table_calls
    assert state.label == "stressed"


def test_no_signals_at_all_behaves_as_it_did_before_fusion(monkeypatch):
    """With only EEG present, the outcome matches EEG-only behaviour."""
    _install(monkeypatch, CONSENT_ALL, eeg=EEG_CALM)
    assert decider.get_session_signal_state(SESSION, USER).label == "focused"

    _install(monkeypatch, CONSENT_ALL,
             eeg=[{"session_id": SESSION, "focus": 0.4, "stress": 0.8,
                   "engagement": 0.4, "raw": {"confidence": 0.9}}])
    assert decider.get_session_signal_state(SESSION, USER).label == "stressed"


def test_no_session_reads_nothing(monkeypatch):
    fake = _install(monkeypatch, CONSENT_ALL, eeg=EEG_CALM)
    assert decider.get_session_signal_state(None, USER) is None
    assert fake.table_calls == []


def test_a_broken_signals_table_does_not_retract_the_others(monkeypatch):
    fake = _FakeSupabase(
        {"signal_consent": [CONSENT_ALL], "cognitive_signals": EEG_CALM},
        table_raises={"heart_signals"},
    )
    monkeypatch.setattr(decider, "supabase", fake)

    state = decider.get_session_signal_state(SESSION, USER)
    assert state.label == "focused", "a broken heart read suppressed the EEG"


def test_a_heart_row_from_a_declined_sensor_is_never_read(monkeypatch):
    """Consent is per sensor: headband allowed, camera declined, so an rppg row is not acted on."""
    _install(monkeypatch,
             {"eeg_enabled": False, "headband_optical_enabled": True,
              "camera_enabled": False, "user_id": USER},
             heart=[{"session_id": SESSION, "stress_category": "high",
                     "trusted": True, "source": "rppg"}])

    state = decider.get_session_signal_state(SESSION, USER)
    assert state.label != "stressed", "acted on a row from a declined sensor"
    assert state.channels["heart"] == "no heart samples"


def test_a_permitted_sensor_is_still_read_when_another_is_declined(monkeypatch):
    """The consent filter narrows rather than blocks."""
    _install(monkeypatch,
             {"eeg_enabled": False, "headband_optical_enabled": True,
              "camera_enabled": False, "user_id": USER},
             heart=[{"session_id": SESSION, "stress_category": "high",
                     "trusted": True, "source": "muse_optics"}])

    assert decider.get_session_signal_state(SESSION, USER).label == "stressed"


def test_a_low_confidence_emotion_does_not_withhold_the_increase(monkeypatch):
    """The gate is `emotion_confidence`."""
    fake = _install(monkeypatch, CONSENT_ALL, eeg=EEG_CALM,
                    face=[{"session_id": SESSION, "emotion": "sad",
                           "emotion_confidence": 0.05, "emotion_trusted": True}])

    state = decider.get_session_signal_state(SESSION, USER)
    assert state.label == "focused", (
        "a low-confidence emotion withheld the increase"
    )
    assert "face_signals" in fake.table_calls


def test_an_untrusted_emotion_is_rejected_outright(monkeypatch):
    """As with heart `trusted`: a confidence figure can't override an untrusted label."""
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


def test_the_fusion_gate_reads_raw_confidence_not_engagement(monkeypatch):
    """`engagement` is the focus index, not signal quality."""
    disengaged_good_contact = [{"session_id": SESSION, "focus": 0.2, "stress": 0.8,
                                "engagement": 0.2, "raw": {"confidence": 0.9}}]
    _install(monkeypatch, CONSENT_ALL, eeg=disengaged_good_contact)
    assert decider.get_session_signal_state(SESSION, USER).label == "stressed"

    focused_bad_strap = [{"session_id": SESSION, "focus": 0.9, "stress": 0.2,
                          "engagement": 0.9, "raw": {"confidence": 0.2}}]
    _install(monkeypatch, CONSENT_ALL, eeg=focused_bad_strap)
    assert decider.get_session_signal_state(SESSION, USER).label == "insufficient_signal"


@pytest.mark.parametrize("bad", ["0.9", True, 7.0, -1.0, None])
def test_a_garbage_raw_confidence_is_skipped_not_believed(monkeypatch, bad):
    """`raw` is client-supplied JSON on the push path."""
    rows = [{"session_id": SESSION, "focus": 0.9, "stress": 0.2,
             "engagement": 0.9, "raw": {"confidence": bad}}]
    _install(monkeypatch, CONSENT_ALL, eeg=rows)
    with_garbage = decider.get_session_signal_state(SESSION, USER)
    _install(monkeypatch, CONSENT_ALL, eeg=[{**rows[0], "raw": {}}])
    without = decider.get_session_signal_state(SESSION, USER)
    assert with_garbage.label == without.label
    assert with_garbage.label != "focused", "a bool must not read as full confidence"


def test_engagement_is_served_from_focus_never_from_the_stored_column():
    """The stored column's meaning changed with nothing marking which, so no reader surfaces it."""
    import main as backend_main
    shaped = backend_main._shape_summary({"focus": 0.8, "stress": 0.3, "engagement": 0.2,
                                          "cognitive_samples": 5})
    assert shaped["engagement"] == pytest.approx(0.8)
    import inspect
    src = inspect.getsource(backend_main)
    assert 'r.get("engagement")' not in src and 't.get("avg_engagement")' not in src
    assert 'cog_roll.get("avg_engagement")' not in src


def test_the_cohort_trend_serves_engagement_from_focus_too():
    import main as backend_main
    part = [{"day": "2026-06-10", "channel": "cognitive", "avg_focus": 0.8,
             "avg_stress": 0.3, "avg_engagement": 0.2,
             "sample_count": 10, "trusted_sample_count": 10, "student_count": 2}]
    merged = backend_main._merge_cohort_trend([part])
    assert merged[0]["avg_engagement"] == pytest.approx(0.8)


def test_a_derived_none_removes_the_clients_value_under_that_key():
    """Otherwise a client's `raw.confidence` reaches the fusion gate."""
    import signal_mapping
    merged = signal_mapping._raw({"raw": {"confidence": "0.99", "note": "kept"}},
                                 confidence=None, device_id="d1")
    assert "confidence" not in merged
    assert merged["note"] == "kept" and merged["device_id"] == "d1"


def test_the_cohort_trend_no_longer_fetches_the_stored_engagement():
    import main as backend_main
    assert "avg_engagement" not in backend_main._COHORT_TREND_METRICS


def test_every_cognitive_row_records_the_score_scale_it_was_measured_on():
    """Rows without the key predate the bounds widening that re-anchored the scores."""
    import signal_mapping
    row = signal_mapping.map_eeg_to_cognitive(
        {"timestamp": "t", "features": {"focus_score": 60.0, "calm_score": 50.0,
                                        "confidence": 80.0, "signal_quality": "good",
                                        "quality_basis": "contact"}}, "s", "u")
    assert row["raw"]["score_scale"] == signal_mapping.SCORE_SCALE_VERSION == 2


def test_the_score_scale_comes_from_the_rollup_rows_never_a_date():
    """The rollout is per sidecar process, so no calendar date labels it; the rollup records it."""
    import inspect
    import main as backend_main
    assert not hasattr(backend_main, "_SCORE_SCALE_2_SINCE")
    rows = [{"channel": "cognitive", "score_scale_min": 1, "score_scale_max": 1},
            {"channel": "cognitive", "score_scale_min": 2, "score_scale_max": 2},
            {"channel": "heart", "score_scale_min": None, "score_scale_max": None}]
    assert backend_main._scale_range(rows) == {"min": 1, "max": 2}
    assert backend_main._scale_range(rows[:1]) == {"min": 1, "max": 1}
    assert backend_main._scale_range([{"channel": "cognitive"}]) is None, "unrecorded is not scale 1"
    # Carried by every rollup-backed surface.
    assert '"score_scale": _scale_range(b["scale_rows"])' in inspect.getsource(backend_main._signal_trend)
    assert '"score_scale": _combine_ranges(scale_by_user.values())' in inspect.getsource(backend_main._cohort_signals)
    assert '"score_scale": scale_by_user.get(sid)' in inspect.getsource(backend_main._cohort_signals), \
        "the roster rows are labelled beside the chart, outlier flag included"
    assert 'summary["score_scale"] = _scale_ranges_many' in inspect.getsource(backend_main._signal_summary)
    assert 'out[str(sid)]["score_scale"] = scales.get(str(sid))' in inspect.getsource(backend_main._signal_summaries)
    assert backend_main._combine_ranges([{"min": 1, "max": 1}, None, {"min": 2, "max": 2}]) == {"min": 1, "max": 2}
    assert backend_main._combine_ranges([None]) is None
    assert '"score_scale": _scale_range(rollup_by.values())' in inspect.getsource(backend_main._weekly_signal_report)
    # Never on a heart or emotion row, which no re-anchoring touched.
    part = [{"day": "2026-09-07", "channel": "heart", "avg_heart_rate_bpm": 70.0,
             "sample_count": 10, "trusted_sample_count": 10, "student_count": 2}]
    assert "score_scale" not in backend_main._merge_cohort_trend([part])[0]
