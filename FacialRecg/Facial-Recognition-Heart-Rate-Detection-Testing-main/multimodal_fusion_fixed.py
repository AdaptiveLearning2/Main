import argparse
import json
import math
from datetime import datetime
from pathlib import Path


DEFAULT_MAX_TIME_GAP_SECONDS = 1.5

# Provisional behavioral stress-support mapping.
# These are engineering defaults, not validated psychological measurements.
EMOTION_STRESS_SUPPORT = {
    "happy": 0.10,
    "neutral": 0.25,
    "surprise": 0.45,
    "contempt": 0.55,
    "sad": 0.65,
    "disgust": 0.75,
    "angry": 0.80,
    "fear": 0.85,
}

EEG_FOCUS_WEIGHT = 0.85
EMOTION_FOCUS_WEIGHT = 0.15

EEG_STRESS_WEIGHT = 0.50
RPPG_STRESS_WEIGHT = 0.30
EMOTION_STRESS_WEIGHT = 0.20


def clamp01(value):
    return max(0.0, min(1.0, float(value)))


def safe_number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result):
        return None
    return result


def parse_timestamp(value):
    if not value:
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        return json.load(file)


def normalize_eeg_rows(payload):
    """Accept common EEG formats: list, {samples: []}, {cognitive: []}, or one row."""
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("samples"), list):
            rows = payload["samples"]
        elif isinstance(payload.get("cognitive"), list):
            rows = payload["cognitive"]
        elif isinstance(payload.get("snapshots"), list):
            rows = payload["snapshots"]
        else:
            rows = [payload]
    else:
        return []

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
        features = row.get("features") if isinstance(row.get("features"), dict) else {}
        state = raw.get("state") if isinstance(raw.get("state"), dict) else {}

        focus = safe_number(
            row.get("focus", features.get("focus", state.get("focus_score")))
        )
        stress = safe_number(row.get("stress", features.get("stress")))
        calm = safe_number(
            row.get("calm", features.get("calm", state.get("calm_score")))
        )
        confidence = safe_number(
            row.get(
                "confidence",
                features.get("confidence", row.get("engagement", state.get("confidence"))),
            )
        )

        # Convert 0..100 forms when needed.
        if focus is not None and focus > 1:
            focus /= 100.0
        if calm is not None and calm > 1:
            calm /= 100.0
        if confidence is not None and confidence > 1:
            confidence /= 100.0

        if stress is None and calm is not None:
            stress = 1.0 - calm
        elif stress is not None and stress > 1:
            stress /= 100.0

        quality = (
            row.get("signal_quality")
            or features.get("signalQuality")
            or raw.get("signal_quality")
            or "unknown"
        )
        quality = str(quality).lower()

        trusted = quality == "good"
        if quality == "degraded":
            trusted = True
        if quality in {"poor", "no_signal", "insufficient_signal"}:
            trusted = False

        normalized.append(
            {
                "timestamp": row.get("ts") or row.get("timestamp"),
                "focus": clamp01(focus) if focus is not None else None,
                "stress": clamp01(stress) if stress is not None else None,
                "confidence": clamp01(confidence) if confidence is not None else None,
                "quality": quality,
                "trusted": trusted,
                "raw": row,
            }
        )

    return normalized


def normalize_facial_rows(payload):
    """Read snapshots from the facial rPPG report."""
    if isinstance(payload, dict) and isinstance(payload.get("snapshots"), list):
        rows = payload["snapshots"]
    elif isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = [payload]
    else:
        return []

    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        raw_signal = row.get("rawSignal") if isinstance(row.get("rawSignal"), dict) else {}
        features = row.get("features") if isinstance(row.get("features"), dict) else {}
        quality = row.get("quality") if isinstance(row.get("quality"), dict) else {}
        emotion = features.get("emotion") if isinstance(features.get("emotion"), dict) else {}
        facial_stress = (
            features.get("facialStress")
            if isinstance(features.get("facialStress"), dict)
            else {}
        )

        rppg_score = safe_number(
            features.get("facialStressSupport", facial_stress.get("score"))
        )
        if rppg_score is not None and rppg_score > 1:
            rppg_score /= 100.0

        emotion_label = emotion.get("label")
        emotion_confidence = safe_number(emotion.get("confidence"))
        emotion_trusted = emotion.get("trusted") is True

        normalized.append(
            {
                "timestamp": row.get("timestamp") or row.get("ts"),
                "heartRateBpm": safe_number(raw_signal.get("heartRateBpm")),
                "rmssdMs": safe_number(raw_signal.get("rmssdMs")),
                "sqi": safe_number(raw_signal.get("sqi")),
                "rppgStress": clamp01(rppg_score) if rppg_score is not None else None,
                "rppgTrusted": quality.get("trusted") is True and rppg_score is not None,
                "emotion": emotion_label,
                "emotionConfidence": emotion_confidence,
                "emotionTrusted": emotion_trusted and emotion_label is not None,
                "raw": row,
            }
        )

    return normalized


