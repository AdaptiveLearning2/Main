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

## Next

1. Open the PR for `eeg-accuracy-phase1`.
2. **Phase 2 hardware run**: sidecar stopped, `capture_eeg_reference.py --source bridge`, 2 min
   eyes closed + 2 min eyes open, prepared contact. Then `eeg_spectrum.py` (Welch, 2 s epochs, 1/f
   slope fit, per-region bands) behind `EEG_SPECTRUM_SOURCE=sdk|local`, default `sdk`. That is the
   only way to learn whether an alpha peak exists under the slope on this headband.
3. Protocol v2 capture with **silent** arithmetic; then 1.7 (thresholds, and whether `focused`
   needs calm at all) from that replay.
4. Phase 3: frontend `Confidence` label on the debug readout → *Signal quality*; re-read the fusion
   asymmetry test after 1.7.

## Two things noticed on the way, not fixed

- `start.ps1 -Preview` writes `FACE_DEBUG_PREVIEW_ENABLED` to `EEGResearch/.env`; a checkout without
  the camera-preview branch refuses to boot on it (pydantic `extra_forbidden`). Delete the line.
- `add_question_to_supabase`'s dedupe lookup raises on a missing `ccss_standard` column and turns
  every generated question into a 500 until `npx supabase migration up` is run — the deploy-ordering
  trap in its usual shape.
