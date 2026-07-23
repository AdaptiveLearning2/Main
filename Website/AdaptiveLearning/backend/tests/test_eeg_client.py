"""Tests for the EEG sidecar client's payload mapping.

map_eeg_to_cognitive is the only thing standing between the sidecar's 0..100
scores and what gets persisted to cognitive_signals, so a mistake here is
silent: the rows look plausible and nothing downstream can detect it.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import eeg_client  # noqa: E402


def _payload(**features):
    return {
        "timestamp": "2026-07-22T10:00:00+00:00",
        "features": features,
        "bands": {"alpha": 0.3, "beta": 0.4, "theta": 0.1, "delta": 0.4, "gamma": 0.05},
    }


@pytest.mark.parametrize(
    "score, expected",
    [
        (0.0, 0.0),
        (1.2, 0.012),
        (50.0, 0.5),
        (100.0, 1.0),
    ],
)
def test_scores_rescale_from_the_fixed_0_100_contract(score, expected):
    row = eeg_client.map_eeg_to_cognitive(_payload(focus_score=score), "s", "u")
    assert row["focus"] == pytest.approx(expected)


def test_low_focus_is_not_stored_as_maximum_focus():
    """The regression this file exists for.

    The old scale-sniffing (`if v > 1.5: v /= 100`) let any score at or below
    1.5 skip the divide and clamp to 1.0. A barely-engaged student reading 1.2
    out of 100 was persisted as 100% focus -- the exact inverse of the truth,
    in the range that should be driving difficulty *down*.
    """
    row = eeg_client.map_eeg_to_cognitive(_payload(focus_score=1.2), "s", "u")
    assert row["focus"] < 0.05, "a 1.2/100 focus score must not read as near-maximum"


def test_stress_is_the_complement_of_calm():
    row = eeg_client.map_eeg_to_cognitive(_payload(calm_score=25.0), "s", "u")
    assert row["stress"] == pytest.approx(0.75)


def test_missing_and_malformed_scores_stay_null():
    row = eeg_client.map_eeg_to_cognitive(_payload(focus_score=None, calm_score="abc"), "s", "u")
    assert row["focus"] is None
    assert row["stress"] is None, "stress must stay null rather than defaulting to 1.0"


def test_out_of_range_scores_are_clamped():
    row = eeg_client.map_eeg_to_cognitive(_payload(focus_score=140.0), "s", "u")
    assert row["focus"] == 1.0
