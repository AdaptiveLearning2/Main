"""HTTP client for the EEGResearch FastAPI sidecar service (port 8001)."""
from __future__ import annotations

import os
import requests
from typing import Optional

EEG_API_URL     = os.getenv("EEG_API_URL", "http://127.0.0.1:8001")
EEG_API_TOKEN   = os.getenv("EEG_API_TOKEN", "learner-token-123")
EEG_ADMIN_TOKEN = os.getenv("EEG_ADMIN_TOKEN", "admin-token-123")

_LEARNER = {"Authorization": f"Bearer {EEG_API_TOKEN}"}
_ADMIN   = {"Authorization": f"Bearer {EEG_ADMIN_TOKEN}"}


def is_alive(timeout: float = 1.5) -> bool:
    try:
        r = requests.get(f"{EEG_API_URL}/healthz", timeout=timeout)
        return r.status_code == 200
    except Exception:
        return False


def start_session() -> dict:
    """Tells the EEG service to start its simulator/bridge stream."""
    r = requests.post(f"{EEG_API_URL}/api/v1/session/start", headers=_ADMIN, timeout=3)
    r.raise_for_status()
    return r.json()


def stop_session() -> dict:
    r = requests.post(f"{EEG_API_URL}/api/v1/session/stop", headers=_ADMIN, timeout=3)
    r.raise_for_status()
    return r.json()


def get_state(timeout: float = 2.0) -> Optional[dict]:
    """Returns the latest interpreted EEG snapshot, or None if idle / unavailable."""
    try:
        r = requests.get(f"{EEG_API_URL}/api/v1/state", headers=_LEARNER, timeout=timeout)
        if r.status_code != 200:
            return None
        body = r.json()
        if body.get("status") != "ok":
            return None
        return body.get("data")
    except Exception:
        return None


def get_muse_status() -> dict:
    try:
        r = requests.get(f"{EEG_API_URL}/api/v1/muse/status", headers=_LEARNER, timeout=2)
        if r.status_code != 200:
            return {"available": False}
        body = r.json().get("data", {}) or {}
        body["available"] = True
        return body
    except Exception:
        return {"available": False}


def map_eeg_to_cognitive(eeg: dict, session_id: str, user_id: str) -> dict:
    """Convert EEG service payload → cognitive_signals row.

    EEG service produces focus_score, calm_score, confidence (0..1 or 0..100).
    Our DB stores focus, engagement, stress (0..1).
    """
    f = eeg.get("features") or {}
    b = eeg.get("bands") or {}

    def norm(v):
        """Accept 0..1 or 0..100; clamp to 0..1."""
        if v is None: return None
        try:    v = float(v)
        except: return None
        if v > 1.5: v = v / 100.0
        return max(0.0, min(1.0, v))

    focus      = norm(f.get("focus_score"))
    calm       = norm(f.get("calm_score"))
    confidence = norm(f.get("confidence"))
    stress     = (1.0 - calm) if calm is not None else None

    return {
        "session_id": session_id,
        "user_id":    user_id,
        "ts":         eeg.get("timestamp"),
        "focus":      focus,
        "engagement": confidence,
        "stress":     stress,
        "alpha":      b.get("alpha"),
        "beta":       b.get("beta"),
        "theta":      b.get("theta"),
        "delta":      b.get("delta"),
        "gamma":      b.get("gamma"),
        "raw": {
            "channels":        eeg.get("channels"),
            "state":           eeg.get("state"),
            "question_policy": eeg.get("question_policy"),
            "signal_quality":  f.get("signal_quality"),
            "ingestion":       eeg.get("ingestion"),
        },
    }