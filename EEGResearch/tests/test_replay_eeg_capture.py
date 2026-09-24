"""The replay harness reproduces a capture's own scores (synthetic rows, never a person's recording)."""

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


def _synthetic_capture(n: int = 40, tick_s: float = 0.25, aroused_at=None) -> list[dict]:
    """Capture-shaped rows scored by the shipped path on a row-advancing clock.

    `aroused_at(i)` picks the spectrum per row; the default alternates in 20-row blocks.
    """
    t0 = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    clock = [0.0]
    processor = SignalProcessor(window_size=8, clock=lambda: clock[0])
    adaptation = AdaptationEngine(clock=lambda: clock[0])
    rows = []
    for i in range(n):
        clock[0] = i * tick_s
        t = t0 + timedelta(seconds=i * tick_s)
        # Blocks longer than the label cooldown, so labels move and the cooldown has work to do.
        aroused = aroused_at(i) if aroused_at else (i // 20) % 2 == 1
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
    """On monotonic(), the cooldown would hold the first label for the whole file."""
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
    """Relaxed 5 s then aroused 95 s: armed at the lesson, late scores sit at 50; from row 0 they do not."""
    rows = _synthetic_capture(n=400, aroused_at=lambda i: i >= 20)
    plain = replay.replay(rows, window_size=8)
    armed = replay.replay(rows, window_size=8, arm_at="arithmetic")
    assert plain[:20] == armed[:20], "nothing before the arm point may differ"
    assert [r["focus_log_ratio"] for r in plain] == [r["focus_log_ratio"] for r in armed]
    late_plain = [r["focus_score"] for r in plain[300:]]
    late_armed = [r["focus_score"] for r in armed[300:]]
    assert late_armed[-1] == pytest.approx(50.0, abs=1.0)
    assert abs(late_plain[-1] - late_armed[-1]) > 2.0


def test_a_real_gap_is_an_ok_row_with_the_no_signal_payload():
    """A no-data tick is status ok with zeroed scores; replay must reset, not score a 0 uV sample."""
    rows = _synthetic_capture(n=12)
    gap = dict(rows[6], status="ok", tp9=0.0, af7=0.0, af8=0.0, tp10=0.0,
               focus_score=0.0, calm_score=0.0, confidence=0.0,
               signal_quality="no_signal", label="no_signal",
               delta=0.0, theta=0.0, alpha=0.0, beta=0.0, gamma=0.0)
    rows[6] = gap
    assert replay.is_gap(gap)
    out = replay.replay(rows, window_size=8)
    assert out[6]["label"] == "no_signal" and out[6]["focus_score"] is None
    assert out[7]["samples_rejected"] == 0


def test_a_held_no_signal_label_on_a_real_row_is_not_a_gap():
    """The engine holds "no_signal" after a gap on ticks with real bands; keying on the label compounds gaps."""
    rows = _synthetic_capture(n=12)
    rows[6] = dict(rows[6], status="error", message="no data", tp9=None,
                   signal_quality="no_signal", label="no_signal")
    for i in (7, 8, 9):
        rows[i] = dict(rows[i], label="no_signal", signal_quality="degraded")
    assert replay.is_gap(rows[6])
    assert not any(replay.is_gap(rows[i]) for i in (7, 8, 9))
    out = replay.replay(rows, window_size=8)
    assert out[7]["focus_score"] is not None
    # Round trip: replaying the replay finds the same one gap.
    again = replay.replay(out, window_size=8)
    assert sum(r["signal_quality"] == "no_signal" for r in again) == 1


def test_arm_at_an_unknown_segment_is_refused_not_ignored(tmp_path, capsys):
    rows = _synthetic_capture(n=10)
    with pytest.raises(ValueError, match="no such segment"):
        replay.replay(rows, window_size=8, arm_at="lesson")
    path = tmp_path / "cap.jsonl"
    with path.open("w", encoding="utf-8") as fh:
        fh.write(json.dumps({"header": True, "script_version": 1}) + "\n")
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    assert replay.main([str(path), "--window-size", "8", "--arm-at", "lesson"]) == 2
    assert "no such segment" in capsys.readouterr().err


def test_arm_at_restarts_the_label_engine_as_the_live_arm_does():
    """StreamManager.arm_baseline restarts both baseline and label engine."""
    import inspect
    src = inspect.getsource(replay.replay)
    assert "processor.restart_baseline()" in src and "adaptation.restart()" in src
    # A cooldown-held focused label is gone on the arm row itself.
    rows = _synthetic_capture(n=60, aroused_at=lambda i: i < 20)
    for r in rows[16:20]:
        r["label"] = "focused"
    out = replay.replay(rows, window_size=8, arm_at="arithmetic")
    assert out[20]["label"] != "focused"
