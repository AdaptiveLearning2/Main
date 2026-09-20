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


def test_the_flattened_row_carries_every_feature_field_the_sidecar_declares():
    """A field added to schemas.FeatureData must reach the capture, or the
    next hardware recording has no column for the thing it was added to
    tune. Derived from the model, not from a list here."""
    from src.app.schemas import FeatureData
    row = capture.flatten_state(_envelope(), segment="x", t="2026-09-13T00:00:00+00:00")
    missing = [f for f in FeatureData.model_fields if f not in row]
    assert missing == [], f"flatten_state does not carry {missing}"


def test_the_arithmetic_prompt_asks_for_silence():
    """Aloud, the segment measures speech muscle: beta rose 0.08 with gamma
    0.10 and good contact halved, and broadband EMG corrupts the temporal
    alpha residual the local calm is. The method moved to silent for the
    raw capture while this prompt still said aloud -- and the wearer reads
    the prompt, not the method note."""
    prompt = next(p for s, _, p in capture.DEFAULT_PROTOCOL if s == "arithmetic")
    assert "aloud" not in prompt.lower()
    assert "silent" in prompt.lower()


def test_the_short_protocol_is_the_long_one_s_first_two_segments():
    """Sliced, not restated: the five-minute run is compared against the
    long one segment by segment, so a prompt or a duration that drifted
    between them would be an unrecorded difference in method."""
    assert capture.CLOSED_OPEN_PROTOCOL == capture.DEFAULT_PROTOCOL[:2]
    assert [s for s, _, _ in capture.CLOSED_OPEN_PROTOCOL] == [
        "eyes_closed_rest", "eyes_open_rest"]


def test_the_header_records_the_protocol_that_was_selected():
    """`--protocol closed_open` exists so the file does not claim segments
    nobody performed -- running the long protocol and stopping after two
    would write a nine-segment header over a two-segment recording."""
    args = capture.build_parser().parse_args(
        ["--out", "x", "--source", "bridge", "--protocol", "closed_open"])
    h = capture.header(args, capture.CLOSED_OPEN_PROTOCOL)
    assert h["protocol"] == [{"segment": "eyes_closed_rest", "seconds": 120},
                             {"segment": "eyes_open_rest", "seconds": 120}]


# -- the live slope check --

def _feed(monitor, series):
    """Push one frame per sample of the temporal pair."""
    for v in series:
        monitor.push({"tp9": float(v), "tp10": float(v)})


def _white(n, seed=0):
    import numpy as np
    return np.random.default_rng(seed).normal(0.0, 30.0, n)


def test_the_slope_monitor_says_nothing_until_its_window_is_full():
    """Enough samples for Welch to succeed, fewer than the window asks for:
    the guard has to be what withholds the number, not an exception from a
    short buffer, or a half-window slope gets reported as a reading."""
    m = capture.SlopeMonitor(window_seconds=8.0)
    half = _white(4 * 256)
    _feed(m, half)
    assert m.slope() is None and m.line() is None, "a partial window is not a reading"
    # The same samples do produce one once the window is full.
    _feed(m, _white(4 * 256, seed=1))
    assert m.slope() is not None


def test_a_flat_spectrum_is_reported_as_flat():
    """Broadband power flattens the 1/f slope. That is what muscle does, it
    sits on the alpha band the local calm reads, and on the second wearer's
    capture nothing on screen said so until it was scored afterwards."""
    m = capture.SlopeMonitor(window_seconds=4.0, warn_above=-1.0)
    _feed(m, _white(4 * 256))
    s = m.slope()
    assert s is not None and s > -1.0, "white noise is flat by construction"
    assert "FLAT" in m.line()


def test_a_steep_spectrum_passes_and_is_measured_as_steeper():
    """The comparison, not the absolute value, is what the check rests on:
    a 1/f signal must read steeper than a flat one through this same path."""
    from tests.test_eeg_spectrum import pink
    steep = capture.SlopeMonitor(window_seconds=4.0, warn_above=-0.5)
    _feed(steep, pink(4 * 256, 0))
    flat = capture.SlopeMonitor(window_seconds=4.0, warn_above=-0.5)
    _feed(flat, _white(4 * 256))
    assert steep.slope() < flat.slope()
    assert steep.line().endswith("ok") and "FLAT" in flat.line()


def test_the_slope_check_never_raises_on_a_bad_frame():
    """A diagnostic must not be able to kill the recording it describes."""
    m = capture.SlopeMonitor(window_seconds=4.0)
    for bad in ({}, {"tp9": None}, {"tp9": "x", "tp10": []}, {"tp10": float("nan")}):
        m.push(bad)
    assert m.slope() is None
    _feed(m, _white(4 * 256))
    assert m.line() is not None


def test_the_slope_check_is_on_by_default_and_can_be_turned_off():
    ap = capture.build_parser()
    assert ap.parse_args(["--out", "x"]).no_slope_check is False
    assert ap.parse_args(["--out", "x"]).slope_warn == -1.0
    assert ap.parse_args(["--out", "x", "--no-slope-check"]).no_slope_check is True


def _feed_one(monitor, series, channel):
    """Only one temporal channel delivers -- a dead or lifted contact."""
    for v in series:
        monitor.push({channel: float(v)})


def test_a_starved_temporal_channel_says_so_rather_than_going_quiet():
    """The state the monitor exists for: calm is an alpha residual at the
    temporal pair, so a contact that stops delivering both ruins the capture
    and silences the check. Returning None for it looked exactly like the
    opening seconds of a recording -- frame counts ticking, no slope."""
    m = capture.SlopeMonitor(window_seconds=4.0)
    _feed_one(m, _white(3 * 4 * 256), "tp9")
    assert m.slope() is None, "one channel cannot give a pair slope"
    line = m.line()
    assert line is not None, "a starved channel must not be silent"
    assert "cannot read the slope" in line and "tp10" in line


def test_a_channel_delivering_nothing_usable_is_the_same_state():
    """A contact present but sending None reaches the same place as one
    sending nothing at all, and must say so the same way."""
    m = capture.SlopeMonitor(window_seconds=4.0)
    for v in _white(4 * 256):
        m.push({"tp9": float(v), "tp10": None})
    assert "cannot read the slope" in (m.line() or "")


def test_a_window_that_cannot_be_fit_is_reported_not_swallowed():
    """Both channels full, nothing finite in them: the fit resolves to
    nothing and the operator is told, rather than reading silence as fine."""
    m = capture.SlopeMonitor(window_seconds=4.0)
    nan = [float("nan")] * (4 * 256)
    _feed(m, nan)
    assert m.slope() is None
    assert "cannot read the slope" in (m.line() or "")


def test_a_filling_window_stays_silent_and_a_starved_one_does_not():
    """The two states this split exists to separate, asserted together so
    neither drifts into the other's branch."""
    filling = capture.SlopeMonitor(window_seconds=4.0)
    _feed(filling, _white(256))
    assert filling.line() is None, "an ordinary start has nothing to say"
    starved = capture.SlopeMonitor(window_seconds=4.0)
    _feed_one(starved, _white(4 * 256), "tp9")
    assert starved.line() is not None
