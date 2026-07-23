# EEG Scoring & Adaptive Question Generation — Overview

A plain-language reference for how the system reads a student's brain activity and uses it to
adjust question difficulty in real time.

## 1. What gets measured

Three values, recomputed ~4 times per second internally and saved about once per second:

| Value | Range | Meaning |
|---|---|---|
| **Focus score** | 0–100 | How mentally engaged the student appears |
| **Calm score** | 0–100 | How relaxed vs. aroused/agitated the student appears |
| **Confidence** | 0–100 | How much to trust the two scores above right now |

There is **no separate "stress" score** — stress is inferred as the low end of the calm score
(see §3).

## 2. How the scores are calculated

- **Focus score**: the ratio of "active engagement" brainwave activity (beta waves) to
  "drowsy/idle" activity (alpha + theta waves). More beta relative to alpha+theta → higher focus.
- **Calm score**: the ratio of "relaxed" brainwave activity (alpha waves) to "aroused" activity
  (beta + gamma waves). More alpha → higher calm.
- Both ratios are blended 75/25 with a simpler backup measure — the raw electrical signal
  strength and how much the four electrodes agree with each other — so a usable score still
  comes out even before proper brainwave-band data is available (e.g. right after connecting).
- **Confidence** is separate from focus/calm — it answers "how much should we trust this
  reading," not "how does the student feel." It combines: how long the sensor has been warmed
  up, how stable the last few seconds of signal have been, and whether real brainwave-band data
  is available versus the cruder backup measure.

All the specific thresholds and blend weights are hand-picked estimates grounded in general EEG
science and real device signal ranges — they have **not** been validated against a controlled
study on this system's actual target population (kids with learning disabilities). Treat them as
a starting point, not ground truth (see §6).

## 3. The weights, spelled out

Two separate blends feed into the numbers above. Both are fixed constants, not learned or
tuned per student.

**Focus/calm blend (75/25)** — combines the brainwave-band ratio with the raw-amplitude backup:

```
focus_ratio = 0.75 × (brainwave-band ratio) + 0.25 × (raw amplitude ratio)
calm_ratio  = 0.75 × (brainwave-band ratio) + 0.25 × (raw amplitude ratio)
```
The band ratio is the "real" signal — it's grounded in actual EEG frequency content — but it can
be jittery moment-to-moment and isn't available in the first instant after connecting. The 25%
amplitude share smooths that out while keeping the band data dominant.

**Confidence blend (28/32/32/8)** — combines four inputs into the trust score:

```
confidence = 0.28 × (sensor warmup progress)
           + 0.32 × (calm score)
           + 0.32 × (signal stability over the last few seconds)
           + 0.08 bonus if real brainwave-band data is available
```
(floored at a minimum of 20, so confidence never reports as fully zero once any data is flowing.)

**How solid are these numbers?** Only the direction is justified in the code — "smooth the
spectral measurement instead of trusting it outright," "reward warmup, calm, and stability
roughly evenly, with a small bonus for having real band data." The *specific* split points
(75/25, and 28/32/32/8) have no documented derivation — no cited study, no fitted data, no
formula behind why those exact numbers were picked over, say, 80/20 or an even 25/25/25/25.
They're best read as "felt reasonable to the person who wrote them," not calibrated values.

## 4. From scores to a state label

| Condition | Resulting label |
|---|---|
| Confidence too low | `insufficient_signal` |
| High focus **and** high calm | `focused` |
| Low calm (regardless of focus) | `stressed` |
| Anything else | `neutral` |

Once the label changes, it's held for a minimum of 3 seconds before it's allowed to change
again — this stops the system from flickering between states on momentary signal noise.

## 5. From state label to question difficulty

| State | System action | Effect on next question |
|---|---|---|
| `focused` | increase difficulty | one level harder (capped at level 5) |
| `stressed` | decrease difficulty | one level easier (floored at level 1) |
| `neutral` | reinforce topic | same difficulty, more practice on current topic |
| `insufficient_signal` | fallback | ignore EEG, use default difficulty progression |

## 6. End-to-end flow, per question

1. Student starts a practice session; the EEG sensor begins streaming continuously.
2. Roughly every second, the system computes focus/calm/confidence → resolves a state label →
   decides whether to raise, lower, or hold difficulty.
3. When the student is ready for the next question, the system picks the topic they're currently
   weakest in and asks the local AI model to generate a question at the current difficulty level.
4. The AI generates the question, the correct answer, and a few plausible wrong answers.
5. The student answers → their performance record updates → the loop repeats with a fresh,
   EEG-driven difficulty decision for the next question.

## 7. Known limitations

- Score thresholds and weights are engineering estimates, not derived from a validated study on
  the target population.
- "Stress" isn't independently measured — it's purely the low end of the calm score.
- Only EEG feeds this pipeline today. A camera-based heart-rate (rPPG) detector exists as a
  separate research prototype but is **not** yet connected to scoring or difficulty decisions —
  see the EEG/rPPG conflict-resolution design notes for the proposed (unbuilt) plan to combine
  them.
- No real logged EEG session data currently backs these calibrations — the numbers above should
  be revisited once genuine multi-subject sessions are recorded.
