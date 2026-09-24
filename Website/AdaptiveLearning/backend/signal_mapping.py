"""Sidecar payloads to database rows, shared by both ingestion paths (pull and push).

One copy of the unit conversion so the two paths cannot store different scales.
Pure: no I/O. See docs/signals.md.
"""

from __future__ import annotations

import math

from typing import Any


def _ratio(value: Any) -> float | None:
    """Rescale a 0..100 score to 0..1, clamped.

    Always divides: the input scale is fixed, and guessing it would read 1.2% as full focus.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    # Clamping alone is not enough: `min(1.0, nan)` is 1.0, i.e. 100% focus.
    if not math.isfinite(v):
        return None
    return max(0.0, min(1.0, v / 100.0))


def _raw(payload: dict, **derived: Any) -> dict:
    """The `raw` blob: the caller's own `raw` merged with what we derived.

    Derived keys win on a collision, even when None: the client's value under
    that key is removed, and the null itself is not written.
    """
    merged = dict(payload.get("raw") or {})
    for key, value in derived.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return merged


# Bump when the sidecar's population bounds change; written into `raw.score_scale`.
SCORE_SCALE_VERSION = 2
# Local calm is on a different span, so it is its own scale; unknown sources take the SDK's.
SCORE_SCALE_BY_CALM_SOURCE = {"sdk": SCORE_SCALE_VERSION, "local": 3}
# Seconds a local calm may be held before it is stale and `stress` is nulled.
CALM_HOLD_MAX_SECONDS = 10.0

# Nulled together: same electrodes, same window.
_MEASUREMENT_COLUMNS = ("focus", "stress", "engagement",
                        "alpha", "beta", "theta", "delta", "gamma")


def eeg_quality(eeg: dict) -> str:
    """What may be stored from this payload: `ok`, `contact_poor`, `no_signal`."""
    f = eeg.get("features") or {}
    quality = f.get("signal_quality")
    if quality == "no_signal":
        return "no_signal"
    # Only a contact-based "poor" counts: the legacy heuristic says "poor" for any focused student.
    if quality == "poor" and f.get("quality_basis") == "contact":
        return "contact_poor"
    return "ok"


def map_eeg_to_cognitive(eeg: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar EEG payload (0..100 scores) to a `cognitive_signals` row (0..1 ratios).

    `None` when there is no signal: zeroed scores are not a reading of zero.
    On poor contact the row is kept with measurements nulled, since `class_live`
    derives staleness from the newest row's `ts`.
    """
    verdict = eeg_quality(eeg)
    if verdict == "no_signal":
        return None

    f = eeg.get("features") or {}
    b = eeg.get("bands") or {}

    focus = _ratio(f.get("focus_score"))
    calm = _ratio(f.get("calm_score"))
    confidence = _ratio(f.get("confidence"))
    # Client-supplied on push; only a string may be used as a dict key.
    calm_source = f.get("calm_source") if isinstance(f.get("calm_source"), str) else None
    # Gate the stress column: a present but mistyped value withholds it ("false" is not False);
    # absent means an older sidecar, i.e. measured.
    calm_measured = f.get("calm_measured")
    calm_measured_ok = calm_measured is None or isinstance(calm_measured, bool)
    held = f.get("calm_held_seconds")
    held_ok = held is None or (isinstance(held, (int, float)) and not isinstance(held, bool)
                               and math.isfinite(held) and held >= 0)

    row = {
        "session_id": session_id,
        "user_id": user_id,
        "ts": eeg.get("timestamp"),
        "focus": focus,
        # The focus index, beta/(alpha+theta) (Pope et al.).
        "engagement": focus,
        # Inverted calm, not a stress measurement: never average with heart_signals.stress_score.
        "stress": (1.0 - calm) if calm is not None else None,
        "alpha": b.get("alpha"),
        "beta": b.get("beta"),
        "theta": b.get("theta"),
        "delta": b.get("delta"),
        "gamma": b.get("gamma"),
        "raw": _raw(
            eeg,
            device_id=eeg.get("device_id"),
            channels=eeg.get("channels"),
            state=eeg.get("state"),
            signal_quality=f.get("signal_quality"),
            # Explains a nulled row: bad contact vs. the legacy "poor".
            quality_basis=f.get("quality_basis"),
            # Why this tick was held (a held score is the previous tick's).
            artifact_reason=f.get("artifact_reason"),
            # Picks the stressed line and the score scale.
            calm_source=calm_source,
            # Centred on the session baseline vs. the population midpoint; a bool or nothing.
            focus_centred=(f.get("focus_centred")
                           if isinstance(f.get("focus_centred"), bool) else None),
            calm_centred=(f.get("calm_centred")
                          if isinstance(f.get("calm_centred"), bool) else None),
            calm_measured=calm_measured if calm_measured_ok else None,
            calm_held_seconds=held if held_ok else None,
            # Which key was rejected, so a nulled stress doesn't read as an older sidecar.
            calm_invalid=([k for k, ok in (("calm_measured", calm_measured_ok),
                                           ("calm_held_seconds", held_ok)) if not ok]
                          or None),
            ingestion=eeg.get("ingestion"),
            # EEG signal quality, 0..1; no column carries it. signal_fusion.eeg_channel gates on it.
            confidence=confidence,
            # Population scale the scores were measured on; read by the rollup via score_scale_of(raw).
            score_scale=SCORE_SCALE_BY_CALM_SOURCE.get(calm_source or "sdk",
                                                       SCORE_SCALE_VERSION),
        ),
    }
    if (calm_measured is False or not calm_measured_ok or not held_ok
            or (held is not None and held > CALM_HOLD_MAX_SECONDS)):
        # A placeholder or stale calm is not a stress reading; focus stays, `raw` says why.
        row["stress"] = None
    if verdict == "contact_poor":
        for column in _MEASUREMENT_COLUMNS:
            row[column] = None
        # Confidence goes too, or it drags the averaged confidence under the fusion gate.
        row["raw"].pop("confidence", None)
    if f.get("artifact_reason") == "malformed_bands":
        # Held or midpoint score with verdict `ok`: null it like contact_poor.
        for column in _MEASUREMENT_COLUMNS:
            row[column] = None
        row["raw"].pop("confidence", None)
    return row


