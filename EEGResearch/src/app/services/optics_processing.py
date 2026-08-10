"""The headband's optical window, as the sidecar's `heart` record.

`ppg_processing` turns optical samples into a rate; this turns a window into
the block that gets recorded. The split is the same one the camera path makes
between `pos_rppg`/`ppg_processing` and `face_processing`: the estimator is
pure signal processing with no opinion about what is worth storing, and the
gates that decide that live next to the record they shape.

Separate from `face_processing` rather than shared with it, for one structural
reason: `face_processing` imports `face_ingestion`, which is behind the `face`
extra. Reaching this from the headband path through that module would put an
optional camera dependency on a headband-only deployment -- the arrangement
`stream_manager._face_payload` keeps a local import to protect. The constants
below therefore repeat that module's rather than importing them, and they are
not all the same numbers: they are two different sensors.

**Seated use only.** Validated against a watch ECG at a desk: 14 of 16 windows
accepted, 2.1 bpm max error. Through gait the same estimator reports step
cadence at confidence 1.00, and nothing derived from these channels can tell
the two oscillators apart. Nothing here changes that -- see CLAUDE.md and
`tests/fixtures/README.md`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.app.services.eeg_ingestion import OpticsWindow
from src.app.services.hrv_processing import estimate_hrv
from src.app.services.ppg_processing import HeartRateTracker

# How much optical history a rate is derived over. 25s is what the estimator
# was validated on -- shorter windows were not, and autocorrelation needs
# several beats before its peak is decisive.
RATE_WINDOW_SECONDS = 25.0

# How often a reading is produced. Also the validated figure: the fixture
# sweeps step 25s windows by 10s, and `MAX_BPM_CHANGE_PER_S` -- the continuity
# rule that rejects octave errors -- is calibrated against that step.
#
# It is not a rate limit dressed up as a signal constant, though it serves as
# one too. The tick rate is 4Hz, so emitting per tick would write ~14k rows an
# hour, each one 99% the same 25 seconds of signal as the last.
EMIT_EVERY_SECONDS = 10.0

# Fraction of the window that must be present before a rate is reported. A
# window three-quarters full has gaps, and a gap is not a slow heart.
MIN_WINDOW_COVERAGE = 0.80

# Longest interval between consecutive samples that interpolation may fill.
# Beyond it the straight line between two samples is invention rather than
# measurement, and at a second it lands squarely in the pulse band.
MAX_GAP_SECONDS = 1.0

# Nyquist for MAX_BPM (180 bpm = 3 Hz) is 6 Hz. 10 Hz leaves margin for the
# waveform's shape rather than only its fundamental. A headband delivering
# below this is not a slow headband, it is a broken link -- PRESET_1035 runs
# at ~64 Hz.
MIN_SAMPLE_RATE = 10.0


def build_heart_record(window: OpticsWindow, tracker: HeartRateTracker,
                       seconds_since_previous: float) -> dict[str, Any]:
    """The `heart` block for one optical window.

    Always returns a dict, with `bpm: None` and a `rejected_by` naming the gate
    when the window is not measurable. Callers enqueue on *the reading*, not on
    the block's presence -- `source` is set unconditionally, so it does not
    test anything -- exactly as they do for the camera's equivalent.
    """
    record: dict[str, Any] = {
        # The sensor, not the signal. `heart_signals.source` is what consent is
        # enforced against per row, and what tells a reader why a chart got
        # noisier halfway through a lesson.
        "source": "muse_optics",
        # The reading's own timestamp, not the tick's. A block stays on the
        # payload until a newer one replaces it, so that both a 4Hz push
        # consumer and a 1Hz poller see every reading exactly once; without a
        # stable stamp the first would record one reading many times and the
        # second would miss most of them entirely.
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "bpm": None,
        "confidence": 0.0,
        "window_coverage": round(window.span_seconds / RATE_WINDOW_SECONDS, 3),
        # Measured, never nominal. Reported even when it is the reason for the
        # rejection, since "the link is delivering 6 Hz" is the diagnosis.
        "sample_rate_hz": round(window.fs, 2) if window.fs is not None else None,
        "largest_gap_s": (round(window.largest_gap_seconds, 3)
                          if window.largest_gap_seconds is not None else None),
        "channel_count": window.channel_count or None,
        # Present on every record, not only accepted ones, so the block has one
        # shape and a consumer never has to read an absent field as a third
        # state. This module's verdict, not a restatement of `confidence`.
        "trusted": False,
        "rejected_by": None,
        # RMSSD is an *enrichment*: present when the beats can be counted, absent
        # otherwise, and never a reason the rate is or is not recorded. Its own
        # gates get their own field, because `rejected_by` names why there is no
        # heart rate and a reader that saw one name in both places would take a
        # perfectly good bpm for a refused window.
        "rmssd_ms": None,
        "beat_coverage": None,
        "rmssd_rejected_by": None,
    }

    if len(window.channels) == 0:
        record["rejected_by"] = "no_samples"
        return record

    if record["window_coverage"] < MIN_WINDOW_COVERAGE:
        # Distinguished from a failed estimate. The first 25s of every session
        # lands here, and so does every gap after a headband slips; neither is
        # a fault, and neither is a measurement.
        record["rejected_by"] = "warming_up"
        return record

    if window.fs is None:
        # No measured rate means no time base. Falling back to the preset's
        # nominal 64 Hz is exactly the assumption the measurement removes: a
        # link running at 48 Hz would report every rate 33% high, confidently.
        record["rejected_by"] = "unmeasured_sample_rate"
        return record

    if window.fs < MIN_SAMPLE_RATE:
        record["rejected_by"] = "sample_rate_too_low"
        return record

    if window.largest_gap_seconds is not None and window.largest_gap_seconds > MAX_GAP_SECONDS:
        record["rejected_by"] = "sampling_gap"
        return record

    # Through the tracker, not `estimate_window` directly. Continuity is the
    # only test that catches an octave error -- cross-channel agreement cannot,
    # since all four channels make the same one -- and it needs the previous
    # window to compare against, which is state this call cannot hold.
    estimate = tracker.update(window.channels, window.fs, seconds_since_previous)
    record["confidence"] = round(float(estimate.confidence), 3)
    if estimate.bpm is None:
        record["rejected_by"] = estimate.rejected_by or "confidence"
        return record
    record["bpm"] = round(float(estimate.bpm), 1)
    # Carried so the row can say the sidecar vouched for it. `trusted` and
    # `confidence` are not the same claim: the second is a number, the first is
    # this module's verdict on it, and `heart_signals` stores both.
    record["trusted"] = True

    # Only after the rate is settled, and only ever adding to the record. The
    # rate is the measurement `stress_score` is defined on; RMSSD enriches it
    # when the beats are countable and is simply absent when they are not --
    # which is roughly one window in five even on a good recording, and always
    # while the headband is off. A score that changed meaning when this dropped
    # out would be unreadable across a session, so nothing below may reach
    # `bpm`, `trusted` or `rejected_by`.
    #
    # The same 25s window and the same rate, deliberately: `estimate_hrv` takes
    # the rate rather than re-deriving it so that the two cannot disagree about
    # whether the window is usable. It is also the reason RMSSD cannot simply be
    # given a longer window of its own.
    hrv = estimate_hrv(window.channels, window.fs,
                       estimate.bpm, estimate.confidence)
    # Only where beats were actually counted. `coverage` is None when the rate
    # confidence gate returned before detection ran, and a 0.0 there would be
    # stored as "0% of beats detected" on a window nobody looked for beats in --
    # `_raw` drops nulls but keeps zeros, so the distinction has to survive here
    # or it does not survive at all.
    if hrv.coverage is not None:
        record["beat_coverage"] = round(float(hrv.coverage), 3)
    if hrv.rmssd_ms is None:
        record["rmssd_rejected_by"] = hrv.rejected_by or "no_rmssd"
        return record
    record["rmssd_ms"] = round(float(hrv.rmssd_ms), 1)
    return record
