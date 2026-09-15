# Handoff — EEG focus / calm / confidence accuracy

Written 2026-09-13, updated 2026-09-15 after #182 merged. A snapshot; **`CLAUDE.md` is what is kept current** — its section *EEG focus,
calm and confidence: measured on a person once, and most of it failed* holds everything durable
from this work. This file is what is in flight and what to do next.

## Where things stand

Phase 1 merged as #181 (`0594ff4`), Phase 2 as #182 (`1dad99c`, 2026-09-15). Both rollup
migrations (`20260917000000`, `20260918000000`) list on remote. Sidecar 699, backend 1842,
frontend 712 at the last run on the Phase 2 head; every test in both phases mutation-checked.

Captures (not in the repo): `C:\eeg_captures\2026-09-13_{a,b}.jsonl` (sidecar source, with
`replay_before_{a,b}.jsonl` for `--against`) and `2026-09-14_raw.jsonl` (bridge source, 142,604
frames at 256.4/s). Findings and method: `EEGResearch/tests/fixtures/EEG_REFERENCE.md` and the two
CLAUDE.md paragraphs. In one line: alpha is real at TP9/TP10 (10 Hz, 4.6x above 1/f, AUC 0.92 at
4 s), nothing in the spectrum measures effort, and the artifact poison withholds the local calm
most of the time on this wearer.

Shipped and dark: `services/eeg_spectrum.py`, calm from the 1/f-relative temporal alpha residual
under `EEG_SPECTRUM_SOURCE=local` (`start.ps1 -Muse -LocalCalm` / `start.sh --muse --local-calm`);
default `sdk` by decision. Focus stays the SDK ratio, documented as unmeasured.

## Next

1. **A second wearer** (ideally a child, with consent) on the raw capture, eyes closed / eyes open
   only: `capture_eeg_reference.py --source bridge` with the sidecar stopped. Until then the local
   source stays dark. If the temporal alpha separation holds on a second person, flip
   `EEG_SPECTRUM_SOURCE` to `local` by default -- that changes what every stored calm value means,
   so it lands with a `score_scale` bump in `signal_mapping.py`.
2. **Two decisions on that capture, before the line is re-set** (EEG_REFERENCE.md, "derived before
   the artifact poison"): how long an artifact poisons the buffer (the full 4 s, or the 2 s epoch
   it landed in), and what the local calm's centre is before its own latch (the arm keeps the
   previous centre, and the latch can outlast a session at the capture's 7-19% artifact rate).
   `replay_raw_capture.py` prints the fresh / artifact / poisoned / stale shares to judge both by.
3. Step 1.7, half done: the local stressed line is 0.25, one adult's, from a table that predates
   the poison. Re-set it from the second wearer once 2 is decided, per source in both packages.
   `focused` remains unreachable by design until a marker exists.
4. Phase 3: frontend `Confidence` label on the debug readout -> *Signal quality*; re-read the
   fusion asymmetry test after 3.
5. `rearchive_session_charts.py --before 2026-09-14` (dry run), then `--apply`, against production
   storage: re-renders archives that still draw the engagement series, skipping sessions whose raw
   rows have expired. Unblocked -- the migration is on remote -- and not yet run.

## Two things noticed on the way, not fixed

- `start.ps1 -Preview` writes `FACE_DEBUG_PREVIEW_ENABLED` to `EEGResearch/.env`; a checkout without
  the camera-preview branch refuses to boot on it (pydantic `extra_forbidden`). Delete the line.
- `add_question_to_supabase`'s dedupe lookup raises on a missing `ccss_standard` column and turns
  every generated question into a 500 until `npx supabase migration up` is run — the deploy-ordering
  trap in its usual shape.
