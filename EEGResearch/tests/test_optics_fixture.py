"""The recorded optical fixtures: format, clock, and what spectral components they actually hold."""

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
    """The derivation assumes ~64 Hz when reconstructing a clock from sample index."""
    span_s = (frames[-1]["mono_ts_ms"] - frames[0]["mono_ts_ms"]) / 1000.0
    rate = len(frames) / span_s
    assert rate == pytest.approx(EXPECTED_RATE_HZ, abs=0.5)


def test_fixture_timestamps_are_not_a_usable_clock(frames):
    """mono_ts_ms reflects BLE batching; a clock from it would report Bluetooth scheduling."""
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
    """Subtract a moving average: a deliberately crude high-pass, corner near 1 Hz."""
    w = int(round(window_s * fs))
    return x - numpy.convolve(x, numpy.ones(w) / w, mode="same")


def test_baseline_drift_dominates_the_low_end_of_the_pulse_band(frames):
    """Drift's tail is the largest thing at 0.7 Hz, so the derivation must high-pass, not narrow the band."""
    numpy = pytest.importorskip("numpy")
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    fs = EXPECTED_RATE_HZ

    for ch in range(a.shape[1]):
        x = a[:, ch] - a[:, ch].mean()
        spec = numpy.abs(numpy.fft.rfft(x * numpy.hanning(len(x))))
        freqs = numpy.fft.rfftfreq(len(x), d=1.0 / fs)
        at = lambda f: spec[int(numpy.argmin(numpy.abs(freqs - f)))]  # noqa: E731
        assert at(0.2) > at(0.7), f"channel {ch}: expected drift below the pulse band"

    # Channels disagree by ~28 bpm, each looking like a plausible resting rate alone.
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
    """Both 44.5 and 72.5 bpm are interior maxima on every channel, at comparable amplitude."""
    numpy = pytest.importorskip("numpy")
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    fs = EXPECTED_RATE_HZ

    # 8 bins clears the Hanning main lobe (~2 bins); every channel passes out to 24.
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

    # Near-ties make a per-channel argmax depend on the filter, not the signal.
    near_ties = sum(1 for r in ratios if 0.9 < r < 1.1)
    assert near_ties >= 2, f"expected at least two near-ties, got ratios {ratios}"


def test_850L_is_the_only_channel_with_a_decisive_margin(frames):
    """Grounds for per-channel confidence, not for making 850L primary on one recording."""
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
    """Time is reconstructed from sample index, which is sound only if `seq` is contiguous."""
    seqs = [f["seq"] for f in frames]
    assert all(s is not None for s in seqs), "re-capture with a bridge that emits seq"
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), "sample(s) lost in capture"


# ── the exertion pair ────────────────────────────────────────────────────────
# Rest, then exercise, then recovery on one headband: identifies which component is the heart.

REST_FIXTURE = Path(__file__).parent / "fixtures" / "optics_rest_60s.jsonl.gz"
RECOVERY_FIXTURE = Path(__file__).parent / "fixtures" / "optics_recovery_150s.jsonl.gz"

REST_BPM = 67.9
RECOVERY_BPM = 76.4
NON_CARDIAC_BPM = 44.5   # present in both, moves in neither


def _load(path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _channel_peaks(numpy, frames, lo_hz=0.7, hi_hz=3.0):
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    return [_peak_bpm(numpy, _butter_highpass(numpy, a[:, c], EXPECTED_RATE_HZ),
                      EXPECTED_RATE_HZ, lo_hz, hi_hz)
            for c in range(a.shape[1])]


def test_exertion_pair_lost_no_samples():
    for path in (REST_FIXTURE, RECOVERY_FIXTURE):
        seqs = [f["seq"] for f in _load(path)]
        assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), f"{path.name}: sample lost"


def test_the_pulse_rises_after_exertion():
    """A component that responds to exercise is cardiac."""
    numpy = pytest.importorskip("numpy")
    rest = _channel_peaks(numpy, _load(REST_FIXTURE))
    recovery = _channel_peaks(numpy, _load(RECOVERY_FIXTURE))

    assert all(abs(p - REST_BPM) < 3 for p in rest), f"rest peaks {rest}"
    assert all(abs(p - RECOVERY_BPM) < 3 for p in recovery), f"recovery peaks {recovery}"
    assert min(recovery) > max(rest), (
        f"every channel should read higher after exertion: {rest} vs {recovery}"
    )


