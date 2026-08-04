Research and testing materials for Remote photoplethysmography testing using Opencv and Openrppg.

Facial rPPG Heart Rate and Emotion Detection

This module adds webcam-based heart-rate estimation and emotion detection to the AdaptiveLearning project.

Features

Facial rPPG heart-rate estimation

Signal Quality Index (SQI)

RMSSD calculation

Per-session baseline calibration

Provisional facial stress-support score

FER+ ONNX emotion detection

Emotion confidence and trusted status

Local JSON export

Posting to POST /api/signals/face

Stress and emotion pie-chart reports

Main Files

rPPG_LF_export_stress_pie_backend_emotion.py — main heart-rate and emotion script

emotion-ferplus-8.onnx — FER+ emotion model

emotion_onnx_test.py — standalone emotion test

generate_session_report_fixed.py — HTML summary cards and pie charts

requirements.txt — Python dependencies

Setup

Use Python 3.11.

py -3.11 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Verify:

python -c "import cv2, rppg, onnxruntime, numpy; print('Setup working')"

Local Test

python .\rPPG_LF_export_stress_pie_backend_emotion.py `
  --launch window `
  --out facial_session.json `
  --local-only

Press q to stop and save the JSON.

For better readings, keep the face centered, use stable front lighting, avoid backlighting, and remain mostly still during baseline calibration.

Backend Integration

The FastAPI backend should be running at:

http://127.0.0.1:8000

The script posts samples to:

POST /api/signals/face

Set the logged-in student's access token:

$env:SUPABASE_ACCESS_TOKEN="STUDENT_ACCESS_TOKEN"

Run using the current quiz session ID:

python .\rPPG_LF_export_stress_pie_backend_emotion.py `
  --launch window `
  --session-id "SESSION_ID" `
  --backend-url "http://127.0.0.1:8000" `
  --out "facial_session_SESSION_ID.json"

Do not include --local-only when posting to the backend.

Session Behavior

Each new quiz should use a new session_id.

For every quiz:

Receive the current quiz session ID.

Start the script with that ID.

Reset baseline calibration and snapshots.

Save a new session-specific JSON file.

Post all readings using the same session ID.

Stop capture when the quiz ends.

Suggested filename:

facial_session_<session_id>.json

Backend Sample Shape

{
  "session_id": "SESSION_ID",
  "samples": [
    {
      "ts": "2026-07-30T02:21:13.433362Z",
      "emotion": "neutral",
      "attention": null,
      "gaze_x": null,
      "gaze_y": null,
      "identity_confidence": null,
      "raw": {
        "rawSignal": {
          "heartRateBpm": 70.83,
          "sqi": 0.5024,
          "rmssdMs": 77.02
        },
        "features": {
          "facialStressSupport": 43.48,
          "emotion": {
            "label": "neutral",
            "confidence": 0.9809,
            "trusted": true
          }
        },
        "quality": {
          "trusted": true,
          "trustLevel": "usable"
        },
        "baseline": {
          "status": "ready",
          "heartRateBpm": 73.71,
          "rmssdMs": 74.73
        }
      }
    }
  ]
}

Generate the Report

python .\generate_session_report_fixed.py `
  --fusion fusion_output_v2.json `
  --facial facial_session.json `
  --out session_report.html

Open it:

start .\session_report.html

The report includes summary cards, trusted rPPG percentage, a stress pie chart, and an emotion pie chart.

Current Status

Completed:

Heart-rate detection

SQI and RMSSD

Baseline calibration

Facial stress support

Emotion detection

JSON output

Backend payload support

Pie-chart report generation

Handled separately during website integration:

Frontend start/stop toggle

Automatic Python process launch and stop

Consent and data handling

Final website display

Security

Do not commit:

.env

Access tokens

Service-role keys

.venv/

__pycache__/

Generated JSON files

Generated HTML reports

Generated output (session JSON, the fusion output, the rendered report, and the
downloaded ONNX model) is ignored by the repository root .gitignore. Do not add a
second list here -- the one that used to live in this spot named files no script
writes any more and missed rppg_test_output.json, which is the one that grows.

Note

The facial stress-support score is an experimental research metric and is not a clinical or medically validated stress measurement.
