from __future__ import annotations

from collections import deque
from math import log
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

    def __init__(self, window_size: int = 20) -> None:
        self.window: deque[EegSample] = deque(maxlen=window_size)
        # IS_GOOD reports whether the last second of EEG was usable per channel,
        # and libMuse notes it dips on eye blinks and muscle movement. Those are
        # normal and constant, so the instantaneous value flickers -- smooth it
        # over the same window rather than letting one blink downgrade the
        # reported quality.
        self._is_good_history: deque[float] = deque(maxlen=window_size)
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
        try:
            alpha = float(bands.get("alpha", 0.0))
            beta = float(bands.get("beta", 0.0))
            theta = float(bands.get("theta", 0.0))
            gamma = float(bands.get("gamma", 0.0))
        except (TypeError, ValueError):
            return (None, None)
        # Bridge reports exact zeros across every band when it has no band
        # features yet. Note this must stay an all-zero check: libMuse band
        # powers are logarithms, so individual bands are legitimately negative
        # and a "<= 0" test on any single band would discard valid frames.
        if alpha == 0.0 and beta == 0.0 and theta == 0.0 and gamma == 0.0:
            return (None, None)

        # libMuse ABSOLUTE band powers are logarithmic (Bels): "the logarithm
        # of the sum of the Power Spectral Density ... some of the values will
        # be negative". They must be converted back to linear power before
        # being added, because adding logs multiplies the underlying powers.
        # Previously these were fed straight into log() -- taking a logarithm
        # of a logarithm -- and summed in log space, which made both ratios
        # meaningless (and could take log() of a negative sum, since theta in
        # particular is routinely negative).
        # linear = 10 ** bels, per the libMuse documentation.
        alpha_p = 10.0**alpha
        beta_p = 10.0**beta
        theta_p = 10.0**theta
        gamma_p = 10.0**gamma

        focus_log_ratio = log(beta_p + self.EPSILON) - log(alpha_p + theta_p + self.EPSILON)
        calm_log_ratio = log(alpha_p + self.EPSILON) - log(beta_p + gamma_p + self.EPSILON)
        return (focus_log_ratio, calm_log_ratio)

    def _signal_quality(
        self, meta: dict[str, Any] | None, confidence_ratio: float, calm_ratio: float
    ) -> str:
        """How trustworthy the EEG signal is -- i.e. how well the electrodes are
        seated -- which is a separate question from whether the wearer is calm.

        Prefers the headband's own HSI_PRECISION (per-channel fit: 1 good,
        2 mediocre, 4 poor) and IS_GOOD (per-channel validity) when the bridge
        reports them. Falls back to the older calm/confidence heuristic only
        when they're unavailable (no headband, or a bridge that predates them).

        The fallback is known to under-report: it gates on calm_ratio, which is
        driven by alpha/(beta+gamma), and alpha is suppressed in an alert,
        eyes-open student -- so a perfectly-fitted headband on a focused
        learner reports "poor". That's why real contact data is preferred.
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
                # Average over the window so a blink (which briefly zeroes the
                # frontal channels) doesn't flip the reported quality; sustained
                # bad data still pulls the average down.
                self._is_good_history.append(instant)
                good_channels = fmean(self._is_good_history)

        fit_score: float | None = None
        if isinstance(hsi, list) and hsi:
            try:
                # Map 1 -> 1.0 (good), 2 -> 0.5 (mediocre), 4 -> 0.0 (poor);
                # ignore 0, which means "not reported for this channel".
                rated = [float(v) for v in hsi if float(v) > 0.0]
                if rated:
                    fit_score = sum(
                        1.0 if v <= 1.0 else (0.5 if v <= 2.0 else 0.0) for v in rated
                    ) / len(rated)
            except (TypeError, ValueError):
                fit_score = None

        if fit_score is None and good_channels is None:
            # No contact data available -- fall back to the legacy heuristic.
            if confidence_ratio >= 0.75 and calm_ratio >= 0.55:
                return "good"
            if confidence_ratio >= 0.45 and calm_ratio >= 0.3:
                return "degraded"
            return "poor"

        # Use whichever signals are present; when both are, take the worse of
        # the two so a channel that's seated but noisy still counts against us.
        parts = [p for p in (fit_score, good_channels) if p is not None]
        contact = min(parts)
        # 0.8 keeps "good" at "at most one of four electrodes is mediocre"
        # (0.875); two mediocre channels (0.75) drops to degraded.
        if contact >= 0.8:
            return "good"
        if contact >= 0.4:
            return "degraded"
        return "poor"

    def _collect_baseline(self, focus_raw: float, calm_raw: float) -> None:
        """Accumulate the opening samples of a session as that person's baseline.

        Absolute EEG amplitudes and band ratios vary enormously between people
        (skull thickness, hair, electrode placement), so fixed population bounds
        can only ever be roughly right for anyone. Scoring each learner against
        their own resting values removes that dependency, and also cancels part
        of any per-session contact offset, since the baseline is captured under
        the same contact conditions as the samples it is compared against.
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
        # "at your own resting level" reads as 0.5 and a full population-width
        # swing still saturates.
        half_span = (hi - lo) / 2.0
        if half_span <= 0:
            return 0.5
        return self._clamp01(0.5 + (raw - baseline) / (2.0 * half_span))

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
        if self.window and not self._sample_is_usable(bands):
            self._samples_rejected += 1
        else:
            self.window.append(sample)
        all_values: list[float] = []
        per_sample_spreads: list[float] = []
        per_sample_means: list[float] = []
        for item in self.window:
            values = [item.channel_tp9, item.channel_af7, item.channel_af8, item.channel_tp10]
            all_values.extend(values)
            per_sample_spreads.append(max(values) - min(values))
            per_sample_means.append(fmean(values))
        mean_level = fmean(all_values)
        mean_spread = fmean(per_sample_spreads)

        focus_span = self.FOCUS_MAX_LEVEL - self.FOCUS_MIN_LEVEL
        focus_amp_ratio = self._clamp01((mean_level - self.FOCUS_MIN_LEVEL) / focus_span)

        calm_span = self.CALM_MAX_SPREAD - self.CALM_MIN_SPREAD
        calm_amp_ratio = self._clamp01(1.0 - ((mean_spread - self.CALM_MIN_SPREAD) / calm_span))

        band_focus_raw, band_calm_raw = self._extract_band_log_ratios(bands)
        using_band_features = band_focus_raw is not None and band_calm_raw is not None
        if using_band_features:
            self._collect_baseline(band_focus_raw, band_calm_raw)
            focus_band_ratio = self._score_against_baseline(band_focus_raw, "focus")
            calm_band_ratio = self._score_against_baseline(band_calm_raw, "calm")
            # Blend toward amplitude-derived values to preserve continuity during
            # transient band jitter while still prioritizing spectral features.
            focus_ratio = (0.75 * focus_band_ratio) + (0.25 * focus_amp_ratio)
            calm_ratio = (0.75 * calm_band_ratio) + (0.25 * calm_amp_ratio)
        else:
            focus_ratio = focus_amp_ratio
            calm_ratio = calm_amp_ratio

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
        signal_quality = self._signal_quality(bands, confidence_ratio, calm_ratio)
        return {
            "focus_score": round(focus_score, 3),
            "calm_score": round(calm_score, 3),
            "confidence": round(confidence, 3),
            "signal_quality": signal_quality,
        }
