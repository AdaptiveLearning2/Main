import argparse
import json
import math
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
from statistics import median
from datetime import datetime, timezone

import cv2
import numpy as np
import onnxruntime as ort
import rppg

# config
COLOR_GREEN = (0, 255, 0)
COLOR_RED = (0, 0, 255)
COLOR_YELLOW = (0, 255, 255)
MIN_SIGNAL_SECONDS = 60
RMSSD_UPDATE_INTERVAL = 1  # seconds between RMSSD recalculations
SIGNAL_WINDOW_SECONDS = 60  # only use the last N seconds of signal

# export config
JSON_OUTPUT_PATH = "rppg_test_output.json"
SCHEMA_VERSION = "1.0"
EXPORT_SOURCE = "facial_rppg"

# Optional backend ingestion. The script still works and saves JSON locally
# when backend posting is disabled or temporarily unavailable.
DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_FACE_BATCH_SIZE = 5
ACCESS_TOKEN_ENV_VAR = "SUPABASE_ACCESS_TOKEN"

# Emotion-recognition configuration
EMOTION_MODEL_URL = (
    "https://huggingface.co/onnxmodelzoo/emotion-ferplus-8/"
    "resolve/main/emotion-ferplus-8.onnx"
)
DEFAULT_EMOTION_MODEL_PATH = "emotion-ferplus-8.onnx"
EMOTION_UPDATE_INTERVAL = 0.25
EMOTION_MIN_CONFIDENCE = 0.50
EMOTION_LABELS = [
    "neutral",
    "happy",
    "surprise",
    "sad",
    "angry",
    "disgust",
    "fear",
    "contempt",
]

# Provisional quality thresholds based on current webcam tests.
# Keep these configurable and validate them with more test sessions.
SQI_MIN_USABLE = 0.40
SQI_GOOD = 0.55
QUALITY_RULE_VERSION = "facial-quality-v0.1"
FACIAL_SCHEMA_VERSION = "facial-snapshot-v0.1"
BASELINE_VERSION = "facial-baseline-v0.1"
BASELINE_REQUIRED_TRUSTED_SNAPSHOTS = 20
STRESS_RULE_VERSION = "facial-stress-v0.1"
STRESS_LOW_MAX = 33
STRESS_MODERATE_MAX = 66
STRESS_HR_WEIGHT = 0.60
STRESS_RMSSD_WEIGHT = 0.40


# Suppress harmless rppg thread error 
def _suppress_rppg_thread_error(args):
    if args.exc_type is RuntimeError and "cannot join current thread" in str(args.exc_value):
        return
    threading.__excepthook__(args)


def lock_camera_settings(cap):
    """Lock auto-exposure and auto-white-balance to reduce signal noise."""
    cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)       # 1 = manual mode
    cap.set(cv2.CAP_PROP_AUTO_WB, 0)             # disable auto white balance
    cap.set(cv2.CAP_PROP_FPS, 30)                # match rppg model expectation
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)


def compute_rmssd(model, window_start):
    """Use the rppg library's built-in HR/HRV pipeline with SQI gating."""
    try:
        result = model.hr(start=window_start)
        if result is None:
            return None, None, None
        sqi = result.get('SQI')
        hr = result.get('hr')
        hrv = result.get('hrv', {})
        rmssd = hrv.get('rmssd') if hrv else None
        return rmssd, sqi, hr
    except Exception:
        return None, None, None



def download_emotion_model(model_path):
    """Download the FER+ ONNX model if it is not already present."""
    if os.path.exists(model_path):
        return
    print(f"Downloading emotion model to {model_path}...")
    urllib.request.urlretrieve(EMOTION_MODEL_URL, model_path)
    print("Emotion model downloaded.")


def create_emotion_session(model_path):
    """Create a CPU ONNX Runtime session for FER+."""
    download_emotion_model(model_path)
    session = ort.InferenceSession(
        model_path,
        providers=["CPUExecutionProvider"],
    )
    return session, session.get_inputs()[0].name


def emotion_softmax(values):
    values = np.asarray(values, dtype=np.float32)
    values = values - np.max(values)
    exp_values = np.exp(values)
    denominator = np.sum(exp_values)
    if denominator <= 0:
        return np.zeros_like(exp_values)
    return exp_values / denominator


def prepare_emotion_face(face_bgr):
    """Convert a BGR face crop to the FER+ N x 1 x 64 x 64 input."""
    gray = cv2.cvtColor(face_bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, (64, 64), interpolation=cv2.INTER_AREA)
    tensor = gray.astype(np.float32)
    return np.expand_dims(np.expand_dims(tensor, axis=0), axis=0)


