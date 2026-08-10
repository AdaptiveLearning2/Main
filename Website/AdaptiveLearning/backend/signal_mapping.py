"""Sidecar payloads to database rows, for both ingestion paths.

Two paths now reach these tables and they need the same arithmetic:

- **pull** — `eeg_poller` runs inside this backend and polls the sidecar over
  HTTP. Works only because `start.ps1` puts backend and sidecar on one machine.
- **push** — the sidecar runs on the student's own device and POSTs to
  `/api/signals/*` with the student's bearer token. This is the deployment the
  camera forces: a hosted backend has no route to a laptop.

The mapping used to live in `eeg_client.py`, which is the *pull transport*. The
push path would have had to import an HTTP client it never calls to reach a pure
function, or keep a second copy — and a second copy of a unit conversion is how
one path ends up storing percentages while the other stores ratios, with nothing
to notice but a chart that looks wrong later.

Nothing here does I/O, so both callers can be tested without a sidecar.
"""

from __future__ import annotations

from typing import Any


def _ratio(value: Any) -> float | None:
    """Rescale a 0..100 score to 0..1, clamped.

    This used to sniff the scale with `if v > 1.5: v /= 100`, which inverted the
    bottom of the range: `SignalProcessor.update` always returns ratio * 100.0,
    so a genuine focus_score of 1.2 (meaning 1.2%) fell under the threshold,
    skipped the divide, and clamped to 1.0 -- stored as *100%* focus. That is
    exactly the disengaged region that should be driving difficulty down.

    The producer contract is fixed, so there is nothing to detect: divide
    unconditionally.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, v / 100.0))


def map_eeg_to_cognitive(eeg: dict, session_id: str, user_id: str) -> dict:
    """Sidecar EEG payload to a `cognitive_signals` row.

    The sidecar reports focus/calm/confidence on a fixed 0..100 scale; this
    table stores 0..1 ratios.
    """
    f = eeg.get("features") or {}
    b = eeg.get("bands") or {}

    focus = _ratio(f.get("focus_score"))
    calm = _ratio(f.get("calm_score"))
    confidence = _ratio(f.get("confidence"))

    return {
        "session_id": session_id,
        "user_id": user_id,
        "ts": eeg.get("timestamp"),
        "focus": focus,
        "engagement": confidence,
        # `stress` is `1.0 - calm`, and there is no `calm` column -- so this
        # column *is* the calm score, stored inverted. It is not a measurement
        # of stress and must never be averaged with `heart_signals.stress_score`,
        # which is one. See the CLAUDE.md rule of the same name.
        "stress": (1.0 - calm) if calm is not None else None,
        "alpha": b.get("alpha"),
        "beta": b.get("beta"),
        "theta": b.get("theta"),
        "delta": b.get("delta"),
        "gamma": b.get("gamma"),
        "raw": {
            "device_id": eeg.get("device_id"),
            "channels": eeg.get("channels"),
            "state": eeg.get("state"),
            "signal_quality": f.get("signal_quality"),
            # Stored alongside signal_quality because it is what distinguishes a
            # row whose measurements were nulled for bad electrode contact from
            # one where the legacy heuristic merely said "poor". Without it, a
            # null-measurement row cannot be explained after the fact.
            "quality_basis": f.get("quality_basis"),
            "ingestion": eeg.get("ingestion"),
        },
    }


def map_heart_to_heart_signal(payload: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar camera/headband payload to a `heart_signals` row.

    Returns None when the payload carries no heart block -- a camera running
    emotion-only, or a headband whose optical channel is off. None rather than a
    row of nulls: an absent channel is not a reading of nothing, and a row would
    be counted as a sample by every aggregate downstream.

    Unlike the EEG mapping there is no rescaling. `heart_rate_bpm`, `rmssd_ms`
    and `stress_score` are absolute units all the way through -- the sidecar
    derives them in bpm, ms and 0..100 respectively, and the table stores them
    unchanged.
    """
    heart = payload.get("heart")
    if not heart:
        return None
    source = heart.get("source")
    if not source:
        # The table constrains `source`, and consent is decided per sensor, so a
        # row that cannot say which sensor produced it cannot be consent-checked.
        # Dropping it is the only safe answer.
        return None

    return {
        "session_id": session_id,
        "user_id": user_id,
        "ts": payload.get("timestamp"),
        "source": source,
        "heart_rate_bpm": heart.get("bpm"),
        "rmssd_ms": heart.get("rmssd_ms"),
        "sqi": heart.get("sqi"),
        "stress_score": heart.get("stress_score"),
        "stress_category": heart.get("stress_category"),
        # Carried rather than derived here. The sidecar owns the quality gate
        # and this is its verdict; recomputing it from `confidence` would put a
        # second, drifting definition of "trusted" in the system.
        "trusted": heart.get("trusted"),
        "raw": {
            "device_id": payload.get("device_id"),
            "confidence": heart.get("confidence"),
            "rejected_by": heart.get("rejected_by"),
            "measured_fps": heart.get("measured_fps"),
            "window_coverage": heart.get("window_coverage"),
            "ingestion": payload.get("ingestion"),
        },
    }


def map_face_to_face_signal(payload: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar camera payload to a `face_signals` row.

    None when there is no face block, for the same reason as above.

    `emotion_confidence` and `identity_confidence` are both carried and are not
    interchangeable: one is how sure the classifier is of the expression, the
    other how sure it is whose face this is. The fusion rule reads only the
    first, and read the second by mistake once.
    """
    face = payload.get("face")
    if not face:
        return None

    return {
        "session_id": session_id,
        "user_id": user_id,
        "ts": payload.get("timestamp"),
        "emotion": face.get("emotion"),
        "emotion_confidence": face.get("emotion_confidence"),
        "emotion_trusted": face.get("trusted"),
        "attention": face.get("attention"),
        "identity_confidence": face.get("identity_confidence"),
        "raw": {
            "device_id": payload.get("device_id"),
            "rejected_by": face.get("rejected_by"),
            "degraded": face.get("degraded"),
            "ingestion": payload.get("ingestion"),
        },
    }
