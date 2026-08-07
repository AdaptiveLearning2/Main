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


# The two components this recording actually contains, in Hz.
SLOW_HZ = 0.742   # 44.5 bpm
FAST_HZ = 1.208   # 72.5 bpm


def _butter_highpass(numpy, x, fs, corner_hz=0.6, order=4):
    signal = pytest.importorskip("scipy.signal")
    b, a = signal.butter(order, corner_hz / (fs / 2), btype="high")
    return signal.filtfilt(b, a, x)


def test_the_recording_holds_two_comparable_components(frames):
    """Both 44.5 and 72.5 bpm are real, on every channel.

    Under a 4th-order Butterworth high-pass each is an interior local maximum
    on all four traces -- neither is a band-edge artefact, and neither is
    confined to one emitter. Their amplitudes are of the same order, ranging
    from roughly 2:1 in favour of the slow component to 2:1 against it.

    This is the property the derivation has to cope with, and it is stronger
    than what an earlier version of this file asserted."""
    numpy = pytest.importorskip("numpy")
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    fs = EXPECTED_RATE_HZ

    # Bin width here is 0.0083 Hz and a Hanning main lobe is ~2 bins either
    # side, so comparing against +/-2 stays inside the same lobe and barely
    # distinguishes a peak from a shoulder. 8 clears the lobe; every channel
    # passes out to 24, so this is not a tuned threshold.
    margin = 8

    ratios = []
    for ch in range(a.shape[1]):
        x = _butter_highpass(numpy, a[:, ch], fs)
        x = x - x.mean()
        spec = numpy.abs(numpy.fft.rfft(x * numpy.hanning(len(x))))
        freqs = numpy.fft.rfftfreq(len(x), d=1.0 / fs)

        def at(f, freqs=freqs):
            return int(numpy.argmin(numpy.abs(freqs - f)))

        for f in (SLOW_HZ, FAST_HZ):
            i = at(f)
            assert spec[i] >= spec[i - margin] and spec[i] >= spec[i + margin], (
                f"channel {ch}: {f * 60:.1f} bpm should be an interior local maximum"
            )
        ratios.append(spec[at(SLOW_HZ)] / spec[at(FAST_HZ)])

    assert all(0.3 < r < 3.0 for r in ratios), (
        f"expected comparable amplitudes, got ratios {ratios}"
    )

    # The near-tie is the property that makes a per-channel argmax unusable:
    # on these channels the two components are within a few percent, so which
    # one wins is decided by whatever filter runs first rather than by the
    # signal. A one-second moving average gives 3-1 for the fast component; a
    # 4th-order Butterworth gives 2-2 on the same data.
    #
    # Asserted as a fact about the recording rather than as a disagreement
    # between two particular filters -- the latter would go red the moment
    # anyone retunes a helper here, with nothing about the data having changed.
    near_ties = sum(1 for r in ratios if 0.9 < r < 1.1)
    assert near_ties >= 2, f"expected at least two near-ties, got ratios {ratios}"


def test_850L_is_the_only_channel_with_a_decisive_margin(frames):
    """The one channel-level fact that survives every method tried.

    850L has roughly double the pulse SNR of the others and reports 72.5 bpm
    under a moving average, a Butterworth and a raw peak alike, because its
    slow-to-fast amplitude ratio is ~0.49 rather than the near-ties elsewhere.

    Not grounds for nominating it primary -- one recording, and the physics
    reason (850nm IR is the conventional PPG wavelength) is not the same as
    evidence that this emitter is always best seated. It is grounds for a
    per-channel confidence that can notice the difference."""
    numpy = pytest.importorskip("numpy")
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    fs = EXPECTED_RATE_HZ
    ch_850L = 2

    for detrend in (_highpass, _butter_highpass):
        bpm = _peak_bpm(numpy, detrend(numpy, a[:, ch_850L], fs), fs)
        assert abs(bpm - FAST_HZ * 60) < 2, (
            f"850L should report ~{FAST_HZ * 60:.1f} bpm under {detrend.__name__}, got {bpm:.1f}"
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
