from __future__ import annotations

from collections import deque
from datetime import datetime
from math import exp, log, log10
from time import monotonic
from statistics import fmean, median, pstdev
from typing import Any, Callable

from src.app.models import EegSample


class SignalProcessor:
    """Computes smoothed focus/calm features from incoming samples."""

    # Raw amplitude bounds. These serve ONLY the no-bands fallback (a bridge
    # that reports no band powers); with band features present the scores
    # are purely spectral. Live Muse S captures show mean levels near 950 and
    # cross-channel spreads in the hundreds of uV; narrower bounds clamp both
    # amplitude terms to their extremes on real hardware.
    FOCUS_MIN_LEVEL = 400.0
    FOCUS_MAX_LEVEL = 1000.0
    # Floor stays low: a well-seated headband on a quiet signal legitimately
    # produces single- to low-double-digit spreads, and raising the floor makes
    # those saturate at maximum calm.
    CALM_MIN_SPREAD = 5.0
    CALM_MAX_SPREAD = 300.0
    STABILITY_STD_MAX = 45.0

    # Log-ratio bounds for the spectral terms:
    #   focus = beta / (alpha + theta), calm = alpha / (beta + gamma)
    # (computed on linear power -- see _extract_band_log_ratios).
    #
    # Calibrated for a seated student working through problems with their eyes
    # open. Alpha is the relaxed, eyes-closed rhythm and is suppressed during
    # engaged mental effort (alpha desynchronization), so too-high a calm floor
    # would pin calm_band_ratio to 0 and make "not stressed" unreachable while
    # concentrating.
    #
    # Calm spans ratio 0.20 (aroused/stressed) to 1.60 (clearly alpha-dominant,
    # relaxed); focus spans 0.40 (drowsy, theta/alpha heavy) to 2.00 (strongly
    # beta-dominant). These are physiology-informed heuristics, not values from
    # a controlled study -- a clean multi-subject baseline capture (good
    # contact on all four electrodes) should be used to tighten them.
    FOCUS_LOG_RATIO_MIN = -0.916  # ln(0.40)
    FOCUS_LOG_RATIO_MAX = 0.693  # ln(2.00)
    CALM_LOG_RATIO_MIN = -1.609  # ln(0.20)
    CALM_LOG_RATIO_MAX = 0.470  # ln(1.60)
    EPSILON = 1e-6

    # The per-session baseline: how long, in seconds of the sample clock, the
    # opening stretch of admitted ticks on at least degraded contact runs
    # before the scores centre on this learner's own level. It used to be
    # the first 60 usable ticks -- ~15 s -- with no contact condition, and on
    # both reference captures that window fell entirely in the loose-strap
    # settling period (60 of 60 ticks at 0.7 good electrodes, with nearly
    # half of them labelled stressed), so the whole session was scored
    # against a strap being adjusted. Time-based so a bursty stream cannot
    # latch it early; the sample floor is a backstop against a stream that
    # ticks once a minute. Fixed once latched, by decision: scores mean
    # "relative to how this session started", and a session that starts
    # engaged still reads focus over the lesson. Once latched the centre
    # ramps from the population midpoint to the session mean over
    # BASELINE_RAMP_SECONDS, so the latch is not a step in the scores.
    BASELINE_SECONDS = 45.0
    BASELINE_MIN_SAMPLES = 20
    BASELINE_RAMP_SECONDS = 10.0

    # Wall-clock window over which per-electrode contact readings are averaged.
    # Time-based rather than sample-based: a count-based window would silently
    # span a much longer period whenever the bridge reports contact
    # intermittently.
    CONTACT_SMOOTHING_SECONDS = 5.0
    # Contact verdict lines on the smoothed 0..1 contact ratio. Two of four
    # electrodes (0.5) is "degraded", and degraded is the ordinary state on
    # real hardware -- even prepared and at rest the reference capture sat at
    # 2-3 good electrodes -- so degraded is the regime the scores must work
    # in, and poor is the fault. Nothing below gates on "good".
    CONTACT_GOOD = 0.8
    CONTACT_DEGRADED = 0.4

    # Confidence is a *signal quality* number: how much of the spectral
    # score can be believed this tick. Its inputs are the window warm-up, the
    # electrode contact, the spectral stability of the raw focus ratio over
    # the window, and whether band powers are present at all. Calm is
    # deliberately not among them: it was, at 32%, and that made a stressed
    # student -- low calm -- the one most likely to be discarded as
    # insufficient_signal, so fusion treated exactly the state worth acting
    # on as no opinion. Measured on hardware the old confidence never left
    # 40..98 and gated on 2 ticks in ~5000, poor contact included
    # (tests/fixtures/EEG_REFERENCE.md).
    #
    # Weights are provisional -- set so that poor contact alone takes a
    # steady signal below the 0.45 gate and degraded contact does not. The
    # contact term is 0 anywhere below CONTACT_DEGRADED and rises linearly
    # to 1 at full contact, rather than the raw ratio: with a linear ratio
    # no weighting puts every "poor" reading under the gate while keeping
    # "degraded" above it, since the two meet at 0.4. The other three terms
    # sum to 0.40, so nothing but contact can lift a poor reading past 0.45.
    CONFIDENCE_WEIGHT_WARMUP = 0.10
    CONFIDENCE_WEIGHT_CONTACT = 0.60
    CONFIDENCE_WEIGHT_STABILITY = 0.22
    CONFIDENCE_WEIGHT_BANDS = 0.08
    # pstdev of the raw focus log-ratio over the window at which spectral
    # stability reads 0. Tick-level sd at rest on the reference capture was
    # 0.5-0.9 on poor contact; 1.0 puts a steady degraded signal near 0.5.
    RATIO_STD_MAX = 1.0
    # Contact the confidence assumes when the bridge reports none: neither
    # good nor poor, since nothing was measured.
    CONTACT_UNKNOWN = 0.5

    # Per-tick artifact gate. A tick that trips it holds the previous scores
    # and enters neither the window nor the baseline -- an artifact is not a
    # low score, and the three-state rule (rejected, no signal, low) holds
    # here as everywhere. Bounds are from tests/fixtures/EEG_REFERENCE.md:
    #   - delta doubles on blinks (0.76-0.95 vs 0.31-0.41 at rest) and rises
    #     ~1.7x on fidgeting; rest p90 is ~2.1x the median, so 2.2x the
    #     running median catches blinks at a cost of ~10% of rest ticks.
    #   - gamma exceeding beta by 0.5 Bels (3x in power) never happened at
    #     rest (gamma-beta sat at -0.15..-0.40) and did on a jaw clench with
    #     contact intact (gamma p90 +1.1 against beta 0.27).
    #   - raw spread is contact-dependent (147 uV at rest on one fitting, 23
    #     on another) so it is gated relative to its own running median,
    #     where an artifact roughly doubles it.
    # Running medians are over the usable ticks of the last
    # ARTIFACT_HISTORY ticks (~20 s at 4 Hz) and no gate fires until
    # ARTIFACT_MIN_HISTORY of them exist.
    #
    # Tuned by replaying both captures over a grid (EEG_REFERENCE.md,
    # "Artifact gate"): per-tick SDK bands are noisy enough that no setting
    # separates artifact ticks from rest by better than ~3:1. 3.0x delta and
    # 3.5x spread hold 12% of resting ticks and 39% of blink/fidget/clench
    # ticks on the prepared-contact run. A false hold costs one 250 ms tick
    # of the previous score, so that trade is taken on the side of holding.
    DELTA_JUMP_FACTOR = 3.0
    EMG_GAMMA_EXCESS = 0.5
    SPREAD_JUMP_FACTOR = 3.5
    ARTIFACT_HISTORY = 80
    ARTIFACT_MIN_HISTORY = 8

    # Time constant of the exponential smoothing on each raw log ratio,
    # applied before scaling. The SDK recomputes its bands ~10 times a
    # second over its own ~1 s window and each 4 Hz tick scored whatever was
    # latest; per-tick sd of the focus ratio at rest was 0.5-0.9 against
    # between-segment differences of ~0.5 (EEG_REFERENCE.md). Time-based on
    # the sample timestamps, so a stalled or bursty stream does not change
    # the smoothing; a tick whose timestamp has not advanced counts as one
    # nominal tick. Held and rejected ticks leave the smoothed value where
    # it was. 4 s reaches 63% of a step in 4 s and 92% in 10 s, under the
    # decider's ~10 s cadence.
    RATIO_SMOOTHING_SECONDS = 4.0
    NOMINAL_TICK_SECONDS = 0.25

    def __init__(self, window_size: int = 20, clock: Callable[[], float] = monotonic) -> None:
        # Wall clock for the time-based smoothing windows. Injectable so a
        # recorded capture can be replayed at its own pace (scripts/
        # replay_eeg_capture.py): against the real monotonic() a replay runs
        # in milliseconds and every time window collapses to one tick.
        self._clock = clock
        # Holds the good-channel values per admitted sample, not the raw
        # EegSample: which electrodes were trustworthy is a property of the
        # moment the sample arrived, so the mask must be applied on the way in,
        # not recomputed later against whatever contact data is current. See
        # _good_channel_values.
        self.window: deque[list[float]] = deque(maxlen=window_size)
        # IS_GOOD reports whether the last second of EEG was usable per channel;
        # it dips on eye blinks and muscle movement, which are normal, so the
        # instantaneous value flickers -- smooth it over the same window rather
        # than letting one blink downgrade the reported quality.
        # (monotonic_seconds, value) pairs, pruned by elapsed time, not count
        # (see _smoothed). maxlen is a backstop only, sized well above what the
        # time window can hold at realistic stream rates, so correctness
        # doesn't rest solely on monotonic() advancing.
        self._contact_history_cap = max(64, window_size * 16)
        self._is_good_history: deque[tuple[float, float]] = deque(maxlen=self._contact_history_cap)
        self._hsi_history: deque[tuple[float, float]] = deque(maxlen=self._contact_history_cap)
        # Diagnostic: how many frames were kept out of the window as unusable.
        self._samples_rejected = 0
        # Raw focus log-ratios of the admitted ticks in the window, for the
        # spectral-stability term of confidence.
        self._ratio_history: deque[float] = deque(maxlen=window_size)
        # Artifact gate state: running histories of delta and per-frame
        # spread over admitted ticks, the count of ticks the gate rejected,
        # and the scores held from the last admitted tick.
        self._delta_history: deque[float] = deque(maxlen=self.ARTIFACT_HISTORY)
        self._spread_history: deque[float] = deque(maxlen=self.ARTIFACT_HISTORY)
        self._samples_artifact = 0
        self._held_ratios: tuple[float, float] | None = None
        # Smoothed raw log ratios and the timestamp they were last advanced
        # to. None until the first admitted tick, which seeds them.
        self._ema_focus: float | None = None
        self._ema_calm: float | None = None
        self._ema_ts: datetime | None = None
        # Per-session baseline: scores are relative to this learner's own
        # resting values rather than fixed population constants.
        self._baseline_focus: list[float] = []
        self._baseline_calm: list[float] = []
        self._baseline_focus_mean: float | None = None
        self._baseline_calm_mean: float | None = None
        self._baseline_ready = False
        self._baseline_started: datetime | None = None
        self._baseline_latched: datetime | None = None
        # Whether ticks are being gathered for a (re)latch, and the centre
        # each score was using when the current baseline latched -- the ramp
        # runs from there, which is the population midpoint for the first
        # latch and the previous session mean for a restart.
        self._baseline_collecting = True
        self._centre_from: dict[str, float | None] = {"focus": None, "calm": None}

    def restart_baseline(self) -> None:
        """Discard the baseline and gather a fresh one from the next admitted
        ticks, ramping to it from the centre in effect now.

        Called when recording is armed -- the first question -- rather than
        at stream start. The processor lives from Connect, and both reference
        captures showed the opening 45 s of a stream to be the strap being
        adjusted on two electrodes with the muscle bands high: a baseline
        taken there put every later score near zero. Nothing before arming
        reaches the signal tables, so the only baseline that is ever recorded
        against is the one gathered after this call. The scores do not step:
        whatever centre was in use stays in use until the new latch, then
        ramps.
        """
        self._baseline_focus.clear()
        self._baseline_calm.clear()
        self._baseline_started = None
        self._baseline_collecting = True

    def reset(self) -> None:
        """Drop all buffered samples (e.g. after a signal-loss gap) so the next
        real reading warms back up cleanly instead of blending pre-gap and
        post-gap samples."""
        self.window.clear()
        self._is_good_history.clear()
        self._hsi_history.clear()
        self._samples_rejected = 0
        self._ratio_history.clear()
        self._delta_history.clear()
        self._spread_history.clear()
        self._samples_artifact = 0
        self._held_ratios = None
        self._ema_focus = None
        self._ema_calm = None
        self._ema_ts = None
        # The baseline is deliberately NOT cleared here. The stream manager
        # calls reset() on every tick with no sample, which flapping contact
        # does repeatedly, so a strap slipping at minute 20 would otherwise
        # make the next 45 s of re-fitting the session's new zero point --
        # the failure restart_baseline() exists to prevent, arriving through
        # a path nothing arms. The baseline belongs to the session, not to
        # the stream's continuity; only restart_baseline() replaces it.

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _extract_band_log_ratios(self, bands: dict[str, Any] | None) -> tuple[float | None, float | None]:
        if not bands:
            return (None, None)
        # The exponentiation below is inside this try, not after it: 10.0**x
        # raises OverflowError past x~308, which means the same thing as a
        # malformed value (an upstream producer not emitting Bels). Better to
        # return (None, None) and fall back to the amplitude path than let it
        # escape and drop the whole tick upstream.
        try:
            alpha = float(bands.get("alpha", 0.0))
            beta = float(bands.get("beta", 0.0))
            theta = float(bands.get("theta", 0.0))
            gamma = float(bands.get("gamma", 0.0))

            # Bridge reports exact zeros across every band when it has no band
            # features yet. Must stay an all-zero check: libMuse band powers
            # are logarithms, so individual bands are legitimately negative and
            # a "<= 0" test on any single band would discard valid frames.
            if alpha == 0.0 and beta == 0.0 and theta == 0.0 and gamma == 0.0:
                return (None, None)

            # libMuse ABSOLUTE band powers are logarithmic (Bels) and must be
            # converted back to linear power before being added, since adding
            # logs multiplies the underlying powers. linear = 10 ** bels.
            alpha_p = 10.0**alpha
            beta_p = 10.0**beta
            theta_p = 10.0**theta
            gamma_p = 10.0**gamma

            focus_log_ratio = log(beta_p + self.EPSILON) - log(alpha_p + theta_p + self.EPSILON)
            calm_log_ratio = log(alpha_p + self.EPSILON) - log(beta_p + gamma_p + self.EPSILON)
        except (TypeError, ValueError, OverflowError):
            return (None, None)
        return (focus_log_ratio, calm_log_ratio)

    def _smoothed(self, history: deque, now: float, value: float) -> float:
        """Append a contact reading and average it over a fixed time window.

        Smoothed by elapsed time, not sample count: a count-based window
        silently spans a much longer wall-clock period when the bridge reports
        contact intermittently.
        """
        history.append((now, value))
        cutoff = now - self.CONTACT_SMOOTHING_SECONDS
        while history and history[0][0] < cutoff:
            history.popleft()
        return fmean([v for _, v in history])

    def _contact_ratio(self, meta: dict[str, Any] | None, now: float) -> float | None:
        """Smoothed electrode contact in 0..1, or None when the bridge reports
        no contact data at all (an older bridge).

        Computed once per tick and shared by the quality verdict and the
        confidence score, so the two cannot disagree about the same
        electrodes. Takes the worse of the HSI fit and the IS_GOOD fraction
        when both are present, so a channel that is seated but noisy still
        counts against us.
        """
        hsi = (meta or {}).get("hsi")
        is_good = (meta or {}).get("is_good")

        good_channels: float | None = None
        if isinstance(is_good, list) and is_good:
            try:
                instant = sum(1 for v in is_good if float(v) >= 1.0) / len(is_good)
            except (TypeError, ValueError):
                instant = None
            if instant is not None:
                good_channels = self._smoothed(self._is_good_history, now, instant)

        fit_score: float | None = None
        if isinstance(hsi, list) and hsi:
            try:
                # Map 1 -> 1.0 (good), 2 -> 0.5 (mediocre), 4 -> 0.0 (poor);
                # ignore 0, which means "not reported for this channel".
                rated = [float(v) for v in hsi if float(v) > 0.0]
            except (TypeError, ValueError):
                rated = []
            if rated:
                instant_fit = sum(
                    1.0 if v <= 1.0 else (0.5 if v <= 2.0 else 0.0) for v in rated
                ) / len(rated)
                # Smoothed on the same basis as IS_GOOD: taking the worse of a
                # smoothed and an instantaneous input would let a single-frame
                # HSI blip drop straight to "poor", the flapping this prevents.
                fit_score = self._smoothed(self._hsi_history, now, instant_fit)

        parts = [p for p in (fit_score, good_channels) if p is not None]
        return min(parts) if parts else None

    def _signal_quality(
        self,
        contact: float | None,
        confidence_ratio: float,
        calm_ratio: float,
    ) -> tuple[str, str]:
        """How trustworthy the EEG signal is -- i.e. how well the electrodes are
        seated -- which is a separate question from whether the wearer is calm.

        Returns (quality, basis) where basis is "contact" when the headband's
        own HSI_PRECISION / IS_GOOD backed the verdict, or "heuristic" when it
        did not and the legacy calm/confidence rule was used instead.

        That distinction matters downstream: the heuristic is known to
        under-report. It gates on calm_ratio (alpha/(beta+gamma)), and alpha is
        suppressed in an alert, eyes-open student, so a perfectly-fitted
        headband on a focused learner scores "poor". Callers acting on "poor"
        must not treat a heuristic verdict as evidence of bad electrodes, or an
        older bridge without contact data would silently disable a session.
        """
        if contact is None:
            if confidence_ratio >= 0.75 and calm_ratio >= 0.55:
                return ("good", "heuristic")
            if confidence_ratio >= 0.45 and calm_ratio >= 0.3:
                return ("degraded", "heuristic")
            return ("poor", "heuristic")

        # 0.8 keeps "good" at "at most one of four electrodes is mediocre"
        # (0.875); two mediocre channels (0.75) drops to degraded.
        if contact >= self.CONTACT_GOOD:
            return ("good", "contact")
        if contact >= self.CONTACT_DEGRADED:
            return ("degraded", "contact")
        return ("poor", "contact")

    def _collect_baseline(self, focus_raw: float, calm_raw: float, ts: datetime,
                          contact: float | None) -> None:
        """Accumulate the opening stretch of a session as that person's baseline.

        Absolute EEG amplitudes and band ratios vary enormously between people
        (skull thickness, hair, electrode placement), so fixed population bounds
        can only ever be roughly right for anyone. Scoring each learner against
        their own resting values removes that dependency, and also cancels part
        of any per-session contact offset, since the baseline is captured under
        the same contact conditions as the samples it is compared against.

        Only ticks on at least degraded contact count, and the clock only
        starts on the first of them: the strap being adjusted is exactly what
        must not become the zero point (tests/fixtures/EEG_REFERENCE.md,
        finding 6). Degraded rather than good, because degraded is the
        ordinary state on this hardware and a baseline gated on good would
        never latch.

        Known limitation, accepted by decision: the opening stretch is taken
        as representative. A learner who starts already engaged makes that
        engagement their zero point.
        """
        if not self._baseline_collecting:
            return
        if contact is not None and contact < self.CONTACT_DEGRADED:
            return
        if self._baseline_started is None:
            self._baseline_started = ts
        self._baseline_focus.append(focus_raw)
        self._baseline_calm.append(calm_raw)
        elapsed = (ts - self._baseline_started).total_seconds()
        if elapsed >= self.BASELINE_SECONDS and len(self._baseline_focus) >= self.BASELINE_MIN_SAMPLES:
            # The ramp starts from wherever each score's centre is right now,
            # so a restart mid-session is as step-free as the first latch.
            self._centre_from = {which: self._centre(which, ts) for which in ("focus", "calm")}
            self._baseline_focus_mean = fmean(self._baseline_focus)
            self._baseline_calm_mean = fmean(self._baseline_calm)
            self._baseline_ready = True
            self._baseline_latched = ts
            self._baseline_collecting = False

    def _score_against_baseline(self, raw: float, which: str, ts: datetime | None = None) -> float:
        """Map a log-ratio to 0..1, centred on this session's baseline once
        there is one and on the population midpoint until then.

        One scale for both: the population range, so "at your own resting
        level" reads as 0.5 and a session crossing the latch does not change
        gain. The centre ramps from the midpoint to the session mean over
        BASELINE_RAMP_SECONDS after the latch, so the crossing is not a step
        either.
        """
        lo, hi = self._bounds(which)
        span = hi - lo
        if span <= 0:
            return 0.5
        return self._clamp01(0.5 + (raw - self._centre(which, ts)) / span)

    def _bounds(self, which: str) -> tuple[float, float]:
        if which == "focus":
            return self.FOCUS_LOG_RATIO_MIN, self.FOCUS_LOG_RATIO_MAX
        return self.CALM_LOG_RATIO_MIN, self.CALM_LOG_RATIO_MAX

    def _centre(self, which: str, ts: datetime | None) -> float:
        """The value that scores 0.5 for `which` at `ts`: the population
        midpoint until a baseline latches, then a ramp from the centre in use
        at the latch to the session mean."""
        lo, hi = self._bounds(which)
        midpoint = (lo + hi) / 2.0
        baseline = self._baseline_focus_mean if which == "focus" else self._baseline_calm_mean
        if not self._baseline_ready or baseline is None:
            return midpoint
        start = self._centre_from.get(which)
        if start is None:
            start = midpoint
        if ts is None or self._baseline_latched is None:
            return baseline
        fraction = self._clamp01(
            (ts - self._baseline_latched).total_seconds() / self.BASELINE_RAMP_SECONDS)
        return start + fraction * (baseline - start)

    def _smooth_ratios(self, focus_raw: float, calm_raw: float, ts: datetime) -> tuple[float, float]:
        """Advance the smoothed log ratios to this admitted tick and return them.

        Seeded by the first admitted tick rather than by zero, so a session
        does not open with a ramp from a value nobody measured.
        """
        if self._ema_focus is None or self._ema_calm is None or self._ema_ts is None:
            self._ema_focus, self._ema_calm, self._ema_ts = focus_raw, calm_raw, ts
            return focus_raw, calm_raw
        dt = (ts - self._ema_ts).total_seconds()
        if dt <= 0.0:
            dt = self.NOMINAL_TICK_SECONDS
        weight = 1.0 - exp(-dt / self.RATIO_SMOOTHING_SECONDS)
        self._ema_focus += weight * (focus_raw - self._ema_focus)
        self._ema_calm += weight * (calm_raw - self._ema_calm)
        self._ema_ts = ts
        return self._ema_focus, self._ema_calm

    def _artifact_reason(self, bands: dict[str, Any], frame_spread: float | None) -> str | None:
        """Why this tick is an artifact, or None if it is not.

        Judged against the running medians of admitted ticks, so a bound is
        "this tick is unlike the recent signal" rather than an absolute
        number that would move with the strap. No verdict until enough
        history exists; the first ticks of a session are admitted as they
        come, and the baseline's own contact gate is what protects those.
        """
        # Each gate waits for its own history, not for delta's: a stream
        # that never reports delta must still catch a clench and a spread
        # jump, or it reports zero artifacts and reads as clean.
        try:
            delta = float(bands.get("delta"))
        except (TypeError, ValueError):
            delta = None
        try:
            beta = float(bands.get("beta", 0.0))
            gamma = float(bands.get("gamma", 0.0))
        except (TypeError, ValueError):
            return None
        # Bels are logs, and the medians are of log values, so "N times the
        # running median" is log10(N) above it -- delta near 0 Bels at rest
        # would make a ratio of the raw numbers meaningless.
        if (delta is not None and len(self._delta_history) >= self.ARTIFACT_MIN_HISTORY
                and delta - median(self._delta_history) > log10(self.DELTA_JUMP_FACTOR)):
            return "delta_jump"
        if gamma - beta > self.EMG_GAMMA_EXCESS:
            return "emg_gamma"
        if (frame_spread is not None and len(self._spread_history) >= self.ARTIFACT_MIN_HISTORY
                and frame_spread > self.SPREAD_JUMP_FACTOR * max(median(self._spread_history), 1.0)):
            return "spread_jump"
        return None

    @staticmethod
    def _good_channel_values(sample: EegSample, meta: dict[str, Any] | None) -> list[float]:
        """The raw channel values the headband reports as usable.

        Mirrors the mask the native bridge applies when averaging band powers
        (update_band_power in muse_bridge_service.cpp): a channel counts when
        IS_GOOD says the last second was usable AND HSI says the fit is at
        least mediocre (<= 2; 0 means "not reported", not evidence of a bad
        fit).

        Without this, the amplitude path averages all four electrodes
        unconditionally, so ear contacts failing while the frontals read
        cleanly would pass straight through -- _sample_is_usable only rejects a
        frame when every electrode is bad, and mean_spread (max-min across
        channels) is maximally sensitive to a railing electrode.

        Falls back to all four whenever contact data is absent or would exclude
        everything, so this never yields less data than not filtering at all.

        Deliberately stricter than _sample_is_usable, which consults IS_GOOD
        only: that asks "is this frame worth keeping at all", this asks "which
        electrodes within it can be trusted" -- a poor HSI fit is a reason to
        distrust one channel, not to discard a frame whose other channels are
        fine.

        One visible consequence: a frame reporting is_good=[1,1,1,1] with
        hsi=[4,4,4,4] (IS_GOOD says usable, HSI says nothing is seated) is
        admitted by _sample_is_usable, then excludes every channel here and
        falls back to all four -- the conservative choice given the two
        readings disagree and the headband still claims usable data.
        """
        values = [sample.channel_tp9, sample.channel_af7, sample.channel_af8, sample.channel_tp10]
        meta = meta or {}
        is_good = meta.get("is_good")
        hsi = meta.get("hsi")
        has_is_good = isinstance(is_good, list) and len(is_good) == len(values)
        has_hsi = isinstance(hsi, list) and len(hsi) == len(values)
        if not has_is_good and not has_hsi:
            return values
        kept: list[float] = []
        try:
            for i, value in enumerate(values):
                valid = (not has_is_good) or float(is_good[i]) >= 1.0
                seated = (not has_hsi) or float(hsi[i]) <= 2.0
                if valid and seated:
                    kept.append(value)
        except (TypeError, ValueError):
            return values
        return kept or values

    @staticmethod
    def _sample_is_usable(meta: dict[str, Any] | None) -> bool:
        """False when the headband reports every electrode as invalid.

        IS_GOOD drops on blinks and movement, so an unusable frame is normal
        and transient -- but admitting it into the rolling window skews mean
        level, spread and stability for the whole window length afterwards (5s
        at the default size). Better to leave the window holding the last
        known-good samples than average an artifact into it.

        Intentionally consults IS_GOOD only, unlike _good_channel_values (which
        also requires HSI <= 2 per channel, see note there): a bad fit is a
        reason to distrust one electrode, not to discard a frame outright.
        """
        is_good = (meta or {}).get("is_good")
        if not isinstance(is_good, list) or not is_good:
            return True  # no contact data -- can't judge, so don't discard
        try:
            return any(float(v) >= 1.0 for v in is_good)
        except (TypeError, ValueError):
            return True

    def update(self, sample: EegSample, bands: dict[str, Any] | None = None) -> dict[str, float]:
        # Never let filtering empty the window -- the feature maths below needs
        # at least one sample, and a run of bad frames should hold the last good
        # reading rather than fail.
        usable = self._sample_is_usable(bands)
        now = self._clock()
        contact = self._contact_ratio(bands, now)
        band_focus_raw, band_calm_raw = self._extract_band_log_ratios(bands)
        using_band_features = band_focus_raw is not None and band_calm_raw is not None

        frame_values = self._good_channel_values(sample, bands)
        frame_spread = (max(frame_values) - min(frame_values)) if len(frame_values) >= 2 else None
        artifact_reason = self._artifact_reason(bands, frame_spread) if using_band_features else None
        if artifact_reason is not None:
            self._samples_artifact += 1
        # A tick is admitted -- to the window, the baseline and the spectral
        # histories -- when the headband vouches for it and the artifact gate
        # does not reject it.
        admit = usable and artifact_reason is None

        if self.window and not admit:
            if not usable:
                self._samples_rejected += 1
        else:
            # Store only the electrodes the headband vouches for, so a failed
            # contact cannot skew mean level or spread for the whole window.
            self.window.append(frame_values)
        per_sample_spreads: list[float] = []
        per_sample_means: list[float] = []
        for values in self.window:
            per_sample_means.append(fmean(values))
            # A spread needs at least two electrodes. With one good channel
            # max-min is 0, which would score as a perfectly steady signal --
            # fabricating "maximally calm" out of an almost-dead headband.
            if len(values) >= 2:
                per_sample_spreads.append(max(values) - min(values))
        # Mean of the per-frame means, not of every channel value pooled
        # together: window entries vary in length since bad electrodes are
        # dropped, and pooling would weight a 4-channel frame twice as heavily
        # as a 2-channel one. Identical to pooling when every frame has the
        # same width.
        mean_level = fmean(per_sample_means)
        mean_spread = fmean(per_sample_spreads) if per_sample_spreads else None

        focus_span = self.FOCUS_MAX_LEVEL - self.FOCUS_MIN_LEVEL
        focus_amp_ratio = self._clamp01((mean_level - self.FOCUS_MIN_LEVEL) / focus_span)

        calm_span = self.CALM_MAX_SPREAD - self.CALM_MIN_SPREAD
        calm_amp_ratio = (
            None if mean_spread is None
            else self._clamp01(1.0 - ((mean_spread - self.CALM_MIN_SPREAD) / calm_span))
        )

        if using_band_features:
            # Gated on the same verdict as the window: the baseline latches
            # once and is never revisited, so letting it ingest rejected or
            # held frames would skew every score for the session.
            if admit:
                self._collect_baseline(band_focus_raw, band_calm_raw, sample.timestamp, contact)
                self._ratio_history.append(band_focus_raw)
            if usable:
                # The gate's reference is every usable tick, held ones
                # included. Feeding it only the ticks it admitted lets it
                # ratchet: the median settles low, anything above it is
                # held, held ticks never raise the median. Measured on the
                # reference capture that held a third of resting ticks.
                #
                # delta is not among the bands the ratios read, so a tick can
                # have usable band features and no delta -- `.get` with a
                # default does not catch an explicit None, and an unguarded
                # float() here would fail the whole tick.
                try:
                    self._delta_history.append(float(bands.get("delta")))
                except (TypeError, ValueError):
                    pass
                if frame_spread is not None:
                    self._spread_history.append(frame_spread)
            # The spectral terms take full weight. They used to be blended
            # 75/25 with the amplitude terms "for continuity", and the
            # amplitude terms are not brain activity: mean raw level is ADC
            # offset plus electrode drift, and cross-electrode spread is
            # impedance mismatch -- rest spread was 147 uV on one strap
            # fitting and 23 uV on another for the same person at the same
            # task (tests/fixtures/EEG_REFERENCE.md). A quarter of every
            # score moved with the strap.
            if admit:
                focus_smooth, calm_smooth = self._smooth_ratios(
                    band_focus_raw, band_calm_raw, sample.timestamp)
                focus_ratio = self._score_against_baseline(focus_smooth, "focus", sample.timestamp)
                calm_ratio = self._score_against_baseline(calm_smooth, "calm", sample.timestamp)
                self._held_ratios = (focus_ratio, calm_ratio)
            elif self._held_ratios is not None:
                # Hold the last admitted scores: a blink is not a change in
                # focus, and scoring it would write one. The smoothed ratios
                # stay where they were too.
                focus_ratio, calm_ratio = self._held_ratios
            else:
                # Nothing admitted yet this session: score the raw tick, so a
                # session that opens on a bad frame still has a number.
                focus_ratio = self._score_against_baseline(band_focus_raw, "focus", sample.timestamp)
                calm_ratio = self._score_against_baseline(band_calm_raw, "calm", sample.timestamp)
        else:
            focus_ratio = focus_amp_ratio
            # Neutral rather than 1.0: no spread data is absence of evidence.
            calm_ratio = 0.5 if calm_amp_ratio is None else calm_amp_ratio

        warmup_factor = len(self.window) / self.window.maxlen
        if using_band_features:
            # Spread of the raw focus ratio over the window: a spectrum that
            # jumps tick to tick is one the score should not be trusted on.
            ratio_std = pstdev(self._ratio_history) if len(self._ratio_history) > 1 else 0.0
            stability_factor = self._clamp01(1.0 - (ratio_std / self.RATIO_STD_MAX))
        else:
            # Fallback path: the only signal is the raw level, so its
            # steadiness is the only stability there is.
            stability_std = pstdev(per_sample_means) if len(per_sample_means) > 1 else 0.0
            stability_factor = self._clamp01(1.0 - (stability_std / self.STABILITY_STD_MAX))
        contact_term = (
            self.CONTACT_UNKNOWN if contact is None
            else self._clamp01((contact - self.CONTACT_DEGRADED) / (1.0 - self.CONTACT_DEGRADED))
        )
        confidence_ratio = self._clamp01(
            (self.CONFIDENCE_WEIGHT_WARMUP * warmup_factor)
            + (self.CONFIDENCE_WEIGHT_CONTACT * contact_term)
            + (self.CONFIDENCE_WEIGHT_STABILITY * stability_factor)
            + (self.CONFIDENCE_WEIGHT_BANDS if using_band_features else 0.0)
        )
        confidence_ratio = max(0.2, confidence_ratio)
        focus_score = focus_ratio * 100.0
        calm_score = calm_ratio * 100.0
        confidence = confidence_ratio * 100.0
        signal_quality, quality_basis = self._signal_quality(contact, confidence_ratio, calm_ratio)
        return {
            "focus_score": round(focus_score, 3),
            "calm_score": round(calm_score, 3),
            "confidence": round(confidence, 3),
            "signal_quality": signal_quality,
            # "contact" when the headband's own electrode data backed the
            # verdict, "heuristic" when it did not. Consumers that act on
            # "poor" must check this -- see _signal_quality.
            "quality_basis": quality_basis,
            # Diagnostics, surfaced so a session that scored oddly can be
            # explained after the fact: frames the contact filter dropped, and
            # electrodes the bridge averaged into the band values (4 = all).
            "samples_rejected": self._samples_rejected,
            "band_channels_used": (bands or {}).get("band_channels_used"),
            # The smoothed 0..1 contact the quality verdict and confidence
            # were computed from; None when the bridge reports no contact.
            "contact_ratio": None if contact is None else round(contact, 3),
            # Artifact gate: ticks held this session, and why this one was
            # (None when it was admitted). Held is not rejected: a held tick
            # still carries the previous scores and its own contact verdict.
            "samples_artifact": self._samples_artifact,
            "artifact_reason": artifact_reason,
            # Raw, pre-baseline log ratios -- diagnostics for the accuracy
            # capture (HANDOFF.md Phase 0). None on a frame with no usable
            # bands, distinct from a real ratio of 0.
            "focus_log_ratio": band_focus_raw,
            "calm_log_ratio": band_calm_raw,
            # The smoothed ratios the scores were actually scaled from.
            "focus_log_ratio_smoothed": self._ema_focus,
            "calm_log_ratio_smoothed": self._ema_calm,
        }
