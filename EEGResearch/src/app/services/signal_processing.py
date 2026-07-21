from __future__ import annotations

from collections import deque
from math import log
from time import monotonic
from statistics import fmean, pstdev
from typing import Any

from src.app.models import EegSample


class SignalProcessor:
    """Computes smoothed focus/calm features from incoming samples."""

    # Raw amplitude bounds. Widened from the original 500-850 / 5-120: live
    # Muse S captures show mean levels near 950 and cross-channel spreads in
    # the hundreds of uV, so the old bounds clamped both amplitude terms to
    # their extremes on real hardware.
    FOCUS_MIN_LEVEL = 400.0
    FOCUS_MAX_LEVEL = 1000.0
    # Floor stays low: a well-seated headband on a quiet signal legitimately
    # produces single- to low-double-digit spreads, and raising the floor makes
    # those saturate at maximum calm. Only the ceiling needed widening.
    CALM_MIN_SPREAD = 5.0
    CALM_MAX_SPREAD = 300.0
    STABILITY_STD_MAX = 45.0

    # Log-ratio bounds for the spectral terms:
    #   focus = beta / (alpha + theta), calm = alpha / (beta + gamma)
    # (computed on linear power -- see _extract_band_log_ratios).
    #
    # Recalibrated for the actual use case: a seated student working through
    # problems with their eyes open. Alpha is the relaxed, eyes-closed rhythm
    # and is suppressed during engaged mental effort (alpha desynchronization),
    # so the previous calm floor of ln(0.70) sat above where an engaged learner
    # actually lives -- pinning calm_band_ratio to 0 and making "not stressed"
    # unreachable while concentrating.
    #
    # Calm spans ratio 0.20 (aroused/stressed) to 1.60 (clearly alpha-dominant,
    # relaxed); focus spans 0.40 (drowsy, theta/alpha heavy) to 2.00 (strongly
    # beta-dominant). These are physiology-informed heuristics, not values
    # derived from a controlled study -- a clean multi-subject baseline capture
    # (good contact on all four electrodes) should be used to tighten them.
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

    def __init__(self, window_size: int = 20) -> None:
        # Holds the good-channel values per admitted sample, not the raw
        # EegSample: which electrodes were trustworthy is a property of the
        # moment the sample arrived, so the mask has to be applied on the way
        # in rather than recomputed later against whatever contact data is
        # current. See _good_channel_values.
        self.window: deque[list[float]] = deque(maxlen=window_size)
        # IS_GOOD reports whether the last second of EEG was usable per channel,
        # and libMuse notes it dips on eye blinks and muscle movement. Those are
        # normal and constant, so the instantaneous value flickers -- smooth it
        # over the same window rather than letting one blink downgrade the
        # reported quality.
        # (monotonic_seconds, value) pairs, pruned by elapsed time rather than
        # by count -- see _smoothed. maxlen is a backstop only: pruning is what
        # bounds these, but without it correctness would rest entirely on
        # monotonic() advancing, and an unbounded deque is a bad thing to leave
        # one assumption away from growing forever. Sized well above the sample
        # count the time window can hold at realistic stream rates.
        self._contact_history_cap = max(64, window_size * 16)
        self._is_good_history: deque[tuple[float, float]] = deque(maxlen=self._contact_history_cap)
        self._hsi_history: deque[tuple[float, float]] = deque(maxlen=self._contact_history_cap)
        # Diagnostic: how many frames were kept out of the window as unusable.
        self._samples_rejected = 0
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
        # Baseline is per-session: a gap long enough to reset the window means
        # contact conditions likely changed, so the old baseline no longer
        # describes the signal we're about to score against it.
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
        # The exponentiation below is inside this try, not after it. 10.0**x
        # raises OverflowError once x is past ~308, and a band value that large
        # means the same thing as a malformed one: an upstream producer that
        # isn't emitting Bels. Letting it escape only pushes the failure up to
        # stream_manager's broad except, which drops the whole tick -- and a
        # dropped tick freezes latest_payload at its last value, which is
        # exactly the staleness _no_signal_payload exists to prevent. Better to
        # return (None, None) here and fall back to the amplitude path.
        try:
            alpha = float(bands.get("alpha", 0.0))
            beta = float(bands.get("beta", 0.0))
            theta = float(bands.get("theta", 0.0))
            gamma = float(bands.get("gamma", 0.0))

            # Bridge reports exact zeros across every band when it has no band
            # features yet. Note this must stay an all-zero check: libMuse band
            # powers are logarithms, so individual bands are legitimately
            # negative and a "<= 0" test on any single band would discard valid
            # frames.
            if alpha == 0.0 and beta == 0.0 and theta == 0.0 and gamma == 0.0:
                return (None, None)

            # libMuse ABSOLUTE band powers are logarithmic (Bels): "the
            # logarithm of the sum of the Power Spectral Density ... some of the
            # values will be negative". They must be converted back to linear
            # power before being added, because adding logs multiplies the
            # underlying powers. Previously these were fed straight into log()
            # -- taking a logarithm of a logarithm -- and summed in log space,
            # which made both ratios meaningless (and could take log() of a
            # negative sum, since theta in particular is routinely negative).
            # linear = 10 ** bels, per the libMuse documentation.
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

        Both inputs are smoothed by elapsed time rather than by sample count.
        A count-based window silently spans a much longer wall-clock period
        when the bridge reports contact intermittently, so "averaged over the
        window" would not mean a fixed duration.
        """
        history.append((now, value))
        cutoff = now - self.CONTACT_SMOOTHING_SECONDS
        while history and history[0][0] < cutoff:
            history.popleft()
        return fmean([v for _, v in history])

    def _signal_quality(
        self,
        meta: dict[str, Any] | None,
        confidence_ratio: float,
        calm_ratio: float,
        now: float,
    ) -> tuple[str, str]:
        """How trustworthy the EEG signal is -- i.e. how well the electrodes are
        seated -- which is a separate question from whether the wearer is calm.

        Returns (quality, basis) where basis is "contact" when the headband's
        own HSI_PRECISION / IS_GOOD backed the verdict, or "heuristic" when it
        did not and the legacy calm/confidence rule was used instead.

        That distinction matters downstream. The heuristic is known to
        under-report: it gates on calm_ratio, driven by alpha/(beta+gamma), and
        alpha is suppressed in an alert, eyes-open student -- so a
        perfectly-fitted headband on a focused learner scores "poor". Callers
        that act on "poor" (skipping persistence, blanking the UI) must not
        treat a heuristic "poor" as evidence of bad electrodes, or an older
        bridge that predates these packets would silently disable data
        collection for a whole session.
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
                # Smoothed on the same basis as IS_GOOD. Taking the worse of a
                # smoothed and an instantaneous input would let a single-frame
                # HSI blip bypass the smoothing entirely and drop straight to
                # "poor", which is the flapping this is meant to prevent.
                fit_score = self._smoothed(self._hsi_history, now, instant_fit)

        if fit_score is None and good_channels is None:
            if confidence_ratio >= 0.75 and calm_ratio >= 0.55:
                return ("good", "heuristic")
            if confidence_ratio >= 0.45 and calm_ratio >= 0.3:
                return ("degraded", "heuristic")
            return ("poor", "heuristic")

        # Use whichever signals are present; when both are, take the worse of
        # the two so a channel that's seated but noisy still counts against us.
        parts = [p for p in (fit_score, good_channels) if p is not None]
        contact = min(parts)
        # 0.8 keeps "good" at "at most one of four electrodes is mediocre"
        # (0.875); two mediocre channels (0.75) drops to degraded.
        if contact >= 0.8:
            return ("good", "contact")
        if contact >= 0.4:
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
        zero point, and subsequent genuine engagement then reads as neutral.
        Validation so far is simulated only; confirming this against a real
        seated capture, where the opening seconds are usually settling-in
        rather than work, is the outstanding piece.
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
        # "at your own resting level" reads as 0.5. Note this makes the
        # baseline path twice as sensitive as population scaling: the score
        # saturates at +-half the population range, not a full width. That is
        # deliberate -- within-person variation is much narrower than the
        # between-person spread the population bounds have to cover -- but it
        # does mean the two paths are not interchangeable, and a session
        # crossing over from one to the other will change gain at that point.
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
        least mediocre (<= 2; 0 means "not reported", which is not evidence of
        a bad fit).

        Without this the amplitude path averaged all four electrodes
        unconditionally, so the exact scenario this PR exists for -- ear
        contacts failing while the frontals read cleanly -- passed straight
        through: _sample_is_usable only rejects a frame when *every* electrode
        is bad. mean_spread is max-min across channels, which is maximally
        sensitive to a railing electrode, and the amplitude term is 25% of the
        blended scores and 32% of the confidence weight.

        Falls back to all four whenever contact data is absent or would exclude
        everything, so this never yields less data than the previous behaviour.
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

        IS_GOOD drops on blinks and movement, so an unusable frame is normal and
        transient -- but admitting it into the rolling window skews mean level,
        spread and stability for the whole window length afterwards (5s at the
        default size). Better to leave the window holding the last known-good
        samples than to average an artifact into it.
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
        if self.window and not usable:
            self._samples_rejected += 1
        else:
            # Store only the electrodes the headband vouches for, so a failed
            # contact cannot skew mean level or spread for the whole window.
            self.window.append(self._good_channel_values(sample, bands))
        all_values: list[float] = []
        per_sample_spreads: list[float] = []
        per_sample_means: list[float] = []
        for values in self.window:
            all_values.extend(values)
            per_sample_means.append(fmean(values))
            # A spread needs at least two electrodes. With one good channel
            # max-min is 0, which would score as a perfectly steady signal --
            # fabricating "maximally calm" out of an almost-dead headband.
            if len(values) >= 2:
                per_sample_spreads.append(max(values) - min(values))
        mean_level = fmean(all_values)
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
            # Gated on the same verdict as the window. The baseline used to
            # ingest exactly the frames the window rejected, which is worse
            # than the window pollution it mirrors: the baseline latches after
            # BASELINE_SAMPLES and is never revisited, so one burst of artifact
            # frames during warm-up skews every score for the rest of the
            # session.
            if usable:
                self._collect_baseline(band_focus_raw, band_calm_raw)
            focus_band_ratio = self._score_against_baseline(band_focus_raw, "focus")
            calm_band_ratio = self._score_against_baseline(band_calm_raw, "calm")
            # Blend toward amplitude-derived values to preserve continuity during
            # transient band jitter while still prioritizing spectral features.
            focus_ratio = (0.75 * focus_band_ratio) + (0.25 * focus_amp_ratio)
            # With no usable spread the amplitude term carries no information,
            # so the spectral term takes full weight rather than blending in a
            # value that was invented rather than measured.
            calm_ratio = (
                calm_band_ratio if calm_amp_ratio is None
                else (0.75 * calm_band_ratio) + (0.25 * calm_amp_ratio)
            )
        else:
            focus_ratio = focus_amp_ratio
            # Neutral rather than 1.0: no spread data is absence of evidence.
            calm_ratio = 0.5 if calm_amp_ratio is None else calm_amp_ratio

        warmup_factor = len(self.window) / self.window.maxlen
        stability_std = pstdev(per_sample_means) if len(per_sample_means) > 1 else 0.0
        stability_factor = self._clamp01(1.0 - (stability_std / self.STABILITY_STD_MAX))
        band_presence_bonus = 0.08 if using_band_features else 0.0
        confidence_ratio = self._clamp01(
            (0.28 * warmup_factor) + (0.32 * calm_ratio) + (0.32 * stability_factor) + band_presence_bonus
        )
        confidence_ratio = max(0.2, confidence_ratio)
        focus_score = focus_ratio * 100.0
        calm_score = calm_ratio * 100.0
        confidence = confidence_ratio * 100.0
        signal_quality, quality_basis = self._signal_quality(
            bands, confidence_ratio, calm_ratio, monotonic()
        )
        return {
            "focus_score": round(focus_score, 3),
            "calm_score": round(calm_score, 3),
            "confidence": round(confidence, 3),
            "signal_quality": signal_quality,
            # "contact" when the headband's own electrode data backed the
            # verdict, "heuristic" when it did not. Consumers that act on
            # "poor" must check this -- see _signal_quality.
            "quality_basis": quality_basis,
            # Diagnostics. Both are surfaced rather than kept internal so a
            # session that scored oddly can be explained after the fact:
            # how many frames the contact filter dropped, and how many
            # electrodes the bridge averaged into the band values (4 = all).
            "samples_rejected": self._samples_rejected,
            "band_channels_used": (bands or {}).get("band_channels_used"),
        }
