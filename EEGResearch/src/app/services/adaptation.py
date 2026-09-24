from __future__ import annotations

import time
from typing import Callable

from src.app.models import LearnerState


# Stressed calm line per source. Must equal signal_fusion's table; a test on each side pins both.
STRESSED_CALM_MAX = {"sdk": 0.377, "local": 0.25}
# Seconds a local calm may be carried and still label. Must equal signal_mapping.CALM_HOLD_MAX_SECONDS.
CALM_HOLD_MAX_SECONDS = 10.0


class AdaptationEngine:
    """Turns features into a learner-state label. It does **not** choose
    difficulty; the website backend does (see CLAUDE.md, fusion).
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.last_label = "neutral"
        # -inf so the first real transition is never blocked by cooldown.
        self.last_change_ts = float("-inf")
        self.cooldown_seconds = 3.0
        # Consecutive readings a new label must hold before commit (1 s at 4 Hz).
        self.persist_ticks = 4
        # A pending run survives a signal-loss reset, but ages out after this.
        self.pending_max_age_seconds = 5.0
        self._pending_label: str | None = None
        self._pending_count = 0
        self._pending_ts: float | None = None
        # Injectable for replaying a capture at its own pace.
        self._clock = clock

    def restart(self) -> None:
        """Forget the label state: a fresh engine. Called when recording is
        armed, so no label formed during pairing carries into the lesson."""
        self.last_label = "neutral"
        self.last_change_ts = float("-inf")
        self._pending_label = None
        self._pending_count = 0
        self._pending_ts = None

    def end_session(self) -> None:
        """Fresh engine reporting no signal; unlike signal loss, the pending run is dropped."""
        self.restart()
        self.last_label = "no_signal"

    def reset_for_signal_loss(self) -> None:
        """After a gap, skip the cooldown but still require persist_ticks readings.

        The pending run is kept, or contact flapping every other tick never
        accumulates a label; it ages out via pending_max_age_seconds."""
        self.last_label = "no_signal"
        self.last_change_ts = float("-inf")

    def infer_state(self, features: dict[str, float]) -> LearnerState:
        focus = features["focus_score"]
        calm = features["calm_score"]
        confidence = features["confidence"]
        # Accept either 0..1 or 0..100 scales.
        focus_ratio = focus / 100.0 if focus > 1.0 else focus
        calm_ratio = calm / 100.0 if calm > 1.0 else calm
        confidence_ratio = confidence / 100.0 if confidence > 1.0 else confidence
        target = LearnerState("neutral", confidence, focus, calm, "Stable but moderate attention")
        # sdk and local calm live on different spans, so each has its own line.
        stressed_line = STRESSED_CALM_MAX.get(features.get("calm_source") or "sdk", STRESSED_CALM_MAX["sdk"])
        # A never-measured calm is a midpoint placeholder: neither stressed nor focused.
        calm_measured = features.get("calm_measured", True) is not False
        held = features.get("calm_held_seconds")
        calm_stale = isinstance(held, (int, float)) and held > CALM_HOLD_MAX_SECONDS
        if confidence_ratio < 0.45:
            target = LearnerState("insufficient_signal", confidence, focus, calm, "Low confidence")
        elif not calm_measured:
            target = LearnerState("neutral", confidence, focus, calm, "Calm not yet measured")
        elif calm_stale:
            target = LearnerState("neutral", confidence, focus, calm, "Calm carried too long")
        # Same numbers as signal_fusion.EEG_FOCUSED_FOCUS_MIN / EEG_STRESSED_CALM_MAX; see docs/signals.md.
        elif focus_ratio >= 0.624 and calm_ratio >= 0.5:
            target = LearnerState("focused", confidence, focus, calm, "Sustained focus")
        elif calm_ratio < stressed_line:
            target = LearnerState("stressed", confidence, focus, calm, "High variation detected")

        now = self._clock()
        if target.label == self.last_label:
            self._pending_label = None
            self._pending_count = 0
            self._pending_ts = None
            return target
        # A change of label: persistence first, then cooldown. insufficient_signal is
        # exempt from both -- losing the signal applies at once; regaining it must persist.
        # Cooldown is also skipped after a signal loss (no prior state worth holding).
        if target.label != "insufficient_signal":
            stale = (self._pending_ts is not None
                     and (now - self._pending_ts) > self.pending_max_age_seconds)
            if target.label == self._pending_label and not stale:
                self._pending_count += 1
            else:
                self._pending_label = target.label
                self._pending_count = 1
            self._pending_ts = now
            if self._pending_count < self.persist_ticks:
                return LearnerState(self.last_label, confidence, focus, calm,
                                    "Awaiting persistence: hold prior state")
            if (self.last_label not in ("no_signal", "insufficient_signal")
                    and (now - self.last_change_ts) < self.cooldown_seconds):
                return LearnerState(self.last_label, confidence, focus, calm, "Cooldown: hold prior state")
        self.last_label = target.label
        self.last_change_ts = now
        self._pending_label = None
        self._pending_count = 0
        self._pending_ts = None
        return target
