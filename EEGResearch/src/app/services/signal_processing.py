from __future__ import annotations

from collections import deque
from datetime import datetime
from math import exp, isfinite, log, log10
from time import monotonic
from statistics import fmean, median, pstdev
from typing import Any, Callable

from src.app.models import EegSample


class SignalProcessor:
    """Computes smoothed focus/calm features from incoming samples."""

    # Raw amplitude bounds (uV), for the no-bands fallback only.
    FOCUS_MIN_LEVEL = 400.0
    FOCUS_MAX_LEVEL = 1000.0
    # Floor stays low: a quiet, well-seated signal legitimately has single-digit spreads.
    CALM_MIN_SPREAD = 5.0
    CALM_MAX_SPREAD = 300.0
    STABILITY_STD_MAX = 45.0

    # Population log-ratio bounds: focus = beta / (alpha + theta), calm = alpha / (beta + gamma),
    # on linear power. Must bracket what a wearer produces; label thresholds in
    # adaptation.py and signal_fusion.py are scaled to these spans. See docs/signals.md.
    FOCUS_LOG_RATIO_MIN = -1.897  # ln(0.15)
    FOCUS_LOG_RATIO_MAX = 0.693  # ln(2.00)
    CALM_LOG_RATIO_MIN = -1.833  # ln(0.16)
    CALM_LOG_RATIO_MAX = 0.693  # ln(2.00)
    # Local-spectrum calm scale: 1/f-relative alpha residual (log10); midpoint 0 = no alpha above background.
    CALM_ALPHA_RESIDUAL_MIN = -0.6
    CALM_ALPHA_RESIDUAL_MAX = 0.6
    EPSILON = 1e-6

    # Baseline: covered seconds (sample clock) of admitted ticks on >= degraded contact
    # before scores centre on this learner; each tick adds <= BASELINE_TICK_CAP_SECONDS.
    # Fixed once latched; the centre ramps in over BASELINE_RAMP_SECONDS.
    BASELINE_SECONDS = 45.0
    BASELINE_RAMP_SECONDS = 10.0
    BASELINE_TICK_CAP_SECONDS = 1.0
    BASELINE_MAX_SAMPLES = 2000

    # Seconds of wall clock contact readings are averaged over; time-based, not count-based.
    CONTACT_SMOOTHING_SECONDS = 5.0
    # Lines on the smoothed 0..1 contact ratio. Degraded is the ordinary state on real
    # hardware and must be scoreable; poor is the fault. Nothing gates on "good".
    CONTACT_GOOD = 0.8
    CONTACT_DEGRADED = 0.4

    # Confidence is signal quality, not calm. Provisional weights: poor contact alone falls
    # below the 0.45 gate, degraded clears it. The contact term steps to
    # CONTACT_TERM_AT_DEGRADED at CONTACT_DEGRADED, then rises linearly to 1 at CONTACT_GOOD.
    CONFIDENCE_WEIGHT_WARMUP = 0.10
    CONFIDENCE_WEIGHT_CONTACT = 0.60
    CONFIDENCE_WEIGHT_STABILITY = 0.22
    CONFIDENCE_WEIGHT_BANDS = 0.08
    CONTACT_TERM_AT_DEGRADED = 0.5
    # pstdev of the raw focus log-ratio at which spectral stability reads 0.
    RATIO_STD_MAX = 1.0
    # Assumed when the bridge reports no contact: neither good nor poor.
    CONTACT_UNKNOWN = 0.5

    # Artifact gate: a tripped tick holds the previous scores and enters neither window
    # nor baseline (rejected, not low). Blink = delta jump, EMG = gamma over beta by
    # EMG_GAMMA_EXCESS Bels, spread vs its running median. See docs/signals.md.
    DELTA_JUMP_FACTOR = 3.0
    EMG_GAMMA_EXCESS = 0.5
    SPREAD_JUMP_FACTOR = 3.5
    # Entry cap is a backstop, sized for ARTIFACT_HISTORY_SECONDS at 64 Hz.
    ARTIFACT_HISTORY = 1280
    ARTIFACT_HISTORY_SECONDS = 20.0
    ARTIFACT_MIN_HISTORY = 8

    # Seconds; EMA time constant on each raw log ratio, on sample timestamps. A tick
    # whose timestamp has not advanced counts as NOMINAL_TICK_SECONDS.
    RATIO_SMOOTHING_SECONDS = 4.0
    NOMINAL_TICK_SECONDS = 0.25

    def __init__(self, window_size: int = 20, clock: Callable[[], float] = monotonic,
                 calm_source: str = "sdk", calm_centre_on_arm: str = "keep") -> None:
        # Local calm's centre between arm and new latch: "keep" the one in use, or
        # "midpoint". EEG_CALM_CENTRE_ON_ARM; an open decision.
        if calm_centre_on_arm not in ("keep", "midpoint"):
            raise ValueError(f"calm_centre_on_arm must be 'keep' or 'midpoint', got {calm_centre_on_arm!r}")
        self.calm_centre_on_arm = calm_centre_on_arm
        # Injectable so a recorded capture can be replayed at its own pace.
        self._clock = clock
        # "sdk": the bridge's alpha/(beta+gamma) log-ratio. "local": the alpha residual
        # from eeg_spectrum.py, passed per tick as `spectrum`. Focus is the SDK ratio on both.
        self.calm_source = "local" if calm_source == "local" else "sdk"
        # Good-channel values per sample: the contact mask applies at arrival, not later.
        self.window: deque[list[float]] = deque(maxlen=window_size)
        # IS_GOOD flickers on blinks, so it is smoothed. (monotonic_seconds, value)
        # pairs pruned by elapsed time; maxlen is a backstop only.
        self._contact_history_cap = max(64, window_size * 16)
        self._is_good_history: deque[tuple[float, float]] = deque(maxlen=self._contact_history_cap)
        self._hsi_history: deque[tuple[float, float]] = deque(maxlen=self._contact_history_cap)
        self._samples_rejected = 0
        # Raw focus log-ratios in the window, for confidence's stability term.
        self._ratio_history: deque[float] = deque(maxlen=window_size)
        # (monotonic_seconds, value) pairs over admitted ticks, pruned by elapsed time.
        self._delta_history: deque[tuple[float, float]] = deque(maxlen=self.ARTIFACT_HISTORY)
        self._spread_history: deque[tuple[float, float]] = deque(maxlen=self.ARTIFACT_HISTORY)
        self._samples_artifact = 0
        # Session totals of usable ticks the blink / spread gates had no reference for.
        self._samples_no_delta = 0
        self._samples_no_spread = 0
        self._held_ratios: tuple[float, float] | None = None
        # Smoothed raw log ratios; None until the first admitted tick seeds them.
        self._ema_focus: float | None = None
        self._ema_calm: float | None = None
        self._ema_ts: datetime | None = None
        # Bounded: a stalled sample clock covers nothing, so the list would otherwise grow.
        self._baseline_focus: deque[float] = deque(maxlen=self.BASELINE_MAX_SAMPLES)
        self._baseline_calm: deque[float] = deque(maxlen=self.BASELINE_MAX_SAMPLES)
        self._baseline_focus_mean: float | None = None
        self._baseline_calm_mean: float | None = None
        self._baseline_ready = False
        self._baseline_started: datetime | None = None
        self._baseline_last_ts: datetime | None = None
        self._baseline_coverage = 0.0
        self._baseline_latched: datetime | None = None
        # Post-latch ramp progress in covered seconds.
        self._ramp_elapsed = 0.0
        self._ramp_last_ts: datetime | None = None
        # Calm latches on its own coverage (ticks that had a calm value), with its own ramp.
        self._calm_collecting = True
        self._calm_ready = False
        self._calm_coverage = 0.0
        self._calm_last_ts: datetime | None = None
        self._calm_latched: datetime | None = None
        self._calm_ramp_elapsed = 0.0
        self._calm_ramp_last_ts: datetime | None = None
        # Whether a calm was scored since the last gap (a midpoint placeholder is not
        # one), and when the last fresh local estimate arrived.
        self._calm_ever = False
        self._calm_fresh_ts: datetime | None = None
        # The centre each score used when the current baseline latched; the ramp starts there.
        self._baseline_collecting = True
        self._centre_from: dict[str, float | None] = {"focus": None, "calm": None}

    def restart_baseline(self) -> None:
        """Gather a fresh baseline from the next admitted ticks, ramping from the current centre.

        Called at arm (first question), not stream start, to skip strap settling.
        No step: the first ~45 s of a recording are scored against the pre-arm centre.
        """
        self._baseline_focus.clear()
        self._baseline_calm.clear()
        self._baseline_started = None
        self._baseline_last_ts = None
        self._baseline_coverage = 0.0
        self._baseline_collecting = True
        self._calm_collecting = True
        self._calm_coverage = 0.0
        self._calm_last_ts = None
        if self.calm_centre_on_arm == "midpoint" and self.calm_source == "local":
            # Population midpoint until the new latch; local only, where the poison can starve the latch.
            self._calm_ready = False
            self._baseline_calm_mean = None
            self._calm_latched = None
            self._centre_from["calm"] = None

    def clear_session(self) -> None:
        """A session has ended: forget everything, baseline and counters included.

        reset() is for a gap within a session and keeps both.
        """
        self.reset()
        self._is_good_history.clear()
        self._hsi_history.clear()
        self._delta_history.clear()
        self._spread_history.clear()
        self._samples_rejected = 0
        self._samples_artifact = 0
        self._samples_no_delta = 0
        self._samples_no_spread = 0
        self.restart_baseline()
        self._baseline_focus_mean = None
        self._baseline_calm_mean = None
        self._baseline_ready = False
        self._baseline_latched = None
        self._ramp_elapsed = 0.0
        self._ramp_last_ts = None
        self._calm_ready = False
        self._calm_latched = None
        self._calm_ramp_elapsed = 0.0
        self._calm_ramp_last_ts = None
        self._centre_from = {"focus": None, "calm": None}

    def reset(self) -> None:
        """Drop the window and per-tick state after a signal-loss gap.

        Keeps the session baseline, the counters, and the contact and artifact
        histories, which are time-pruned and exist to be read across a gap.
        """
        self.window.clear()
        # Contact histories kept: cleared, one post-gap blip would read as full contact.
        self._ratio_history.clear()
        # Artifact medians kept: reset() runs on every no-sample tick, and cleared they never fill.
        self._held_ratios = None
        # After a gap no calm has been measured; the placeholder must say so.
        self._calm_ever = False
        self._calm_fresh_ts = None
        self._ema_focus = None
        self._ema_calm = None
        self._ema_ts = None
        # Baseline NOT cleared: it belongs to the session; only restart_baseline() replaces it.

    @staticmethod
    def _clamp01(value: float) -> float:
        return max(0.0, min(1.0, value))

    def _extract_band_log_ratios(self, bands: dict[str, Any] | None) -> tuple[float | None, float | None]:
        if not bands:
            return (None, None)
        # The exponentiation is inside the try: 10.0**x overflows past x~308 (not Bels).
        try:
            alpha = float(bands.get("alpha", 0.0))
            beta = float(bands.get("beta", 0.0))
            theta = float(bands.get("theta", 0.0))
            gamma = float(bands.get("gamma", 0.0))
            # NaN/inf pass float() and poison every mean downstream.
            if not all(isfinite(v) for v in (alpha, beta, theta, gamma)):
                return (None, None)

            # All-zero means no band features yet. Must stay all-zero: single bands are legitimately negative Bels.
            if alpha == 0.0 and beta == 0.0 and theta == 0.0 and gamma == 0.0:
                return (None, None)

            # Bels to linear power before adding: linear = 10 ** bels.
            alpha_p = 10.0**alpha
            beta_p = 10.0**beta
            theta_p = 10.0**theta
            gamma_p = 10.0**gamma

            focus_log_ratio = log(beta_p + self.EPSILON) - log(alpha_p + theta_p + self.EPSILON)
            calm_log_ratio = log(alpha_p + self.EPSILON) - log(beta_p + gamma_p + self.EPSILON)
        except (TypeError, ValueError, OverflowError):
            return (None, None)
        return (focus_log_ratio, calm_log_ratio)

    @staticmethod
    def _bands_malformed(bands: dict[str, Any] | None) -> bool:
        """Bands were supplied and at least one ratio band is unusable.

        Distinct from "no bands" (older bridge, amplitude fallback): a malformed tick must not take that path.
        """
        if not bands:
            return False
        ratio_keys = ("alpha", "beta", "theta", "gamma")
        present = [k for k in ratio_keys if k in bands]
        # Partial is malformed (a missing band would default to 0 Bels); none at all is an older bridge.
        if present and len(present) < len(ratio_keys):
            return True
        for key in present:
            try:
                if not isfinite(float(bands[key])):
                    return True
            except (TypeError, ValueError):
                return True
        # delta not consulted: it feeds only the blink gate, which checks isfinite itself.
        return False

    def _smoothed(self, history: deque, now: float, value: float) -> float:
        """Append a contact reading and average it over CONTACT_SMOOTHING_SECONDS of elapsed time."""
        history.append((now, value))
        cutoff = now - self.CONTACT_SMOOTHING_SECONDS
        while history and history[0][0] < cutoff:
            history.popleft()
        return fmean([v for _, v in history])

    def _contact_ratio(self, meta: dict[str, Any] | None, now: float) -> float | None:
        """Smoothed electrode contact in 0..1, or None when the bridge reports none.

        Shared by quality and confidence so they cannot disagree; the worse of HSI fit and IS_GOOD.
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
                # HSI 1 -> 1.0, 2 -> 0.5, 4 -> 0.0; 0 means "not reported".
                rated = [float(v) for v in hsi if float(v) > 0.0]
            except (TypeError, ValueError):
                rated = []
            if rated:
                instant_fit = sum(
                    1.0 if v <= 1.0 else (0.5 if v <= 2.0 else 0.0) for v in rated
                ) / len(rated)
                # Smoothed like IS_GOOD, or one HSI blip would drop straight to "poor".
                fit_score = self._smoothed(self._hsi_history, now, instant_fit)

        parts = [p for p in (fit_score, good_channels) if p is not None]
        return min(parts) if parts else None

    def _signal_quality(
        self,
        contact: float | None,
        confidence_ratio: float,
        calm_ratio: float,
    ) -> tuple[str, str]:
        """How well the electrodes are seated, not whether the wearer is calm.

        Returns (quality, basis): "contact" when HSI / IS_GOOD backed it, else "heuristic".
        The heuristic under-reports (alpha drops when focused); never treat its "poor" as bad electrodes.
        """
        if contact is None:
            # 0.65: with CONTACT_UNKNOWN, confidence tops out at 0.70.
            if confidence_ratio >= 0.65 and calm_ratio >= 0.55:
                return ("good", "heuristic")
            if confidence_ratio >= 0.45 and calm_ratio >= 0.3:
                return ("degraded", "heuristic")
            return ("poor", "heuristic")

        # "good" = at most one of four electrodes mediocre (0.875).
        if contact >= self.CONTACT_GOOD:
            return ("good", "contact")
        if contact >= self.CONTACT_DEGRADED:
            return ("degraded", "contact")
        return ("poor", "contact")

    def _collect_baseline(self, focus_raw: float, calm_raw: float, ts: datetime,
                          contact: float | None) -> None:
        """Accumulate the opening stretch of a session as that person's baseline.

        Only ticks on at least degraded contact count (good would never latch).
        Accepted limitation: a learner who starts engaged makes that their zero point.
        """
        if contact is not None and contact < self.CONTACT_DEGRADED:
            return
        if self._baseline_collecting:
            if self._baseline_started is None:
                self._baseline_started = ts
                self._baseline_coverage = 0.0
            else:
                self._baseline_coverage += self._covered(ts, self._baseline_last_ts)
            self._baseline_last_ts = ts
            self._baseline_focus.append(focus_raw)
            if self._baseline_coverage >= self.BASELINE_SECONDS:
                # Ramp from the current centre, so a restart is step-free too.
                self._centre_from["focus"] = self._centre("focus", ts)
                self._baseline_focus_mean = fmean(self._baseline_focus)
                self._baseline_ready = True
                self._baseline_latched = ts
                self._ramp_elapsed = 0.0
                self._ramp_last_ts = ts
                self._baseline_collecting = False
        # Calm on its own clock over ticks that had a value; keeps collecting after focus latches.
        if self._calm_collecting and calm_raw is not None:
            if self._calm_last_ts is not None:
                self._calm_coverage += self._covered(ts, self._calm_last_ts)
            self._calm_last_ts = ts
            self._baseline_calm.append(calm_raw)
            if self._calm_coverage >= self.BASELINE_SECONDS:
                self._centre_from["calm"] = self._centre("calm", ts)
                self._baseline_calm_mean = fmean(self._baseline_calm)
                self._calm_ready = True
                self._calm_latched = ts
                self._calm_ramp_elapsed = 0.0
                self._calm_ramp_last_ts = ts
                self._calm_collecting = False

    def _covered(self, ts: datetime, last: datetime) -> float:
        """Covered seconds between two admitted ticks, capped per tick.

        A stalled or backwards clock counts as one nominal tick, or the baseline never latches.
        """
        since_last = (ts - last).total_seconds()
        if since_last <= 0.0:
            since_last = self.NOMINAL_TICK_SECONDS
        return min(since_last, self.BASELINE_TICK_CAP_SECONDS)

    def _score_against_baseline(self, raw: float, which: str, ts: datetime | None = None) -> float:
        """Map a log-ratio to 0..1 on the population span, centred per `_centre`.

        Gain never changes across the latch; "at your resting level" reads 0.5.
        """
        lo, hi = self._bounds(which)
        span = hi - lo
        if span <= 0:
            return 0.5
        return self._clamp01(0.5 + (raw - self._centre(which, ts)) / span)

    def _bounds(self, which: str) -> tuple[float, float]:
        if which == "focus":
            return self.FOCUS_LOG_RATIO_MIN, self.FOCUS_LOG_RATIO_MAX
        if self.calm_source == "local":
            return self.CALM_ALPHA_RESIDUAL_MIN, self.CALM_ALPHA_RESIDUAL_MAX
        return self.CALM_LOG_RATIO_MIN, self.CALM_LOG_RATIO_MAX

    def _centre(self, which: str, ts: datetime | None) -> float:
        """The value that scores 0.5 for `which` at `ts`.

        The population midpoint until a latch, then a ramp from the centre in use to the session mean.
        """
        lo, hi = self._bounds(which)
        midpoint = (lo + hi) / 2.0
        if which == "focus":
            baseline, ready, latched, elapsed = (self._baseline_focus_mean, self._baseline_ready,
                                                self._baseline_latched, self._ramp_elapsed)
        else:
            baseline, ready, latched, elapsed = (self._baseline_calm_mean, self._calm_ready,
                                                self._calm_latched, self._calm_ramp_elapsed)
        if not ready or baseline is None:
            return midpoint
        start = self._centre_from.get(which)
        if start is None:
            start = midpoint
        if ts is None or latched is None:
            return baseline
        # Covered time from _advance_ramp, not latch age: a backwards clock neither freezes nor completes it.
        fraction = self._clamp01(elapsed / self.BASELINE_RAMP_SECONDS)
        return start + fraction * (baseline - start)

    def _advance_ramp(self, ts: datetime) -> None:
        """Advance the post-latch ramp by covered time since the last admitted tick (via `_covered`)."""
        if self._baseline_latched is not None:
            if self._ramp_last_ts is not None:
                self._ramp_elapsed += self._covered(ts, self._ramp_last_ts)
            self._ramp_last_ts = ts
        if self._calm_latched is not None:
            if self._calm_ramp_last_ts is not None:
                self._calm_ramp_elapsed += self._covered(ts, self._calm_ramp_last_ts)
            self._calm_ramp_last_ts = ts

    def _smooth_ratios(self, focus_raw: float, calm_raw: float, ts: datetime) -> tuple[float, float]:
        """Advance the smoothed log ratios to this admitted tick and return them; seeded by the first tick."""
        if self._ema_focus is None or self._ema_ts is None:
            self._ema_focus, self._ema_calm, self._ema_ts = focus_raw, calm_raw, ts
            return focus_raw, calm_raw
        dt = (ts - self._ema_ts).total_seconds()
        if dt <= 0.0:
            dt = self.NOMINAL_TICK_SECONDS
        weight = 1.0 - exp(-dt / self.RATIO_SMOOTHING_SECONDS)
        self._ema_focus += weight * (focus_raw - self._ema_focus)
        # Local calm can be absent for a tick; the smoothed value holds.
        if calm_raw is not None:
            self._ema_calm = (calm_raw if self._ema_calm is None
                              else self._ema_calm + weight * (calm_raw - self._ema_calm))
        self._ema_ts = ts
        return self._ema_focus, self._ema_calm

    def _artifact_reason(self, bands: dict[str, Any], frame_spread: float | None,
                         now: float) -> str | None:
        """Why this tick is an artifact, or None if it is not.

        Relative to running medians, not absolute; no verdict until each gate has ARTIFACT_MIN_HISTORY.
        """
        # Each gate waits for its own history, so a stream without delta still catches clench and spread.
        try:
            delta = float(bands.get("delta"))
        except (TypeError, ValueError):
            delta = None
        if delta is not None and not isfinite(delta):
            # NaN compares false against everything.
            delta = None
        try:
            beta = float(bands.get("beta", 0.0))
            gamma = float(bands.get("gamma", 0.0))
        except (TypeError, ValueError):
            return None
        # Bels are logs, so "N times the median" is log10(N) above it.
        deltas = self._artifact_values(self._delta_history, now)
        if (delta is not None and len(deltas) >= self.ARTIFACT_MIN_HISTORY
                and delta - median(deltas) > log10(self.DELTA_JUMP_FACTOR)):
            return "delta_jump"
        if gamma - beta > self.EMG_GAMMA_EXCESS:
            return "emg_gamma"
        spreads = self._artifact_values(self._spread_history, now)
        if (frame_spread is not None and len(spreads) >= self.ARTIFACT_MIN_HISTORY
                and frame_spread > self.SPREAD_JUMP_FACTOR * max(median(spreads), 1.0)):
            return "spread_jump"
        return None

    def _artifact_values(self, history: deque, now: float) -> list[float]:
        """Prune an artifact history to ARTIFACT_HISTORY_SECONDS and return its values."""
        cutoff = now - self.ARTIFACT_HISTORY_SECONDS
        while history and history[0][0] < cutoff:
            history.popleft()
        return [v for _, v in history]

    @staticmethod
    def _good_channel_values(sample: EegSample, meta: dict[str, Any] | None) -> list[float]:
        """The raw channel values the headband reports as usable.

        Mirrors the bridge's band-power mask (update_band_power): IS_GOOD AND HSI <= 2.
        Stricter than _sample_is_usable, per channel. Falls back to all four when
        contact data is absent or would exclude everything.
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

        IS_GOOD only: a bad HSI fit distrusts one electrode, not the whole frame.
        """
        is_good = (meta or {}).get("is_good")
        if not isinstance(is_good, list) or not is_good:
            return True  # no contact data -- can't judge, so don't discard
        try:
            return any(float(v) >= 1.0 for v in is_good)
        except (TypeError, ValueError):
            return True

    def update(self, sample: EegSample, bands: dict[str, Any] | None = None,
               spectrum: dict[str, Any] | None = None) -> dict[str, float]:
        # Filtering never empties the window: a run of bad frames holds the last good reading.
        usable = self._sample_is_usable(bands)
        now = self._clock()
        contact = self._contact_ratio(bands, now)
        band_focus_raw, band_calm_raw = self._extract_band_log_ratios(bands)
        sdk_calm_raw = band_calm_raw
        spectrum_ready = bool(spectrum and spectrum.get("ready"))
        alpha_residual = spectrum.get("alpha_residual_temporal") if spectrum_ready else None
        if self.calm_source == "local":
            # Until the buffer fills, calm is held, never taken from the SDK ratio (different scale).
            band_calm_raw = alpha_residual
            if spectrum_ready:
                self._calm_fresh_ts = sample.timestamp
        # A missing local calm does not send the tick down the amplitude fallback.
        using_band_features = band_focus_raw is not None and (
            band_calm_raw is not None or self.calm_source == "local")

        frame_values = self._good_channel_values(sample, bands)
        # No spread if any channel is non-finite: max()/min() with NaN return an arbitrary finite value.
        frame_spread = ((max(frame_values) - min(frame_values))
                        if len(frame_values) >= 2 and all(isfinite(v) for v in frame_values)
                        else None)
        # Unusable bands are a held tick with their own reason, not the amplitude fallback.
        malformed = self._bands_malformed(bands)
        using_band_features = using_band_features and not malformed
        artifact_reason = (self._artifact_reason(bands, frame_spread, now)
                           if using_band_features
                           else ("malformed_bands" if malformed else None))
        if artifact_reason is not None:
            self._samples_artifact += 1
        # Admitted to window, baseline and spectral histories.
        admit = usable and artifact_reason is None

        if usable:
            # The gate's reference is every usable tick, held ones included, or its median ratchets down.
            # Outside the band branch: spread comes from the raw channels.
            try:
                delta_value = float((bands or {}).get("delta"))
            except (TypeError, ValueError):
                delta_value = None
            if delta_value is not None and isfinite(delta_value):
                self._delta_history.append((now, delta_value))
            elif bands:
                # Counted, or a blink gate that never armed reads as a flawless recording.
                self._samples_no_delta += 1
            # Same guard as delta: one NaN channel would poison the median.
            if frame_spread is not None and isfinite(frame_spread):
                self._spread_history.append((now, frame_spread))
            elif len(frame_values) >= 2:
                # A non-finite channel. Single-electrode frames are a contact fact, not counted here.
                self._samples_no_spread += 1

        if self.window and not admit:
            if not usable:
                self._samples_rejected += 1
        else:
            self.window.append(frame_values)
        per_sample_spreads: list[float] = []
        per_sample_means: list[float] = []
        for values in self.window:
            per_sample_means.append(fmean(values))
            # One electrode's spread is 0, which would fabricate "maximally calm".
            if len(values) >= 2:
                per_sample_spreads.append(max(values) - min(values))
        # Mean of per-frame means, so frames of differing width weigh equally.
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
            # The baseline latches once, so it must never ingest held frames.
            if admit:
                self._collect_baseline(band_focus_raw, band_calm_raw, sample.timestamp, contact)
                self._ratio_history.append(band_focus_raw)
            # Spectral terms at full weight: amplitude is ADC offset and impedance, not brain activity.
            if admit:
                focus_smooth, calm_smooth = self._smooth_ratios(
                    band_focus_raw, band_calm_raw, sample.timestamp)
                self._advance_ramp(sample.timestamp)
                focus_ratio = self._score_against_baseline(focus_smooth, "focus", sample.timestamp)
                if calm_smooth is not None:
                    calm_ratio = self._score_against_baseline(calm_smooth, "calm", sample.timestamp)
                    self._calm_ever = True
                else:
                    # Local source, opening buffer fill: the midpoint.
                    calm_ratio = 0.5
                self._held_ratios = (focus_ratio, calm_ratio)
            elif self._held_ratios is not None:
                # Hold the last admitted scores: a blink is not a change in focus.
                focus_ratio, calm_ratio = self._held_ratios
            else:
                # Nothing admitted yet: score the raw tick so the session has a number.
                focus_ratio = self._score_against_baseline(band_focus_raw, "focus", sample.timestamp)
                calm_ratio = (self._score_against_baseline(band_calm_raw, "calm", sample.timestamp)
                              if band_calm_raw is not None else 0.5)
        elif malformed:
            # Hold, as for any artifact; with nothing held yet, the midpoint.
            focus_ratio, calm_ratio = self._held_ratios or (0.5, 0.5)
        else:
            focus_ratio = focus_amp_ratio
            # Neutral rather than 1.0: no spread data is absence of evidence.
            calm_ratio = 0.5 if calm_amp_ratio is None else calm_amp_ratio

        # Whether calm_ratio is a measurement: a midpoint placeholder looks like a real 0.5.
        calm_measured = (self._calm_ever
                         or (using_band_features and band_calm_raw is not None)
                         or (not using_band_features and not malformed))
        # Seconds a local calm has been carried; None on the SDK source.
        calm_held_seconds = None
        if self.calm_source == "local" and self._calm_fresh_ts is not None:
            calm_held_seconds = round(max(0.0, (sample.timestamp - self._calm_fresh_ts).total_seconds()), 2)

        warmup_factor = len(self.window) / self.window.maxlen
        if using_band_features:
            ratio_std = pstdev(self._ratio_history) if len(self._ratio_history) > 1 else 0.0
            stability_factor = self._clamp01(1.0 - (ratio_std / self.RATIO_STD_MAX))
        else:
            # Fallback path: raw level steadiness is the only stability.
            stability_std = pstdev(per_sample_means) if len(per_sample_means) > 1 else 0.0
            stability_factor = self._clamp01(1.0 - (stability_std / self.STABILITY_STD_MAX))
        if contact is None:
            contact_term = self.CONTACT_UNKNOWN
        elif contact < self.CONTACT_DEGRADED:
            contact_term = 0.0
        else:
            above = self._clamp01(
                (contact - self.CONTACT_DEGRADED) / (self.CONTACT_GOOD - self.CONTACT_DEGRADED))
            contact_term = (self.CONTACT_TERM_AT_DEGRADED
                            + (1.0 - self.CONTACT_TERM_AT_DEGRADED) * above)
        confidence_ratio = self._clamp01(
            (self.CONFIDENCE_WEIGHT_WARMUP * warmup_factor)
            + (self.CONFIDENCE_WEIGHT_CONTACT * contact_term)
            + (self.CONFIDENCE_WEIGHT_STABILITY * stability_factor)
            + (self.CONFIDENCE_WEIGHT_BANDS if using_band_features else 0.0)
        )
        confidence_ratio = max(0.2, confidence_ratio)
        if malformed:
            # The floor, under the 0.45 gate, whatever the contact says.
            confidence_ratio = 0.2
        focus_score = focus_ratio * 100.0
        calm_score = calm_ratio * 100.0
        confidence = confidence_ratio * 100.0
        signal_quality, quality_basis = self._signal_quality(contact, confidence_ratio, calm_ratio)
        return {
            "focus_score": round(focus_score, 3),
            "calm_score": round(calm_score, 3),
            "confidence": round(confidence, 3),
            "signal_quality": signal_quality,
            # Consumers acting on "poor" must check this; see _signal_quality.
            "quality_basis": quality_basis,
            # Electrodes the bridge averaged into the band values (4 = all).
            "samples_rejected": self._samples_rejected,
            "band_channels_used": (bands or {}).get("band_channels_used"),
            # None when the bridge reports no contact.
            "contact_ratio": None if contact is None else round(contact, 3),
            # Held, not rejected: a held tick carries the previous scores. None when admitted.
            "samples_artifact": self._samples_artifact,
            "artifact_reason": artifact_reason,
            # Climbing here means the blink detector never armed.
            "samples_no_delta": self._samples_no_delta,
            "samples_no_spread": self._samples_no_spread,
            # Raw, pre-baseline; None with no usable bands, distinct from a real 0.
            "focus_log_ratio": band_focus_raw,
            # Always the SDK ratio, whichever source calm is scored from.
            "calm_log_ratio": sdk_calm_raw,
            # Local-spectrum calm, carried on both sources for comparison.
            "calm_source": self.calm_source,
            "calm_alpha_residual": alpha_residual,
            "spectrum_ready": spectrum_ready,
            "spectrum_reason": (spectrum or {}).get("reason") if not spectrum_ready else None,
            # Carried for comparison, not scored.
            "spectrum_slope": (spectrum or {}).get("slope_temporal") if spectrum_ready else None,
            "calm_measured": calm_measured,
            "calm_held_seconds": calm_held_seconds,
            # Whether each score is centred on its own latch yet or on the population midpoint.
            # Local calm can stay uncentred all session.
            "focus_centred": self._baseline_ready,
            "calm_centred": self._calm_ready,
            # None with no bands: the last value would read as a steady measurement.
            "focus_log_ratio_smoothed": self._ema_focus if using_band_features else None,
            "calm_log_ratio_smoothed": self._ema_calm if using_band_features else None,
        }
