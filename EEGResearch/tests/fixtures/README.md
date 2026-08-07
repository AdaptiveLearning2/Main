# Test fixtures

## `optics_rest_64hz.jsonl.gz`

Two minutes of real `OPTICS` data from a Muse S Athena (MS-03) on `PRESET_1035`,
worn, at rest. 7710 frames, 4 channels, 230 KB gzipped — larger than an earlier
capture of the same length because the bridge now serializes 12 significant
digits rather than 6, which is roughly six more digits of entropy per sample and
does not compress away.

Captured with `scripts/capture_optics.py`. One JSON object per line:

    {"seq": 1969, "mono_ts_ms": 12345678, "n": 4, "ch": [730L, 730R, 850L, 850R]}

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

| Channel | dc (µA) | sd | Raw peak, 0.7–3.0 Hz | High-passed, 0.7–3.0 Hz | SNR |
| --- | --- | --- | --- | --- | --- |
| 730L | 5.657 | 0.094 | 44.5 bpm | **72.5 bpm** | 5.8 |
| 730R | 4.743 | 0.152 | 44.5 bpm | 44.5 bpm | 3.2 |
| 850L | 5.791 | 0.090 | 72.5 bpm | **72.5 bpm** | 11.4 |
| 850R | 4.456 | 0.132 | 44.5 bpm | **72.5 bpm** | 5.7 |

The raw column is not uniformly wrong, which is worse than if it were: 850L's
pulse beats the drift tail and reads correctly while the weaker three do not.
An unfiltered peak therefore yields channels differing by ~28 bpm, each
individually plausible as a resting rate.

After a high-pass, three channels agree on 72.5 bpm. **730R does not**, and
that survives detrending — it has the lowest SNR of the four, and 44.5 bpm is
a real feature of that trace rather than drift leaking in.

Two consequences for the derivation:

- **High-pass, don't narrow the search band.** Searching 1.0–1.5 Hz recovers
  72.5 on every channel, but only because this rate sits inside a band chosen
  after seeing the answer; a genuinely slow or fast heart would fall outside it.
  Removing drift and then searching 0.7–3.0 Hz (42–180 bpm) is the honest form.
- **Cross-channel agreement, downstream of detrending.** A majority carries the
  rate and the outlier is identifiable by its own SNR, so no channel has to be
  nominated primary in advance. 850L is the strongest here at roughly double
  the others — consistent with 850nm IR being the conventional PPG wavelength,
  and it was also strongest in an earlier live sample. Two sessions is not
  enough to promote that to a rule, which is the argument for majority
  agreement rather than a preferred channel.

### This analysis was wrong twice before it was right

Recorded because the errors are instructive, and because each looked correct:

1. **Sample rate from the median inter-frame gap.** The duplicate timestamps
   are exactly what breaks a median. Every bpm figure came out 30% high.
2. **Raw FFT peak over 0.7–3.0 Hz.** Baseline drift dominates the low end, so
   three channels returned the band edge and the conclusion drawn was that one
   *emitter* was poorly seated — the wrong channel, for the wrong reason.
3. **Peak over a narrowed 1.0–1.5 Hz band.** Forced agreement by construction,
   and produced "all four channels agree", which is also not true.

### The timestamps are not a sample clock

`mono_ts_ms` is the packet's own timestamp and reflects **BLE delivery
batching**, not when the sample was taken. In this recording:

- 660 frames share a timestamp with their predecessor — about 9%.
- The median inter-frame gap is 19 ms against a nominal 15.6, and the maximum
  is 42 ms: samples arrive in bursts rather than evenly.
- A uniform clock at the measured rate drifts up to 103.6 ms from the stamps,
  **40.7 ms rms**.

RMSSD is the root-mean-square of *successive differences* between beat
intervals, and typical values are 20–50 ms. Feeding it timestamps carrying
40 ms rms of transport jitter would produce a number dominated by Bluetooth
scheduling rather than by heart-rate variability.

**So the time base is reconstructed from sample index and the measured mean
rate, not from `mono_ts_ms`.** A slow drift between the two cancels almost
entirely in successive differences; per-sample jitter does not. The stamps are
kept in the fixture anyway — they are what the device actually sends, and a test
asserting the derivation does *not* depend on them needs them present.
