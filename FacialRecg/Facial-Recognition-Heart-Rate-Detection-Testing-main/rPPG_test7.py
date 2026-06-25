import os

import heartpy as hp
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import rppg
from scipy.signal import butter, filtfilt

RPPG_MODEL = 'PhysNet.rlap'
DIFF_THRESHOLD_MS = 100.0
MIN_OVERLAP_SAMPLES = 20


def bandpass_filter(signal, fps):
    nyquist = fps / 2.0
    low = 0.7 / nyquist
    high = min(3.5 / nyquist, 0.99)
    b, a = butter(4, [low, high], btype='band')
    return filtfilt(b, a, signal)


def load_ground_truth(folder):
    path = os.path.join(folder, 'ground_truth.txt')
    if not os.path.exists(path):
        print(f"  Warning: missing {path}")
        return None, None

    try:
        data = np.loadtxt(path)
    except Exception as e:
        print(f"  Error reading {path}: {e}")
        return None, None

    trace = np.asarray(data[0], dtype=float)
    time = np.asarray(data[2], dtype=float)
    trace = trace - np.mean(trace)
    std = np.std(trace)
    if std != 0:
        trace = trace / std
    return trace, time


def extract_rr(signal, fps):
    try:
        measures, _ = hp.process(np.asarray(signal, dtype=float), fps, high_precision=True, clean_rr=True)
    except Exception as e:
        print(f"  Warning: RR extraction failed ({e})")
        return None, None

    mask = np.asarray(measures["RR_masklist"], dtype=int)
    rr = np.asarray(measures["RR_list"], dtype=float)[np.where(1 - mask)] / 1000.0
    if len(rr) < 3:
        return None, None
    return np.cumsum(rr), rr * 1000.0


def plot_comparison(subject_name, gt_time, gt_rr_ms, rppg_time, rppg_rr_ms):
    start = max(gt_time[0], rppg_time[0])
    end = min(gt_time[-1], rppg_time[-1])
    if end <= start:
        return False

    common_time = np.unique(np.concatenate((gt_time, rppg_time)))
    common_time = common_time[(common_time >= start) & (common_time <= end)]
    if len(common_time) < MIN_OVERLAP_SAMPLES:
        return False

    gt_common = np.interp(common_time, gt_time, gt_rr_ms)
    rppg_common = np.interp(common_time, rppg_time, rppg_rr_ms)
    diff_mask = np.abs(gt_common - rppg_common) >= DIFF_THRESHOLD_MS

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(common_time, gt_common, label='Ground Truth HRV', color='tab:blue')
    ax.plot(common_time, rppg_common, label='rPPG HRV', color='tab:red', alpha=0.85)

    if np.any(diff_mask):
        ax.fill_between(
            common_time,
            0,
            1,
            where=diff_mask,
            interpolate=True,
            transform=ax.get_xaxis_transform(),
            color='black',
            alpha=0.15,
            zorder=0,
            label='Different'
        )

    ax.set_title(f'HRV Graph Comparison - {subject_name}')
    ax.set_xlabel('Time (seconds)')
    ax.set_ylabel('RR Interval (ms)')
    ax.grid(True, alpha=0.3)
    ax.legend(loc='upper right')
    fig.text(
        0.5,
        0.01,
        f'Segments shaded when difference is at least {DIFF_THRESHOLD_MS:.0f} ms',
        ha='center',
        fontsize=9
    )
    fig.tight_layout(rect=(0, 0.04, 1, 1))

    output_path = os.path.join('Dataset_2', f'{subject_name}_hrv_difference.png')
    fig.savefig(output_path, dpi=120, bbox_inches='tight')
    plt.close(fig)

    print(f"  Compared {len(common_time)} samples")
    print(f"  Different points: {int(np.count_nonzero(diff_mask))}")
    print(f"  Plot saved to {output_path}")
    return True


def process_subject(folder, subject_name):
    gt_trace, gt_signal_time = load_ground_truth(folder)
    if gt_trace is None or gt_signal_time is None or len(gt_signal_time) < 2:
        print("  Warning: invalid ground truth signal")
        return

    gt_duration = gt_signal_time[-1] - gt_signal_time[0]
    if gt_duration <= 0:
        print("  Warning: invalid ground truth timestamps")
        return

    gt_fps = len(gt_signal_time) / gt_duration
    gt_rr_time, gt_rr_ms = extract_rr(gt_trace, gt_fps)
    if gt_rr_time is None:
        print("  Warning: could not extract ground truth RR intervals")
        return

    video_path = os.path.join(folder, 'vid.avi')
    if not os.path.exists(video_path):
        print(f"  Warning: missing {video_path}")
        return

    try:
        model = rppg.Model(RPPG_MODEL)
        model.process_video(video_path)
        bvp_signal, _ = model.bvp(start=0)
    except Exception as e:
        print(f"  Error during rPPG processing: {e}")
        return

    if bvp_signal is None or len(bvp_signal) == 0:
        print("  Warning: empty rPPG BVP signal")
        return

    rppg_rr_time, rppg_rr_ms = extract_rr(bandpass_filter(np.asarray(bvp_signal, dtype=float), model.fps), model.fps)
    if rppg_rr_time is None:
        print("  Warning: could not extract rPPG RR intervals")
        return

    if not plot_comparison(subject_name, gt_rr_time, gt_rr_ms, rppg_rr_time, rppg_rr_ms):
        print("  Warning: not enough overlapping RR data to compare")


def process_dataset(root='Dataset_2'):
    print(f"Processing dataset from: {root}")

    if not os.path.isdir(root):
        print(f"Error: dataset folder '{root}' not found.")
        return

    subject_names = sorted(
        entry for entry in os.listdir(root)
        if os.path.isdir(os.path.join(root, entry)) and entry not in {'.', '..', 'desktop.ini'}
    )

    if not subject_names:
        print("No subject folders found.")
        return

    for index, subject_name in enumerate(subject_names, start=1):
        folder = os.path.join(root, subject_name)
        print(f"\n[{index}/{len(subject_names)}] {folder}")
        process_subject(folder, subject_name)

    print("\nDataset processing complete.")


if __name__ == "__main__":
    process_dataset()
