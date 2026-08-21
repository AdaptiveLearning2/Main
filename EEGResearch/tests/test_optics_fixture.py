"""Smoke test for the recorded optical fixture.

The fixture is a 230KB gzipped blob that the heart-rate derivation is built on.
This checks the format is what the derivation assumes, and the properties that
make the recording usable at all.
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
    """~64Hz is what PRESET_1035 specifies, and what the derivation assumes
    when reconstructing a clock from sample index."""
    span_s = (frames[-1]["mono_ts_ms"] - frames[0]["mono_ts_ms"]) / 1000.0
    rate = len(frames) / span_s
    assert rate == pytest.approx(EXPECTED_RATE_HZ, abs=0.5)


def test_fixture_timestamps_are_not_a_usable_clock(frames):
    """mono_ts_ms reflects BLE delivery batching, not sample time: ~9% of
    frames share a stamp with their predecessor, and samples arrive in
    bursts, 40ms rms away from a uniform clock. RMSSD measures 20-50ms
    differences, so a clock built from these timestamps would just report
    Bluetooth scheduling."""
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
    """Subtract a moving average -- a deliberately crude high-pass.

    Removes baseline wander before searching, instead of dodging it by
    narrowing the search band (which only works here because 72.5 bpm happens
    to fall inside the chosen band, and would silently exclude a genuinely
    slow or fast heart). A one-second window puts the corner near 1 Hz. Crude
    but enough to make the point; the real derivation should use a proper
    bandpass."""
    w = int(round(window_s * fs))
    return x - numpy.convolve(x, numpy.ones(w) / w, mode="same")


def test_baseline_drift_dominates_the_low_end_of_the_pulse_band(frames):
    """A raw FFT peak over 0.7-3.0 Hz is not a pulse detector.

    Every channel's spectrum peaks at ~0.2 Hz and decays from there --
    baseline wander from perfusion, breathing and micro-movement. Its tail is
    still the largest thing in the band at 0.7 Hz, so an unfiltered argmax
    returns ~44 bpm: the band edge, not a heartbeat. The derivation must
    high-pass rather than just restrict the search band."""
    numpy = pytest.importorskip("numpy")
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    fs = EXPECTED_RATE_HZ

    for ch in range(a.shape[1]):
        x = a[:, ch] - a[:, ch].mean()
        spec = numpy.abs(numpy.fft.rfft(x * numpy.hanning(len(x))))
        freqs = numpy.fft.rfftfreq(len(x), d=1.0 / fs)
        at = lambda f: spec[int(numpy.argmin(numpy.abs(freqs - f)))]  # noqa: E731
        assert at(0.2) > at(0.7), f"channel {ch}: expected drift below the pulse band"

    # Worse than uniformly wrong: 850L's pulse beats the drift tail and reads
    # correctly, but the three weaker channels don't and land near the band
    # edge. So a raw argmax gives channels that disagree by ~28 bpm while
    # each looks like a plausible resting rate on its own.
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
    """Both 44.5 and 72.5 bpm are real components, present on every channel.

    Under a 4th-order Butterworth high-pass each is an interior local maximum
    on all four traces -- neither is a band-edge artefact, and neither is
    confined to one emitter. Their amplitudes are the same order of
    magnitude, ranging from roughly 2:1 in favour of the slow component to
    2:1 against it. The derivation has to cope with this."""
    numpy = pytest.importorskip("numpy")
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    fs = EXPECTED_RATE_HZ

    # Bin width is 0.0083 Hz and a Hanning main lobe is ~2 bins either side,
    # so +/-2 would stay inside the same lobe and barely tell a peak from a
    # shoulder. 8 clears the lobe; every channel passes out to 24, so this
    # isn't a tuned threshold.
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

    # This near-tie is what makes a per-channel argmax unusable: the two
    # components are within a few percent, so which one wins depends on
    # whatever filter runs first, not the signal. A one-second moving average
    # gives 3-1 for the fast component; a 4th-order Butterworth gives 2-2 on
    # the same data. Checked as a fact about the recording, not as a
    # disagreement between two filters, since the latter would break if
    # someone retunes a helper with the data unchanged.
    near_ties = sum(1 for r in ratios if 0.9 < r < 1.1)
    assert near_ties >= 2, f"expected at least two near-ties, got ratios {ratios}"


def test_850L_is_the_only_channel_with_a_decisive_margin(frames):
    """850L has roughly double the pulse SNR of the others and reports 72.5
    bpm under a moving average, a Butterworth and a raw peak alike, because
    its slow-to-fast amplitude ratio is ~0.49 rather than the near-ties
    elsewhere.

    Not grounds for making it the primary channel -- one recording, and the
    physics reason (850nm IR is the conventional PPG wavelength) isn't
    evidence this emitter is always best seated. It is grounds for a
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
    """The derivation reconstructs time from sample index, not mono_ts_ms.
    That's only sound if nothing went missing between the headband and the
    file, which the samples themselves can't show. `seq` is the bridge's
    monotonic counter, so a contiguous run is proof, not assumption."""
    seqs = [f["seq"] for f in frames]
    assert all(s is not None for s in seqs), "re-capture with a bridge that emits seq"
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), "sample(s) lost in capture"