def nearest_eeg_row(facial_timestamp, eeg_rows, max_gap_seconds):
    target = parse_timestamp(facial_timestamp)
    if target is None:
        return None

    best = None
    best_gap = None

    for row in eeg_rows:
        current = parse_timestamp(row.get("timestamp"))
        if current is None:
            continue
        gap = abs((current - target).total_seconds())
        if best_gap is None or gap < best_gap:
            best = row
            best_gap = gap

    if best is None or best_gap is None or best_gap > max_gap_seconds:
        return None

    result = dict(best)
    result["matchedGapSeconds"] = round(best_gap, 3)
    return result


def weighted_average(items):
    """items: iterable of (value, weight). Missing values are ignored and weights renormalized."""
    valid = [(clamp01(v), float(w)) for v, w in items if v is not None and w > 0]
    if not valid:
        return None
    denominator = sum(weight for _, weight in valid)
    if denominator <= 0:
        return None
    return sum(value * weight for value, weight in valid) / denominator


def emotion_stress_support(label, confidence):
    if not label:
        return None
    base = EMOTION_STRESS_SUPPORT.get(str(label).lower())
    if base is None:
        return None
    confidence = clamp01(confidence) if confidence is not None else 0.5
    # Low confidence moves the estimate toward neutral 0.5.
    return (base * confidence) + (0.5 * (1.0 - confidence))


def emotion_focus_support(label, confidence):
    if not label:
        return None

    label = str(label).lower()
    mapping = {
        "happy": 0.75,
        "neutral": 0.60,
        "surprise": 0.50,
        "contempt": 0.40,
        "sad": 0.30,
        "disgust": 0.25,
        "angry": 0.20,
        "fear": 0.20,
    }
    base = mapping.get(label)
    if base is None:
        return None

    confidence = clamp01(confidence) if confidence is not None else 0.5
    return (base * confidence) + (0.5 * (1.0 - confidence))


def classify_level(value):
    if value is None:
        return "unknown"
    if value < 0.34:
        return "low"
    if value < 0.67:
        return "moderate"
    return "high"


def decide_action(final_focus, final_stress, eeg, facial):
    """Simple conservative policy based on the team's proposed rule."""
    eeg_good = bool(eeg and eeg.get("trusted"))
    rppg_good = bool(facial.get("rppgTrusted"))

    eeg_focus = eeg.get("focus") if eeg else None
    eeg_stress = eeg.get("stress") if eeg else None
    rppg_stress = facial.get("rppgStress")

    warnings = []
    positives = []

    if eeg_good and eeg_stress is not None:
        if eeg_stress >= 0.67:
            warnings.append("EEG stress was high")
        elif eeg_focus is not None and eeg_focus >= 0.67 and eeg_stress < 0.50:
            positives.append("EEG showed good focus and manageable stress")

    if rppg_good and rppg_stress is not None:
        if rppg_stress >= 0.67:
            warnings.append("facial rPPG stress support was high")
        elif rppg_stress < 0.50:
            positives.append("facial rPPG stress support was not elevated")

    if warnings:
        return {
            "action": "decrease_or_hold",
            "reason": "; ".join(warnings),
        }

    # Only increase when both trusted physiological/cognitive signals agree.
    if eeg_good and rppg_good and len(positives) >= 2:
        return {
            "action": "increase_difficulty",
            "reason": "Trusted EEG and facial rPPG signals both supported increasing difficulty.",
        }

    if final_focus is not None and final_stress is not None:
        if final_focus < 0.34 or final_stress >= 0.67:
            return {
                "action": "decrease_or_hold",
                "reason": "The combined state showed low focus or high stress.",
            }

    return {
        "action": "hold_difficulty",
        "reason": "Signals were incomplete, mixed, or not strong enough to justify a change.",
    }


