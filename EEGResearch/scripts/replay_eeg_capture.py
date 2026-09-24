"""Replay a sidecar-mode EEG reference capture through the scoring path.

Feeds each row through fresh ``SignalProcessor.update`` / ``AdaptationEngine.infer_state`` on the
capture's own clock (row ``t``), so smoothing and cooldowns behave as live. ``--diff`` against the
recorded columns is offset by the sidecar's pre-capture baseline; to score a formula change, compare
replay to replay with ``--save`` then ``--against PATH``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys
from datetime import datetime
from typing import Any

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from src.app.models import EegSample  # noqa: E402
from src.app.services.adaptation import AdaptationEngine  # noqa: E402
from src.app.services.signal_processing import SignalProcessor  # noqa: E402

BAND_KEYS = ("delta", "theta", "alpha", "beta", "gamma")
CONTACT_KEYS = ("hsi", "is_good", "band_channels_used")
# What the replay recomputes; everything else on the row is carried through.
REPLAYED_KEYS = (
    "focus_log_ratio", "calm_log_ratio", "focus_score", "calm_score", "confidence",
    "signal_quality", "quality_basis", "samples_rejected", "label", "reason",
    "contact_ratio", "samples_artifact", "artifact_reason", "samples_no_delta",
    "samples_no_spread", "calm_source", "calm_alpha_residual", "spectrum_ready",
    "spectrum_reason", "spectrum_slope", "calm_measured", "calm_held_seconds",
    "focus_centred", "calm_centred",
    "focus_log_ratio_smoothed", "calm_log_ratio_smoothed",
)


def is_gap(row: dict[str, Any]) -> bool:
    """Whether the sidecar had no data on this tick.

    A gap arrives as status "ok" with signal_quality "no_signal" and zeroed channels. The label is
    not consulted: the engine holds "no_signal" on ticks that carry real bands.
    """
    return (row.get("status") != "ok" or row.get("tp9") is None
            or row.get("signal_quality") == "no_signal")


def _capture_module():
    spec = importlib.util.spec_from_file_location(
        "capture_eeg_reference", HERE / "capture_eeg_reference.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _parse_t(value: str) -> datetime:
    return datetime.fromisoformat(value)


def replay(rows: list[dict[str, Any]], *, window_size: int = 20,
           arm_at: str | None = None) -> list[dict[str, Any]]:
    """Return one row per input row with the scored fields recomputed.

    `arm_at`: segment whose first row restarts the baseline, as the first question does;
    without it the baseline comes from Connect.
    """
    if not rows:
        return []
    if arm_at is not None and not any(r.get("segment") == arm_at for r in rows):
        # Loud: an unknown segment would silently replay un-armed.
        present = sorted({r.get("segment") for r in rows if r.get("segment")})
        raise ValueError(f"--arm-at {arm_at!r}: no such segment; capture has {present}")
    t0 = _parse_t(rows[0]["t"])
    clock_now = [0.0]
    processor = SignalProcessor(window_size=window_size, clock=lambda: clock_now[0])
    adaptation = AdaptationEngine(clock=lambda: clock_now[0])
    out: list[dict[str, Any]] = []
    armed = False
    for row in rows:
        clock_now[0] = (_parse_t(row["t"]) - t0).total_seconds()
        if arm_at is not None and not armed and row.get("segment") == arm_at:
            # Both halves of StreamManager.arm_baseline.
            processor.restart_baseline()
            adaptation.restart()
            armed = True
        replayed = dict(row)
        if is_gap(row):
            processor.reset()
            adaptation.reset_for_signal_loss()
            for k in REPLAYED_KEYS:
                replayed[k] = None
            replayed["label"] = "no_signal"
            replayed["signal_quality"] = "no_signal"
            out.append(replayed)
            continue
        sample = EegSample(
            timestamp=_parse_t(row["ts"]) if row.get("ts") else _parse_t(row["t"]),
            channel_tp9=float(row["tp9"]), channel_af7=float(row["af7"]),
            channel_af8=float(row["af8"]), channel_tp10=float(row["tp10"]),
        )
        meta = {k: row.get(k) for k in BAND_KEYS + CONTACT_KEYS}
        if all(meta.get(k) is None for k in BAND_KEYS):
            meta = {k: row.get(k) for k in CONTACT_KEYS}
        features = processor.update(sample, meta)
        state = adaptation.infer_state(features)
        for k in REPLAYED_KEYS:
            if k in features:
                replayed[k] = features[k]
        replayed["label"] = state.label
        replayed["reason"] = state.reason
        # Diagnostics the shipped path may add beyond the capture's columns.
        for k, v in features.items():
            replayed.setdefault(k, v)
        out.append(replayed)
    return out


def _print_diff(capture, recorded: list[dict[str, Any]], replayed: list[dict[str, Any]]) -> None:
    before = capture.summarize(recorded)
    after = capture.summarize(replayed)
    fields = ("focus_score", "calm_score", "confidence")
    print(f"\n{'segment':22} " + " ".join(f"{f + ' rec/new':>26}" for f in fields) + "   focused rec/new")
    for seg in before["order"]:
        b = before["segments"][seg]
        a = after["segments"].get(seg, b)

        def m(s, f):
            v = s["fields"][f]["mean"]
            return "   --  " if v is None else f"{v:7.1f}"

        cols = " ".join(f"{m(b, f):>12} /{m(a, f):>12}" for f in fields)
        print(f"{seg:22} {cols}   {b['labels'].get('focused', 0):3} / {a['labels'].get('focused', 0):3}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", help="a sidecar-mode capture (.jsonl or .jsonl.gz)")
    ap.add_argument("--diff", action="store_true",
                    help="print recorded score means beside the replayed ones "
                         "(offset by the sidecar's pre-capture baseline; see module doc)")
    ap.add_argument("--save", metavar="PATH",
                    help="write the replayed rows as jsonl, for a later --against")
    ap.add_argument("--against", metavar="PATH",
                    help="diff against a previous --save instead of the recorded columns")
    ap.add_argument("--window-size", type=int, default=20)
    ap.add_argument("--arm-at", metavar="SEGMENT",
                    help="restart the baseline at this segment's first row, as the first question does")
    args = ap.parse_args(argv)

    capture = _capture_module()
    rows = capture.read_rows(args.path)
    try:
        replayed = replay(rows, window_size=args.window_size, arm_at=args.arm_at)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    capture.print_summary(capture.summarize(replayed))
    if args.save:
        capture.refuse_if_inside_repo(pathlib.Path(args.save))
        with open(args.save, "w", encoding="utf-8") as fh:
            for r in replayed:
                fh.write(json.dumps(r) + "\n")
        print(f"saved {len(replayed)} replayed rows to {args.save}", file=sys.stderr)
    if args.against:
        _print_diff(capture, capture.read_rows(args.against), replayed)
    elif args.diff:
        _print_diff(capture, rows, replayed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