def infer_emotion(session, input_name, face_bgr):
    """Return emotion label, confidence, and trust flag for one face crop."""
    if face_bgr is None or face_bgr.size == 0:
        return {
            "label": None,
            "confidence": None,
            "trusted": False,
            "reason": "No usable face crop was available.",
        }

    try:
        tensor = prepare_emotion_face(face_bgr)
        output = session.run(None, {input_name: tensor})[0]
        logits = np.asarray(output).reshape(-1)

        if logits.size != len(EMOTION_LABELS):
            return {
                "label": None,
                "confidence": None,
                "trusted": False,
                "reason": (
                    f"Emotion model returned {logits.size} outputs; "
                    f"expected {len(EMOTION_LABELS)}."
                ),
            }

        probabilities = emotion_softmax(logits)
        index = int(np.argmax(probabilities))
        confidence = float(probabilities[index])
        label = EMOTION_LABELS[index]
        trusted = confidence >= EMOTION_MIN_CONFIDENCE

        return {
            "label": label,
            "confidence": confidence,
            "trusted": trusted,
            "reason": (
                f"Emotion classified as {label} with "
                f"{confidence:.0%} confidence."
                if trusted
                else (
                    f"Emotion candidate {label} had only "
                    f"{confidence:.0%} confidence."
                )
            ),
        }
    except Exception as exc:
        return {
            "label": None,
            "confidence": None,
            "trusted": False,
            "reason": f"Emotion inference failed: {type(exc).__name__}: {exc}",
        }


# ── Export helpers (new — not present in rPPG_LF.py) ─────────────────────

def utc_now_iso():
    """Return the current UTC time as an ISO 8601 string with a 'Z' suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sanitize_number(value):
    """Convert a value to a plain Python int/float, or None if it is missing,
    NaN, Infinity, a NumPy scalar with a non-finite value, or otherwise not a
    safe finite number for JSON export.

    - None stays None.
    - NumPy scalar types (np.floating / np.integer) are converted to native
      Python float/int via float()/int() (this also correctly handles plain
      Python int/float, since those support the same calls).
    - NaN / Infinity (in either native or NumPy form) become None, since
      json.dumps(..., allow_nan=False) would otherwise raise on them.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    # Preserve int-ness for values that are already whole numbers of int type
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    return number


def classify_quality(heart_rate, sqi, rmssd):
    """Classify whether one facial/rPPG snapshot is trustworthy.

    These thresholds are provisional project defaults, not clinical cutoffs.
    A snapshot is trusted only when HR and RMSSD are available and SQI is at
    least SQI_MIN_USABLE.
    """
    heart_rate = sanitize_number(heart_rate)
    sqi = sanitize_number(sqi)
    rmssd = sanitize_number(rmssd)

    flags = []

    if sqi is None:
        flags.append("missing_sqi")
        return {
            "trusted": False,
            "trustLevel": "missing",
            "confidence": 0.0,
            "flags": flags,
            "ruleVersion": QUALITY_RULE_VERSION,
            "reason": "The reading is untrusted because SQI was unavailable.",
        }

    if heart_rate is None:
        flags.append("missing_heart_rate")
        return {
            "trusted": False,
            "trustLevel": "poor",
            "confidence": sqi,
            "flags": flags,
            "ruleVersion": QUALITY_RULE_VERSION,
            "reason": "The reading is untrusted because heart rate was unavailable.",
        }

    if sqi < SQI_MIN_USABLE:
        flags.append("poor_signal_quality")
        if rmssd is None:
            flags.append("rmssd_unavailable")
        return {
            "trusted": False,
            "trustLevel": "poor",
            "confidence": sqi,
            "flags": flags,
            "ruleVersion": QUALITY_RULE_VERSION,
            "reason": (
                f"The reading is untrusted because SQI {sqi:.2f} is below "
                f"the provisional usable threshold of {SQI_MIN_USABLE:.2f}."
            ),
        }

    if rmssd is None:
        flags.append("rmssd_unavailable")
        return {
            "trusted": False,
            "trustLevel": "limited",
            "confidence": sqi,
            "flags": flags,
            "ruleVersion": QUALITY_RULE_VERSION,
            "reason": (
                "Heart rate was available, but RMSSD was unavailable, so the "
                "snapshot is not trusted for HRV-based reporting."
            ),
        }

    if sqi < SQI_GOOD:
        return {
            "trusted": True,
            "trustLevel": "usable",
            "confidence": sqi,
            "flags": [],
            "ruleVersion": QUALITY_RULE_VERSION,
            "reason": (
                f"Heart rate and RMSSD were available. SQI {sqi:.2f} met the "
                "provisional usable threshold but remained below the good threshold."
            ),
        }

    return {
        "trusted": True,
        "trustLevel": "good",
        "confidence": sqi,
        "flags": [],
        "ruleVersion": QUALITY_RULE_VERSION,
        "reason": (
            f"Heart rate and RMSSD were available with good signal quality "
            f"(SQI {sqi:.2f})."
        ),
    }


