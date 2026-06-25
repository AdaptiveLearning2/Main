import threading
import time

import cv2
import heartpy as hp
import matplotlib.pyplot as plt
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

    return duration


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


# ── Plotting ──────────────────────────────────────────────
def plot_bvp(ax, bvp_signal, fps):
    """Plot the Blood Volume Pulse waveform."""
    time_axis = np.linspace(0, len(bvp_signal) / fps, len(bvp_signal))
    ax.plot(time_axis, bvp_signal, color="tab:red", linewidth=0.6)
    ax.set(title="Blood Volume Pulse (BVP)", xlabel="Time (s)", ylabel="Amplitude")
    ax.grid(True, alpha=0.3)


def plot_rr_tachogram(ax, rr_times, rr_intervals):
    """Plot the RR interval tachogram."""
    ax.plot(rr_times, rr_intervals * 1000, "o-", markersize=3, color="tab:blue")
    ax.set(title="RR Interval Tachogram", xlabel="Time (s)", ylabel="RR (ms)")
    ax.grid(True, alpha=0.3)


def plot_psd(ax, freqs, psd):
    """Plot the HRV power spectral density with LF/HF band shading."""
    ax.semilogy(freqs, psd, color="black", linewidth=0.8)

    lf_mask = (freqs >= LF_BAND[0]) & (freqs < LF_BAND[1])
    hf_mask = (freqs >= HF_BAND[0]) & (freqs < HF_BAND[1])
    ax.fill_between(freqs[lf_mask], psd[lf_mask], alpha=0.4, color="tab:orange", label="LF")
    ax.fill_between(freqs[hf_mask], psd[hf_mask], alpha=0.4, color="tab:cyan", label="HF")

    ax.set(title="HRV Power Spectral Density", xlabel="Frequency (Hz)",
           ylabel="PSD", xlim=(0, 0.5))
    ax.legend()
    ax.grid(True, alpha=0.3)


def show_plots(duration, fps, bvp_signal, metrics, rr_intervals, rr_times, freqs, psd):
    """Build and display all analysis plots in a single figure."""
    has_rr = rr_intervals is not None and len(rr_intervals) > 2
    num_plots = 3 if has_rr else 1

    fig, axes = plt.subplots(num_plots, 1, figsize=(10, 3.5 * num_plots))
    if num_plots == 1:
        axes = [axes]

    plot_bvp(axes[0], bvp_signal, fps)

    if has_rr:
        plot_rr_tachogram(axes[1], rr_times, rr_intervals)
        plot_psd(axes[2], freqs, psd)

    # Summary banner across the bottom
    summary = "  |  ".join(f"{k}: {v}" for k, v in metrics.items())
    fig.text(0.5, -0.05, summary, ha="center", fontsize=9, style="italic",
             bbox=dict(boxstyle="round", facecolor="lightyellow", alpha=0.8))
    fig.suptitle(f"HRV Analysis ({duration:.0f}s)", fontsize=13)

    fig.tight_layout()
    plt.show()


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
        duration = record_video(model)

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

        show_plots(duration, model.fps, bvp_signal, metrics,
                   rr_intervals, rr_times, freqs, psd)

        print("\n── Results ──")
        for key, value in metrics.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    main()