def fuse_row(facial, eeg):
    emotion_stress = (
        emotion_stress_support(
            facial.get("emotion"),
            facial.get("emotionConfidence"),
        )
        if facial.get("emotionTrusted")
        else None
    )
    emotion_focus = (
        emotion_focus_support(
            facial.get("emotion"),
            facial.get("emotionConfidence"),
        )
        if facial.get("emotionTrusted")
        else None
    )

    eeg_focus = eeg.get("focus") if eeg and eeg.get("trusted") else None
    eeg_stress = eeg.get("stress") if eeg and eeg.get("trusted") else None
    rppg_stress = facial.get("rppgStress") if facial.get("rppgTrusted") else None

    # Final focus requires trusted EEG. Emotion may support EEG focus,
    # but emotion alone must never create a cognitive focus score.
    final_focus = (
        weighted_average(
            [
                (eeg_focus, EEG_FOCUS_WEIGHT),
                (emotion_focus, EMOTION_FOCUS_WEIGHT),
            ]
        )
        if eeg_focus is not None
        else None
    )

    # Final stress requires at least one trusted physiological/cognitive source:
    # EEG stress or facial rPPG stress. Emotion is supporting context only.
    has_primary_stress_source = (
        eeg_stress is not None or rppg_stress is not None
    )
    final_stress = (
        weighted_average(
            [
                (eeg_stress, EEG_STRESS_WEIGHT),
                (rppg_stress, RPPG_STRESS_WEIGHT),
                (emotion_stress, EMOTION_STRESS_WEIGHT),
            ]
        )
        if has_primary_stress_source
        else None
    )

    decision = decide_action(final_focus, final_stress, eeg, facial)

    if final_focus is None and final_stress is None:
        decision = {
            "action": "hold_difficulty",
            "reason": (
                "EEG and trusted physiological stress data were unavailable; "
                "emotion alone was not used to create final scores."
            ),
        }

    sources_used = []
    if eeg_focus is not None or eeg_stress is not None:
        sources_used.append("eeg")
    if rppg_stress is not None:
        sources_used.append("facial_rppg")
    if emotion_stress is not None or emotion_focus is not None:
        sources_used.append("emotion")

    return {
        "timestamp": facial.get("timestamp"),
        "matchedEegGapSeconds": eeg.get("matchedGapSeconds") if eeg else None,
        "inputs": {
            "eeg": {
                "focus": eeg_focus,
                "stress": eeg_stress,
                "confidence": eeg.get("confidence") if eeg else None,
                "quality": eeg.get("quality") if eeg else "missing",
                "trusted": bool(eeg and eeg.get("trusted")),
            },
            "facialRppg": {
                "stress": rppg_stress,
                "sqi": facial.get("sqi"),
                "trusted": facial.get("rppgTrusted"),
            },
            "emotion": {
                "label": facial.get("emotion"),
                "confidence": facial.get("emotionConfidence"),
                "focusSupport": emotion_focus,
                "stressSupport": emotion_stress,
                "trusted": facial.get("emotionTrusted"),
            },
        },
        "final": {
            "focus": round(final_focus, 4) if final_focus is not None else None,
            "stress": round(final_stress, 4) if final_stress is not None else None,
            "focusLevel": classify_level(final_focus),
            "stressLevel": classify_level(final_stress),
            "sourcesUsed": sources_used,
            "state": (
                "insufficient_signal"
                if final_focus is None and final_stress is None
                else "multimodal_state_available"
            ),
        },
        "decision": decision,
    }


def build_summary(rows):
    focus_values = [
        row["final"]["focus"]
        for row in rows
        if row["final"]["focus"] is not None
    ]
    stress_values = [
        row["final"]["stress"]
        for row in rows
        if row["final"]["stress"] is not None
    ]

    actions = {}
    for row in rows:
        action = row["decision"]["action"]
        actions[action] = actions.get(action, 0) + 1

    return {
        "fusedSnapshotCount": len(rows),
        "averageFinalFocus": (
            round(sum(focus_values) / len(focus_values), 4)
            if focus_values
            else None
        ),
        "averageFinalStress": (
            round(sum(stress_values) / len(stress_values), 4)
            if stress_values
            else None
        ),
        "decisionDistribution": actions,
        "note": (
            "This is a provisional engineering fusion rule. It is not a "
            "clinical or validated psychological assessment."
        ),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Offline EEG + facial rPPG + emotion fusion prototype."
    )
    parser.add_argument("--eeg", required=True, help="EEG JSON file.")
    parser.add_argument("--facial", required=True, help="Facial report JSON file.")
    parser.add_argument("--out", default="fusion_output.json")
    parser.add_argument(
        "--max-gap",
        type=float,
        default=DEFAULT_MAX_TIME_GAP_SECONDS,
        help="Maximum timestamp difference allowed for EEG matching.",
    )
    args = parser.parse_args()

    eeg_payload = load_json(args.eeg)
    facial_payload = load_json(args.facial)

    eeg_rows = normalize_eeg_rows(eeg_payload)
    facial_rows = normalize_facial_rows(facial_payload)

    fused_rows = []
    for facial in facial_rows:
        eeg = nearest_eeg_row(
            facial_timestamp=facial.get("timestamp"),
            eeg_rows=eeg_rows,
            max_gap_seconds=args.max_gap,
        )
        fused_rows.append(fuse_row(facial, eeg))

    output = {
        "schemaVersion": "multimodal-fusion-v0.1",
        "weights": {
            "focus": {
                "eeg": EEG_FOCUS_WEIGHT,
                "emotion": EMOTION_FOCUS_WEIGHT,
            },
            "stress": {
                "eeg": EEG_STRESS_WEIGHT,
                "facialRppg": RPPG_STRESS_WEIGHT,
                "emotion": EMOTION_STRESS_WEIGHT,
            },
        },
        "matching": {
            "maximumTimestampGapSeconds": args.max_gap,
            "eegRowsLoaded": len(eeg_rows),
            "facialRowsLoaded": len(facial_rows),
        },
        "snapshots": fused_rows,
        "summary": build_summary(fused_rows),
    }

    Path(args.out).write_text(
        json.dumps(output, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(f"Fusion output written to: {args.out}")
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()