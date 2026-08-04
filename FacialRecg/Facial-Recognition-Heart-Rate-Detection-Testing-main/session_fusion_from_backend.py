import argparse
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

import multimodal_fusion_v2 as fusion


DEFAULT_BACKEND_URL = "http://127.0.0.1:8000"
DEFAULT_TOKEN_ENV = "SUPABASE_ACCESS_TOKEN"


def fetch_session_signals(backend_url, session_id, access_token, timeout=15):
    endpoint = f"{backend_url.rstrip('/')}/api/signals/session/{session_id}"
    request = urllib.request.Request(
        endpoint,
        method="GET",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Backend returned HTTP {exc.code}: {details}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not connect to the backend: {exc.reason}") from exc


def extract_eeg_rows(payload):
    if not isinstance(payload, dict):
        return []

    for key in ("cognitive", "cognitive_signals", "eeg", "eeg_signals"):
        value = payload.get(key)
        if isinstance(value, list):
            return value

    signals = payload.get("signals")
    if isinstance(signals, dict):
        for key in ("cognitive", "eeg"):
            value = signals.get(key)
            if isinstance(value, list):
                return value

    return []


def extract_face_rows(payload):
    if not isinstance(payload, dict):
        return []

    rows = None
    for key in ("facial", "face", "face_signals", "facial_signals"):
        value = payload.get(key)
        if isinstance(value, list):
            rows = value
            break

    if rows is None:
        signals = payload.get("signals")
        if isinstance(signals, dict):
            for key in ("facial", "face"):
                value = signals.get(key)
                if isinstance(value, list):
                    rows = value
                    break

    if rows is None:
        return []

    snapshots = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        raw = row.get("raw")
        if isinstance(raw, dict):
            snapshot = dict(raw)
            snapshot.setdefault("timestamp", row.get("ts"))
            features = snapshot.setdefault("features", {})

            emotion_label = row.get("emotion")
            if emotion_label and not isinstance(features.get("emotion"), dict):
                features["emotion"] = {
                    "label": emotion_label,
                    "confidence": None,
                    "trusted": True,
                    "reason": "Emotion label was loaded from face_signals.",
                }

            snapshots.append(snapshot)
        else:
            snapshots.append(row)

    return snapshots


def build_output(endpoint_payload, max_gap):
    eeg_db_rows = extract_eeg_rows(endpoint_payload)
    facial_snapshots = extract_face_rows(endpoint_payload)

    eeg_rows = fusion.normalize_eeg_rows(eeg_db_rows)
    facial_rows = fusion.normalize_facial_rows({"snapshots": facial_snapshots})

    fused_rows = []
    for facial_row in facial_rows:
        eeg_row = fusion.nearest_eeg_row(
            facial_timestamp=facial_row.get("timestamp"),
            eeg_rows=eeg_rows,
            max_gap_seconds=max_gap,
        )
        fused_rows.append(fusion.fuse_row(facial_row, eeg_row))

    return {
        "schemaVersion": "multimodal-fusion-v0.2",
        "source": "backend-session-endpoint",
        "weights": {
            "focus": {
                "eeg": fusion.EEG_FOCUS_WEIGHT,
                "emotion": fusion.EMOTION_FOCUS_WEIGHT,
            },
            "stress": {
                "eeg": fusion.EEG_STRESS_WEIGHT,
                "facialRppg": fusion.RPPG_STRESS_WEIGHT,
                "emotion": fusion.EMOTION_STRESS_WEIGHT,
            },
        },
        "matching": {
            "maximumTimestampGapSeconds": max_gap,
            "eegRowsLoaded": len(eeg_rows),
            "facialRowsLoaded": len(facial_rows),
        },
        "snapshots": fused_rows,
        "summary": fusion.build_summary(fused_rows),
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Fetch EEG and facial signals for one shared session ID and "
            "produce a quality-aware multimodal fusion report."
        )
    )
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--backend-url", default=DEFAULT_BACKEND_URL)
    parser.add_argument(
        "--access-token-env",
        default=DEFAULT_TOKEN_ENV,
        help=(
            "Environment variable containing the logged-in user's "
            f"access token (default: {DEFAULT_TOKEN_ENV})."
        ),
    )
    parser.add_argument(
        "--max-gap",
        type=float,
        default=fusion.DEFAULT_MAX_TIME_GAP_SECONDS,
    )
    parser.add_argument("--out", default="session_fusion_output.json")
    parser.add_argument(
        "--raw-out",
        default="session_signals_raw.json",
        help="Save the original endpoint response for debugging.",
    )
    args = parser.parse_args()

    access_token = os.getenv(args.access_token_env)
    if not access_token:
        raise RuntimeError(
            f"Missing access token in environment variable {args.access_token_env}."
        )

    endpoint_payload = fetch_session_signals(
        backend_url=args.backend_url,
        session_id=args.session_id,
        access_token=access_token,
    )

    Path(args.raw_out).write_text(
        json.dumps(endpoint_payload, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    output = build_output(
        endpoint_payload=endpoint_payload,
        max_gap=args.max_gap,
    )
    Path(args.out).write_text(
        json.dumps(output, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    print(f"Raw session response written to: {args.raw_out}")
    print(f"Fusion output written to: {args.out}")
    print(json.dumps(output["summary"], indent=2))


if __name__ == "__main__":
    main()
