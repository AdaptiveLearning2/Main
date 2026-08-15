# Camera rPPG against a simultaneous ECG — the negative result

`face_rgb_ecg_20260808.jsonl.gz`, captured 2026-08-08, is the recording that
decided camera heart rate. It did not pass.

## What was captured

Five minutes of mean face colour through the shipped path — the same
`OpenCvFrameSource`, `FaceLocator` and `mean_rgb` the adapter uses, via
`scripts/capture_face_rgb.py`. No video: three numbers, a quality figure and a
timestamp per frame, which is why the fixture is committable.

8988 samples, 300.4 s, 29.92 Hz measured, mean usable-pixel fraction 1.00, and
**no frame was dropped for want of a face**: 8988 of 8988 produced a colour
sample. That is not 8988 detections — `FaceLocator` re-detects every 15th frame
and reuses the box between, so it is roughly 600 detections and 8400 cache hits.
What it establishes is that the face never left frame and the box never went
stale, not that a detector ran per frame. This is as clean a recording as this hardware
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

## What this measured, and what it did not

**Scope, stated before the analysis, because the first version of this document
overreached.** Everything below is about **POS over the mean RGB of three ROI
boxes** -- which is what this product ships, because RhythmMamba's `.rlap`
weights are behind a per-requester Data Usage Agreement and cannot be
redistributed to student machines.

It is *not* a measurement of whether a webcam can yield a heart rate at all.
Three spatial averages per frame discard almost everything a frame contains, and
a learned model over per-pixel, multi-region, temporally-modelled input has far
more to work with. The reference implementation this project started from
(`FacialRecg/.../rPPG_LF_NRMSSD.py`, RhythmMamba over full video) is reported by
its author at roughly 95% accuracy on its best tests. That is not in conflict
with the failure below; it is a different method on different information.

So the honest conclusion is **"POS over ROI means does not recover a pulse on
this hardware"**, and the thing blocking the alternative is a licence, not
physics. Saying "the pulse is absent from the video" -- as an earlier version of
this file did -- claims far more than the evidence supports and would be quoted
back later as settled.

> **Superseded 2026-08-14, and the scope kept here was the right call.** The
> alternative was run — `RhythmMamba.pure`, and then the `.rlap` weights this
> paragraph names as the blocked path, including `FacePhys.rlap`, all locally,
> which is evaluation rather than distribution. None of them tracks a heart rate,
> and none beats the raw green channel. See *The learned model, and the moving
> truth* below; **the licence is not worth pursuing.** The caveat above is left
> standing because it was correct when written, and the reasoning is what made the
> follow-up worth doing rather than assuming the answer either way.

## Why, and why it is not fixable here

The obvious suspicion was a half-rate lock — the autocorrelation picking every
other beat. It is not that. 87 bpm is 1.45 Hz and the reported 47.7 is 0.795 Hz;
the ratio is 1.82, not 2, and no harmonic relation explains it.

The pulse is simply not in *the recording* -- meaning the three-averages-per-frame
series this fixture holds, not the frames it came from. Per 30 s window, power within
±0.07 Hz of the true 1.45 Hz, as a fraction of the whole 0.7–3.0 Hz pulse band:

| window | spectral peak | power near true rate |
| --- | --- | --- |
| 0–30 s | 0.77 Hz (46 bpm) | 6.4% |
| 36–66 s | 0.80 Hz (48 bpm) | 3.3% |
| 133–163 s | 1.23 Hz (74 bpm) | 7.4% |
| 181–211 s | 0.87 Hz (52 bpm) | 4.1% |

And this is not POS destroying a signal that was present. The **raw** channels
show the same thing — R, G and B each peak at 44–50 bpm, with 6–10% of in-band
power near the true rate. There is nothing for the projection to have lost *in
those three averages*, which is the precise claim: the ROI-mean representation
does not contain a recoverable pulse. Whether the underlying frames do is a
question this recording cannot answer, because the frames were reduced to three
numbers before anything was stored.

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
- **Not compression.** The obvious remaining suspect was MJPEG — lossy chroma and
  temporal denoising are the standard rPPG killers, and would have made "this
  camera in this mode" the right conclusion instead of "this hardware". It was
  checked and ruled out. Through DSHOW the camera reports **YUY2** natively and
  refuses `MJPG` outright; through the default backend the capture path uses, the
  frames carry no 8x8 DCT blocking — mean gradient across block boundaries
  against interior is 0.95 vertically and 0.98 horizontally, where JPEG artefacts
  would put it above 1.15. The stream feeding the failed capture was uncompressed.
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

