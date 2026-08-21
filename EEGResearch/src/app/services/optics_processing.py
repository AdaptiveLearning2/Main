"""Builds the `heart` record from one optical window.

`ppg_processing` computes the rate; this module decides whether it's
trustworthy enough to record, mirroring how the camera path splits
`pos_rppg`/`ppg_processing` from `face_processing`.

Kept separate from `face_processing` rather than sharing it, because that
module imports the `face` extra -- a headband-only deployment shouldn't need
a camera dependency. So the constants below duplicate that module's rather
than importing them; they're also for a different sensor, so the numbers
aren't the same anyway.

Validated seated only: against a watch ECG at a desk, 14 of 16 windows
accepted, 2.1 bpm max error. Through gait, the same estimator confidently
reports step cadence instead of heart rate, and nothing here can tell the two
apart. See CLAUDE.md and `tests/fixtures/README.md`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.app.services.eeg_ingestion import OpticsWindow
from src.app.services.hrv_processing import estimate_hrv
from src.app.services.ppg_processing import HeartRateTracker

# How much optical history a rate is derived over. 25s is what the estimator
# was validated on; autocorrelation needs several beats before its peak is
# decisive.
RATE_WINDOW_SECONDS = 25.0

# How often a reading is produced. Matches the fixture's 10s step, which
# `MAX_BPM_CHANGE_PER_S` (the continuity check that rejects octave errors) is
# calibrated against.
#
# Also doubles as a rate limit: the tick rate is 4Hz, so emitting per tick
# would write ~14k rows an hour, each one 99% the same 25 seconds of signal
# as the last.
EMIT_EVERY_SECONDS = 10.0

# Fraction of the window that must be present before a rate is reported. A
# window three-quarters full has gaps, and a gap is not a slow heart.
MIN_WINDOW_COVERAGE = 0.80

# Longest interval between consecutive samples that interpolation may fill.
# Beyond it, the straight line between two samples is invention rather than
# measurement, and at a second it lands squarely in the pulse band.
#
# Measured by punching 0.92s holes into the resting fixture (69.4 bpm
# intact): one hole reads 69.7, three read 69.6, six read 68.8. A 25s window
# holds ~28 beats, so a few interpolated ones don't trouble the
# autocorrelation.
#
# So this gate is not what protects against sample loss -- `MIN_SAMPLE_RATE`
# against `received_rate_hz` does that, because the damage comes from the
# *proportion* of the window that is reconstructed, not from the length of
# any one hole.
MAX_GAP_SECONDS = 1.0

# Nyquist for MAX_BPM (180 bpm = 3 Hz) is 6 Hz. 10 Hz leaves margin for the
# waveform's shape rather than only its fundamental. A headband delivering
# below this is not a slow headband, it is a broken link -- PRESET_1035 runs
# at ~64 Hz.
#
# Applied to **two** rates, and the second one is the one that bites. The
# link may be running at a healthy 64 Hz while almost none of those samples
# reach us: `seq` counts what was sent, so `fs` is blind to that by
# construction, and the grid gets filled by interpolation without anything
# falling below a threshold. `window_coverage` doesn't catch it either -- it
# is elapsed span, and the span is whatever the surviving samples bracket.
#
# Measured on the resting fixture (watch-confirmed ~68 bpm), decimating a
# window that reads 69.4 bpm intact:
#
#     kept   effective rate   reported            verdict before this gate
#     1/4    16 Hz            69.4 bpm            correct
#     1/8     8 Hz            68.9 bpm            correct, but below Nyquist
#     1/16    4 Hz            69.4 bpm            correct by luck, aliased
#     1/32    2 Hz            55.8 bpm conf 1.00  accepted, 13 bpm wrong
#     1/64    1 Hz            44.0 bpm conf 1.00  accepted, 25 bpm wrong
#
# The failure is confident, not noisy: interpolation manufactures the smooth
# periodicity autocorrelation is built to reward. Note that 1/8 and 1/16 are
# actually correct and still refused -- below Nyquist, nothing here can tell
# them apart from the aliased cases, and a refusal costs one window while an
# acceptance costs a wrong number on a parent's chart.
MIN_SAMPLE_RATE = 10.0


def build_heart_record(window: OpticsWindow, tracker: HeartRateTracker,
                       seconds_since_previous: float) -> dict[str, Any]:
    """Builds the `heart` block for one optical window.

    Always returns a dict. When the window isn't measurable, `bpm` is None
    and `rejected_by` names the gate that rejected it. `source` is always
    set, so callers must check the reading itself, not the block's presence,
    before recording -- same as the camera's equivalent.
    """
    record: dict[str, Any] = {
        # The sensor, not the derived signal. This is what consent is
        # enforced against per row, and what tells a reader why a chart got
        # noisier halfway through a lesson.
        "source": "muse_optics",
        # The reading's own timestamp, not the tick's. A block stays on the
        # payload until a newer one replaces it, so a 4Hz push consumer and a
        # 1Hz poller both see every reading exactly once -- without a stable
        # stamp the first would record one reading many times and the second
        # would miss most of them.
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "bpm": None,
        "confidence": 0.0,
        "window_coverage": round(window.span_seconds / RATE_WINDOW_SECONDS, 3),
        # Measured, not nominal. Reported even when it's the reason for
        # rejection, since it's the diagnosis.
        "sample_rate_hz": round(window.fs, 2) if window.fs is not None else None,
        # Recorded alongside sample_rate_hz so a row can later explain a rate
        # derived from a window that was largely reconstructed.
        "received_rate_hz": (round(window.received_rate_hz, 2)
                             if window.received_rate_hz is not None else None),
        "completeness": (round(window.completeness, 3)
                         if window.completeness is not None else None),
        "largest_gap_s": (round(window.largest_gap_seconds, 3)
                          if window.largest_gap_seconds is not None else None),
        "channel_count": window.channel_count or None,
        # Always present, even on rejected windows, so the block has one
        # consistent shape. This is this module's verdict, separate from the
        # raw `confidence` number.
        "trusted": False,
        "rejected_by": None,
        # RMSSD is an enrichment, not a requirement: present when beats can
        # be counted, absent otherwise, and never a reason to reject the
        # rate. It gets its own rejection field so a valid bpm is never
        # mistaken for a refused window.
        "rmssd_ms": None,
        "beat_coverage": None,
        "rmssd_rejected_by": None,
    }

    if len(window.channels) == 0:
        # Prefer the window's own reason over the generic `no_samples`, which
        # implies the headband produced nothing -- a discarded window is
        # different from that.
        record["rejected_by"] = window.unusable_reason or "no_samples"
        return record

    if record["window_coverage"] < MIN_WINDOW_COVERAGE:
        # Not a failed estimate -- the first 25s of every session lands here,
        # as does any gap after the headband slips. Neither is a fault.
        record["rejected_by"] = "warming_up"
        return record

    if window.fs is None:
        # No measured rate means no time base. Falling back to the preset's
        # nominal 64 Hz would defeat the point of measuring it: a link
        # actually running at 48 Hz would then report every rate 33% high,
        # confidently.
        record["rejected_by"] = "unmeasured_sample_rate"
        return record

    if window.fs < MIN_SAMPLE_RATE:
        record["rejected_by"] = "sample_rate_too_low"
        return record

    # Same Nyquist bar, applied to what actually arrived rather than what
    # was sent -- without this check, a window that's 98% interpolation
    # would still pass. See the table by MIN_SAMPLE_RATE.
    if window.received_rate_hz is None or window.received_rate_hz < MIN_SAMPLE_RATE:
        record["rejected_by"] = "effective_rate_too_low"
        return record

    if window.largest_gap_seconds is not None and window.largest_gap_seconds > MAX_GAP_SECONDS:
        record["rejected_by"] = "sampling_gap"
        return record

    # Goes through the tracker rather than calling `estimate_window`
    # directly, because catching an octave error needs the previous window
    # to compare against -- cross-channel agreement can't catch it, since
    # all four channels make the same error.
    estimate = tracker.update(window.channels, window.fs, seconds_since_previous)
    record["confidence"] = round(float(estimate.confidence), 3)
    if estimate.bpm is None:
        record["rejected_by"] = estimate.rejected_by or "confidence"
        return record
    record["bpm"] = round(float(estimate.bpm), 1)
    # `trusted` is this module's verdict on the number; `confidence` is the
    # number itself. Both are stored so a reader can see both.
    record["trusted"] = True

    # Runs only after the rate is settled, and only adds to the record --
    # nothing below this may change `bpm`, `trusted`, or `rejected_by`.
    # RMSSD is missing in roughly one window in five even on a good
    # recording, and always while the headband is off; `stress_score` is
    # defined on rate alone so it stays meaningful either way.
    #
    # `estimate_hrv` reuses the same window and rate rather than re-deriving
    # them, so the two measurements can't disagree about whether the window
    # is usable. That's also why RMSSD can't just get a longer window of its
    # own.
    hrv = estimate_hrv(window.channels, window.fs,
                       estimate.bpm, estimate.confidence)
    # Only set when beats were actually counted. `coverage` is None when the
    # confidence gate returned before detection ran -- storing 0.0 there
    # would falsely claim 0% of beats were detected on a window nobody
    # checked.
    if hrv.coverage is not None:
        record["beat_coverage"] = round(float(hrv.coverage), 3)
    if hrv.rmssd_ms is None:
        # Every path through `estimate_hrv` that leaves `rmssd_ms` None also
        # names itself in `rejected_by`.
        record["rmssd_rejected_by"] = hrv.rejected_by
        return record
    record["rmssd_ms"] = round(float(hrv.rmssd_ms), 1)
    return record
