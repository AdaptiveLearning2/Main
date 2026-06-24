from dataclasses import dataclass
from datetime import datetime


@dataclass
class EegSample:
    timestamp: datetime
    channel_tp9: float
    channel_af7: float
    channel_af8: float
    channel_tp10: float


@dataclass
class LearnerState:
    label: str
    confidence: float
    focus_score: float
    calm_score: float
    reason: str