# The learned model, and the moving truth (2026-08-14)

The section above scoped itself carefully to POS and named the learned model as
the untested alternative. It has now been tested, and the last sentence of that
Decision — *different hardware, not a different algorithm* — is what the evidence
supports.

## Why a second capture was needed at all

The 08-08 capture holds the true rate at **85–88 bpm for five minutes**, which
that document calls "a rare luxury" because it makes the comparison robust to
alignment. For rejecting POS at −40 bpm it was exactly that.

It is close to useless for *validating* something that works. Over a 3 bpm span,
a method that ignores the video entirely and emits a constant near the mean
scores perfectly on every error metric. Deep rPPG models regress toward their
training-set mean when the signal is absent, so that is the specific failure a
narrow-range test cannot see. **The truth has to move.**

Two captures were run, same rig, same scoring, `--seconds 300` then `--seconds 600`:

| | 19:35 | 20:08 |
| --- | --- | --- |
| frames / face found | 8998 / 100% | 17997 / 100% |
| measured fps | 29.905 | 29.92 |
| exposure locked | yes | yes |
| true rate | 67–69 throughout | ~68, then **76–89** |

The second half of the 20:08 capture is paced breathing — 4 s in, 6 s out, six
per minute — which swings the rate through respiratory sinus arrhythmia while the
subject stays as still as during the first half. That is the point: the range comes
from breathing, not from movement, so it does not buy a moving truth at the cost of
motion artefact. Five ECG strips cover it (81, 76, 86, 89, 87); the first half
reuses the 19:36–19:39 strips at ~68.

## A changing illuminant contaminated the first capture

The 19:35 capture was recorded with a **television on in the room**, which was not
noticed until the series was compared against 08-08:

| | 08-08 | 19:35 (TV) | 20:08 (TV off) |
| --- | --- | --- | --- |
| intensity CV | 2.41% | 14.13% | 2.93% |
| chromaticity CV (green) | 0.65% | 5.00% | **0.20%** |
| in-band RMS | 0.916% of mean | 3.682% | **0.533%** |
| chroma jumps >0.5%/frame | 1.8 per min | 98.0 per min | **0.0** |

A dimming lamp or a hunting exposure scales all three channels together and leaves
chromaticity flat. This did not: a colour jump roughly every 0.6 s, at scene-cut
cadence. **POS is built on a fixed illuminant** — it projects onto a plane chosen
for one — so a changing illuminant colour does not add noise to POS, it violates
the premise. Worth knowing before blaming a result on the method.

The 20:08 capture is the cleanest this rig has produced, better than 08-08 on
every stability measure. Everything below is from it.

## Nothing tracks the rate, `.rlap` weights included

Scored identically — same 25 s windows, same `estimate_window`, same strips. Only
the front end differs.

| | truth | POS | `.pure` | `RhythmMamba.rlap` | `FacePhys.rlap` |
| --- | --- | --- | --- | --- | --- |
| first half (normal) | ~68 | 55.4 | 62.1 | 74.0 | 79.9 |
| paced half | ~84 | 46.5 | 61.8 | 75.4 | 89.1 |
| **change** | **+16.0** | **−8.9** | **−0.3** | **+1.4** | **+9.2** |

**The heart rate rose 16 bpm; POS moved 9 the wrong way and `.pure` did not move
at all.** The RLAP-trained weights were run because a `.pure` failure settles
nothing about them — PURE is ten subjects under controlled conditions, RLAP is far
larger and more varied, and "try the better weights" is the obvious next proposal.
They were run locally, which is evaluation and not distribution; the Data Usage
Agreement question only bites on shipping to student machines.

`RhythmMamba.rlap` finds a *better constant* — ~75, near the middle of the true
range — and accepts 33 of 33 paced windows, reaching confidence 0.92 and 1.00 on
errors of −23.7 and −13.5. `FacePhys.rlap` is the package default, the strongest
model here, and the only one whose median moves substantially: +9.2 of the +16.

## The between-half shift is confounded; the within-half test is not

Paced breathing changes chest and head motion as well as heart rate, so a model
responding to breathing produces the same between-half signature as one tracking a
pulse. `FacePhys.rlap`'s +9.2 has exactly that shape.

The five strips inside the paced half span 76–89 under **one** breathing regime, so
correlating against them removes the confound. Ungated — `estimate_window`'s
confidence is inapplicable to a single optical channel, so letting it discard
windows throws away the only comparison available — the band peak per strip window:

