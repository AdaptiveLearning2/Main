import os
import threading

import cv2
import heartpy as hp
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import rppg
from scipy.signal import butter, filtfilt

# ── Configuration ──────────────────────────────────────────
RPPG_MODEL = 'PhysNet.rlap'
RMSSD_WINDOW_SIZE = 5

# Bandpass filter for cleaning BVP (0.7–3.5 Hz ≈ 42–210 BPM)
BPF_LOW = 0.7
BPF_HIGH = 3.5
BPF_ORDER = 4


def _suppress_rppg_thread_error(args):
    """Silence the harmless 'cannot join current thread' error from the rppg library."""
    if args.exc_type is RuntimeError and "cannot join current thread" in str(args.exc_value):
        return
    threading.__excepthook__(args)


def bandpass_filter(signal, fps):
    """Apply a Butterworth bandpass filter to isolate heart-rate frequencies."""
    nyq = fps / 2.0
    low = BPF_LOW / nyq
    high = min(BPF_HIGH / nyq, 0.99)
    b, a = butter(BPF_ORDER, [low, high], btype='band')
    return filtfilt(b, a, signal)


def load_ground_truth(vid_folder):
    """Load ground truth data from a DATASET_2 subject folder.

    Returns (gt_trace, gt_time, gt_hr) or (None, None, None) on failure.
    gt_trace is normalized to zero mean and unit variance.
    """
    gt_filename = os.path.join(vid_folder, 'ground_truth.txt')
    if not os.path.exists(gt_filename):
        print(f"  Warning: No ground truth file found in {vid_folder}")
        return None, None, None

    try:
        gt_data = np.loadtxt(gt_filename)
        gt_trace = gt_data[0, :].T
        gt_time = gt_data[2, :].T
        gt_hr = gt_data[1, :].T
    except Exception as e:
        print(f"  Error reading {gt_filename}: {e}")
        return None, None, None

    # Normalize (zero mean, unit variance)
    gt_trace = gt_trace - np.mean(gt_trace)
    std = np.std(gt_trace)
    if std != 0:
        gt_trace = gt_trace / std

    return gt_trace, gt_time, gt_hr


def compute_gt_rmssd(gt_trace, gt_time):
    """Compute RMSSD from the ground truth contact-PPG signal.

    Detects peaks in the PPG waveform, derives RR intervals, and computes
    RMSSD.  Returns (rmssd, avg_hr) or (None, None) on failure.
    """
    try:
        gt_fps = len(gt_time) / (gt_time[-1] - gt_time[0])
        measures, _ = hp.process(
            np.array(gt_trace, dtype=float), gt_fps,
            high_precision=True, clean_rr=True
        )

        rr_mask = np.array(measures["RR_masklist"])
        rr_intervals = np.array(measures["RR_list"])[np.where(1 - rr_mask)] / 1000.0

        if len(rr_intervals) < RMSSD_WINDOW_SIZE:
            return None, None

        rr_ms = rr_intervals * 1000
        rr_diffs = np.diff(rr_ms)
        rmssd = float(np.sqrt(np.mean(rr_diffs ** 2)))
        avg_hr = 60000.0 / float(np.mean(rr_ms))
        return rmssd, avg_hr
    except Exception as e:
        print(f"  Warning: ground truth RMSSD extraction failed ({e})")
        return None, None


def process_video_with_rppg(video_path):
    """Process a video file through the rppg model using its native file API.

    Returns (hr, sqi, bvp_signal, model_fps) or Nones on failure.
    """
    model = rppg.Model(RPPG_MODEL)

    try:
        model.process_video(video_path)

        result = model.hr(start=0, return_hrv=True)
        bvp_signal, _ = model.bvp(start=0)

        if not result or not result.get("hr"):
            return None, None, None, model.fps

        return result["hr"], result.get("SQI"), bvp_signal, model.fps

    except Exception as e:
        print(f"  Error during rPPG processing: {e}")
        return None, None, None, None


