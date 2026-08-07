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

Measured mean rate **64.271 Hz** over the 120 s span. Per-channel spectral peak
in the 0.7–3.0 Hz band:

| Channel | dc (µA) | sd | Peak | SNR |
| --- | --- | --- | --- | --- |
| 730L | 5.601 | 0.226 | 70.7 bpm | 4.0 |
| 730R | 4.625 | 0.344 | 72.6 bpm | 4.1 |
| 850L | 5.561 | 0.182 | **91.4 bpm** | 5.1 |
| 850R | 4.266 | 0.256 | 72.6 bpm | 3.9 |

Three channels agree near 72 bpm and one does not — which is the case
cross-channel agreement exists to handle, and the reason the quality gate is
per channel rather than per device. Note it is **850L** that disagrees here,
while in an earlier live sample 850L was the *best* channel: which emitter is
well-seated changes between sessions, so no channel can be trusted as primary
by construction.

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
