# Handoff — EEG focus / calm / confidence accuracy

Written 2026-09-04. A snapshot, not a standing document — **`CLAUDE.md` is the
thing that is kept current**, and anything here that turns out to be durable
belongs there instead. Read `CLAUDE.md` first; this file covers one piece of
work that has been assessed and not started.

---

## 1. Where things stand

**Nothing is in flight.** `main` is at `46ab3fc`, no open PRs, working tree
clean. The headband-connection work (#169–#177) is merged and verified on
hardware; the previous handoff's contents are all in `CLAUDE.md` now.

One stray file may exist locally and is not in any PR:
`Website/AdaptiveLearning/frontend/src/__axis_probe.test.jsx` — a reviewer's
scratch probe. Delete it if present.

The task below was assessed in conversation on 2026-09-04 and the user asked
for it to be written up as the next piece of work. **No code has changed.**

---

## 2. The task

Make the EEG-derived `focus_score`, `calm_score` and `confidence` more
accurate, and look at whether the band powers they are built on (alpha,
beta, theta, gamma, delta) can be obtained more accurately.

### 2a. The pipeline today, stage by stage

| Stage | Where | What happens |
| --- | --- | --- |
| Band power | libMuse, inside the SDK | `*_ABSOLUTE` packets: log10 of PSD summed over the band, per electrode, over the SDK's own ~1 s window, ~10 Hz |
| Channel average | `EEGResearch/native_bridge/src/muse_bridge_service.cpp` `update_band_power` | Mean of the **log** values (geometric mean of power) across electrodes passing IS_GOOD ≥ 1 and HSI ≤ 2; all four when none pass or no contact data yet; `band_channels_used` reports the count |
| Transport | `main.cpp` status line → TCP 8765 → `eeg_ingestion.parse_bridge_message` | Five floats ride on the status/meta dict, one value per band (already averaged) |
| Ratios | `EEGResearch/src/app/services/signal_processing.py` `_extract_band_log_ratios` | Back to linear (`10**x`), then `focus = ln beta − ln(alpha+theta)`, `calm = ln alpha − ln(beta+gamma)`. **Delta is parsed and never used.** |
| Scaling | `_score_against_baseline` | Fixed population bounds (`FOCUS_LOG_RATIO_MIN/MAX`, `CALM_…`) until `BASELINE_SAMPLES` (60) usable ticks have latched a per-session mean; then centred on that mean with half the population range, i.e. gain doubles at the moment it latches |
| Amplitude blend | `update` | `focus = 0.75·spectral + 0.25·focus_amp`, same for calm. `focus_amp` is the mean raw level of good electrodes mapped over 400–1000 µV; `calm_amp` is `1 −` cross-electrode spread (max−min) mapped over 5–300 µV. Both from **one raw sample per tick** (`stream_manager.py:~409`, `samples[-1]`), 20-tick window ≈ 5 s |
| Confidence | `update` | `0.28·warmup + 0.32·calm_ratio + 0.32·stability + 0.08·band_present`, floored at 0.2. `stability` is `1 − pstdev(per-tick mean level)/45` |
| Quality | `_signal_quality` | Contact basis from smoothed HSI/IS_GOOD when present; else a heuristic on confidence and calm that under-reports (documented) |
| Label | `adaptation.py` `infer_state` | `insufficient_signal` if confidence < 0.45; `focused` if focus ≥ 0.7 and calm ≥ 0.5; `stressed` if calm < 0.35; else `neutral`; cooldown holds a label |
| Backend | `Website/AdaptiveLearning/backend/signal_mapping.py` | `focus` = focus/100, `stress` = 1 − calm, **`engagement` = confidence**. `signal_fusion.py` re-labels with the same thresholds (`EEG_MIN_CONFIDENCE` 0.45 etc.) |

The simulator (`SimulatedMuseIngestionAdapter` in `eeg_ingestion.py`) emits
band values solved **from** these same formulas, so every green test on this
path is the formula agreeing with itself.

### 2b. What is wrong, ranked by cost

1. **The amplitude terms are not brain activity.** Mean raw level is ADC
   offset plus electrode drift; max−min across four electrodes at one
   instant is artifact and impedance mismatch. They carry 25% of every focus
   and calm score, and level stability is 32% of confidence. A student who
   shifts in the chair loses calm and confidence together. These belong in
   quality/artifact detection, never in the scores.
2. **Confidence contains calm, and `engagement` is stored as confidence.** A
   stressed student's low calm drags confidence toward the 0.45 cutoff, so
   the state most worth reporting is the one most likely to be discarded as
   `insufficient_signal` — and fusion then treats it as no opinion. The
   `engagement` column is a third calm, a third DC-offset steadiness.
3. **The baseline is the worst fifteen seconds of the session.** It latches
   on the first 60 usable ticks, which on hardware is strap adjustment (the
   loose strap read `stressed` in every hardware run), never revisits, and
   flips gain when it latches.
4. **Gamma from a forehead band is mostly muscle.** Jaw/brow EMG lands in
   30–44 Hz and upper beta, so a clench reads as arousal and a frown as
   focus. Delta, which spikes on blinks and movement, is discarded rather
   than used as an artifact gate.
5. **No smoothing on the ratios.** The SDK updates bands ~10 Hz; each 4 Hz
   tick scores whatever value is latest. Only the SDK's ~1 s window smooths.
6. **Nothing has ground truth.** Heart rate has six ECG runs
   (`EEGResearch/tests/fixtures/README.md`); focus/calm have only the
   simulator described above.

---

## 3. The plan

Four phases, in this order. Phase 0 gates everything after it: **do not
change a formula until there is a capture to score it against.**

### Phase 0 — Capture a labelled reference (hardware, ~30 min)

Nothing in `scripts/` records the EEG feature path. `capture_optics.py`
and `capture_backend.py` are the closest patterns; `watch_live_state.ps1`
reads `/api/v1/state`.

1. **Write `EEGResearch/scripts/capture_eeg_reference.py`.** Connects to the
   sidecar (`EEG_SOURCE=muse`, `./start.ps1 -Muse` or `run_native_bridge.ps1`
   + sidecar alone), polls `/api/v1/state` at the tick rate, and writes one
   row per tick: timestamp, the five raw band values as the bridge sent
   them, `band_channels_used`, `hsi`, `is_good`, the two log ratios
   *before* scaling (needs a small hook — see step 2), `focus_score`,
   `calm_score`, `confidence`, `signal_quality`, `quality_basis`, label,
   plus a `segment` column driven by a prompted script. Also capture the
   **raw 256 Hz EEG** — see investigation §4.1 for whether the drain path
   delivers every sample or only a batch tail. Refuse to write inside the
   repo, like `capture_face_video_ecg.py`; EEG of a named person is not
   committable.
2. **Expose the unscaled log ratios.** `update()` returns only the scaled
   scores. Add `focus_log_ratio` / `calm_log_ratio` (raw, pre-baseline) to
   the returned features dict as diagnostics, the way `samples_rejected`
   is. Check `stream_manager` forwards extra feature keys unchanged, and
   check `InterpretedEegData` in `schemas.py` — **pydantic drops undeclared
   keys at `/api/v1/state`** (CLAUDE.md, the `heart` incident;
   `tests/test_state_envelope.py` derives the check from source and will
   fail if they are not declared).
3. **Run the script.** One adult, seated, good contact confirmed on the
   debug panel first (`quality_basis: contact`, `good`), then:
   - 2 min eyes closed, resting
   - 2 min eyes open, resting, looking at a blank wall
   - 2 min mental arithmetic (two-digit multiplication aloud, or n-back)
   - 1 min eyes open rest
   - 10 s jaw clench, 10 s rest, 10 s deliberate blinking, 10 s rest
   - 30 s head turning / fidgeting
   - optional: 2 min of an actual adaptive session on the student page
   Repeat at least twice on different days; a second subject if available.
4. **Score it before touching anything.** Per segment, mean and spread of
   each raw log ratio and of each score. The acceptance questions:
   - Eyes closed vs eyes open: alpha (and `calm`) must rise clearly. **If it
     does not, the pipeline is broken upstream of any formula** — check
     units (§4.2) before anything else.
   - Arithmetic vs eyes-open rest: `focus` must rise; does it clear 0.7 with
     calm ≥ 0.5, i.e. can the `focused` label actually be reached?
   - Jaw clench: gamma and beta will jump. Does `calm` fall below 0.35 and
     label `stressed`? That is the false positive to eliminate.
   - Blinks / fidget: do the amplitude terms move the scores? Does
     confidence drop below 0.45 (label lost)?
   - How much does the baseline latch change scores at tick 60, and what was
     the subject doing at ticks 0–60?
   Write the numbers into `EEGResearch/tests/fixtures/EEG_REFERENCE.md`
   alongside the heart evidence, with method, date, subject count.

### Phase 1 — Cheap fixes in `signal_processing.py` (no hardware assumptions)

Each is its own commit with a test; re-score the Phase 0 capture after each
(replay: feed the recorded rows through `SignalProcessor.update` — a
replay harness is worth writing once, `replay_into_backend.py` is the
pattern).

1. **Remove the amplitude blend from the scores.** Spectral path takes full
   weight when bands are present. Keep `mean_level`/`mean_spread` for the
   no-bands fallback only (an older bridge), and reuse spread as an
   artifact input in step 4. Constants `FOCUS_MIN/MAX_LEVEL`,
   `CALM_MIN/MAX_SPREAD` then only serve the fallback — say so.
2. **Redefine confidence as signal quality.** Inputs: warmup, band
   presence, contact (the smoothed HSI/IS_GOOD fit already computed in
   `_signal_quality` — refactor so it is computed once and shared), and
   spectral stability (spread of the log ratios over the window, not of the
   DC level). **Calm must not appear in it.** Keep the 0.2 floor and the
   0–100 scale so `adaptation.py`, `signal_fusion.py` and
   `signal_mapping.py` need no change. Check `contactQuality.js` and the
   debug panel in `Adaptive.jsx` for anything reading confidence as a
   proxy for calm.
3. **Decide what `engagement` means.** Options: (a) keep it as the new
   confidence — then it is a quality number and the parent/teacher tiles
   that render it (`grep -rn engagement frontend/src`) are mislabelled;
   (b) make it its own index (Pope's beta/(alpha+theta) *is* engagement in
   the literature, and focus is currently that — so engagement and focus
   would be the same number); (c) null it and drop the tiles. This is a
   product decision — **ask the user**, and note `rollup_signal_day`
   averages `engagement`, so a definition change is a discontinuity in the
   term trend (`avg_engagement` in the cohort/trend endpoints).
4. **Artifact gate per tick.** Reject a tick from the window and from the
   baseline when: delta jumps by more than N× its running median (blink /
   movement), or gamma jumps relative to beta beyond a bound (EMG), or the
   raw spread exceeds a bound. Rejected ticks hold the previous score, like
   `_sample_is_usable` does now. Pick N from the Phase 0 clench/blink
   segments, not by hand. Delta is already parsed; it needs a field on the
   simulator's payload only if a test drives it.
5. **Smooth the log ratios.** An EMA with a 3–5 s time constant on each
   raw log ratio *before* scaling, time-based like `_smoothed`. The
   `cooldown_seconds` in `AdaptationEngine` already damps label changes;
   check the combined lag against the 10 s decision cadence the decider
   runs on so a focused reading is not always a question late.
6. **Fix the baseline.** Three changes: collect only while
   `quality_basis == "contact"` and quality is `good`; require a longer
   window (30–60 s of good ticks, i.e. 120–240 at 4 Hz — make
   `BASELINE_SAMPLES` time-based); and replace the latch with a slowly
   rolling reference (median of the last 2–3 min of good ticks) so a
   session that starts engaged does not make engagement its zero forever.
   Remove the gain step at latch: use one scaling for both paths, or
   ramp. `reset()` semantics stay. **Trade-off to record:** a rolling
   reference means a sustained state decays toward 0.5 — that is what
   "relative to your own recent level" means; if the product wants
   absolute focus over a whole lesson, keep a fixed baseline but gate it
   on contact. Ask the user which.
7. **Thresholds.** After 1–6, re-derive `EEG_FOCUSED_*` / `EEG_STRESSED_*`
   (duplicated in `adaptation.py` and `signal_fusion.py` — keep them
   agreeing) from the Phase 0 segments: the `focused` label should be
   reachable in the arithmetic segment and absent in rest; `stressed`
   should not fire on the clench. Consider whether `focused` needs calm ≥
   0.5 at all — alpha is suppressed during effort by definition, so
   requiring calm alongside focus may be why "the focused state is
   difficult to hold".

Tests for each: `EEGResearch/tests/test_app.py` holds the existing
`SignalProcessor` tests; add `test_signal_processing.py`. Pin with
mutation checks (revert the change, the test must fail). Update the
simulator only where a test needs a knob — it is a test double, not
evidence.

### Phase 2 — Compute our own spectrum from the raw stream (bigger)

Only if Phase 0 shows the SDK bands are the limit (e.g. eyes-closed alpha
is weak or EMG dominates beta) or the artifact gate cannot be built on
five numbers per tick.

1. **Confirm the raw stream** (§4.1). The bridge emits `kind:"eeg"` frames
   with `mono_ts_ms` and four channels at 256 Hz (PRESET_21 is 256 Hz on
   Muse S; the comment at `muse_bridge_service.cpp:473` says 220 Hz — check
   which and fix the comment). There is **no `seq`** on EEG frames, unlike
   optics; add one in the bridge (`main.cpp:~399`) so dropped samples can
   be counted the way `optics_processing` does — the BLE stamp is delivery
   time, not sample time (CLAUDE.md, heart section).
2. **New module `EEGResearch/src/app/services/eeg_spectrum.py`**, pure
   numpy/scipy (both already deps), modelled on `optics_processing` /
   `ppg_processing`: a ring buffer per channel, Welch PSD over 2 s epochs
   with 50% overlap, 1 Hz resolution, notch check at 50/60 Hz, band edges
   as constants — delta 1–4, theta 4–8, alpha 8–13, low beta 13–20 (keep
   EMG out), high beta 20–30, gamma 30–44. Per-epoch artifact rejection:
   peak-to-peak amplitude bound, flat-line detection, and the delta/gamma
   rules from Phase 1 step 4 applied per channel.
3. **Aperiodic (1/f) correction.** Fit `log P(f) = b − χ·log f` over 2–40 Hz
   excluding the alpha peak region (or use the specparam/FOOOF algorithm
   — check licence and weight before adding a dependency; a linear fit in
   log-log is enough to start) and take band power as the residual above
   the fit. This is the change most likely to matter: raw theta/beta and
   alpha ratios are dominated by the slope, which moves with contact and
   arousal and is not a rhythm. Report χ as its own diagnostic.
4. **Per-region use.** Frontal pair (AF7/AF8) for beta/theta engagement;
   temporal pair (TP9/TP10) for alpha; frontal alpha asymmetry
   (`ln alpha_AF8 − ln alpha_AF7`) as an optional approach/withdrawal
   diagnostic — literature reliability is weak, so diagnostic only.
5. **Wire it behind a setting** (`EEG_SPECTRUM_SOURCE=sdk|local`, default
   `sdk`) so the SDK path stays the shipped one until the capture shows
   the local one is better. The features dict gains the local bands under
   distinct keys so both can be logged side by side in the Phase 0 script.
6. **Cost check.** Welch on 4 × 512 samples at 4 Hz is trivial, but it runs
   in the sidecar's async loop — offload with `to_thread` like
   `snapshot()` does, and measure tick latency before and after.

### Phase 3 — Downstream and documentation

- `Website/AdaptiveLearning/backend/tests/test_decide_bias.py` and
  `signal_fusion` tests assume the label thresholds; re-run and re-read the
  asymmetry property test after Phase 1 step 7.
- Every "measured" number about focus/calm in `CLAUDE.md` is currently
  "validated in simulation only"; replace with the Phase 0 numbers and add
  a section in the same shape as *Headband BPM: cleared seated, fails under
  gait*. Record what confidence now means, what engagement was decided to
  mean, and the baseline trade-off chosen.
- Frontend: `contactQuality.js`, the debug panel readout, and any tile
  reading `confidence`/`engagement` (`SignalPanel.jsx`, parent dashboard
  tiles) — check labels still say what the number is.

---

## 4. Investigations the next agent must make first

1. **Does the sidecar receive every raw sample?** `TcpMuseBridgeAdapter.
   drain_samples(max_batch)` (`eeg_ingestion.py:739`) and the tick in
   `stream_manager.py:~347–420` (`period = 1/eeg_sample_hz`, default 4 Hz).
   `samples[-1]` is used, the rest dropped. Confirm `max_batch` ≥ 64 so a
   tick never truncates, confirm the bridge forwards all 256 Hz frames
   (`main.cpp:~366` mentions queueing between samples), and confirm the
   raw channel values are in µV with the Muse offset (~800 at rest) —
   `FOCUS_MIN/MAX_LEVEL` 400–1000 and the comment "mean levels near 950"
   suggest so.
2. **Units of the `*_ABSOLUTE` packets.** The code assumes Bels (log10 of
   µV²/Hz summed). libMuse docs say "absolute band power … log of the
   sum"; verify against a capture — typical values around 0.5–1.5 for alpha
   at rest, negative allowed. The simulator emits alpha 0.10–0.55, which
   is plausible. If the SDK were emitting natural log or dB the `10**x`
   step is wrong by a constant factor that the ratios *partly* cancel —
   but not the `+` inside them. The eyes-closed test in Phase 0 is the
   end-to-end check.
3. **Is averaging logs across channels the right thing?** It is a
   geometric mean, robust to one hot channel; an arithmetic mean of linear
   power is the alternative. Decide with the capture; note the bridge
   averages *before* the sidecar sees per-channel values, so Phase 2 step
   4 (per-region use) needs the bridge to forward per-channel bands or the
   local spectrum.
4. **How the SDK's `*_RELATIVE` and `*_SCORE` packets behave.** Relative
   power normalises electrode gain for free and is one registration away
   (`connect_named`, the listener block at `muse_bridge_service.cpp:~477`);
   `*_SCORE` is the SDK's own per-user normalisation and its method is
   undocumented — log it in the capture, do not build on it.
5. **What reads `confidence` downstream.** `grep -rn "confidence"` across
   `backend/`, `frontend/src`, and `EEGResearch/src` — heart and emotion
   have their own qualified `confidence`s; only the EEG one changes. The
   `EEG_MIN_CONFIDENCE` gate in fusion and the `insufficient_signal` label
   are the two consumers that matter.
6. **What reads `engagement`.** Rollup (`rollup_signal_day`), trend
   endpoints, cohort panels, parent tiles. A meaning change here is
   visible in a term trend and should be dated in CLAUDE.md.
7. **The decider's cadence.** `LLM_topic_decider` reads the fused label at
   decision time; measure the total lag from a state change to a decision
   after adding smoothing (Phase 1 step 5) so smoothing does not push a
   `focused` push a whole question late.
8. **Whether the `focused` label needs calm at all** (Phase 1 step 7). The
   hardware runs said focused is hard to hold; alpha suppression under
   effort is the textbook reason. The capture answers it.

---

## 5. Constraints that apply here

- **Fusion asymmetry is untouchable**: easing wins, pushing defers
  (`signal_fusion.py`, brute-force test). Changing what feeds it is fine;
  changing how it combines is not.
- **Three-state rule**: a rejected tick, a no-signal tick and a real low
  score are different facts. Artifact-rejected ticks hold the last score
  and increment a counter (`samples_rejected` exists); they must not
  write zeros.
- **`stress` column is `1 − calm`** and is not a stress measurement; do not
  make it one by side effect, and never merge it with
  `heart_signals.stress_score`.
- **Run `pytest` from the repo root**, `EEG_SOURCE=sim`, `API_TOKEN`,
  `ADMIN_TOKEN` set; a locally edited `EEGResearch/.env` overrides
  `Settings` defaults.
- **Nothing recorded of a person goes in the repo.** Captures live outside
  it; only aggregate numbers and method go into `tests/fixtures/*.md`.
- **Bridge changes** (`seq` on EEG frames, per-channel bands, RELATIVE
  registration) need the `ENABLE_LIBMUSE=ON` build against the vendored
  SDK and a hardware run; CI compiles the OFF build only.
- Each phase is a branch and a PR; nothing lands on `main` directly.
