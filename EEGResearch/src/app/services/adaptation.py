from __future__ import annotations

import time

from src.app.models import LearnerState


class AdaptationEngine:
    """Turns features into a learner-state label. It does **not** choose
    difficulty -- that's decided by the website backend's `LLM_topic_decider`,
    from correctness, topic history, grade and manual bias, none of which the
    sidecar can see.
    """

    def __init__(self) -> None:
        self.last_label = "neutral"
        # -inf so the first real transition is never blocked by cooldown.
        self.last_change_ts = float("-inf")
        self.cooldown_seconds = 3.0

    def reset_for_signal_loss(self) -> None:
        """After a data gap, apply the next real reading immediately instead of
        holding it under the cooldown meant for flapping between readings."""
        self.last_label = "no_signal"
        self.last_change_ts = float("-inf")

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

        now = time.monotonic()
        if target.label != self.last_label and (now - self.last_change_ts) < self.cooldown_seconds:
            return LearnerState(self.last_label, confidence, focus, calm, "Cooldown: hold prior state")
        if target.label != self.last_label:
            self.last_label = target.label
            self.last_change_ts = now
        return target
