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

### There are two comparable components, not one pulse

The recording contains **two** genuine spectral features — ~44.5 bpm
(0.742 Hz) and ~72.5 bpm (1.208 Hz). Under a 4th-order Butterworth high-pass
both are interior local maxima on *every* channel, so neither is a band-edge
artefact and neither belongs to one bad emitter.

Their amplitude ratio, A(44.5) / A(72.5):

| Channel | dc (µA) | sd | Ratio | SNR | Argmax: MA(1 s) | Argmax: Butterworth |
| --- | --- | --- | --- | --- | --- | --- |
| 730L | 5.657 | 0.094 | 0.97 | 5.8 | 72.5 | 72.5 |
| 730R | 4.743 | 0.152 | 1.95 | 3.2 | 44.5 | 44.5 |
| 850L | 5.791 | 0.090 | **0.49** | 11.4 | 72.5 | 72.5 |
| 850R | 4.456 | 0.132 | 1.06 | 5.7 | 72.5 | 44.5 |

**730L and 850R are within a few percent of a tie**, so which component an
argmax returns is decided by the filter rather than by the signal: a shallow
one-second moving average gives 3–1 for the fast component, a Butterworth gives
2–2 on the same data. Only 850L has a decisive margin, and it reports 72.5
under every method tried.

Whether 44.5 is respiration-linked, a motion artefact, or the true rate with
72.5 as a harmonic-adjacent feature is **not settled by one at-rest
recording**, and does not need to be here. It is named as unresolved.

### Consequences for the derivation

- **High-pass; don't narrow the search band.** Searching 1.0–1.5 Hz returns
  72.5 everywhere, but only because that band was chosen after seeing the
  answer. A genuinely slow or fast heart falls outside it. Remove drift, then
  search 0.7–3.0 Hz (42–180 bpm).
- **Cross-channel agreement has to arbitrate a bimodal spectrum**, not merely
  tolerate one bad emitter. A naive "take each channel's peak, then majority
  vote" fails on this recording — the answer it gives depends on the filter. It
  needs something that sees both components, such as comparing full spectra
  across channels rather than reducing each to a single peak first.
- **Per-channel confidence is still worth having.** 850L's decisive margin and
  double SNR are visible without nominating it primary in advance. The physics
  reason — 850nm IR is the conventional PPG wavelength — is not the same as
  evidence that this emitter is always best seated.

### This analysis was wrong three times before it was right

Recorded because each looked correct at the time:

1. **Sample rate from the median inter-frame gap.** The duplicate timestamps
   are exactly what breaks a median. Every bpm figure came out 30% high.
2. **Raw FFT peak over 0.7–3.0 Hz.** Drift dominates the low end, so three
   channels returned the band edge and the conclusion named an emitter as
   poorly seated — the wrong channel, for the wrong reason.
3. **Peak over a narrowed 1.0–1.5 Hz band.** Manufactured agreement by
   construction, producing "all four channels agree".
4. **Peak after a one-second moving average.** Gain 0.685 at 0.742 Hz versus
   0.842 at 1.208 Hz — about 19% of relative attenuation, which barely
   separates two comparable components. The resulting 3–1 majority was the
   filter's, not the signal's.

Each was a reduction applied before the data justified it. The pattern is
worth more than any of the individual errors.

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
