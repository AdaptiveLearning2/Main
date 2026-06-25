import threading
import time

import cv2
import heartpy as hp
import matplotlib.pyplot as plt
import numpy as np
import rppg

# ── Configuration ──────────────────────────────────────────
RECORDING_DURATION = 60  # seconds

# UI colors (BGR)
COLOR_GREEN = (0, 255, 0)
COLOR_RED = (0, 0, 255)
COLOR_GRAY = (60, 60, 60)

MIN_SIGNAL_SECONDS = 5
RMSSD_WINDOW_SIZE = 5  # minimum RR intervals needed to compute a rolling RMSSD


# ── Video capture & overlay ───────────────────────────────
def draw_overlay(frame, elapsed, remaining, box):
    """Draw bounding box, timer, and progress bar on the preview frame."""
    height, width = frame.shape[:2]

    if box is not None:
        y1, y2 = box[0]
        x1, x2 = box[1]
        cv2.rectangle(frame, (x1, y1), (x2, y2), COLOR_GREEN, 2)

    mins, secs = divmod(int(elapsed), 60)
    timer_text = f"REC {mins:02d}:{secs:02d}  |  {int(remaining)}s left"
    cv2.putText(frame, timer_text, (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, COLOR_RED, 2)

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
def extract_rr_intervals(bvp_signal, fps):
    """Extract cleaned RR intervals (seconds) and their cumulative timestamps.

    Returns (rr_intervals, rr_times) or (None, None) on failure.
    """
    try:
        measures, _ = hp.process(
            np.array(bvp_signal), fps, high_precision=True, clean_rr=True
        )
        rr_mask = np.array(measures["RR_masklist"])
        rr_intervals = np.array(measures["RR_list"])[np.where(1 - rr_mask)] / 1000.0
        rr_times = np.cumsum(rr_intervals)
        return rr_intervals, rr_times
    except Exception as e:
        print(f"Warning: RR interval extraction failed ({e})")
        return None, None


def compute_rolling_rmssd(rr_intervals, rr_times):
    """Compute a cumulative RMSSD at each beat from the first RMSSD_WINDOW_SIZE intervals onward.

    Returns (rmssd_times, rmssd_values) arrays. Each entry is the RMSSD
    computed from all RR intervals up to that point.
    """
    rr_ms = rr_intervals * 1000
    rmssd_times = []
    rmssd_values = []

    for i in range(RMSSD_WINDOW_SIZE, len(rr_ms) + 1):
        diffs = np.diff(rr_ms[:i])
        rmssd = float(np.sqrt(np.mean(diffs ** 2)))
        rmssd_times.append(rr_times[i - 1])
        rmssd_values.append(rmssd)

    return np.array(rmssd_times), np.array(rmssd_values)


# ── Plotting ──────────────────────────────────────────────
def show_rmssd_plot(duration, rmssd_times, rmssd_values, final_rmssd):
    """Display the RMSSD over time plot."""
    fig, ax = plt.subplots(figsize=(10, 4))

    ax.plot(rmssd_times, rmssd_values, "o-", markersize=3, color="tab:blue", linewidth=1.2)
    ax.axhline(final_rmssd, color="tab:red", linestyle="--", linewidth=0.8,
               label=f"Final RMSSD: {final_rmssd:.1f} ms")

    ax.set(title=f"RMSSD Over Time ({duration:.0f}s recording)",
           xlabel="Time (s)", ylabel="RMSSD (ms)")
    ax.legend()
    ax.grid(True, alpha=0.3)

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

        bvp_signal, _ = model.bvp(start=0)

        if len(bvp_signal) < model.fps * MIN_SIGNAL_SECONDS:
            print("Not enough signal. Try better lighting and keep your face steady.")
            return

        rr_intervals, rr_times = extract_rr_intervals(bvp_signal, model.fps)

        if rr_intervals is None or len(rr_intervals) < RMSSD_WINDOW_SIZE:
            print("Not enough RR intervals to compute RMSSD.")
            return

        rmssd_times, rmssd_values = compute_rolling_rmssd(rr_intervals, rr_times)
        final_rmssd = rmssd_values[-1]

        print(f"\n── RMSSD: {final_rmssd:.1f} ms ──")

        show_rmssd_plot(duration, rmssd_times, rmssd_values, final_rmssd)


if __name__ == "__main__":
    main()