from __future__ import annotations

import time

from src.app.models import LearnerState


class AdaptationEngine:
    MIN_DIFFICULTY = 1
    MAX_DIFFICULTY = 5

    def __init__(self) -> None:
        self.current_difficulty = 2
        self.last_label = "neutral"
        # Ensure the first real state transition is never blocked by cooldown.
        self.last_change_ts = float("-inf")
        self.cooldown_seconds = 3.0

    def reset_for_signal_loss(self) -> None:
        """Mark the learner state as unknown after a data gap so the next real
        reading is applied immediately instead of being held by the cooldown
        meant for flapping between real readings."""
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

    def next_question_policy(self, state: LearnerState) -> dict[str, str | int]:
        if state.label == "insufficient_signal":
            action = "fallback_default"
        elif state.label == "focused":
            self.current_difficulty = min(self.MAX_DIFFICULTY, self.current_difficulty + 1)
            action = "increase_difficulty"
        elif state.label == "stressed":
            self.current_difficulty = max(self.MIN_DIFFICULTY, self.current_difficulty - 1)
            action = "decrease_difficulty"
        else:
            action = "reinforce_topic"
        return {"action": action, "difficulty": self.current_difficulty}
