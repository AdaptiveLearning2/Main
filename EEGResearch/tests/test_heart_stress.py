"""The headband's arousal score: a session baseline, then heart rate against it."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from src.app.services.heart_stress import (
    BASELINE_READINGS,
    CALIBRATION_MAX_GAP,
    CATEGORIES,
    HeartStressScorer,
)


def _reading(bpm=70.0, *, rmssd=None, trusted=True):
    return {"source": "muse_optics", "bpm": bpm, "trusted": trusted,
            "rejected_by": None, "rmssd_ms": rmssd}


def _calibrated(bpm=70.0, rmssd=None):
    scorer = HeartStressScorer()
    for _ in range(BASELINE_READINGS):
        scorer.score(_reading(bpm, rmssd=rmssd))
    assert scorer.baseline_bpm == bpm
    return scorer


def _scored(scorer, bpm, rmssd=None):
    record = _reading(bpm, rmssd=rmssd)
    scorer.score(record)
    return record


def test_readings_calibrate_until_the_baseline_forms():
    scorer = HeartStressScorer()
    records = [_scored(scorer, 70.0) for _ in range(BASELINE_READINGS)]

    for record in records[:-1]:
        assert (record["stress_category"], record["stress_score"]) == ("calibrating", None)
    # The reading that completes the baseline is scored against it.
    assert (records[-1]["stress_category"], records[-1]["stress_score"]) == ("low", 50.0)
    assert records[-1]["stress_baseline_bpm"] == 70.0


@pytest.mark.parametrize("record", [
    _reading(None, trusted=False),
    _reading(70.0, trusted=False),
    _reading(70.0, trusted=None),
])
def test_no_trusted_rate_is_neither_scored_nor_calibrated_on(record):
    scorer = HeartStressScorer()
    for _ in range(BASELINE_READINGS):
        scorer.score(dict(record))
    scorer.score(record)

    assert (record["stress_score"], record["stress_category"],
            record["stress_baseline_bpm"]) == (None, None, None)
    assert scorer.baseline_bpm is None


def test_an_unsettled_start_keeps_calibrating_until_a_settled_run():
    """A spread over 12 bpm is not a resting state; the baseline waits for one."""
    scorer = HeartStressScorer()
    first = _scored(scorer, 95.0)
    rest = [_scored(scorer, 70.0) for _ in range(BASELINE_READINGS - 1)]
    assert first["stress_category"] == "calibrating"
    assert rest[-1]["stress_category"] == "calibrating"
    assert scorer.baseline_bpm is None

    # One more settled reading slides the 95 out.
    assert _scored(scorer, 70.0)["stress_category"] == "low"
    assert scorer.baseline_bpm == 70.0


@pytest.mark.parametrize("gap, restarted", [(CALIBRATION_MAX_GAP, False),
                                             (CALIBRATION_MAX_GAP + 1, True)])
def test_a_long_gap_in_calibration_restarts_it(gap, restarted):
    """Readings minutes apart are not one settled run."""
    scorer = HeartStressScorer()
    for _ in range(BASELINE_READINGS - 1):
        scorer.score(_reading(70.0))
    for _ in range(gap):
        scorer.score(_reading(None, trusted=False))

    after = _scored(scorer, 70.0)
    assert after["stress_category"] == ("calibrating" if restarted else "low")
    assert (scorer.baseline_bpm is None) == restarted


def test_only_a_gap_in_a_row_counts():
    """Short gaps split by trusted readings are ordinary seated refusals, not one long gap."""
    scorer = HeartStressScorer()
    for _ in range(BASELINE_READINGS - 1):
        scorer.score(_reading(None, trusted=False))
        scorer.score(_reading(None, trusted=False))
        scorer.score(_reading(70.0))
    assert _scored(scorer, 70.0)["stress_category"] == "low"


def test_a_gap_after_the_baseline_formed_keeps_it():
    scorer = _calibrated(70.0)
    for _ in range(10 * CALIBRATION_MAX_GAP):
        scorer.score(_reading(None, trusted=False))
    assert _scored(scorer, 80.0)["stress_category"] == "high"


def test_the_baseline_is_the_median_so_one_outlier_does_not_move_it():
    scorer = HeartStressScorer()
    for bpm in (70, 70, 70, 71, 72, 81):
        scorer.score(_reading(float(bpm)))
    assert scorer.baseline_bpm == 70.5


@pytest.mark.parametrize("bpm, category, score", [
    (70.0, "low", 50.0),
    (60.0, "low", 25.0),
    (74.8, "low", 62.0),
    (75.0, "moderate", 62.5),
    (79.9, "moderate", 74.8),
    (80.0, "high", 75.0),
    (90.0, "high", 100.0),
    (130.0, "high", 100.0),
    (40.0, "low", 0.0),
])
def test_heart_rate_against_the_baseline(bpm, category, score):
    record = _scored(_calibrated(70.0), bpm)
    assert (record["stress_category"], record["stress_score"]) == (category, score)


def test_the_baseline_does_not_drift_with_the_session():
    """A rolling reference would decay a sustained rise back to "low"."""
    scorer = _calibrated(70.0)
    for _ in range(50):
        record = _scored(scorer, 85.0)
    assert record["stress_category"] == "high"
    assert scorer.baseline_bpm == 70.0


def test_rmssd_nudges_the_score_and_never_the_category():
    scorer = _calibrated(70.0, rmssd=40.0)
    assert scorer.baseline_rmssd_ms == 40.0

    halved = _scored(scorer, 76.0, rmssd=20.0)
    absent = _scored(scorer, 76.0)
    doubled = _scored(scorer, 76.0, rmssd=80.0)
    quartered = _scored(scorer, 76.0, rmssd=10.0)

    assert absent["stress_score"] == 65.0
    assert halved["stress_score"] == 75.0
    assert doubled["stress_score"] == 55.0
    assert quartered["stress_score"] == 75.0, "the nudge is bounded"
    assert {r["stress_category"] for r in (halved, absent, doubled, quartered)} == {"moderate"}


def test_too_few_rmssd_readings_in_calibration_leave_rmssd_out_for_the_session():
    scorer = HeartStressScorer()
    for i in range(BASELINE_READINGS):
        scorer.score(_reading(70.0, rmssd=40.0 if i < 2 else None))
    assert scorer.baseline_bpm == 70.0
    assert scorer.baseline_rmssd_ms is None
    assert _scored(scorer, 70.0, rmssd=10.0)["stress_score"] == 50.0


def test_a_zero_rmssd_is_treated_as_absent():
    scorer = _calibrated(70.0, rmssd=40.0)
    assert _scored(scorer, 70.0, rmssd=0.0)["stress_score"] == 50.0


def test_the_rate_fields_are_never_touched():
    record = _reading(80.0, rmssd=20.0)
    before = {k: record[k] for k in ("bpm", "trusted", "rejected_by", "rmssd_ms")}
    _calibrated(70.0, rmssd=40.0).score(record)
    assert {k: record[k] for k in before} == before


def test_every_category_is_one_the_database_accepts():
    """A category outside the CHECK is refused as malformed, so it is never recorded.

    Read from the newest migration defining the constraint, so a later change is seen."""
    check = None
    for path in sorted((Path(__file__).resolve().parents[2] / "supabase/migrations").glob("*.sql")):
        for m in re.finditer(r'(ADD|DROP) CONSTRAINT (IF EXISTS )?"heart_signals_stress_category_check"'
                             r'([^;]*);', path.read_text(encoding="utf-8")):
            check = m.group(3) if m.group(1) == "ADD" else None
    assert check is not None
    allowed = set(re.findall(r"'(\w+)'::\"text\"", check))
    assert set(CATEGORIES) <= allowed
    assert "calibrating" in CATEGORIES and "high" in CATEGORIES
