from __future__ import annotations

from collections import deque
from math import log
from time import monotonic
from statistics import fmean, pstdev
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

    # Samples of usable band data collected at the start of a session before
    # scores switch from the fixed population bounds to this learner's own
    # baseline. At the default 4Hz stream rate this is roughly 15 seconds.
    BASELINE_SAMPLES = 60

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
        # Per-session baseline: scores are relative to this learner's own
        # resting values rather than fixed population constants.
        self._baseline_focus: list[float] = []
        self._baseline_calm: list[float] = []
        self._baseline_focus_mean: float | None = None
        self._baseline_calm_mean: float | None = None
        self._baseline_ready = False

    def reset(self) -> None:
        """Drop all buffered samples (e.g. after a signal-loss gap) so the next
        real reading warms back up cleanly instead of blending pre-gap and
        post-gap samples."""
        self.window.clear()
        self._is_good_history.clear()
        self._hsi_history.clear()
        self._samples_rejected = 0
        self._ratio_history.clear()
        # Baseline is per-session: a gap long enough to reset the window means
        # contact conditions likely changed, so the old baseline no longer
        # describes the signal it would be scored against.
        self._baseline_focus.clear()
        self._baseline_calm.clear()
        self._baseline_focus_mean = None
        self._baseline_calm_mean = None
        self._baseline_ready = False

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

    def _collect_baseline(self, focus_raw: float, calm_raw: float) -> None:
        """Accumulate the opening samples of a session as that person's baseline.

        Absolute EEG amplitudes and band ratios vary enormously between people
        (skull thickness, hair, electrode placement), so fixed population bounds
        can only ever be roughly right for anyone. Scoring each learner against
        their own resting values removes that dependency, and also cancels part
        of any per-session contact offset, since the baseline is captured under
        the same contact conditions as the samples it is compared against.

        Known limitation: this assumes the opening ~15s is representative of
        rest. A learner who starts already engaged makes that engagement their
        zero point, so genuine engagement afterward reads as neutral. Validated
        so far only in simulation, not against a real seated capture.
        """
        if self._baseline_ready:
            return
        self._baseline_focus.append(focus_raw)
        self._baseline_calm.append(calm_raw)
        if len(self._baseline_focus) >= self.BASELINE_SAMPLES:
            self._baseline_focus_mean = fmean(self._baseline_focus)
            self._baseline_calm_mean = fmean(self._baseline_calm)
            self._baseline_ready = True

    def _score_against_baseline(self, raw: float, which: str) -> float:
        """Map a log-ratio to 0..1 relative to this session's baseline.

        Falls back to the fixed population bounds until enough baseline samples
        have been collected, so a session still produces usable (if less well
        calibrated) scores from its first moments rather than nothing.
        """
        if which == "focus":
            baseline = self._baseline_focus_mean
            lo, hi = self.FOCUS_LOG_RATIO_MIN, self.FOCUS_LOG_RATIO_MAX
        else:
            baseline = self._baseline_calm_mean
            lo, hi = self.CALM_LOG_RATIO_MIN, self.CALM_LOG_RATIO_MAX

        if not self._baseline_ready or baseline is None:
            return self._clamp01((raw - lo) / (hi - lo))

        # Centre on the baseline and spread by half the population range, so
        # "at your own resting level" reads as 0.5. This makes the baseline
        # path twice as sensitive as population scaling (saturates at +-half
        # the range, not the full width) -- deliberate, since within-person
        # variation is much narrower than the between-person spread the
        # population bounds cover. A session crossing from one path to the
        # other changes gain at that point.
        half_span = (hi - lo) / 2.0
        if half_span <= 0:
            return 0.5
        return self._clamp01(0.5 + (raw - baseline) / (2.0 * half_span))

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
        if self.window and not usable:
            self._samples_rejected += 1
        else:
            # Store only the electrodes the headband vouches for, so a failed
            # contact cannot skew mean level or spread for the whole window.
            self.window.append(self._good_channel_values(sample, bands))
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

        band_focus_raw, band_calm_raw = self._extract_band_log_ratios(bands)
        using_band_features = band_focus_raw is not None and band_calm_raw is not None
        if using_band_features:
            # Gated on the same verdict as the window: the baseline latches
            # after BASELINE_SAMPLES and is never revisited, so letting it
            # ingest rejected frames would skew every score for the session.
            if usable:
                self._collect_baseline(band_focus_raw, band_calm_raw)
                self._ratio_history.append(band_focus_raw)
            # The spectral terms take full weight. They used to be blended
            # 75/25 with the amplitude terms "for continuity", and the
            # amplitude terms are not brain activity: mean raw level is ADC
            # offset plus electrode drift, and cross-electrode spread is
            # impedance mismatch -- rest spread was 147 uV on one strap
            # fitting and 23 uV on another for the same person at the same
            # task (tests/fixtures/EEG_REFERENCE.md). A quarter of every
            # score moved with the strap.
            focus_ratio = self._score_against_baseline(band_focus_raw, "focus")
            calm_ratio = self._score_against_baseline(band_calm_raw, "calm")
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
            # Raw, pre-baseline log ratios -- diagnostics for the accuracy
            # capture (HANDOFF.md Phase 0). None on a frame with no usable
            # bands, distinct from a real ratio of 0.
            "focus_log_ratio": band_focus_raw,
            "calm_log_ratio": band_calm_raw,
        }
