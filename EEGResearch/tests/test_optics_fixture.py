"""Smoke test for the recorded optical fixture.

The fixture is a 230KB gzipped blob that the heart-rate derivation will be built
on. Without a consumer it could be corrupt, truncated or reshaped and nothing
would say so until whichever PR first tried to use it -- at which point the
failure looks like a bug in the new code rather than in the data under it.

This pins the format the derivation is allowed to assume, and the properties
that make the recording usable at all.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "optics_rest_64hz.jsonl.gz"

# Recorded on a Muse S Athena (MS-03), PRESET_1035, worn, at rest. seq contiguous.
EXPECTED_FRAMES = 7710
EXPECTED_CHANNELS = 4
EXPECTED_RATE_HZ = 64.234


@pytest.fixture(scope="module")
def frames() -> list[dict]:
    with gzip.open(FIXTURE, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_fixture_parses_and_has_the_expected_shape(frames):
    assert len(frames) == EXPECTED_FRAMES
    for f in frames:
        assert f["n"] == EXPECTED_CHANNELS
        assert len(f["ch"]) == f["n"]
        assert all(isinstance(v, (int, float)) for v in f["ch"])


def test_fixture_timestamps_advance(frames):
    ts = [f["mono_ts_ms"] for f in frames]
    assert ts == sorted(ts), "timestamps must be monotonic even where they repeat"


def test_fixture_rate_matches_the_recorded_preset(frames):
    """~64Hz is what PRESET_1035 specifies, and what the derivation assumes when
    it reconstructs a clock from sample index."""
    span_s = (frames[-1]["mono_ts_ms"] - frames[0]["mono_ts_ms"]) / 1000.0
    rate = len(frames) / span_s
    assert rate == pytest.approx(EXPECTED_RATE_HZ, abs=0.5)


def test_fixture_timestamps_are_not_a_usable_clock(frames):
    """The property that decided the design, pinned so it cannot be forgotten.

    mono_ts_ms reflects BLE delivery batching rather than sample time: ~9% of
    frames share a stamp with their predecessor and samples arrive in bursts,
    leaving a uniform clock 40ms rms away from them. RMSSD measures 20-50ms
    differences, so a clock built from these would be reporting Bluetooth
    scheduling. Anyone tempted to use them should have to delete this first."""
    ts = [f["mono_ts_ms"] for f in frames]
    deltas = [b - a for a, b in zip(ts, ts[1:])]
    duplicates = sum(1 for d in deltas if d == 0)
    assert duplicates > 100, "expected many samples sharing a timestamp"
    assert max(deltas) > 2 * (1000 / EXPECTED_RATE_HZ), "expected batching gaps"


def _peak_bpm(numpy, x, fs, lo_hz=0.7, hi_hz=3.0):
    x = x - x.mean()
    spec = numpy.abs(numpy.fft.rfft(x * numpy.hanning(len(x))))
    freqs = numpy.fft.rfftfreq(len(x), d=1.0 / fs)
    band = (freqs >= lo_hz) & (freqs <= hi_hz)
    return float(freqs[band][numpy.argmax(spec[band])]) * 60.0


def _highpass(numpy, x, fs, window_s=1.0):
    """Subtract a moving average -- a crude high-pass, deliberately.

    The point is to remove baseline wander *before* searching, rather than to
    dodge it by narrowing the search band. Narrowing works on this recording
    only because 72.5 bpm happens to fall inside the band chosen, and would
    silently exclude a genuinely slow or fast heart.

    A one-second window puts the corner near 1 Hz. Crude, and adequate to make
    the point; the derivation should use a real bandpass."""
    w = int(round(window_s * fs))
    return x - numpy.convolve(x, numpy.ones(w) / w, mode="same")


def test_baseline_drift_dominates_the_low_end_of_the_pulse_band(frames):
    """Why a raw FFT peak over 0.7-3.0 Hz is not a pulse detector.

    Every channel's spectrum peaks at ~0.2 Hz and decays monotonically from
    there -- baseline wander from perfusion, breathing and micro-movement. Its
    tail is still the largest thing in the band at 0.7 Hz, so an unfiltered
    argmax returns ~44 bpm: the band edge, not a heartbeat.

    This is pinned because it produced two wrong answers before it was
    understood, and the derivation must high-pass rather than merely restrict
    the search band."""
    numpy = pytest.importorskip("numpy")
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    fs = EXPECTED_RATE_HZ

    for ch in range(a.shape[1]):
        x = a[:, ch] - a[:, ch].mean()
        spec = numpy.abs(numpy.fft.rfft(x * numpy.hanning(len(x))))
        freqs = numpy.fft.rfftfreq(len(x), d=1.0 / fs)
        at = lambda f: spec[int(numpy.argmin(numpy.abs(freqs - f)))]  # noqa: E731
        assert at(0.2) > at(0.7), f"channel {ch}: expected drift below the pulse band"

    # The unfiltered answer is not uniformly wrong -- it is worse than that.
    # 850L's pulse is strong enough to beat the drift tail and reads correctly;
    # the three weaker channels do not and land near the band edge. So a raw
    # argmax gives channels that disagree by ~28 bpm while each looks like a
    # plausible resting rate on its own, which is precisely the failure that is
    # hard to notice.
    naive = [_peak_bpm(numpy, a[:, ch], fs, 0.7, 3.0) for ch in range(a.shape[1])]
    assert max(naive) - min(naive) > 20, (
        f"expected unfiltered peaks to disagree sharply, got {naive}"
    )


def test_high_pass_then_search_the_full_band_finds_the_pulse(frames):
    """The recommended technique, over the band the derivation actually needs.

    High-pass first, then search 0.7-3.0 Hz -- 42 to 180 bpm, wide enough for a
    real heart rather than a band chosen to dodge drift. Three of four channels
    land on the same rate.

    The fourth is a genuine failure, not an artefact: 730R has the lowest SNR
    of the four (3.2) and reports 44.5 bpm, and that survives detrending. This
    is what cross-channel agreement is for -- a majority carries the answer and
    the outlier is identifiable by its own SNR, without needing a preferred
    channel nominated in advance."""
    numpy = pytest.importorskip("numpy")
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    fs = EXPECTED_RATE_HZ

    peaks = [_peak_bpm(numpy, _highpass(numpy, a[:, ch], fs), fs)
             for ch in range(a.shape[1])]
    # Agreement within a couple of FFT bins (~0.5 bpm each here).
    agreeing = max(sum(1 for q in peaks if abs(q - p) <= 2) for p in peaks)
    assert agreeing >= 3, f"expected 3+ channels agreeing after high-pass, got {peaks}"

    majority = [p for p in peaks if sum(1 for q in peaks if abs(q - p) <= 2) >= 3]
    assert all(45 <= p <= 110 for p in majority), (
        f"the agreeing majority should be a resting rate, got {majority}"
    )


def test_fixture_lost_no_samples(frames):
    """The property that makes an index-based clock legitimate on this data.

    The derivation reconstructs time from sample index rather than from
    mono_ts_ms. That is only sound if nothing went missing between the headband
    and the file -- and three paths could drop a sample silently, none of them
    visible in the samples themselves. `seq` is the bridge's monotonic counter,
    so a contiguous run is proof rather than assumption.

    An earlier version of this fixture predated `seq` and this test asserted
    that absence as a known limitation. It has been re-captured."""
    seqs = [f["seq"] for f in frames]
    assert all(s is not None for s in seqs), "re-capture with a bridge that emits seq"
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), "sample(s) lost in capture"
