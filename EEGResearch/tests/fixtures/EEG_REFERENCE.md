# EEG focus / calm / confidence — labelled reference

What the shipped scoring path does on a real headband while the wearer does known things.
The captures themselves are **not in the repo** (a named person's EEG); only the aggregates
and the method are. Regenerate with `scripts/capture_eeg_reference.py`, score with
`scripts/replay_eeg_capture.py`.

## Method

Two captures on 2026-09-13, one adult, MuseS (`PRESET_21`, no optics), sidecar source at
8 Hz polling, one row per sidecar tick. The script prompts each segment; the wearer presses
Enter when ready, so the `between` rows are settling time with the strap being adjusted.

| segment | seconds | instruction |
| --- | --- | --- |
| eyes_closed_rest | 120 | eyes closed, still |
| eyes_open_rest | 120 | eyes open, blank wall |
| arithmetic | 120 | two-digit multiplication **aloud** |
| eyes_open_rest_2 | 60 | eyes open, rest |
| jaw_clench / rest | 10 / 10 | clench and hold, then relax |
| blinking / rest | 10 / 10 | blink about once a second, then relax |
| fidget | 30 | head turning, shifting in the seat |

Run **a**: strap as worn, no preparation. Run **b**: electrodes wetted, strap tightened,
contact watched until all four electrodes read good before starting (it did not hold).
"Good-contact rows" below means `is_good` on at least 3 of 4 electrodes.

## Contact

| segment | good electrodes, mean (a / b) | `signal_quality: good` rows (b) |
| --- | --- | --- |
| between | 1.7 / 0.7 | 2 of 790 |
| eyes_closed_rest | 2.0 / 2.7 | 168 of 465 |
| eyes_open_rest | 1.7 / 2.5 | 83 of 464 |
| arithmetic (aloud) | 0.6 / 1.5 | 4 of 463 |
| eyes_open_rest_2 | 1.7 / 2.4 | 0 of 232 |
| jaw_clench | 0.3 / 0.7 | 0 |
| blinking | 0.2 / 2.0 | 9 of 39 |
| fidget | 1.0 / 1.4 | 1 of 116 |

**Degraded contact (2 of 4 electrodes) is the ordinary state**, even prepared and at rest.
Speaking aloud halved it. The wearer confirmed that extended `good` is not achievable in
practice, so every design decision below treats `degraded` as the regime the scores must
work in and `poor` as the fault.

## Band levels, run b, good-contact rows only (Bels, as the bridge sent them)

| segment | n | alpha | beta | theta | gamma | delta | focus_lr | calm_lr |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| between | 76 | 0.27 | 0.42 | 0.14 | 0.20 | 0.44 | −0.21 | −0.87 |
| eyes_closed_rest | 282 | 0.15 | −0.19 | 0.03 | −0.39 | 0.52 | −1.37 | +0.29 |
| eyes_open_rest | 209 | 0.14 | −0.01 | 0.00 | −0.30 | 0.31 | −0.91 | −0.10 |
| arithmetic | 17 | 0.16 | 0.08 | 0.03 | −0.20 | 0.34 | −0.83 | −0.18 |
| eyes_open_rest_2 | 70 | 0.15 | −0.20 | 0.13 | −0.58 | 0.46 | −1.53 | +0.47 |

Run a (worse contact) shows the same shape: alpha −0.01 closed vs +0.10 open; beta −0.01
vs +0.31; gamma −0.07 vs +0.12.

Units: values sit in −0.6…+0.6 with delta highest and gamma lowest, consistent with
log10 of µV²/Hz summed over the band. The units assumption in `_extract_band_log_ratios`
holds.

## Findings against the acceptance questions

1. **Eyes-closed alpha does not rise.** +0.02 Bels closed vs open (run b), +0.06 in run a.
   Beta and gamma fall 0.1–0.2 Bels instead, so `calm` rises through its denominator. The
   second eyes-open rest matches eyes-closed on every band, and gamma drifts monotonically
   over each run (b: +0.25 → −0.55 over 12 min; a: +0.16 → −0.33). **The calm ratio tracks
   muscle tone relaxing over the session, not an alpha rhythm.** Whether an alpha peak
   exists under the aperiodic slope needs the raw stream and our own spectrum (Phase 2).
2. **Arithmetic does not raise focus.** `focus_lr` +0.08 over eyes-open rest, on 17
   good-contact rows, with beta +0.08 *and gamma +0.10* together: speech EMG, not effort.
   `focused` was reached on **0** ticks during arithmetic in both runs. Protocol v2 should
   use silent arithmetic (written answers or an n-back).
3. **Jaw clench is a real false positive and an unreliable gate signal.** Run a: gamma p90
   +1.11 against −0.11 at rest after, 13 of 38 ticks `stressed`, and the highest `focus_lr`
   of the capture (+0.44). Run b: no gamma separation, 2 of 38 `stressed`; contact was 0.7
   good electrodes, so the bridge averaged all four. EMG lands in the scores; gamma alone
   does not identify it.
4. **Delta is a usable blink and movement gate.** Blink ticks: delta mean 0.95 (a) / 0.76
   (b) against 0.37 / 0.41 at eyes-open rest, about 2×. Fidget: 0.57 / 0.75. Rest p90 is
   0.79–0.88, so a per-tick gate at ~2.2× the running median costs about 10% of rest ticks.
   Raw channel spread (max−min µV) separates artifact from rest by ~2× in run b (56 vs 23)
   but rest itself was 147 in run a: spread is contact-dependent and usable only relative
   to its own running level.
5. **Confidence never gates.** 40–98 across both runs, below the 0.45 line on 2 rows of
   ~5000, poor contact included. `insufficient_signal` is unreachable; the gate in
   `adaptation.py` and `signal_fusion.py` protects nothing.
6. **The baseline latched in the loose-strap settling period in both runs.** Run b: 60 of
   60 baseline ticks fell in `between`, at 0.7 good electrodes with 360 of 790 ticks
   labelled `stressed`. Run a: 55 of 60 in the first 15 s of eyes-closed rest at 2 good
   electrodes.
7. **A single spurious `focused` tick becomes a 3 s `focused` label.** 90 of 133 `focused`
   rows in run b, and 43 of 43 in run a's protocol segments, carry the reason
   "Cooldown: hold prior state". The cooldown holds a label that had no persistence to
   begin with.
8. **The amplitude terms are contact, not brain.** Rest spread was 147 µV in run a and
   23 µV in run b for the same person at the same task; the 25% amplitude share of every
   focus and calm score moved with the strap.

## Artifact gate tuning (Phase 1 step 1.4, replayed on both captures)

Percentage of ticks held, rest segments against blink + fidget + clench segments, over a grid
of the two relative bounds. `99` means that bound is off. The gate's reference is the running
median of every usable tick; referenced on admitted ticks only it ratcheted and held a third
of resting ticks.

| delta × | spread × | run a rest / artifact | run b rest / artifact |
| --- | --- | --- | --- |
| 2.2 | 2.5 | 38% / 74% | 23% / 52% |
| 3.0 | 3.5 | 29% / 59% | **12% / 39%** |
| 3.0 | off | 15% / 28% | 7% / 12% |
| 4.0 | 3.5 | 25% / 55% | 8% / 32% |
| 4.0 | off | 9% / 15% | 2% / 4% |
| off | 3.5 | 19% / 45% | 6% / 28% |

No setting separates the two by better than about 3:1: the SDK's per-tick band values are
noisy at that resolution. 3.0 / 3.5 is shipped. A false hold costs one 250 ms tick of the
previous score, a missed blink costs one wrong tick that the ratio smoothing then damps, so
the trade is taken on the side of holding. Run a's higher rest rate is its poor contact.

## What this settles and what it does not

Settles: units; that the amplitude blend, the confidence formula, the baseline window and
the label cooldown all fail on real hardware in the ways HANDOFF.md predicted; a delta
threshold for the artifact gate; that `degraded` contact is the design target.

Does not settle: whether alpha responds to eyes-closed at all on this headband once the
1/f slope is removed (needs the raw 256 Hz stream), or whether beta tracks effort when the
task is silent. Both are Phase 2 questions. No threshold for `focused` can be set from
these captures, because the ratio it would gate on did not move with the task.

One adult, one day, two runs. Nothing here is a validation set for children.

## Raw-stream capture, 2026-09-14 (Phase 2 question)

Same adult, same MuseS on `PRESET_21`, electrodes wetted and the strap tightened, contact
confirmed at 4 of 4 before the start. Paired through the normal stack, then the sidecar stopped
so `capture_eeg_reference.py --source bridge` held the bridge's one TCP slot and recorded every
frame the SDK sent: 142,604 frames over 556 s, **256.4 frames/s**, one sample of the four
channels per frame, no inter-frame gap over 106 ms. Same protocol as the two sidecar captures,
arithmetic done **silently** this time. `C:\eeg_captures\2026-09-14_raw.jsonl`, not in the repo.

Method: Welch PSD, 2 s windows with 50% overlap, per channel, DC removed per segment. The 1/f
slope is fit on log10 power against log10 frequency over 2–40 Hz **excluding 7–13 Hz**, and
every band figure below is the residual above that fit (log10), so a band reads as "power above
what the aperiodic background predicts" rather than as raw power that the slope dominates.
Temporal is TP9+TP10 averaged, frontal AF7+AF8.

| segment | good ch | slope (temporal) | alpha 8–12 residual, temporal | peak in 7–13 Hz | frontal alpha residual |
| --- | --- | --- | --- | --- | --- |
| eyes closed | 3.0 | −1.24 | **+0.203** | **10.0 Hz, +0.66** (4.6×) | −0.371 |
| eyes open | 2.3 | −2.75 | −0.212 | none (7.0 Hz, +0.01) | −0.143 |
| arithmetic, silent | 2.3 | −2.55 | −0.297 | none | −0.470 |
| eyes open 2 | 3.0 | −2.66 | −0.122 | none | −0.432 |
| jaw clench | 0.6 | −0.49 | −0.637 | none | −0.484 |
| blinking | 1.4 | −3.46 | +0.185 | 8.0 Hz, +0.52 | −0.276 |

Findings:

1. **There is a real alpha rhythm, and it is at the temporal pair.** Eyes closed, TP9 and TP10
   each carry a 10 Hz peak 4.6× above the 1/f fit that is absent eyes open. AF8 sees it too
   (AUC 0.93 at 8 s); AF7 does not and is the noisy channel of this run (range −83…+1797 µV
   against ±250 on the others). The frontal *average* therefore hides it. Any live alpha feature
   must be per-region, not the four-channel average the SDK bands are.
2. **The SDK's alpha band did move this time** — +0.288 Bels closed against +0.049 open, where
   run b had shown +0.02. The difference is contact: this run held 3.0 good electrodes eyes
   closed, run b 2.7 with a looser strap. So the SDK band is not blind to alpha, it is
   *unreliable* at the contact the product actually gets, and the 1/f-relative measure on the
   temporal pair is the robust form of the same signal.
3. **Per-epoch separation, temporal pair, 1/f-relative alpha, closed against open** — the
   operating characteristic a live score would run on:

   | epoch | n per class | closed median | open median | AUC |
   | --- | --- | --- | --- | --- |
   | 2 s | 60 | +0.37 | −0.01 | 0.78 |
   | 4 s | 30 | +0.32 | −0.16 | **0.92** |
   | 8 s | 15 | +0.34 | −0.20 | 0.97 |
   | 16 s | 7 | +0.30 | −0.22 | 1.00 |

   Four seconds is the shortest epoch that separates the two states reliably, and it is the
   smoothing constant Phase 1 already uses on the ratios.
4. **Silent arithmetic does not raise beta either.** Beta residual arithmetic against eyes open:
   AUC 0.38–0.43 at every epoch length, i.e. beta is *lower* under the task. Gamma 0.57–0.59,
   nothing. The one task effect present is alpha suppression — arithmetic alpha residual −0.30
   against eyes open −0.21 — and it is weak: AUC 0.56 at 4 s, 0.69 at 16 s. With speech ruled
   out, the beta-over-alpha-plus-theta ratio does not measure this task on this hardware.
5. **The 1/f slope itself separates eyes closed from open** (−1.24 against −2.75) more than any
   band does, which is why a band ratio taken on raw power mostly measures the slope. Whether
   that slope difference is neural or the blink rate is not something this capture can say.
6. **Gamma drifts through the session** here too (−0.12 → −0.82 log10 over minutes 1–7, then
   +0.71 on the fidget), confirming the run b finding that `calm` on the SDK ratio was tracking
   muscle tone relaxing.
7. Blinking shows an "alpha" residual (+0.19, peak 8 Hz) that is the blink harmonic, not alpha:
   the 1/f fit steepens to −3.5 under the blink's low-frequency power and the 8 Hz peak is its
   spread. The Phase 1 delta gate is what keeps such epochs out of a baseline.

What this settles: alpha is recoverable on this headband at the product's contact level, from
the temporal pair, 1/f-relative, at 4 s epochs — that is the measurement `calm` should be.
What it does not settle: any spectral marker of effort. `focus` as a beta ratio has now failed
aloud (speech EMG) and silently (no beta rise), on the SDK bands and on the raw spectrum. The
only candidate left in this data is alpha suppression, which is small and slow. Still one adult,
now three runs; nothing here is a validation set for children.