def compute_rppg_rmssd(bvp_signal, fps):
    """Compute RMSSD from the rPPG-extracted BVP signal.

    Returns (rmssd, hr_from_rr) or (None, None) on failure.
    """
    try:
        bvp_clean = bandpass_filter(np.array(bvp_signal, dtype=float), fps)
        measures, _ = hp.process(bvp_clean, fps, high_precision=True, clean_rr=True)

        rr_mask = np.array(measures["RR_masklist"])
        rr_intervals = np.array(measures["RR_list"])[np.where(1 - rr_mask)] / 1000.0

        if len(rr_intervals) < RMSSD_WINDOW_SIZE:
            return None, None

        rr_ms = rr_intervals * 1000
        rr_diffs = np.diff(rr_ms)
        rmssd = float(np.sqrt(np.mean(rr_diffs ** 2)))
        hr = 60000.0 / float(np.mean(rr_ms))
        return rmssd, hr
    except Exception as e:
        print(f"  Warning: rPPG RMSSD extraction failed ({e})")
        return None, None


def save_plot(fig, dir_name):
    """Save figure to a PNG in the Dataset_2 folder and close it."""
    out_path = os.path.join('Dataset_2', f'{dir_name}_rppg_analysis.png')
    fig.savefig(out_path, dpi=120, bbox_inches='tight')
    plt.close(fig)
    print(f"  Plot saved to {out_path}")


def plot_results(dir_name, gt_trace, gt_time, bvp_signal, model_fps,
                 rppg_hr, gt_hr_avg, rppg_rmssd, gt_rmssd, sqi):
    """Plot ground truth and rPPG BVP signals with HR/RMSSD comparison."""
    has_gt = gt_trace is not None and gt_time is not None
    has_bvp = bvp_signal is not None and len(bvp_signal) > 0
    num_plots = int(has_gt) + int(has_bvp)

    if num_plots == 0:
        return

    fig, axes = plt.subplots(num_plots, 1, figsize=(10, 4 * num_plots))
    if num_plots == 1:
        axes = [axes]

    plot_idx = 0

    if has_gt:
        axes[plot_idx].plot(gt_time, gt_trace, color='tab:blue', linewidth=0.6)
        axes[plot_idx].set_title(f'Normalized Ground Truth PPG - {dir_name}')
        axes[plot_idx].set_xlabel('Time (seconds)')
        axes[plot_idx].set_ylabel('Normalized Amplitude')
        axes[plot_idx].grid(True, alpha=0.3)
        plot_idx += 1

    if has_bvp:
        bvp_arr = np.array(bvp_signal, dtype=float)
        bvp_filtered = bandpass_filter(bvp_arr, model_fps)
        bvp_time = np.linspace(0, len(bvp_filtered) / model_fps, len(bvp_filtered))
        bvp_norm = bvp_filtered - np.mean(bvp_filtered)
        bvp_std = np.std(bvp_norm)
        if bvp_std != 0:
            bvp_norm = bvp_norm / bvp_std
        axes[plot_idx].plot(bvp_time, bvp_norm, color='tab:red', linewidth=0.6)
        axes[plot_idx].set_title(f'rPPG Extracted BVP Signal (filtered) - {dir_name}')
        axes[plot_idx].set_xlabel('Time (seconds)')
        axes[plot_idx].set_ylabel('Normalized Amplitude')
        axes[plot_idx].grid(True, alpha=0.3)

    # Summary banner — HR and RMSSD only
    parts = []
    if rppg_hr is not None:
        parts.append(f"rPPG HR: {rppg_hr:.1f}")
    if gt_hr_avg is not None:
        parts.append(f"GT HR: {gt_hr_avg:.1f}")
    if rppg_rmssd is not None:
        parts.append(f"rPPG RMSSD: {rppg_rmssd:.1f} ms")
    if gt_rmssd is not None:
        parts.append(f"GT RMSSD: {gt_rmssd:.1f} ms")
    if sqi is not None:
        parts.append(f"SQI: {sqi:.2f}")
    summary = "  |  ".join(parts)

    fig.suptitle(f'rPPG Analysis - {dir_name}', fontsize=13)
    fig.text(0.5, -0.02, summary, ha='center', fontsize=9, style='italic',
             bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))
    fig.tight_layout()
    save_plot(fig, dir_name)


