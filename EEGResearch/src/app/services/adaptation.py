from __future__ import annotations

import time
from typing import Callable

from src.app.models import LearnerState


class AdaptationEngine:
    """Turns features into a learner-state label. It does **not** choose
    difficulty -- that's decided by the website backend's `LLM_topic_decider`,
    from correctness, topic history, grade and manual bias, none of which the
    sidecar can see.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.last_label = "neutral"
        # -inf so the first real transition is never blocked by cooldown.
        self.last_change_ts = float("-inf")
        self.cooldown_seconds = 3.0
        # A new label has to hold for this many consecutive readings before
        # it is committed. Without it a single spurious tick became the
        # label, and the cooldown then held it for 3 s: on the reference
        # captures 90 of 133 "focused" readings were cooldown holds of one
        # tick (tests/fixtures/EEG_REFERENCE.md, finding 7). Four ticks is
        # 1 s at the 4 Hz stream rate.
        self.persist_ticks = 4
        self._pending_label: str | None = None
        self._pending_count = 0
        # Injectable for replaying a capture at its own pace; see
        # SignalProcessor.__init__.
        self._clock = clock

    def reset_for_signal_loss(self) -> None:
        """After a data gap, apply the next real reading immediately instead of
        holding it under the cooldown meant for flapping between readings."""
        self.last_label = "no_signal"
        self.last_change_ts = float("-inf")
        self._pending_label = None
        self._pending_count = 0

    def infer_state(self, features: dict[str, float]) -> LearnerState:
        focus = features["focus_score"]
        calm = features["calm_score"]
        confidence = features["confidence"]
        # Accept either 0..1 or 0..100 feature scales.
        focus_ratio = focus / 100.0 if focus > 1.0 else focus
        calm_ratio = calm / 100.0 if calm > 1.0 else calm
        # Accept either 0..1 or 0..100 confidence scales.
        confidence_ratio = confidence / 100.0 if confidence > 1.0 else confidence
        target = LearnerState("neutral", confidence, focus, calm, "Stable but moderate attention")
        if confidence_ratio < 0.45:
            target = LearnerState("insufficient_signal", confidence, focus, calm, "Low confidence")
        elif focus_ratio >= 0.7 and calm_ratio >= 0.5:
            target = LearnerState("focused", confidence, focus, calm, "Sustained focus")
        elif calm_ratio < 0.35:
            target = LearnerState("stressed", confidence, focus, calm, "High variation detected")

        now = self._clock()
        if target.label == self.last_label:
            self._pending_label = None
            self._pending_count = 0
            return target
        # A change: count how many consecutive readings have asked for it.
        # After a signal loss the first reading applies at once -- there is
        # no prior state worth holding -- which reset_for_signal_loss marks
        # with the "no_signal" label.
        if self.last_label != "no_signal":
            if target.label == self._pending_label:
                self._pending_count += 1
            else:
                self._pending_label = target.label
                self._pending_count = 1
            if self._pending_count < self.persist_ticks:
                return LearnerState(self.last_label, confidence, focus, calm,
                                    "Awaiting persistence: hold prior state")
            if (now - self.last_change_ts) < self.cooldown_seconds:
                return LearnerState(self.last_label, confidence, focus, calm, "Cooldown: hold prior state")
        self.last_label = target.label
        self.last_change_ts = now
        self._pending_label = None
        self._pending_count = 0
        return target