# ── the exertion pair ────────────────────────────────────────────────────────
# Two captures minutes apart on the same headband: 60s at rest, then ~1 minute
# of exercise, then 150s sitting still. Together they answer, by experiment,
# which spectral component is the heart.

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
    """A component that responds to exercise is cardiac. Every channel agrees
    in both conditions and every channel is higher after exertion, which is
    what identifies ~68/76 bpm as the heart rather than just the largest
    peak. The wearer's watch recorded a peak of 97 bpm during exercise."""
    numpy = pytest.importorskip("numpy")
    rest = _channel_peaks(numpy, _load(REST_FIXTURE))
    recovery = _channel_peaks(numpy, _load(RECOVERY_FIXTURE))

    assert all(abs(p - REST_BPM) < 3 for p in rest), f"rest peaks {rest}"
    assert all(abs(p - RECOVERY_BPM) < 3 for p in recovery), f"recovery peaks {recovery}"
    assert min(recovery) > max(rest), (
        f"every channel should read higher after exertion: {rest} vs {recovery}"
    )


def _tracked_bpms(numpy, frames):
    """Every window's rate over a recording, through the production path.

    Uses `HeartRateTracker`, not `estimate_window` directly: the tracker is
    what `optics_processing` calls, and it owns the anchor, the continuity
    check, and re-acquisition after two rejections. Threading `previous_bpm`
    by hand would model a path no real session takes, and matters here: it
    would leave the recovery fixture with a single accepted window, since
    nothing would ever drop a bad anchor.

    25s windows stepped 10s, matching `RATE_WINDOW_SECONDS` and the poller
    cadence. None for a window that produced no rate.
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
    """Covers what `test_the_pulse_rises_after_exertion` does not.

    That test takes a spectral peak per channel over the whole recording, so
    it proves the *recording* contains an exertion rise. It proves nothing
    about the code that derives a rate from it. This test checks the code.

    **Compared over the first 60s of recovery, not the whole file.** The
    recovery fixture is 150s of a heart returning to rest, so its median sits
    near the resting rate by construction -- roughly 68 against rest's 69.
    That's a fact about the whole-recording statistic, not evidence the rise
    was tracked. Matching the rest fixture's own 60s window is the
    like-for-like comparison, and there the derivation does follow it.
    """
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

    # Not because of the bad anchor the next test checks. That window reads
    # 127.5 bpm, which would drag any average up on its own, so the rise has
    # to survive dropping it.
    without_anchor = [b for b in early if b <= 97.0]
    assert without_anchor, "every early recovery window was the implausible one"
    assert float(numpy.median(without_anchor)) > rest_bpm + 3.0, (
        "the rise depends on the implausible first window"
    )


# The tracker holds an unanchored candidate until a second window agrees, so
# the 127.5 bpm window is never published.
def test_the_first_window_after_exertion_is_a_plausible_rate():
    """Separate from the test above because it fails for a different reason.
    That one asks whether the rise is tracked at all; this asks whether the
    first reading after motion is one a parent's chart should carry.
    """
    numpy = pytest.importorskip("numpy")
    recovery = _tracked_bpms(numpy, _load(RECOVERY_FIXTURE))

    first = next((b for b in recovery if b is not None), None)
    assert first is not None, "no window of the recovery fixture produced a rate"
    # The watch measured 97 during exercise; nothing after can be higher.
    assert first <= 97.0, f"first accepted recovery rate {first:.1f} bpm exceeds the watch peak"


def test_the_44_bpm_component_is_not_cardiac():
    """It's present in both captures and moves in neither, which rules it out
    as a heart rate and makes it a known interferer. Left unidentified --
    respiration or perfusion are both plausible at 0.74 Hz -- since one
    wearer on one afternoon can't settle which."""
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
    """In the first 25s after exercise every channel reports ~127 bpm --
    roughly twice the true rate -- and they all agree. Later windows split
    113/58, the same signal read an octave apart in both directions.

    So cross-channel agreement can't mean correctness: every channel makes
    the same octave error. The derivation needs a continuity constraint and
    harmonic disambiguation, and should report low confidence for ~25s after
    motion instead of a rate."""
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
