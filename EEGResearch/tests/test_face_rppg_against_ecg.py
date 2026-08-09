"""The recording that killed camera heart rate, as a check rather than a story.

`tests/fixtures/face_rgb_ecg_20260808.jsonl.gz` is five minutes of face colour
captured through the shipped path, alongside five Galaxy Watch ECG readings that
put the true rate at 85-88 bpm throughout. Scored as the product scores it, the
pipeline reports 47.7 bpm at confidence 0.74.

Without this file that fixture is evidence for a reader — someone has to choose
to open the markdown and believe it. With it, anyone who claims to have fixed
camera heart rate has to produce a changed number **against the recording that
killed it**, not against a new recording of their own choosing. That distinction
matters here more than usual: the failure was invisible to every synthetic test
in the suite, and a fresh capture on a quiet ten seconds already fooled one
person into concluding the method worked.

So these tests deliberately assert the **wrong** answers. A failure here is not
a regression, it is news, and the docstrings say what to do about it.

Full analysis: tests/fixtures/FACE_RPPG_ECG.md
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np
import pytest

from src.app.services.face_processing import RATE_WINDOW_SECONDS, build_heart_record

FIXTURE = Path(__file__).parent / "fixtures" / "face_rgb_ecg_20260808.jsonl.gz"

# What the watch measured, across the whole capture. Every reading landed in
# this range, which is why the comparison does not depend on precise alignment.
ECG_BPM_LOW, ECG_BPM_HIGH = 85, 88


@pytest.fixture(scope="module")
def recording():
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as fh:
        lines = [json.loads(line) for line in fh]
    rows = lines[1:]                      # first line is the wall-clock header
    return (
        np.array([r["t"] for r in rows]),
        np.array([r["rgb"] for r in rows]),
        np.array([r["q"] for r in rows]),
    )


def _score_windows(recording, step=5.0):
    """Every rolling window, exactly as the stream loop would score it."""
    t, rgb, q = recording
    fps = 1.0 / float(np.median(np.diff(t)))
    out = []
    for start in np.arange(0.0, t[-1] - RATE_WINDOW_SECONDS, step):
        m = (t >= start) & (t < start + RATE_WINDOW_SECONDS)
        out.append(build_heart_record(
            rgb[m], 30.0, measured_fps=fps, timestamps=t[m],
            window_quality=float(q[m].mean()),
        ))
    return out


def test_the_recording_is_clean_so_the_failure_cannot_be_blamed_on_it(recording):
    """Rule out the easy explanations before asserting the hard one."""
    t, rgb, q = recording
    assert len(t) > 8000
    assert t[-1] - t[0] == pytest.approx(300, abs=5), "not the five-minute capture"
    assert q.min() == pytest.approx(1.0, abs=0.01), "the face was poorly lit"
    assert float(np.median(np.diff(t))) < 0.05, "the camera was running slow"


def test_the_pipeline_still_reports_a_confidently_wrong_rate(recording):
    """The finding, pinned.

    If this fails, camera heart rate behaves differently than when it was
    rejected. That is worth investigating, not silencing: update the numbers
    only alongside FACE_RPPG_ECG.md and a fresh ECG comparison. Do not widen the
    tolerances to make it pass.
    """
    accepted = [r for r in _score_windows(recording) if r["bpm"] is not None]
    assert accepted, "no window was accepted; the pipeline changed shape"

    reported = float(np.median([r["bpm"] for r in accepted]))
    assert reported == pytest.approx(48.7, abs=2.0)
    assert reported < ECG_BPM_LOW - 25, (
        f"reported {reported:.1f} bpm against a true {ECG_BPM_LOW}-{ECG_BPM_HIGH}"
    )


def test_the_confidence_gate_does_not_catch_it(recording):
    """The part that generalises past this webcam.

    A gate that lets a 40 bpm error through a third of the time is not a gate.
    Its three terms assume the headband's four contact channels: `agreement` is
    1.00 by construction against POS's single waveform, and `margin` peaks
    exactly when there is no rival structure to beat.
    """
    windows = _score_windows(recording)
    accepted = [r for r in windows if r["bpm"] is not None]

    assert len(accepted) / len(windows) > 0.2, "acceptance rate changed materially"
    assert max(r["confidence"] for r in accepted) > 0.7, (
        "noise no longer scores as a confident reading -- check what changed"
    )
    for r in accepted:
        assert not (ECG_BPM_LOW - 10 <= r["bpm"] <= ECG_BPM_HIGH + 10), (
            f"a window reported {r['bpm']} bpm, near the true rate. If this is "
            f"real, camera heart rate may be worth reopening -- see "
            f"tests/fixtures/FACE_RPPG_ECG.md before changing this test."
        )


def test_the_pulse_is_absent_rather_than_mis_derived(recording):
    """Why POS is kept rather than deleted.

    87 bpm is 1.45 Hz. If the pulse were present and the derivation wrong, the
    band around it would hold most of the in-band power. It holds under 15%, and
    the raw channels agree, so there is nothing for the projection to have lost.
    """
    t, rgb, _ = recording
    fps = 1.0 / float(np.median(np.diff(t)))
    m = (t >= 0) & (t < 30)

    for channel in range(3):
        x = rgb[m][:, channel]
        x = x - x.mean()
        spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x))))
        freqs = np.fft.rfftfreq(len(x), 1.0 / fps)
        band = (freqs >= 0.7) & (freqs <= 3.0)
        near_true = (freqs >= 1.38) & (freqs <= 1.52)
        share = spectrum[near_true].sum() / spectrum[band].sum()
        assert share < 0.15, (
            f"channel {'RGB'[channel]} now carries {share:.0%} of its pulse-band "
            f"power at the true rate. The signal may be recoverable after all."
        )
