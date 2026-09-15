# Handoff — EEG focus / calm / confidence accuracy

Written 2026-09-13. A snapshot; **`CLAUDE.md` is what is kept current** — its section *EEG focus,
calm and confidence: measured on a person once, and most of it failed* holds everything durable
from this work. This file is what is in flight and what to do next.

## Where things stand

Branch `eeg-accuracy-phase1`, nine commits on top of `main` at `fc1018e`, **no PR yet**:

1. Phase 0 scored: `EEGResearch/tests/fixtures/EEG_REFERENCE.md`, `scripts/replay_eeg_capture.py`
   (+ tests), injectable clocks on `SignalProcessor` / `AdaptationEngine`.
2. Amplitude blend removed from the scores (1.1).
3. Confidence rebuilt as signal quality without calm (1.2).
4. Artifact gate landed (1.4), then tuned on the captures (5).
6. Ratio smoothing (1.5).
7. Contact-gated, time-based, fixed baseline on one scale (1.6).
8. Label persistence (1.7b).
9. `engagement` = focus in `signal_mapping.py` (1.3).

Sidecar suite 617 passed, backend suite 1797 passed, both after the last code change. Every
Phase 1 test was mutation-checked (the step reverted, the test fails). `CLAUDE.md` updated.

Captures (not in the repo): `C:\eeg_captures\2026-09-13_{a,b}.jsonl`, with pre-Phase-1 replays
saved beside them as `replay_before_{a,b}.jsonl` for `--against`.

10. The baseline is taken from the first question: `POST /api/v1/session/arm` on the sidecar,
    called by the poller on `record: true` and by `push/start` on a new session id.

Decided 2026-09-13 after the replay showed a contact-gated baseline still latching in the
settling period (focus 0–14 for the rest of run b). Replayed armed at the first protocol
segment, run b reads eyes-closed 37/60, eyes-open 78/27, arithmetic 50/42.

## Phase 2 (branch `eeg-accuracy-phase2`)

Phase 1 merged as #181 (`0594ff4`). The raw-stream capture was done 2026-09-14 with silent
arithmetic: `C:\eeg_captures\2026-09-14_raw.jsonl`, 142,604 frames at 256.4/s. Findings and method
are in `EEGResearch/tests/fixtures/EEG_REFERENCE.md` (raw-stream section) and the CLAUDE.md Phase 2
paragraph. In one line: alpha is real at TP9/TP10 (10 Hz, 4.6× above 1/f, AUC 0.92 at 4 s), and
nothing in the spectrum measures effort.

Built: `services/eeg_spectrum.py` (`SpectrumEstimator`), fed from the drain, calm from the 1/f-
relative temporal alpha residual when `EEG_SPECTRUM_SOURCE=local`; default `sdk` by decision.
`scripts/replay_raw_capture.py` replays a bridge capture through it. Focus stays the SDK ratio.

## Next

1. Open the PR for `eeg-accuracy-phase2`.
2. **A second wearer** (ideally a child, with consent) on the raw capture, eyes closed / eyes open
   only. Until then the local source stays dark. If the temporal alpha separation holds on a second
   person, flip `EEG_SPECTRUM_SOURCE` to `local` by default — that changes what every stored calm
   value means, so it lands with a `score_scale` bump in `signal_mapping.py`.
3. Step 1.7, half done: the local stressed line is 0.25, set from the capture (EEG_REFERENCE.md,
   "the local stressed line"), per source in both packages. It is one adult's, **and the table it
   came from predates the artifact poison and does not reproduce under it** (the replay applies
   the poison since PR #182's last review round: fresh estimates on 18–21% of eyes-open and task
   ticks, calm held past the 10 s cap on 40% of resting ones, eyes-open medians scored against the carried
   eyes-closed centre). Two decisions before re-setting it from the second wearer: how long an
   artifact poisons (the full 4 s buffer or the 2 s epoch it landed in), and the local calm's
   centre before its own latch. `focused` remains unreachable by design until a marker exists.
4. Phase 3: frontend `Confidence` label on the debug readout → *Signal quality*; re-read the fusion
   asymmetry test after 1.7.
5. `rearchive_session_charts.py --before 2026-09-14 --apply` once the rollup migration shows on
   `npx supabase migration list --linked`.

## Two things noticed on the way, not fixed

- `start.ps1 -Preview` writes `FACE_DEBUG_PREVIEW_ENABLED` to `EEGResearch/.env`; a checkout without
  the camera-preview branch refuses to boot on it (pydantic `extra_forbidden`). Delete the line.
- `add_question_to_supabase`'s dedupe lookup raises on a missing `ccss_standard` column and turns
  every generated question into a 500 until `npx supabase migration up` is run — the deploy-ordering
  trap in its usual shape.
