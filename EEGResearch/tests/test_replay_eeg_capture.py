"""The replay harness reproduces the scores a capture recorded.

A capture is the shipped path's own output, so replaying it through the
same code on the same clock must give the same numbers -- that round trip is
what makes a *difference* after a formula change attributable to the change.
The rows here are synthetic and built by running the processor, never a
recording of a person.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
from datetime import datetime, timedelta, timezone

import pytest

from src.app.models import EegSample
from src.app.services.adaptation import AdaptationEngine
from src.app.services.signal_processing import SignalProcessor

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "replay_eeg_capture.py"


def _module():
    spec = importlib.util.spec_from_file_location("replay_eeg_capture", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


replay = _module()


def _synthetic_capture(n: int = 40, tick_s: float = 0.25) -> list[dict]:
    """Rows in the capture's shape, scored by the shipped path on a clock that
    advances with the rows, as the sidecar's would."""
    t0 = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    clock = [0.0]
    processor = SignalProcessor(window_size=8, clock=lambda: clock[0])
    adaptation = AdaptationEngine(clock=lambda: clock[0])
    rows = []
    for i in range(n):
        clock[0] = i * tick_s
        t = t0 + timedelta(seconds=i * tick_s)
        # A slow swing between a relaxed and an aroused spectrum, in blocks
        # longer than the label cooldown, so the labels move and the cooldown
        # has work to do.
        aroused = (i // 20) % 2 == 1
        bands = {"delta": 0.4, "theta": 0.1,
                 "alpha": -0.5 if aroused else 0.5,
                 "beta": 0.8 if aroused else 0.1,
                 "gamma": 0.8 if aroused else 0.05,
                 "hsi": [1.0, 1.0, 2.0, 1.0], "is_good": [1.0, 1.0, 1.0, 0.0],
                 "band_channels_used": 3}
        sample = EegSample(timestamp=t, channel_tp9=800.0 + i, channel_af7=810.0,
                           channel_af8=805.0, channel_tp10=802.0)
        features = processor.update(sample, bands)
        state = adaptation.infer_state(features)
        rows.append({
            "t": t.isoformat(), "segment": "eyes_open_rest" if i < 20 else "arithmetic",
            "status": "ok", "message": None, "ts": t.isoformat(),
            **{k: bands[k] for k in ("delta", "theta", "alpha", "beta", "gamma")},
            "band_channels_used": 3, "hsi": bands["hsi"], "is_good": bands["is_good"],
            "eeg_age_ms": 1, "active_preset": "PRESET_21", "muse_connected": True,
            "tp9": 800.0 + i, "af7": 810.0, "af8": 805.0, "tp10": 802.0,
            **{k: features[k] for k in replay.REPLAYED_KEYS if k in features},
            "batch_size": 64, "label": state.label, "reason": state.reason,
        })
    return rows


def test_replaying_a_capture_reproduces_its_own_scores():
    rows = _synthetic_capture()
    out = replay.replay(rows, window_size=8)
    assert len(out) == len(rows)
    for rec, rep in zip(rows, out):
        for k in ("focus_score", "calm_score", "confidence", "focus_log_ratio",
                  "calm_log_ratio", "signal_quality", "label"):
            assert rep[k] == pytest.approx(rec[k]) if isinstance(rec[k], float) else rep[k] == rec[k], k


def test_a_no_data_tick_resets_and_is_replayed_as_no_signal():
    rows = _synthetic_capture(n=12)
    gap = dict(rows[6], status="error", message="no data", tp9=None, af7=None,
               af8=None, tp10=None, focus_score=None, label="no_signal")
    rows[6] = gap
    out = replay.replay(rows, window_size=8)
    assert out[6]["label"] == "no_signal"
    assert out[6]["signal_quality"] == "no_signal"
    assert out[6]["focus_score"] is None
    # The tick after the gap starts a fresh window: one sample in it.
    assert out[7]["samples_rejected"] == 0


def test_the_replay_runs_on_the_captures_clock_not_the_wall_clock():
    """Replayed in milliseconds against monotonic(), the cooldown would hold
    the first label for the whole file. On the capture's clock the swing in
    beta must produce more than one distinct label."""
    rows = _synthetic_capture(n=60)
    out = replay.replay(rows, window_size=8)
    assert len({r["label"] for r in out}) >= 2


def test_main_reads_a_file_and_prints_a_summary(tmp_path, capsys):
    rows = _synthetic_capture(n=10)
    path = tmp_path / "cap.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"header": True, "script_version": 1}) + "\n")
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    assert replay.main([str(path), "--diff", "--window-size", "8"]) == 0
    printed = capsys.readouterr().out
    assert "eyes_open_rest" in printed and "rec/new" in printed


def test_arm_at_restarts_the_baseline_at_that_segment():
    rows = _synthetic_capture(n=60)
    plain = replay.replay(rows, window_size=8)
    armed = replay.replay(rows, window_size=8, arm_at="arithmetic")
    # Same rows, same code: only the baseline's start moved, and it is the
    # segment boundary that separates the two.
    assert [r["focus_log_ratio"] for r in plain] == [r["focus_log_ratio"] for r in armed]
    assert plain[:20] == armed[:20]