| source | 81 | 76 | 86 | 89 | 87 | r | slope |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `RhythmMamba.pure` | 67.5 | 51.0 | 55.5 | 64.8 | 54.0 | +0.30 | +0.40 |
| `RhythmMamba.rlap` | 65.8 | 73.8 | 122.6 | 83.3 | 73.0 | +0.37 | +1.60 |
| `FacePhys.rlap` | 64.5 | 111.3 | 85.6 | 128.8 | 93.8 | +0.21 | +0.99 |
| **green (raw)** | 46.5 | 55.3 | 43.2 | 66.8 | 60.5 | **+0.36** | +0.67 |

At n = 5, significance needs |r| > 0.88. All four are noise, and **the raw green
channel scores as well as any of the three networks** — whatever weak correlation
exists is already in the mean of three ROI boxes, and none of the models adds to
it. `FacePhys.rlap`'s slope of +0.99 reads like tracking until r = +0.21 is put
beside it: a line fitted through noise, which is why slope alone must never be
quoted here.

The best available model, on the largest available dataset, reported **128.8 bpm
against a true 89**. There is nothing here to seek a licence for.

RhythmMamba's accepted windows in the paced half:

| ECG | RhythmMamba | confidence | error |
| --- | --- | --- | --- |
| 81 | 62.5 | 0.75 | −18.5 |
| 76 | rejected | 0.44 | |
| 86 | 55.3 | 0.70 | −30.7 |
| 89 | 67.0 | 0.88 | −22.0 |
| 87 | 59.4 | **1.00** | **−27.6** |

Confidence 1.00 on a reading 28 bpm wrong — the same inapplicable-gate failure the
POS section documents, reached by a different front end, which is what makes it a
property of the gate rather than of POS.

## What the first half alone would have said

70 of 83 windows accepted, median error **−5.9 bpm**. That reads like a nearly
shippable result and it is an artefact: the model emits ~62 regardless, and 62
happens to sit near 68.

**A narrow-range validation would have passed a method that measures nothing.**
That is the reusable lesson here, and it is not specific to rPPG — it applies to
anything validated against a physiological quantity that barely moves during the
recording.

## What this closes, and what it does not

Closed: *"a learned model over per-pixel input might do better."* It was the
right caveat, it was the reason to keep POS's rejection narrowly scoped, and it is
now answered — for the RLAP weights too, so the licence is not worth pursuing.
Four front ends, spanning a hand-derived colour projection and three trained
spatiotemporal networks, none of which tracks a 16 bpm swing, and **none of which
beats the raw green channel** on the one unconfounded test. That points past the
algorithm to the input.

Not closed, and worth stating so nobody re-runs this expecting a different answer:

- **It is not a lighting problem.** In-band fluctuation on the clean capture is
  0.533% of mean against a photon-noise floor of 0.03–0.12%, so the recording is
  30–120× above the noise floor with exposure locked and mean green at 118/255.
  More light lowers a floor nothing is limited by. (A *steady* lamp is still worth
  having if the room is dim — that raises the camera's true frame rate. It is not
  the same claim.)
- **The remaining hypothesis is the camera's own processing.** Consumer webcams
  apply temporal denoising and sharpening that suppress exactly the sub-1%
  frame-to-frame variation a pulse consists of. **31.4% of consecutive frames have
  bit-identical ROI means** — in this capture, and identically in 08-08 — so the
  sensor is delivering roughly 20 distinct frames per second inside a 30 fps
  stream. Testing that needs a camera exposing raw uncompressed frames, not more
  analysis of these.
- **Nothing here is about children**, one adult, one room, one webcam.

## Reproducing

`session2_rgb.jsonl.gz` beside this file is the ROI-mean series from the 20:08
capture (18k samples, 600 s, `t` / `rgb` / `usable` per frame) — the same shape as
`face_rgb_ecg_20260808.jsonl.gz`. The frames themselves are not kept, per the rule
that no face video is committable; `scripts/capture_face_video_ecg.py --delete`
removed them and stamped the header.

Every model was run through the package's own pipeline
(`rppg.Model(NAME).process_faces_tensor(frames, fps)`), deliberately
rather than through `scripts/export_rhythmmamba_onnx.py`: the export settles
deployment cost, which was not the question, and going through the authors' own
preprocessing means a wrong answer cannot be a scaling bug on our side. Feeding a
model trained on normalised input raw 0–255 produces something that looks like a
signal and means nothing, which is the exact failure this whole exercise exists to
detect.