def process_ubfc_dataset(root_folder='Dataset_2/'):
    """Iterate through UBFC-RPPG Dataset_2 subjects and process each with the rppg library."""
    threading.excepthook = _suppress_rppg_thread_error

    print(f"Processing dataset from: {root_folder}")
    print(f"Using rPPG model: {RPPG_MODEL}")

    try:
        all_entries = os.listdir(root_folder)
        dirs = [d for d in all_entries
                if os.path.isdir(os.path.join(root_folder, d)) and d not in ['.', '..', 'desktop.ini']]
        dirs.sort()
    except FileNotFoundError:
        print(f"Error: Dataset root folder '{root_folder}' not found.")
        return

    if not dirs:
        print(f"No subdirectories found in '{root_folder}'. Please check the dataset structure.")
        return

    for i, dir_name in enumerate(dirs):
        vid_folder = os.path.join(root_folder, dir_name)
        print(f"\n{'='*60}")
        print(f"  Processing folder {i+1}/{len(dirs)}: {vid_folder}")
        print(f"{'='*60}")

        # ── Load ground truth ─────────────────────────────
        gt_trace, gt_time, gt_hr = load_ground_truth(vid_folder)

        gt_hr_avg = None
        gt_rmssd = None
        if gt_trace is not None:
            print(f"  Number of PPG signal values: {len(gt_trace)}")
            print(f"  Length of ground truth signal: {gt_time[-1]:.2f} seconds")
            gt_hr_avg = float(np.mean(gt_hr))
            print(f"  Ground truth avg HR:  {gt_hr_avg:.1f} BPM")

            gt_rmssd, _ = compute_gt_rmssd(gt_trace, gt_time)
            if gt_rmssd is not None:
                print(f"  Ground truth RMSSD:   {gt_rmssd:.1f} ms")

        # ── Check video file ─────────────────────────────
        video_path = os.path.join(vid_folder, 'vid.avi')
        if not os.path.exists(video_path):
            print(f"  Error: Video file '{video_path}' not found. Skipping this folder.")
            continue

        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        video_length_sec = total_frames / fps if fps > 0 else 0
        cap.release()

        print(f"  Frame Rate (FPS): {fps:.2f}")
        print(f"  Total Number of Frames: {total_frames}")
        print(f"  Length of Video: {video_length_sec:.2f} seconds")

        # ── Process with rPPG ─────────────────────────────
        print(f"  Processing video with rPPG model...")
        rppg_hr, sqi, bvp_signal, model_fps = process_video_with_rppg(video_path)

        if rppg_hr is None:
            print(f"  Warning: rPPG could not extract heart rate for {dir_name}. Skipping.")
            continue

        rppg_rmssd, _ = compute_rppg_rmssd(bvp_signal, model_fps)

        # ── Print comparison table ────────────────────────
        print(f"\n  ── Results for {dir_name} ──")
        print(f"  {'Metric':<20} {'rPPG':>10} {'Ground Truth':>14} {'Diff':>10}")
        print(f"  {'-'*56}")

        gt_hr_str = f"{gt_hr_avg:.1f}" if gt_hr_avg is not None else "N/A"
        hr_diff = f"{abs(rppg_hr - gt_hr_avg):.1f}" if gt_hr_avg is not None else "N/A"
        print(f"  {'Heart Rate (BPM)':<20} {rppg_hr:>10.1f} {gt_hr_str:>14} {hr_diff:>10}")

        rppg_rmssd_str = f"{rppg_rmssd:.1f}" if rppg_rmssd is not None else "N/A"
        gt_rmssd_str = f"{gt_rmssd:.1f}" if gt_rmssd is not None else "N/A"
        if rppg_rmssd is not None and gt_rmssd is not None:
            rmssd_diff = f"{abs(rppg_rmssd - gt_rmssd):.1f}"
        else:
            rmssd_diff = "N/A"
        print(f"  {'RMSSD (ms)':<20} {rppg_rmssd_str:>10} {gt_rmssd_str:>14} {rmssd_diff:>10}")

        if sqi is not None:
            print(f"  Signal Quality (SQI): {sqi:.2f}")

        # ── Plot ──────────────────────────────────────────
        plot_results(dir_name, gt_trace, gt_time, bvp_signal, model_fps,
                     rppg_hr, gt_hr_avg, rppg_rmssd, gt_rmssd, sqi)

    print(f"\n{'='*60}")
    print("  Dataset processing complete.")
    print(f"{'='*60}")


if __name__ == "__main__":
    process_ubfc_dataset('Dataset_2/')
