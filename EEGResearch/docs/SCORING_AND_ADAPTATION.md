# EEG Scoring & Adaptive Question Generation — Overview

A plain-language map of how the system reads a student's headband and uses it, with pointers to
where each rule actually lives. Rewritten 2026-09-15: the first version (PR #26) restated formulas
that the accuracy work of PRs #181 and #182 replaced, and it was never updated with them. **This
file names the constants and the sections that hold them and does not repeat their values**, so
it cannot go stale the same way. `CLAUDE.md` is what is kept current; its section *EEG focus,
calm and confidence: measured on a person once, and most of it failed* is the authority, and
`EEGResearch/tests/fixtures/EEG_REFERENCE.md` holds the measurements behind every number.

## 1. What gets measured

Three values per tick (~4 Hz), from `SignalProcessor` in `src/app/services/signal_processing.py`:

| Value | Range | What it is today |
|---|---|---|
| **Focus** | 0–100 | The SDK band log-ratio beta / (alpha + theta), smoothed over `RATIO_SMOOTHING_SECONDS`, scored against the session's own baseline once it latches. **Unmeasured as a marker of effort**: on the reference captures it did not move with arithmetic, aloud or silent. |
| **Calm** | 0–100 | By `EEG_SPECTRUM_SOURCE`: `sdk` (the default) is the SDK log-ratio alpha / (beta + gamma); `local` is the 1/f-relative alpha residual at the temporal pair from the sidecar's own spectrum (`eeg_spectrum.py`), on its own scale (`CALM_ALPHA_RESIDUAL_*`) with its own stressed line and its own baseline latch. |
| **Confidence** | 0–100 | A **signal-quality** number, not confidence in the scores: warm-up, electrode contact, spectral stability and band presence, weighted by the `CONFIDENCE_WEIGHT_*` constants. Calm is deliberately not in it (it was, and a stressed student was the one most likely to be discarded). The debug readout labels it *Signal quality score*. |

There is no separate stress score. `cognitive_signals.stress` is `1 − calm`, written by
`signal_mapping.map_eeg_to_cognitive` on the website side; `heart_signals.stress_score` is a
different, physiological quantity and the two are never combined (CLAUDE.md, *Two columns are
called stress*).

## 2. What happens to a tick before it is scored

In order, each with its constant named in `signal_processing.py` and its reason in CLAUDE.md:

1. **Contact** — the smoothed HSI / `is_good` fit gives a contact ratio; `degraded` (two of four
   electrodes, `CONTACT_DEGRADED`) is the ordinary state on this hardware and every gate is set at
   the degraded/poor line, never at `good`.
2. **Malformed bands** — a NaN or partial ratio-band dict **holds** the previous scores with
   `artifact_reason: malformed_bands`. A NaN delta is not malformed.
3. **Artifact gate** — delta above `DELTA_JUMP_FACTOR` × its running median (blink), gamma more
   than `EMG_GAMMA_EXCESS` Bels over beta (clench), or raw spread above `SPREAD_JUMP_FACTOR` × its
   median (jolt) **holds** the previous scores; held is a third state beside rejected and low, and
   zeros are never written for an absence. A held tick also poisons the local spectrum buffer
   (`poisons_buffer`) until its samples have left it.
4. **Smoothing** — the log-ratios are EMA-smoothed on the sample clock; held ticks leave the
   smoothed value alone.
5. **Baseline** — taken from the first question (`POST /api/v1/session/arm`), not from Connect:
   `BASELINE_SECONDS` of at-least-degraded contact, fixed for the session, with a
   `BASELINE_RAMP_SECONDS` ramp from the population midpoint to the session mean on one scale.
   Focus and calm latch separately; on the local source the calm latch needs ticks that carried a
   calm, so it can take much longer than focus's (see EEG_REFERENCE.md, *derived before the
   artifact poison*). `focus_centred` / `calm_centred` on the payload say which state each score
   is in.
6. **Scaling** — the population bounds `FOCUS_LOG_RATIO_*` and `CALM_LOG_RATIO_*` (widened
   against the captures on 2026-09-14, which is what `raw.score_scale` records) map the centred
   ratio to 0..1.

The raw-amplitude terms that used to be blended into focus and calm are gone: they measured the
strap, not the brain (147 µV spread on one fitting, 23 µV on another, same person, same task).
The amplitude path survives only for a bridge that reports no band powers.

## 3. From scores to a state label

`AdaptationEngine.infer_state` in `adaptation.py`:

| Condition | Label |
|---|---|
| confidence below the gate | `insufficient_signal` (applies at once) |
| calm is a placeholder (`calm_measured: false`) or carried past `CALM_HOLD_MAX_SECONDS` | `neutral` |
| focus ≥ the focused line **and** calm ≥ 0.5 | `focused` |
| calm below the stressed line **for its source** (`STRESSED_CALM_MAX[calm_source]`) | `stressed` |
| otherwise | `neutral` |

A content label commits only after `persist_ticks` consecutive readings agree, and is then held
for `cooldown_seconds`; on the reference captures 90 of 133 `focused` readings had been the
cooldown holding one spurious tick. The lines are pinned equal to the website's
(`signal_fusion.py`) by a test on each side, and every one of them remains unmeasured against a
task.

**The sidecar's label does not choose difficulty.** It is diagnostic (the debug panel) and is
carried on the payload; the website reads the stored rows, never this label.

## 4. From stored rows to question difficulty

Decided in the website backend (`signal_fusion.py` and `LLM_topic_decider.py`), from whichever of
EEG, heart and facial channels are consented and present, and the rule is **asymmetric on
purpose**: to raise difficulty every channel with an opinion must agree, and a run of correct
answers must also be present; to lower it, any one trusted channel suffices. Facial can only
withhold a raise. A brute-force test asserts that adding a channel never makes a session harder,
and CLAUDE.md's *Fusion is asymmetric on purpose* says why that is the only shape that fails
safely. A single `focused` reading therefore does **not** harden the next question. The old
sidecar-side `question_policy` (focused → one level harder) was removed in 1.3.0 and must not
come back: the sidecar cannot see correctness, topic history or grade.

The EEG channel is gated on `raw.confidence` (the signal-quality number, carried on the row)
and picks the stressed line by `raw.calm_source`; a window whose calm is on two scales, or whose
stress was nulled by the hold rule, is read as `neutral`, not dropped.

## 5. What backs the numbers

Two labelled sidecar-source captures (2026-09-13) and one raw-stream capture (2026-09-14), one
adult, MuseS — scored in `EEG_REFERENCE.md` and re-derivable with `scripts/replay_eeg_capture.py`,
`scripts/replay_raw_capture.py` and `scripts/analyze_raw_capture.py`. What they settled: alpha is
real at the temporal pair at the contact level the product gets; the SDK calm ratio mostly tracked
muscle tone; nothing in the spectrum measured effort; degraded contact is the ordinary state. What
they did not: any threshold against a task, and anything about children — one adult is not a
validation set for this product's users, and neither will two be. The local calm source stays off
by default until a second wearer's capture, and the two open decisions ahead of that are recorded
in HANDOFF.md.

## 6. Other channels

Heart rate comes from the headband's optical channel (`MUSE_ENABLE_OPTICS`, off by default;
validated seated against a watch ECG) and feeds the fusion as its own channel. Camera rPPG was
measured against ECG and **rejected**; the camera ships emotion-only, and its channel may only
withhold a raise. Both are documented at length in CLAUDE.md.
