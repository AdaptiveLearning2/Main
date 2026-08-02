import argparse
import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone

import cv2
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


def build_report(started_at):
    """Return a fresh report dict matching the required file structure."""
    return {
        "schemaVersion": SCHEMA_VERSION,
        "source": EXPORT_SOURCE,
        "session": {
            "startedAt": started_at,
            "endedAt": None,
            "samplingIntervalSeconds": RMSSD_UPDATE_INTERVAL,
        },
        "snapshots": [],
        "summary": {
            "snapshotCount": 0,
            "averageHeartRateBpm": None,
            "averageSqi": None,
            "averageRmssdMs": None,
        },
    }


def update_summary(report):
    """Recompute summary.* in place from the valid numeric values currently
    present in report['snapshots']. Non-numeric / None values are ignored
    when averaging, so a run with some missing readings still produces a
    usable summary from whatever valid data exists."""
    snapshots = report["snapshots"]
    report["summary"]["snapshotCount"] = len(snapshots)

    def _average(key):
        values = [s[key] for s in snapshots if isinstance(s.get(key), (int, float))]
        if not values:
            return None
        return sum(values) / len(values)

    report["summary"]["averageHeartRateBpm"] = sanitize_number(_average("heartRateBpm"))
    report["summary"]["averageSqi"] = sanitize_number(_average("sqi"))
    report["summary"]["averageRmssdMs"] = sanitize_number(_average("rmssdMs"))


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
    return parser.parse_args()


def main(launch="window", out_path=JSON_OUTPUT_PATH):
    threading.excepthook = _suppress_rppg_thread_error
    model = rppg.Model('RhythmMamba.rlap')
    model.face_detect_per_n = 1
    current_rmssd = current_sqi = current_hr = None
    last_update = 0
    last_console_status = 0

    # ── Export setup (new) ──
    report = build_report(started_at=utc_now_iso())
    write_json_atomically(report, out_path)
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

                if box is not None and launch == "window":
                    y1, y2 = box[0]
                    x1, x2 = box[1]
                    cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_GREEN, 2)

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
                    snapshot = {
                        "timestamp": utc_now_iso(),
                        "elapsedSeconds": sanitize_number(elapsed),
                        "heartRateBpm": sanitize_number(hr),
                        "sqi": sanitize_number(sqi),
                        "rmssdMs": sanitize_number(rmssd),
                    }
                    report["snapshots"].append(snapshot)
                    update_summary(report)
                    write_json_atomically(report, out_path)
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
            update_summary(report)
            write_json_atomically(report, out_path)
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
    main(launch=args.launch, out_path=args.out)