def build_facial_state(quality):
    """Return an EEG-compatible state label without inventing stress/focus."""
    if not quality["trusted"]:
        return "insufficient_signal"
    return "facial_signal_available"


def build_eeg_compatible_snapshot(
    timestamp,
    elapsed,
    hr,
    sqi,
    rmssd,
    quality,
    emotion=None,
    session_id=None,
    question_attempt_id=None,
    question_text=None,
    subject=None,
):
    """Build one facial snapshot shaped similarly to the EEG service output."""
    return {
        "schemaVersion": FACIAL_SCHEMA_VERSION,
        "source": EXPORT_SOURCE,
        "timestamp": timestamp,
        "elapsedSeconds": sanitize_number(elapsed),
        "sessionId": session_id,
        "question": {
            "questionAttemptId": question_attempt_id,
            "text": question_text,
            "subject": subject,
        },
        "rawSignal": {
            "heartRateBpm": sanitize_number(hr),
            "sqi": sanitize_number(sqi),
            "rmssdMs": sanitize_number(rmssd),
        },
        "features": {
            "facialStressSupport": None,
            "facialFocusSupport": None,
            "confidence": quality["confidence"],
            "signalQuality": quality["trustLevel"],
            "emotion": emotion or {
                "label": None,
                "confidence": None,
                "trusted": False,
                "reason": "Emotion detection was unavailable.",
            },
        },
        "quality": quality,
        "state": build_facial_state(quality),
        "reason": quality["reason"],
        "baseline": {
            "status": "calibrating",
            "trustedSnapshotsUsed": 0,
            "requiredTrustedSnapshots": BASELINE_REQUIRED_TRUSTED_SNAPSHOTS,
            "heartRateBpm": None,
            "rmssdMs": None,
            "version": BASELINE_VERSION,
        },
    }


def create_baseline_state():
    return {
        "status": "calibrating",
        "requiredTrustedSnapshots": BASELINE_REQUIRED_TRUSTED_SNAPSHOTS,
        "trustedSnapshotsUsed": 0,
        "heartRateBpm": None,
        "rmssdMs": None,
        "completedAt": None,
        "version": BASELINE_VERSION,
    }


def update_baseline(report):
    baseline = report["session"]["baseline"]
    if baseline["status"] == "ready":
        return

    trusted_rows = [
        snapshot
        for snapshot in report["snapshots"]
        if snapshot.get("quality", {}).get("trusted") is True
        and isinstance(snapshot.get("rawSignal", {}).get("heartRateBpm"), (int, float))
        and isinstance(snapshot.get("rawSignal", {}).get("rmssdMs"), (int, float))
    ]

    baseline["trustedSnapshotsUsed"] = min(
        len(trusted_rows),
        BASELINE_REQUIRED_TRUSTED_SNAPSHOTS,
    )

    if len(trusted_rows) < BASELINE_REQUIRED_TRUSTED_SNAPSHOTS:
        baseline["status"] = "calibrating"
        return

    calibration_rows = trusted_rows[:BASELINE_REQUIRED_TRUSTED_SNAPSHOTS]
    baseline["heartRateBpm"] = sanitize_number(
        median(row["rawSignal"]["heartRateBpm"] for row in calibration_rows)
    )
    baseline["rmssdMs"] = sanitize_number(
        median(row["rawSignal"]["rmssdMs"] for row in calibration_rows)
    )
    baseline["status"] = "ready"
    baseline["completedAt"] = utc_now_iso()


def get_baseline_snapshot(report):
    baseline = report["session"]["baseline"]
    return {
        "status": baseline["status"],
        "trustedSnapshotsUsed": baseline["trustedSnapshotsUsed"],
        "requiredTrustedSnapshots": baseline["requiredTrustedSnapshots"],
        "heartRateBpm": baseline["heartRateBpm"],
        "rmssdMs": baseline["rmssdMs"],
        "version": baseline["version"],
    }


