# Camera rPPG against a simultaneous ECG — the negative result

`face_rgb_ecg_20260808.jsonl.gz`, captured 2026-08-08, is the recording that
decided camera heart rate. It did not pass.

## What was captured

Five minutes of mean face colour through the shipped path — the same
`OpenCvFrameSource`, `FaceLocator` and `mean_rgb` the adapter uses, via
`scripts/capture_face_rgb.py`. No video: three numbers, a quality figure and a
timestamp per frame, which is why the fixture is committable.

8988 samples, 300.4 s, 29.92 Hz measured, **face found on 8988/8988 frames**,
mean usable-pixel fraction 1.00. This is as clean a recording as this hardware
produces: seated, front-lit, still, exposure warm-up discarded.

Simultaneously, five Galaxy Watch7 single-lead ECG readings at 500 Hz, all
classified sinus rhythm:

| Created time | ECG bpm |
| --- | --- |
| 22:48:45 | 88 |
| 22:49:25 | 88 |
| 22:50:04 | 85 |
| 22:51:02 | 88 |
| 22:51:50 | 87 |

The true rate is 85–88 bpm across the whole capture, which makes the comparison
robust to the exact alignment offset — a rare luxury, and worth noting because
the headband validation had to be careful about it.

## The result

Scored as the product scores it, in rolling 25 s windows: **18 of 55 windows
accepted (33%)**, all rejections on confidence. The accepted windows reported a
median of **48.7 bpm**, range 45.4–58.2, at confidences of 0.62–0.90.

Against the ECG windows specifically, one window was accepted:

| ECG | rPPG | confidence | error |
| --- | --- | --- | --- |
| 88 | 47.7 | 0.74 | **−40 bpm** |
| 88 | rejected (0.11) | | |
| 85 | rejected (0.00) | | |
| 88 | rejected (0.39) | | |
| 87 | rejected (0.00) | | |

A confident reading, wrong by 40 bpm.

## Why, and why it is not fixable here

The obvious suspicion was a half-rate lock — the autocorrelation picking every
other beat. It is not that. 87 bpm is 1.45 Hz and the reported 47.7 is 0.795 Hz;
the ratio is 1.82, not 2, and no harmonic relation explains it.

The pulse is simply not in the recording. Per 30 s window, power within
±0.07 Hz of the true 1.45 Hz, as a fraction of the whole 0.7–3.0 Hz pulse band:

| window | spectral peak | power near true rate |
| --- | --- | --- |
| 0–30 s | 0.77 Hz (46 bpm) | 6.4% |
| 36–66 s | 0.80 Hz (48 bpm) | 3.3% |
| 133–163 s | 1.23 Hz (74 bpm) | 7.4% |
| 181–211 s | 0.87 Hz (52 bpm) | 4.1% |

And this is not POS destroying a signal that was present. The **raw** channels
show the same thing — R, G and B each peak at 44–50 bpm, with 6–10% of in-band
power near the true rate. There is nothing for the projection to have lost.

The dominant 0.77–0.87 Hz peak is most likely respiration and slow illumination
drift, which sit squarely inside the pulse band and cannot be filtered out of it.

Binned, the 0–30 s spectrum has no peak *anywhere*: power runs 1.4 to 5.1 across
the band, and the 1.4–1.6 Hz bin holding the true rate reads 2.6 — beside 2.4,
2.8 and 2.5 in the bins next to it. The only structure is a mild tilt toward the
low edge, which is 1/f drift. The autocorrelation peak height is **0.02**, where
a real pulse gives 0.3–0.7. So 47.7 bpm is the tallest ripple in a noise
realisation; it is stable across different search floors only because it is the
same noise each time, not because anything periodic is there.

## Why the confidence gate did not catch it

This is the more important half, and it does not depend on the camera.

`confidence = agreement x min(1, snr/0.4) x margin_term`, and on the accepted
window that evaluated to `1.00 x 0.79 x 1.00 = 0.79`. Every term failed in the
same direction, each for a structural reason:

| term | value | why it cannot work on this path |
| --- | --- | --- |
| `agreement` | 1.00 | POS emits **one** channel. A term whose job is to catch one badly-seated emitter among four is vacuous by construction against a single waveform. |
| `margin` | 6.51, capped to 1.0 | "How decisively the chosen period beat its nearest unrelated rival." In flat noise there is no rival structure, so margin is *highest* precisely when there is no pulse. Inverted, not merely weak. |
| `snr` | 0.314 | `estimate_window`'s own comment says a clear pulse sits around 0.3-0.6. Noise scored as a clear pulse. The raw autocorrelation peak was 0.02, where a real pulse gives 0.3-0.7. |

All three were designed and validated against the headband: four channels from a
contact sensor. None of those properties holds for a single camera-derived
waveform, so the gate in front of camera heart rate is not a weak gate, it is an
inapplicable one.

That matters more than the 40 bpm. A bad accuracy number can be blamed on one
camera; this says the protection would not function on better hardware either,
without a confidence measure designed for a single optical channel.

## The hardware limits behind it

Three were measured while getting to this point, and none is fixable in software:

- **Auto-exposure cannot be locked.** `CAP_PROP_AUTO_EXPOSURE` reads back −1.0
  whatever it is set to on this Windows backend, and `set()` returns True
  regardless — which is why `OpenCvFrameSource.locked` reports read-backs now.
  The convergence ramp moves mean green 17% over ~5 s against a pulse under 1%;
  `WARMUP_SECONDS` discards it, but nothing stops the camera re-adjusting later.
- **Frames arrive in buffered pairs**, ~6 ms then ~41 ms, each stamped when read
  rather than when exposed. `CAP_PROP_BUFFERSIZE=1` reports success and does not
  suppress it. Roughly a third of samples carry a stamp that is not when the
  light arrived. Dropping those pairs raised confidence measurably (0.05 → 0.33
  on one probe) without ever recovering the true rate.
- **Residual illumination variation is ~3% and largely achromatic** — POS's
  intended target — against a pulse under 1%.

## What was and was not learned

One window, earlier in the same session's smoke testing, produced 69.2 bpm at
confidence 0.81 on a quiet 10 s stretch. That is close enough to a plausible
resting rate to have been mistaken for the method working, and it is worth
recording that it was not: against ECG, on five minutes of the cleanest data
this camera produces, the method reports noise with confidence.

**Confidence does not discriminate.** 0.74 on a reading 40 bpm wrong is the same
failure the derived-BPM motion rule in CLAUDE.md describes — high confidence on
a wrong number — arrived at by a different route. A confidence threshold in
front of this buys nothing.

The 33% acceptance rate is the only part that behaved well: two thirds of
windows were correctly refused. But a gate that passes a 40 bpm error a third of
the time is not a gate.

## Decision

Camera heart rate is dropped. The camera ships emotion-only. `FACE_HEART_ENABLED`
already defaults to false, and this fixture is the reason it should stay that way
rather than being flipped on later by someone who reads the POS implementation
and assumes it was validated.

Re-opening this needs different hardware — a camera whose exposure genuinely
locks and whose frames carry exposure-time stamps — not a different algorithm.
The POS implementation is kept because it is correct, tested, and the front half
of any future attempt; what is not kept is the claim that it measures anything.
