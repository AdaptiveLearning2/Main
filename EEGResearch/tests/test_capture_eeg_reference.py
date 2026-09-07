"""Tests for the EEG reference capture script.

The two live sources need a headband. What is tested is where a capture may
be written, that a row keeps the fields Phase 0 scores on, and that the
per-segment summary does not fold "no reading" into zero.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

SCRIPT = (pathlib.Path(__file__).resolve().parents[1]
          / "scripts" / "capture_eeg_reference.py")


def _module():
    spec = importlib.util.spec_from_file_location("capture_eeg_reference", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


capture = _module()


def _envelope(**features):
    feats = {
        "focus_score": 61.2, "calm_score": 48.0, "confidence": 70.0,
        "signal_quality": "good", "quality_basis": "contact",
        "samples_rejected": 0, "band_channels_used": 4, "batch_size": 64,
        "focus_log_ratio": -0.31, "calm_log_ratio": 0.12,
    }
    feats.update(features)
    return {
        "status": "ok",
        "message": "ok",
        "data": {
            "timestamp": "2026-09-07T10:00:00+00:00",
            "channels": {"tp9": 800.1, "af7": 801.2, "af8": 799.3, "tp10": 802.4},
            "features": feats,
            "state": {"label": "neutral", "reason": "r", "confidence": 70.0,
                      "focus_score": 61.2, "calm_score": 48.0},
            "bands": {"delta": 0.9, "theta": 0.5, "alpha": 0.7, "beta": 0.4, "gamma": 0.1},
            "ingestion": {"band_channels_used": 4, "hsi": [1, 1, 1, 1],
                          "is_good": [1, 1, 1, 1], "eeg_age_ms": 12,
                          "active_preset": "PRESET_21", "muse_connected": True},
        },
    }


# -- where a capture may be written --

def test_a_path_inside_the_repository_is_refused():
    inside = capture.repo_root() / "EEGResearch" / "tests" / "fixtures" / "eeg.jsonl"
    with pytest.raises(SystemExit) as exc:
        capture.refuse_if_inside_repo(inside)
    assert "inside the repository" in str(exc.value)


def test_a_path_outside_the_repository_is_allowed(tmp_path):
    capture.refuse_if_inside_repo(tmp_path / "eeg.jsonl")


# -- rows --

def test_a_row_carries_the_unscaled_ratios_and_everything_downstream():
    row = capture.flatten_state(_envelope(), segment="arithmetic", t="t0")
    assert row["segment"] == "arithmetic"
    assert row["focus_log_ratio"] == -0.31
    assert row["calm_log_ratio"] == 0.12
    assert row["focus_score"] == 61.2
    assert row["alpha"] == 0.7
    assert row["hsi"] == [1, 1, 1, 1]
    assert row["label"] == "neutral"
    assert row["ts"] == "2026-09-07T10:00:00+00:00"
    assert row["message"] is None, "an ok envelope carries no message"


def test_a_missing_ratio_is_null_not_zero():
    """An older sidecar, or a frame with no usable bands, reports no ratio.
    Zero would read as a real reading at the population midpoint."""
    env = _envelope()
    del env["data"]["features"]["focus_log_ratio"]
    env["data"]["features"]["calm_log_ratio"] = None
    row = capture.flatten_state(env, segment="x", t="t0")
    assert row["focus_log_ratio"] is None
    assert row["calm_log_ratio"] is None


def test_an_idle_envelope_still_produces_a_row_saying_so():
    row = capture.flatten_state({"status": "idle", "message": "not started", "data": None},
                                segment="between", t="t0")
    assert row["status"] == "idle"
    assert row["message"] == "not started"
    assert row["focus_log_ratio"] is None


# -- scoring --

def test_summary_groups_by_segment_and_counts_nulls_apart_from_values():
    rows = [
        capture.flatten_state(_envelope(focus_log_ratio=-0.5), segment="eyes_closed_rest", t="a"),
        capture.flatten_state(_envelope(focus_log_ratio=-0.3), segment="eyes_closed_rest", t="b"),
        capture.flatten_state(_envelope(focus_log_ratio=None), segment="eyes_closed_rest", t="c"),
        capture.flatten_state(_envelope(focus_log_ratio=0.4), segment="arithmetic", t="d"),
    ]
    s = capture.summarize(rows)
    assert s["order"] == ["eyes_closed_rest", "arithmetic"]
    closed = s["segments"]["eyes_closed_rest"]["fields"]["focus_log_ratio"]
    assert closed["n"] == 2
    assert closed["missing"] == 1
    assert closed["mean"] == pytest.approx(-0.4)
    arith = s["segments"]["arithmetic"]["fields"]["focus_log_ratio"]
    assert arith["n"] == 1 and arith["sd"] == 0.0
    assert s["segments"]["arithmetic"]["labels"] == {"neutral": 1}


def test_a_zero_ratio_is_a_value_not_a_gap():
    rows = [capture.flatten_state(_envelope(focus_log_ratio=0.0), segment="s", t="a")]
    f = capture.summarize(rows)["segments"]["s"]["fields"]["focus_log_ratio"]
    assert f == {"n": 1, "missing": 0, "mean": 0.0, "sd": 0.0}


def test_the_baseline_window_reports_what_the_wearer_was_doing():
    """The processor latches its baseline on the first 60 usable ticks.
    Which segment those fell in is the question Phase 0 step 4 asks."""
    rows = [capture.flatten_state(_envelope(focus_log_ratio=None), segment="between", t="0")]
    rows += [capture.flatten_state(_envelope(), segment="between", t=str(i)) for i in range(10)]
    rows += [capture.flatten_state(_envelope(), segment="eyes_closed_rest", t=str(i))
             for i in range(70)]
    s = capture.summarize(rows)
    assert s["baseline_window_rows"] == 60
    assert s["baseline_window_segments"] == {"between": 10, "eyes_closed_rest": 50}


def test_summarize_round_trips_a_written_capture(tmp_path):
    out = tmp_path / "c.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(json.dumps({"header": True, "protocol": []}) + "\n")
        fh.write(json.dumps(capture.flatten_state(_envelope(), segment="s", t="a")) + "\n")
    rows = capture.read_rows(str(out))
    assert len(rows) == 1, "the header line is method, not a row"
    assert capture.summarize(rows)["segments"]["s"]["rows"] == 1


# -- the protocol --

def test_the_protocol_covers_the_segments_the_acceptance_questions_need():
    names = {s for s, _, _ in capture.DEFAULT_PROTOCOL}
    assert {"eyes_closed_rest", "eyes_open_rest", "arithmetic",
            "jaw_clench", "blinking", "fidget"} <= names


def test_the_header_records_method_and_no_identity():
    args = capture.build_parser().parse_args(["--out", "x", "--notes", "laptop, quiet room"])
    h = capture.header(args, capture.DEFAULT_PROTOCOL)
    assert h["source"] == "sidecar"
    assert h["protocol"][0] == {"segment": "eyes_closed_rest", "seconds": 120}
    assert not any(k in h for k in ("subject", "name", "user", "email"))
