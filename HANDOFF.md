# Handoff — EEG focus / calm / confidence accuracy

Written 2026-09-13, updated 2026-09-15 after #182 merged. A snapshot; **`CLAUDE.md` is what is kept current** — its section *EEG focus,
calm and confidence: measured on a person once, and most of it failed* holds everything durable
from this work. This file is what is in flight and what to do next.

## Where things stand

Phase 1 merged as #181 (`0594ff4`), Phase 2 as #182 (`1dad99c`, 2026-09-15). Both rollup
migrations (`20260917000000`, `20260918000000`) list on remote. Phase 3's relabel merged as #184
(`7f73c8e`). At that head: sidecar 699, backend 1842, frontend 714 across 62 files, all passing;
every test in all three phases mutation-checked. (An earlier line here said frontend 712: that was
the *pass* count from a run with one load-induced failure, not the total. Record the total.)

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
   **Both alternatives are built** (2026-09-16): `EEG_SPECTRUM_POISON_SECONDS` (4.0 / 2.0) and
   `EEG_CALM_CENTRE_ON_ARM` (`keep` / `midpoint`), defaults unchanged, and
   `replay_raw_capture.py --matrix` scores all four combinations in one run, availability and
   the candidate-line shares side by side. On the first wearer the centre decision is what fixes
   the eyes-open medians (`midpoint`: 0% under 0.25 on every eyes-open and task segment) and the
   poison decision is what moves availability (2 s: fresh 25–40% of task ticks against 18–21%,
   at 4% of eyes-closed ticks crossing the line). Run the matrix on the second capture, then
   choose.
3. Step 1.7, half done: the local stressed line is 0.25, one adult's, from a table that predates
   the poison. Re-set it from the second wearer once 2 is decided, per source in both packages.
   `focused` remains unreachable by design until a marker exists.
4. Phase 3, half done: the debug readout's `Confidence` bar is *Signal quality score* (2026-09-15,
   the verdict tile above it already said *Signal Quality*). Still to do: re-read the fusion
   asymmetry test after 3.
5. ~~`rearchive_session_charts.py --before 2026-09-14` against production~~ — **done 2026-09-16,
   a no-op**: the dry run reached `ibjsmvzkpmsvkruewien.supabase.co` and considered 0 sessions.
   The query takes sessions closed before that date that already carry a chart archive, and
   production has none: the backend has never been deployed, so no session has ever been closed
   against the production database and the archives the item existed to repair were only ever
   written to local stacks (a local run of the same query found 23). Nothing to apply. The
   script reads the credentials from the shell, never from a `.env`; the key was set through
   `Get-Credential` so it never reached scrollback.

## Two things noticed on the way, not fixed

- `start.ps1 -Preview` writes `FACE_DEBUG_PREVIEW_ENABLED` to `EEGResearch/.env`; a checkout without
  the camera-preview branch refuses to boot on it (pydantic `extra_forbidden`). Delete the line.
- `add_question_to_supabase`'s dedupe lookup raises on a missing `ccss_standard` column and turns
  every generated question into a 500 until `npx supabase migration up` is run — the deploy-ordering
  trap in its usual shape.
