# Test fixtures

## `optics_rest_64hz.jsonl.gz`

Two minutes of real `OPTICS` data from a Muse S Athena (MS-03) on `PRESET_1035`,
worn, at rest. 7710 frames, 4 channels, 97 KB gzipped.

Captured with `scripts/capture_optics.py`. One JSON object per line:

    {"mono_ts_ms": 12345678, "n": 4, "ch": [730L, 730R, 850L, 850R]}

Committed so heart-rate derivation can be developed and regression-tested
against real data. The alternative is a headband session per change — slow,
needs someone wearing it, and not reproducible. A gzipped fixture is opaque to
diffs, which does not matter for a recording that will never be edited.

### What it shows

Measured mean rate **64.234 Hz** over the 120 s span — computed as
`frames / span`, not from the median inter-frame gap. The median is 19 ms,
which would imply 53 Hz; it is wrong because ~9% of frames share a timestamp
with their predecessor.

`seq` runs 1969–9678 with no gaps, so **no sample was lost** between the
headband and the file. That is what makes an index-based clock legitimate here
rather than merely convenient.

### Baseline drift dominates the low end of the pulse band

Every channel's spectrum peaks around **0.2 Hz** and decays monotonically —
perfusion, breathing and micro-movement. Its tail is still the largest thing in
the band at 0.7 Hz, so a plain FFT argmax over 0.7–3.0 Hz returns the band edge
rather than a heartbeat:

| Channel | dc (µA) | sd | Raw peak, 0.7–3.0 Hz | After excluding drift | SNR |
| --- | --- | --- | --- | --- | --- |
| 730L | 5.657 | 0.094 | 44.5 bpm | **72.5 bpm** | 5.8 |
| 730R | 4.743 | 0.152 | 44.5 bpm | **72.5 bpm** | 3.2 |
| 850L | 5.791 | 0.090 | 72.5 bpm | **72.5 bpm** | 11.4 |
| 850R | 4.456 | 0.132 | 44.5 bpm | **72.5 bpm** | 5.7 |

**All four channels carry the same heart rate.** The apparent disagreement was
drift, not a poorly seated emitter — an earlier reading of this data concluded
the opposite, twice.

Note the raw column is not uniformly wrong, which is worse: 850L's pulse is
strong enough to beat the drift tail and reads correctly, while the weaker
three do not. So an unfiltered peak yields channels differing by ~28 bpm, each
individually plausible as a resting rate. That is the failure mode that is hard
to notice.

Two consequences for the derivation:

- **High-pass, don't just narrow the search band.** Restricting to 1.0–1.5 Hz
  recovers the right answer here but only because the rate happens to sit
  inside it; a genuinely slow or fast heart would fall outside a band chosen to
  dodge drift.
- **Cross-channel agreement is usable — downstream of detrending.** On raw
  traces it would have reported three channels agreeing on 44.5 bpm, which is
  not a heart rate. 850L remains the strongest channel by SNR, roughly double
  the others, consistent with 850nm IR being the conventional PPG wavelength.

### The timestamps are not a sample clock

`mono_ts_ms` is the packet's own timestamp and reflects **BLE delivery
batching**, not when the sample was taken. In this recording:

- 523 consecutive frames share a timestamp with their predecessor; runs of up
  to 5 samples arrive on one stamp.
- Inter-frame `dt` clusters at 11 / 22 / 33 ms rather than the nominal 15.6 ms.
- A uniform clock at the measured rate drifts up to 92.8 ms from the stamps,
  **25.6 ms rms**.

RMSSD is the root-mean-square of *successive differences* between beat
intervals, and typical values are 20–50 ms. Feeding it timestamps carrying
25 ms rms of transport jitter would produce a number dominated by Bluetooth
scheduling rather than by heart-rate variability.

**So the time base is reconstructed from sample index and the measured mean
rate, not from `mono_ts_ms`.** A slow drift between the two cancels almost
entirely in successive differences; per-sample jitter does not. The stamps are
kept in the fixture anyway — they are what the device actually sends, and a test
asserting the derivation does *not* depend on them needs them present.