def map_heart_to_heart_signal(payload: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar camera/headband payload to a `heart_signals` row (bpm, ms, 0..100; unscaled).

    None when there is no heart block: an absent channel is not a row of nulls.
    """
    heart = payload.get("heart")
    if not heart:
        return None
    source = heart.get("source")
    if not source:
        # Consent is per sensor, so a sourceless row cannot be consent-checked.
        return None

    return {
        "session_id": session_id,
        "user_id": user_id,
        # The reading's own stamp, so a block repeated across ticks dedupes on
        # (session_id, source, ts). The camera sends none and uses the tick's.
        "ts": heart.get("ts") or payload.get("timestamp"),
        "source": source,
        "heart_rate_bpm": heart.get("bpm"),
        "rmssd_ms": heart.get("rmssd_ms"),
        "sqi": heart.get("sqi"),
        "stress_score": heart.get("stress_score"),
        "stress_category": heart.get("stress_category"),
        # Carried, not derived: the sidecar owns the quality gate.
        "trusted": heart.get("trusted"),
        "raw": _raw(payload,
                    device_id=payload.get("device_id"),
                    confidence=heart.get("confidence"),
                    rejected_by=heart.get("rejected_by"),
                    measured_fps=heart.get("measured_fps"),
                    window_coverage=heart.get("window_coverage"),
                    # Headband counterparts to measured_fps, named apart so a row says which sensor struggled.
                    sample_rate_hz=heart.get("sample_rate_hz"),
                    largest_gap_s=heart.get("largest_gap_s"),
                    channel_count=heart.get("channel_count"),
                    # Simulator mark (source stays `muse_optics` for consent); absent on hardware rows.
                    synthetic=(True if heart.get("synthetic") is True else None),
                    # RMSSD's own gate, separate from `rejected_by`.
                    beat_coverage=heart.get("beat_coverage"),
                    rmssd_rejected_by=heart.get("rmssd_rejected_by"),
                    ingestion=payload.get("ingestion")),
    }


def map_face_to_face_signal(payload: dict, session_id: str, user_id: str) -> dict | None:
    """Sidecar camera payload to a `face_signals` row; None when there is no face block.

    `emotion_confidence` stays qualified so it cannot be confused with an identity confidence.
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
        "gaze_x": face.get("gaze_x"),
        "gaze_y": face.get("gaze_y"),
        # Head pose in degrees (not eye gaze); signs per face_geometry.
        "head_yaw": face.get("head_yaw"),
        "head_pitch": face.get("head_pitch"),
        "head_roll": face.get("head_roll"),
        "raw": _raw(payload,
                    device_id=payload.get("device_id"),
                    rejected_by=face.get("rejected_by"),
                    # Separate from `rejected_by`: emotion and gaze fail independently.
                    gaze_rejected_by=face.get("gaze_rejected_by"),
                    pose_rejected_by=face.get("pose_rejected_by"),
                    degraded=face.get("degraded"),
                    ingestion=payload.get("ingestion")),
    }