def clamp(value, minimum=0.0, maximum=100.0):
    return max(minimum, min(maximum, value))


def classify_stress_category(score):
    if score is None:
        return "unknown"
    if score <= STRESS_LOW_MAX:
        return "low"
    if score <= STRESS_MODERATE_MAX:
        return "moderate"
    return "high"


def calculate_facial_stress_support(report, hr, rmssd, quality):
    """Calculate an experimental facial stress-support score.

    This is a provisional engineering heuristic, not a clinical stress measure.
    It compares trusted current HR/RMSSD values with the frozen session baseline.
    """
    baseline = report["session"]["baseline"]

    if baseline["status"] != "ready":
        return {
            "score": None,
            "category": "calibrating",
            "trusted": False,
            "ruleVersion": STRESS_RULE_VERSION,
            "reason": "Facial stress support is unavailable while the session baseline is still calibrating.",
        }

    if not quality["trusted"]:
        return {
            "score": None,
            "category": "unknown",
            "trusted": False,
            "ruleVersion": STRESS_RULE_VERSION,
            "reason": "Facial stress support was not calculated because the current rPPG reading was untrusted.",
        }

    hr = sanitize_number(hr)
    rmssd = sanitize_number(rmssd)
    baseline_hr = sanitize_number(baseline["heartRateBpm"])
    baseline_rmssd = sanitize_number(baseline["rmssdMs"])

    if (
        hr is None
        or rmssd is None
        or baseline_hr is None
        or baseline_rmssd is None
        or baseline_hr <= 0
        or baseline_rmssd <= 0
    ):
        return {
            "score": None,
            "category": "unknown",
            "trusted": False,
            "ruleVersion": STRESS_RULE_VERSION,
            "reason": "Facial stress support was not calculated because required HR/RMSSD values were unavailable.",
        }

    hr_change_pct = ((hr - baseline_hr) / baseline_hr) * 100.0
    rmssd_drop_pct = ((baseline_rmssd - rmssd) / baseline_rmssd) * 100.0

    # Neutral starts at 50. HR increases and RMSSD decreases raise support.
    # HR decreases and RMSSD increases lower support.
    hr_component = clamp(50.0 + (hr_change_pct * 2.0))
    rmssd_component = clamp(50.0 + (rmssd_drop_pct * 1.5))

    score = round(
        (hr_component * STRESS_HR_WEIGHT)
        + (rmssd_component * STRESS_RMSSD_WEIGHT),
        2,
    )
    category = classify_stress_category(score)

    direction_parts = []
    if hr_change_pct > 2:
        direction_parts.append(f"heart rate was {abs(hr_change_pct):.1f}% above baseline")
    elif hr_change_pct < -2:
        direction_parts.append(f"heart rate was {abs(hr_change_pct):.1f}% below baseline")
    else:
        direction_parts.append("heart rate was close to baseline")

    if rmssd_drop_pct > 5:
        direction_parts.append(f"RMSSD was {abs(rmssd_drop_pct):.1f}% below baseline")
    elif rmssd_drop_pct < -5:
        direction_parts.append(f"RMSSD was {abs(rmssd_drop_pct):.1f}% above baseline")
    else:
        direction_parts.append("RMSSD was close to baseline")

    return {
        "score": score,
        "category": category,
        "trusted": True,
        "ruleVersion": STRESS_RULE_VERSION,
        "components": {
            "heartRateChangePercent": round(hr_change_pct, 2),
            "rmssdDropPercent": round(rmssd_drop_pct, 2),
            "heartRateComponent": round(hr_component, 2),
            "rmssdComponent": round(rmssd_component, 2),
        },
        "reason": (
            "Experimental facial stress support was classified as "
            f"{category} because " + " and ".join(direction_parts) + "."
        ),
    }


def update_stress_distribution(report):
    """Create pie-chart-ready stress category counts from trusted scored rows."""
    distribution = {
        "low": 0,
        "moderate": 0,
        "high": 0,
        "unknown": 0,
        "calibrating": 0,
    }

    for snapshot in report["snapshots"]:
        category = (
            snapshot.get("features", {})
            .get("facialStress", {})
            .get("category", "unknown")
        )
        if category not in distribution:
            category = "unknown"
        distribution[category] += 1

    scored_total = (
        distribution["low"]
        + distribution["moderate"]
        + distribution["high"]
    )

    report["summary"]["stressDistribution"] = distribution
    report["summary"]["stressPieChart"] = [
        {"category": "low", "count": distribution["low"]},
        {"category": "moderate", "count": distribution["moderate"]},
        {"category": "high", "count": distribution["high"]},
    ]
    report["summary"]["scoredStressSnapshotCount"] = scored_total