def _tracked_bpms(numpy, frames):
    """Every window's rate through `HeartRateTracker`, the production path: 25 s windows, 10 s step.

    Threading `previous_bpm` by hand would skip the tracker's re-acquisition. None = no rate.
    """
    from src.app.services.ppg_processing import HeartRateTracker

    a = numpy.array([f["ch"] for f in frames], dtype=float)
    win = int(25.0 * EXPECTED_RATE_HZ)
    step = int(10.0 * EXPECTED_RATE_HZ)

    tracker = HeartRateTracker()
    out = []
    for start in range(0, len(a) - win + 1, step):
        rate = tracker.update(a[start:start + win], EXPECTED_RATE_HZ, 10.0)
        out.append(rate.bpm)
    return out


def test_the_derived_rate_rises_after_exertion():
    """Checks the derivation, not the recording; over recovery's first 60 s, since its median returns to rest."""
    numpy = pytest.importorskip("numpy")
    rest = [b for b in _tracked_bpms(numpy, _load(REST_FIXTURE)) if b is not None]
    recovery = _tracked_bpms(numpy, _load(RECOVERY_FIXTURE))
    # Windows starting inside the first 60s, at a 10s step.
    early = [b for b in recovery[:7] if b is not None]

    assert rest, "no window of the rest fixture produced a rate"
    assert early, "no window of the first 60s of recovery produced a rate"

    rest_bpm = float(numpy.median(rest))
    early_bpm = float(numpy.median(early))
    assert early_bpm > rest_bpm + 3.0, (
        f"derived rest {rest_bpm:.1f} -> early recovery {early_bpm:.1f}; the raw "
        f"spectral peaks rise {REST_BPM} -> {RECOVERY_BPM} on every channel"
    )

    # The rise must survive dropping the implausible 127.5 bpm window.
    without_anchor = [b for b in early if b <= 97.0]
    assert without_anchor, "every early recovery window was the implausible one"
    assert float(numpy.median(without_anchor)) > rest_bpm + 3.0, (
        "the rise depends on the implausible first window"
    )


# The tracker holds an unanchored candidate until a second window agrees.
def test_the_first_window_after_exertion_is_a_plausible_rate():
    numpy = pytest.importorskip("numpy")
    recovery = _tracked_bpms(numpy, _load(RECOVERY_FIXTURE))

    first = next((b for b in recovery if b is not None), None)
    assert first is not None, "no window of the recovery fixture produced a rate"
    # The watch measured 97 during exercise; nothing after can be higher.
    assert first <= 97.0, f"first accepted recovery rate {first:.1f} bpm exceeds the watch peak"


def test_the_44_bpm_component_is_not_cardiac():
    """Present in both captures, moves in neither: a known interferer, source unidentified."""
    numpy = pytest.importorskip("numpy")

    def ratio_to_peak(frames):
        a = numpy.array([f["ch"] for f in frames], dtype=float)
        out = []
        for c in range(a.shape[1]):
            x = _butter_highpass(numpy, a[:, c], EXPECTED_RATE_HZ)
            x = x - x.mean()
            spec = numpy.abs(numpy.fft.rfft(x * numpy.hanning(len(x))))
            freqs = numpy.fft.rfftfreq(len(x), d=1.0 / EXPECTED_RATE_HZ)
            band = (freqs >= 0.7) & (freqs <= 3.0)
            peak_amp = spec[band].max()
            at_44 = spec[int(numpy.argmin(numpy.abs(freqs - NON_CARDIAC_BPM / 60)))]
            out.append(at_44 / peak_amp)
        return out

    for frames in (_load(REST_FIXTURE), _load(RECOVERY_FIXTURE)):
        assert all(r < 0.8 for r in ratio_to_peak(frames)), (
            "44.5 bpm should stay a minor component in both conditions"
        )


def test_motion_settling_produces_octave_errors_that_agreement_cannot_catch():
    """Every channel makes the same octave error, so agreement is not correctness."""
    numpy = pytest.importorskip("numpy")
    frames = _load(RECOVERY_FIXTURE)
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    fs = EXPECTED_RATE_HZ
    window = int(25 * fs)

    early = [_peak_bpm(numpy, _butter_highpass(numpy, a[:window, c], fs), fs, 0.9, 2.2)
             for c in range(a.shape[1])]
    settled_start = int(60 * fs)
    settled = [_peak_bpm(numpy, _butter_highpass(numpy, a[settled_start:, c], fs), fs, 0.9, 2.2)
               for c in range(a.shape[1])]

    # Unanimous and wrong: an octave above where the settled signal lands.
    assert max(early) - min(early) < 5, f"expected the early window to agree: {early}"
    assert min(early) > 1.5 * max(settled), (
        f"expected an octave error early ({early}) against settled ({settled})"
    )
