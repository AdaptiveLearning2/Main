from __future__ import annotations

from collections import deque
from math import log
from statistics import fmean, pstdev
from typing import Any

from src.app.models import EegSample


class SignalProcessor:
    """Computes smoothed focus/calm features from incoming samples."""

    # Tuned for raw Muse ranges observed in pilot runs (roughly 500-850).
    FOCUS_MIN_LEVEL = 500.0
    FOCUS_MAX_LEVEL = 850.0
    CALM_MIN_SPREAD = 5.0
    CALM_MAX_SPREAD = 120.0
    STABILITY_STD_MAX = 45.0
    # Log-ratio bounds derived from prior heuristic ratio ranges:
    # focus: beta / (alpha + theta), calm: alpha / (beta + gamma).
    # Log-ratios are less sensitive to denominator spikes and are easier to calibrate.
    FOCUS_LOG_RATIO_MIN = -0.511  # ln(0.60)
    FOCUS_LOG_RATIO_MAX = 0.588  # ln(1.80)
    CALM_LOG_RATIO_MIN = -0.357  # ln(0.70)
    CALM_LOG_RATIO_MAX = 0.788  # ln(2.20)
    EPSILON = 1e-6

    def __init__(self, window_size: int = 20) -> None:
        self.window: deque[EegSample] = deque(maxlen=window_size)

    def reset(self) -> None:
        """Drop all buffered samples (e.g. after a signal-loss gap) so the next
        real reading warms back up cleanly instead of blending pre-gap and
        post-gap samples."""
        self.window.clear()

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
        # Bridge reports zeros when no band features are available; ignore those frames.
        if alpha <= 0.0 and beta <= 0.0 and theta <= 0.0 and gamma <= 0.0:
            return (None, None)
        focus_log_ratio = log(beta + self.EPSILON) - log(alpha + theta + self.EPSILON)
        calm_log_ratio = log(alpha + self.EPSILON) - log(beta + gamma + self.EPSILON)
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
                good_channels = sum(1 for v in is_good if float(v) >= 1.0) / len(is_good)
            except (TypeError, ValueError):
                good_channels = None

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

    def update(self, sample: EegSample, bands: dict[str, Any] | None = None) -> dict[str, float]:
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
            focus_band_ratio = self._clamp01(
                (band_focus_raw - self.FOCUS_LOG_RATIO_MIN) / (self.FOCUS_LOG_RATIO_MAX - self.FOCUS_LOG_RATIO_MIN)
            )
            calm_band_ratio = self._clamp01(
                (band_calm_raw - self.CALM_LOG_RATIO_MIN) / (self.CALM_LOG_RATIO_MAX - self.CALM_LOG_RATIO_MIN)
            )
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