def update_emotion_distribution(report):
    """Create pie-chart-ready emotion counts from trusted emotion readings."""
    counts = {}

    for snapshot in report["snapshots"]:
        emotion = snapshot.get("features", {}).get("emotion", {})
        label = emotion.get("label")
        trusted = emotion.get("trusted") is True
        if trusted and label:
            counts[label] = counts.get(label, 0) + 1

    report["summary"]["emotionDistribution"] = counts
    report["summary"]["emotionPieChart"] = [
        {"emotion": label, "count": count}
        for label, count in sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]
    report["summary"]["trustedEmotionSnapshotCount"] = sum(counts.values())

def build_report(started_at, session_id=None, student_id=None):
    """Return a fresh report dict matching the required file structure."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": EXPORT_SOURCE,
        "session": {
            "sessionId": session_id,
            "studentId": student_id,
            "startedAt": started_at,
            "endedAt": None,
            "samplingIntervalSeconds": RMSSD_UPDATE_INTERVAL,
            "baseline": create_baseline_state(),
        },
        "snapshots": [],
        "summary": {
            "snapshotCount": 0,
            "averageHeartRateBpm": None,
            "averageSqi": None,
            "averageRmssdMs": None,
            "trustedSnapshotCount": 0,
            "untrustedSnapshotCount": 0,
            "trustedSnapshotPercent": None,
            "allSnapshotAverageHeartRateBpm": None,
            "allSnapshotAverageSqi": None,
            "allSnapshotAverageRmssdMs": None,
            "stressDistribution": {
                "low": 0,
                "moderate": 0,
                "high": 0,
                "unknown": 0,
                "calibrating": 0,
            },
            "stressPieChart": [],
            "scoredStressSnapshotCount": 0,
            "emotionDistribution": {},
            "emotionPieChart": [],
            "trustedEmotionSnapshotCount": 0,
        },
    }


def update_summary(report):
    """Recompute summary.* in place from the valid numeric values currently
    present in report['snapshots']. Non-numeric / None values are ignored
    when averaging, so a run with some missing readings still produces a
    usable summary from whatever valid data exists."""
    snapshots = report["snapshots"]
    report["summary"]["snapshotCount"] = len(snapshots)

    trusted_snapshots = [
        snapshot for snapshot in snapshots
        if snapshot.get("quality", {}).get("trusted") is True
    ]

    def _average_raw_signal(key, rows):
        values = [
            row.get("rawSignal", {}).get(key)
            for row in rows
            if isinstance(row.get("rawSignal", {}).get(key), (int, float))
        ]
        if not values:
            return None
        return sum(values) / len(values)

    # Report averages are based only on trusted snapshots so low-quality
    # readings do not influence teacher/parent summaries.
    report["summary"]["averageHeartRateBpm"] = sanitize_number(
        _average_raw_signal("heartRateBpm", trusted_snapshots)
    )
    report["summary"]["averageSqi"] = sanitize_number(
        _average_raw_signal("sqi", trusted_snapshots)
    )
    report["summary"]["averageRmssdMs"] = sanitize_number(
        _average_raw_signal("rmssdMs", trusted_snapshots)
    )

    trusted_count = len(trusted_snapshots)
    untrusted_count = len(snapshots) - trusted_count
    report["summary"]["trustedSnapshotCount"] = trusted_count
    report["summary"]["untrustedSnapshotCount"] = untrusted_count
    report["summary"]["trustedSnapshotPercent"] = (
        round((trusted_count / len(snapshots)) * 100, 2)
        if snapshots
        else None
    )

    # Keep all-snapshot averages for research/debug comparison only.
    report["summary"]["allSnapshotAverageHeartRateBpm"] = sanitize_number(
        _average_raw_signal("heartRateBpm", snapshots)
    )
    report["summary"]["allSnapshotAverageSqi"] = sanitize_number(
        _average_raw_signal("sqi", snapshots)
    )
    report["summary"]["allSnapshotAverageRmssdMs"] = sanitize_number(
        _average_raw_signal("rmssdMs", snapshots)
    )


def write_json_atomically(report, output_path):
    """Write report as JSON to output_path atomically: write to a temp file
    in the same directory, then os.replace() it into place, so a partially
    written file is never visible at output_path."""
    directory = os.path.dirname(os.path.abspath(output_path)) or "."
    fd, temp_path = tempfile.mkstemp(prefix=".rppg_export_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(json.dumps(report, indent=2, allow_nan=False))
        os.replace(temp_path, output_path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise


def snapshot_to_face_sample(snapshot):
    """Convert one local rPPG snapshot to the existing FaceSample API shape.

    Emotion, attention, gaze, and identity confidence remain null until those
    features are genuinely measured. The complete rPPG snapshot is retained in
    raw so no HR, RMSSD, quality, baseline, or stress-support information is lost.
    """
    emotion = snapshot.get("features", {}).get("emotion", {})
    emotion_label = emotion.get("label") if emotion.get("trusted") else None

    return {
        "ts": snapshot.get("timestamp"),
        "emotion": emotion_label,
        "attention": None,
        "gaze_x": None,
        "gaze_y": None,
        "identity_confidence": None,
        "raw": snapshot,
    }


def post_face_samples(backend_url, access_token, session_id, samples, timeout=10):
    """POST a batch to the existing /api/signals/face endpoint.

    Returns (success, message). Failed batches stay queued so the caller may
    retry on the next interval or at shutdown.
    """
    if not samples:
        return True, "Nothing to send."

    endpoint = f"{backend_url.rstrip('/')}/api/signals/face"
    payload = {
        "session_id": session_id,
        "samples": samples,
    }
    body = json.dumps(payload, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response_body = response.read().decode("utf-8", errors="replace")
            return True, response_body or f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        return False, f"HTTP {exc.code}: {details}"
    except urllib.error.URLError as exc:
        return False, f"Connection error: {exc.reason}"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def flush_face_queue(
    queue,
    backend_url,
    access_token,
    session_id,
    force=False,
    batch_size=DEFAULT_FACE_BATCH_SIZE,
):
    """Send queued samples in batches.

    Samples are removed only after a successful POST. On failure, they remain
    queued and local JSON export continues unaffected.
    """
    if not backend_url or not access_token or not session_id:
        return

    while len(queue) >= batch_size or (force and queue):
        take = min(batch_size, len(queue))
        batch = queue[:take]
        success, message = post_face_samples(
            backend_url=backend_url,
            access_token=access_token,
            session_id=session_id,
            samples=batch,
        )
        if success:
            del queue[:take]
            print(f"Backend: posted {take} facial sample(s).")
        else:
            print(f"Backend warning: could not post facial samples: {message}")
            break


# ── End export helpers ────────────────────────────────────────────────────


def parse_args():
    parser = argparse.ArgumentParser(description="Real-time rPPG RMSSD monitor")
    parser.add_argument(
        "--launch",
        choices=("window", "console"),
        default="window",
    )
    parser.add_argument(
        "--out",
        default=JSON_OUTPUT_PATH,
        help=f"Path to the JSON export file (default: {JSON_OUTPUT_PATH})",
    )
    parser.add_argument("--session-id", default=None)
    parser.add_argument("--student-id", default=None)
    parser.add_argument("--question-attempt-id", default=None)
    parser.add_argument("--question-text", default=None)
    parser.add_argument("--subject", default=None)
    parser.add_argument(
        "--emotion-model",
        default=DEFAULT_EMOTION_MODEL_PATH,
        help=f"FER+ ONNX model path (default: {DEFAULT_EMOTION_MODEL_PATH})",
    )
    parser.add_argument(
        "--disable-emotion",
        action="store_true",
        help="Run rPPG without emotion inference.",
    )
    parser.add_argument(
        "--backend-url",
        default=DEFAULT_BACKEND_URL,
        help=f"Website backend base URL (default: {DEFAULT_BACKEND_URL})",
    )
    parser.add_argument(
        "--access-token-env",
        default=ACCESS_TOKEN_ENV_VAR,
        help=(
            "Environment-variable name containing the logged-in student's "
            f"Supabase access token (default: {ACCESS_TOKEN_ENV_VAR})"
        ),
    )
    parser.add_argument(
        "--face-batch-size",
        type=int,
        default=DEFAULT_FACE_BATCH_SIZE,
        help=f"Facial samples per backend POST (default: {DEFAULT_FACE_BATCH_SIZE})",
    )
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Save JSON locally without posting to the backend.",
    )
    return parser.parse_args()


def main(
    launch="window",
    out_path=JSON_OUTPUT_PATH,
    session_id=None,
    student_id=None,
    question_attempt_id=None,
    question_text=None,
    subject=None,
    emotion_model=DEFAULT_EMOTION_MODEL_PATH,
    disable_emotion=False,
    backend_url=DEFAULT_BACKEND_URL,
    access_token_env=ACCESS_TOKEN_ENV_VAR,
    face_batch_size=DEFAULT_FACE_BATCH_SIZE,
    local_only=False,
):
    threading.excepthook = _suppress_rppg_thread_error
    model = rppg.Model('RhythmMamba.pure')
    model.face_detect_per_n = 1
    current_rmssd = current_sqi = current_hr = None
    last_update = 0
    last_console_status = 0

    emotion_session = None
    emotion_input_name = None
    current_emotion = {
        "label": None,
        "confidence": None,
        "trusted": False,
        "reason": "Emotion detection has not run yet.",
    }
    last_emotion_update = 0.0

    if disable_emotion:
        print("Emotion detection disabled (--disable-emotion).")
    else:
        emotion_session, emotion_input_name = create_emotion_session(emotion_model)
        print("Emotion detection enabled.")

    # ── Export setup (new) ──
    report = build_report(
        started_at=utc_now_iso(),
        session_id=session_id,
        student_id=student_id,
    )
    write_json_atomically(report, out_path)

    # ── Optional backend setup ──
    face_post_queue = []
    access_token = None if local_only else os.getenv(access_token_env)
    backend_enabled = bool(
        not local_only and backend_url and session_id and access_token
    )

    if local_only:
        print("Backend posting disabled (--local-only).")
    elif not session_id:
        print("Backend posting disabled: --session-id was not provided.")
    elif not access_token:
        print(
            f"Backend posting disabled: environment variable "
            f"{access_token_env} is missing."
        )
    else:
        print(
            f"Backend posting enabled: {backend_url.rstrip('/')}"
            f"/api/signals/face"
        )
    # ── end optional backend setup ──
    # ── end export setup ──

    with model.video_capture(0):
        # Lock camera settings to reduce auto-adjustment noise
        if hasattr(model, '_cap') and model._cap is not None:
            lock_camera_settings(model._cap)

        start_time = time.time()
        if launch == "window":
            print("Starting real-time rPPG. Press 'q' to quit.")
        else:
            print("Starting real-time rPPG in console mode. Press Ctrl+C to quit.")

        try:
            for frame, box in model.preview:
                elapsed = time.time() - start_time
                frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

                if (
                    emotion_session is not None
                    and box is not None
                    and (time.time() - last_emotion_update) >= EMOTION_UPDATE_INTERVAL
                ):
                    y1, y2 = box[0]
                    x1, x2 = box[1]

                    margin_x = int((x2 - x1) * 0.08)
                    margin_y = int((y2 - y1) * 0.08)
                    crop_x1 = max(0, x1 - margin_x)
                    crop_y1 = max(0, y1 - margin_y)
                    crop_x2 = min(frame.shape[1], x2 + margin_x)
                    crop_y2 = min(frame.shape[0], y2 + margin_y)

                    face_crop = frame[crop_y1:crop_y2, crop_x1:crop_x2]
                    current_emotion = infer_emotion(
                        emotion_session,
                        emotion_input_name,
                        face_crop,
                    )
                    last_emotion_update = time.time()

                if box is not None and launch == "window":
                    y1, y2 = box[0]
                    x1, x2 = box[1]
                    cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_GREEN, 2)
                    emotion_label = current_emotion.get("label") or "unknown"
                    emotion_confidence = current_emotion.get("confidence")
                    emotion_text = (
                        f"{emotion_label}: {emotion_confidence:.0%}"
                        if isinstance(emotion_confidence, (int, float))
                        else emotion_label
                    )
                    cv2.putText(
                        frame,
                        emotion_text,
                        (x1, max(25, y1 - 10)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.65,
                        COLOR_GREEN,
                        2,
                        cv2.LINE_AA,
                    )

                # Periodically recompute RMSSD once enough signal exists
                if elapsed > MIN_SIGNAL_SECONDS and (time.time() - last_update) >= RMSSD_UPDATE_INTERVAL:
                    window_start = max(0, elapsed - SIGNAL_WINDOW_SECONDS)
                    rmssd, sqi, hr = compute_rmssd(model, window_start)
                    if sqi is not None:
                        current_sqi = sqi
                    if hr is not None:
                        current_hr = hr
                    if rmssd is not None:
                        current_rmssd = rmssd
                    last_update = time.time()

                    # ── Export snapshot (new) ──
                    # Same update block that refreshes HR/SQI/RMSSD, so the
                    # exported snapshot cadence matches the existing ~1s
                    # recompute cadence exactly (see RMSSD_UPDATE_INTERVAL).
                    quality = classify_quality(hr, sqi, rmssd)
                    snapshot = build_eeg_compatible_snapshot(
                        timestamp=utc_now_iso(),
                        elapsed=elapsed,
                        hr=hr,
                        sqi=sqi,
                        rmssd=rmssd,
                        quality=quality,
                        emotion=dict(current_emotion),
                        session_id=session_id,
                        question_attempt_id=question_attempt_id,
                        question_text=question_text,
                        subject=subject,
                    )
                    report["snapshots"].append(snapshot)
                    update_baseline(report)
                    snapshot["baseline"] = get_baseline_snapshot(report)

                    stress_result = calculate_facial_stress_support(
                        report=report,
                        hr=hr,
                        rmssd=rmssd,
                        quality=quality,
                    )
                    snapshot["features"]["facialStressSupport"] = stress_result["score"]
                    snapshot["features"]["facialStress"] = stress_result
                    snapshot["state"] = (
                        f"facial_stress_{stress_result['category']}"
                        if stress_result["score"] is not None
                        else snapshot["state"]
                    )

                    update_summary(report)
                    update_stress_distribution(report)
                    update_emotion_distribution(report)
                    write_json_atomically(report, out_path)

                    if backend_enabled:
                        face_post_queue.append(snapshot_to_face_sample(snapshot))
                        flush_face_queue(
                            queue=face_post_queue,
                            backend_url=backend_url,
                            access_token=access_token,
                            session_id=session_id,
                            batch_size=max(1, face_batch_size),
                        )
                    # ── end export snapshot ──

                if (time.time() - last_console_status) >= RMSSD_UPDATE_INTERVAL:
                    sqi_text = f"{current_sqi:.2f}" if current_sqi is not None else "n/a"
                    hr_text = f"{current_hr:.0f}" if current_hr is not None else "n/a"
                    if current_rmssd is not None:
                        print(f"RMSSD: {current_rmssd:.1f} ms | SQI: {sqi_text} | HR: {hr_text} bpm")
                    else:
                        secs_left = max(0, int(MIN_SIGNAL_SECONDS - elapsed))
                        if secs_left > 0:
                            print(f"Collecting signal... {secs_left}s")
                        else:
                            print(f"RMSSD: N/A | SQI: {sqi_text} (Bad SQI) | HR: {hr_text} bpm")
                    last_console_status = time.time()

                if launch == "window":
                    cv2.imshow("rPPG Real-Time RMSSD", frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            # ── Export finalization (new) ──
            # Runs on normal loop exit, 'q' break, and KeyboardInterrupt,
            # while still inside the `with model.video_capture(0):` block,
            # so the webcam release below happens exactly as it did before.
            report["session"]["endedAt"] = utc_now_iso()
            update_baseline(report)
            update_summary(report)
            update_stress_distribution(report)
            update_emotion_distribution(report)
            write_json_atomically(report, out_path)

            if backend_enabled:
                flush_face_queue(
                    queue=face_post_queue,
                    backend_url=backend_url,
                    access_token=access_token,
                    session_id=session_id,
                    force=True,
                    batch_size=max(1, face_batch_size),
                )
                if face_post_queue:
                    print(
                        f"Backend warning: {len(face_post_queue)} sample(s) "
                        "remain unsent; they are still preserved in the local JSON."
                    )
            # ── end export finalization ──

    if launch == "window":
        cv2.destroyAllWindows()

    if current_rmssd is not None:
        print(f"\nFinal RMSSD: {current_rmssd:.1f} ms")
    else:
        print("\nNot enough signal captured to compute RMSSD.")

    print(f"JSON export written to: {out_path}")


if __name__ == "__main__":
    args = parse_args()
    main(
        launch=args.launch,
        out_path=args.out,
        session_id=args.session_id,
        student_id=args.student_id,
        question_attempt_id=args.question_attempt_id,
        question_text=args.question_text,
        subject=args.subject,
        emotion_model=args.emotion_model,
        disable_emotion=args.disable_emotion,
        backend_url=args.backend_url,
        access_token_env=args.access_token_env,
        face_batch_size=args.face_batch_size,
        local_only=args.local_only,
    )
