import os

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import rppg

from rPPG_test7 import (
    MIN_OVERLAP_SAMPLES,
    RPPG_MODEL,
    bandpass_filter,
    extract_rr,
    load_ground_truth,
)


def align_rr_series(gt_time, gt_rr_ms, rppg_time, rppg_rr_ms):
    start = max(gt_time[0], rppg_time[0])
    end = min(gt_time[-1], rppg_time[-1])
    if end <= start:
        return None, None, None

    common_time = np.unique(np.concatenate((gt_time, rppg_time)))
    common_time = common_time[(common_time >= start) & (common_time <= end)]
    if len(common_time) < MIN_OVERLAP_SAMPLES:
        return None, None, None

    gt_common = np.interp(common_time, gt_time, gt_rr_ms)
    rppg_common = np.interp(common_time, rppg_time, rppg_rr_ms)
    return common_time, gt_common, rppg_common


def compute_rr_mae(gt_common, rppg_common):
    return float(np.mean(np.abs(gt_common - rppg_common)))


def compute_rr_correlation(gt_common, rppg_common):
    if len(gt_common) < 2:
        return None
    if np.std(gt_common) == 0 or np.std(rppg_common) == 0:
        return None
    return float(np.corrcoef(gt_common, rppg_common)[0, 1])


def plot_metric_summary(subject_name, sample_count, rr_mae_ms, rr_corr):
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.axis('off')

    fig.suptitle(f'Test 9 - RR Metric Summary - {subject_name}', fontsize=13)
    ax.text(0.25, 0.62, f'{rr_mae_ms:.1f} ms', ha='center', va='center', fontsize=24, weight='bold')
    ax.text(0.25, 0.38, 'RR MAE', ha='center', va='center', fontsize=11)

    corr_text = f'{rr_corr:.3f}' if rr_corr is not None else 'N/A'
    ax.text(0.75, 0.62, corr_text, ha='center', va='center', fontsize=24, weight='bold')
    ax.text(0.75, 0.38, 'RR correlation', ha='center', va='center', fontsize=11)

    ax.text(0.5, 0.16, f'Aligned samples: {sample_count}', ha='center', va='center', fontsize=10)

    fig.tight_layout(rect=(0, 0, 1, 0.9))
    output_path = os.path.join('Dataset_2', f'{subject_name}_rr_metrics.png')
    fig.savefig(output_path, dpi=120, bbox_inches='tight')
    plt.close(fig)
    print(f"  Plot saved to {output_path}")


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

    rppg_rr_time, rppg_rr_ms = extract_rr(
        bandpass_filter(np.asarray(bvp_signal, dtype=float), model.fps),
        model.fps
    )
    if rppg_rr_time is None:
        print("  Warning: could not extract rPPG RR intervals")
        return

    common_time, gt_common, rppg_common = align_rr_series(gt_rr_time, gt_rr_ms, rppg_rr_time, rppg_rr_ms)
    if common_time is None:
        print("  Warning: not enough overlapping RR data to compare")
        return

    rr_mae_ms = compute_rr_mae(gt_common, rppg_common)
    rr_corr = compute_rr_correlation(gt_common, rppg_common)

    print(f"  Aligned RR samples: {len(common_time)}")
    print(f"  RR MAE: {rr_mae_ms:.1f} ms")
    if rr_corr is None:
        print("  RR correlation: N/A")
    else:
        print(f"  RR correlation: {rr_corr:.3f}")

    plot_metric_summary(subject_name, len(common_time), rr_mae_ms, rr_corr)


def process_dataset(root='Dataset_2'):
    print(f"Processing dataset from: {root}")
    print("Metrics: RR interval mean absolute error (MAE) and RR correlation")

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
