import json
import threading
import time
from datetime import datetime

import cv2
import heartpy as hp
import numpy as np
import rppg
from scipy.interpolate import CubicSpline
from scipy.signal import welch

# ── Configuration ──────────────────────────────────────────
RECORDING_DURATION = 60  # seconds

# UI colors (BGR)
COLOR_GREEN = (0, 255, 0)
COLOR_RED = (0, 0, 255)
COLOR_GRAY = (60, 60, 60)

# HRV frequency bands (Hz)
LF_BAND = (0.04, 0.15)
HF_BAND = (0.15, 0.40)

MIN_SIGNAL_SECONDS = 5
RESAMPLE_RATE_HZ = 4


# ── Video capture & overlay ───────────────────────────────
def draw_overlay(frame, elapsed, remaining, box):
    """Draw bounding box, timer, and progress bar on the preview frame."""
    height, width = frame.shape[:2]

    # Face bounding box
    if box is not None:
        y1, y2 = box[0]
        x1, x2 = box[1]
        cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_GREEN, 2)

    # Elapsed / remaining timer
    mins, secs = divmod(int(elapsed), 60)
    timer_text = f"REC {mins:02d}:{secs:02d}  |  {int(remaining)}s left"
    cv2.putText(frame, timer_text, (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_RED, 2)

    # Progress bar
    bar_width = width - 20
    progress = min(elapsed / RECORDING_DURATION, 1.0)
    cv2.rectangle(frame, (10, 40), (10 + bar_width, 52), COLOR_GRAY, -1)
    cv2.rectangle(frame, (10, 40), (10 + int(bar_width * progress), 52), COLOR_GREEN, -1)


def record_video(model):
    """Show preview window and record BVP data for up to RECORDING_DURATION seconds."""
    start_time = time.time()
    test_start_str = datetime.now().strftime("%Y-%m-%d_%H;%M;%S")

    print(f"Recording for {RECORDING_DURATION}s. Press 'q' to stop early.")

    for frame, box in model.preview:
        elapsed = time.time() - start_time
        remaining = max(0, RECORDING_DURATION - elapsed)

        frame = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        draw_overlay(frame, elapsed, remaining, box)
        cv2.imshow("rPPG Monitor", frame)

        if elapsed >= RECORDING_DURATION or cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cv2.destroyAllWindows()

    duration = time.time() - start_time
    print(f"\nRecording stopped after {duration:.0f}s. Processing...")

    return test_start_str, duration


# ── Signal analysis ───────────────────────────────────────
def compute_rr_and_psd(bvp_signal, fps, hrv):
    """Extract RR intervals and power spectral density from the BVP signal.

    Returns (rr_intervals, rr_times, freqs, psd) or Nones on failure.
    """
    try:
        measures, _ = hp.process(
            np.array(bvp_signal), fps, high_precision=True, clean_rr=True
        )
        rr_mask = np.array(measures["RR_masklist"])
        rr_intervals = np.array(measures["RR_list"])[np.where(1 - rr_mask)] / 1000.0
        rr_times = np.cumsum(rr_intervals)

        rr_resampled = CubicSpline(rr_times, rr_intervals)(
            np.arange(0, rr_times[-1], 1.0 / RESAMPLE_RATE_HZ)
        )
        freqs, psd = welch(
            rr_resampled,
            fs=RESAMPLE_RATE_HZ,
            nperseg=min(len(rr_resampled), 256),
            nfft=4096,
        )

        # Compute time-domain HRV metrics from RR intervals (in ms)
        rr_ms = rr_intervals * 1000
        rr_diffs = np.diff(rr_ms)
        hrv["rmssd"] = float(np.sqrt(np.mean(rr_diffs ** 2)))
        hrv["sdnn"] = float(np.std(rr_ms, ddof=1))

        # Compute LF/HF ratio if not already provided
        if "LF/HF" not in hrv:
            lf_power = np.trapezoid(psd[(freqs >= LF_BAND[0]) & (freqs < LF_BAND[1])])
            hf_power = np.trapezoid(psd[(freqs >= HF_BAND[0]) & (freqs < HF_BAND[1])])
            hrv["LF/HF"] = lf_power / hf_power if hf_power > 0 else float("nan")

        return rr_intervals, rr_times, freqs, psd

    except Exception as e:
        print(f"Warning: RR interval extraction failed ({e})")
        return None, None, None, None


def build_metrics(hr, hrv):
    """Return a dict of formatted summary metrics."""
    return {
        "HR": f"{hr:.1f} BPM",
        "RMSSD": f"{hrv.get('rmssd', float('nan')):.1f} ms",
        "SDNN": f"{hrv.get('sdnn', float('nan')):.1f} ms",
        "LF/HF": f"{hrv.get('LF/HF', float('nan')):.2f}",
    }


# ── Data export ───────────────────────────────────────────
def build_output(test_start_str, duration, fps, sqi, metrics, hrv,
                 bvp_signal, rr_intervals, rr_times, freqs, psd):
    """Assemble a JSON-serializable dict of all recorded data."""
    time_axis = np.linspace(0, len(bvp_signal) / fps, len(bvp_signal))

    def to_native(v):
        """Convert numpy scalars to Python-native types for JSON."""
        if isinstance(v, (np.integer,)):
            return int(v)
        if isinstance(v, (np.floating,)):
            return float(v)
        return v

    output = {
        "test_start": test_start_str,
        "duration_s": round(duration, 1),
        "fps": to_native(fps),
        "metrics": metrics,
        "hrv": {k: to_native(v) for k, v in hrv.items() if isinstance(v, (int, float, str, np.integer, np.floating))},
        "sqi": to_native(sqi),
        "bvp": {
            "time_s": time_axis.tolist(),
            "amplitude": [float(x) for x in bvp_signal],
        },
    }

    has_rr = rr_intervals is not None and len(rr_intervals) > 2
    if has_rr:
        output["rr_intervals"] = {
            "time_s": rr_times.tolist(),
            "rr_ms": (rr_intervals * 1000).tolist(),
        }
        output["psd"] = {
            "frequency_hz": freqs.tolist(),
            "power": psd.tolist(),
        }

    return output


def save_results(output_data, test_start_str):
    """Write output_data to a timestamped JSON file."""
    filename = f"rppg_{test_start_str}.json"
    with open(filename, "w") as f:
        json.dump(output_data, f, indent=2)
    print(f"\nData saved to {filename}")


# ── Main ──────────────────────────────────────────────────
def _suppress_rppg_thread_error(args):
    """Silence the harmless 'cannot join current thread' error from the rppg library."""
    if args.exc_type is RuntimeError and "cannot join current thread" in str(args.exc_value):
        return
    threading.__excepthook__(args)


def main():
    threading.excepthook = _suppress_rppg_thread_error
    model = rppg.Model()

    with model.video_capture(0):
        test_start_str, duration = record_video(model)

        result = model.hr(start=0, return_hrv=True)
        bvp_signal, _ = model.bvp(start=0)

        if not result or not result.get("hr") or len(bvp_signal) < model.fps * MIN_SIGNAL_SECONDS:
            print("Not enough signal. Try better lighting and keep your face steady.")
            return

        hr = result["hr"]
        sqi = result["SQI"]
        hrv = result["hrv"]

        print(f"\n── Average Heart Rate: {hr:.1f} BPM ──")

        rr_intervals, rr_times, freqs, psd = compute_rr_and_psd(bvp_signal, model.fps, hrv)

        metrics = build_metrics(hr, hrv)

        output_data = build_output(
            test_start_str, duration, model.fps, sqi, metrics, hrv,
            bvp_signal, rr_intervals, rr_times, freqs, psd,
        )

        save_results(output_data, test_start_str)

        print("\n── Results ──")
        for key, value in metrics.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()