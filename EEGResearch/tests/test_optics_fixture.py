"""Smoke test for the recorded optical fixture.

The fixture is a 97KB gzipped blob that the heart-rate derivation will be built
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

# Recorded on a Muse S Athena (MS-03), PRESET_1035, worn, at rest.
EXPECTED_FRAMES = 7710
EXPECTED_CHANNELS = 4
EXPECTED_RATE_HZ = 64.271


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

    mono_ts_ms reflects BLE delivery batching rather than sample time: samples
    share stamps and the gaps cluster at multiples of ~11ms rather than the
    nominal 15.6. RMSSD measures 20-50ms differences, so a clock built from
    these would be reporting Bluetooth scheduling. Anyone tempted to use them
    should have to delete this test first."""
    ts = [f["mono_ts_ms"] for f in frames]
    deltas = [b - a for a, b in zip(ts, ts[1:])]
    duplicates = sum(1 for d in deltas if d == 0)
    assert duplicates > 100, "expected many samples sharing a timestamp"
    assert max(deltas) > 2 * (1000 / EXPECTED_RATE_HZ), "expected batching gaps"


def test_fixture_contains_a_pulse(frames):
    """Three of four channels should agree on a plausible resting rate.

    Not a test of any derivation -- a plain FFT peak. It exists so that if the
    fixture is ever replaced with a recording that has no pulse in it, that is
    caught here rather than as an unexplained failure in beat detection."""
    numpy = pytest.importorskip("numpy")
    a = numpy.array([f["ch"] for f in frames], dtype=float)
    fs = EXPECTED_RATE_HZ

    peaks = []
    for ch in range(a.shape[1]):
        x = a[:, ch] - a[:, ch].mean()
        spec = numpy.abs(numpy.fft.rfft(x * numpy.hanning(len(x))))
        freqs = numpy.fft.rfftfreq(len(x), d=1.0 / fs)
        band = (freqs >= 0.7) & (freqs <= 3.0)  # 42-180 bpm
        peaks.append(float(freqs[band][numpy.argmax(spec[band])]) * 60.0)

    plausible = [p for p in peaks if 45 <= p <= 110]
    assert len(plausible) >= 3, f"expected 3+ channels in a resting range, got {peaks}"

    # The agreeing *cluster*, not every plausible channel. In this recording
    # three channels sit near 55 bpm and one reads ~70 -- a poorly seated
    # emitter, which is the case cross-channel agreement exists to survive.
    # Requiring all four to agree would assert the problem away.
    best = max(
        (sum(1 for q in peaks if abs(q - p) <= 5) for p in peaks),
    )
    assert best >= 3, f"expected 3+ channels agreeing within 5 bpm, got {peaks}"


def test_fixture_predates_the_seq_field(frames):
    """Documents a known limitation rather than asserting a desirable property.

    This recording was captured before the bridge emitted `seq`, so sample loss
    in it cannot be ruled out -- the queue bound, a WSAEWOULDBLOCK and a short
    send can each drop one silently. A re-capture with the current bridge will
    carry seq and this test should then be inverted to assert contiguity."""
    assert all(f.get("seq") is None for f in frames)
