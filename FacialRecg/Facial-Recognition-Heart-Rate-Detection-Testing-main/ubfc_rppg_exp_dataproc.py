import os
import threading

import numpy as np
import rppg

# A simple program for exporting openRPPG outputs to UBFC-style subject text files.

RPPG_MODELS = [
    "FacePhys.rlap",
    "ME-chunk.rlap",
    "ME-flow.rlap",
    "ME-chunk.pure",
    "ME-flow.pure",
    "PhysMamba.pure",
    "PhysMamba.rlap",
    "RhythmMamba.rlap",
    "RhythmMamba.pure",
    "PhysFormer.pure",
    "PhysFormer.rlap",
    "TSCAN.rlap",
    "TSCAN.pure",
    "PhysNet.rlap",
    "PhysNet.pure",
    "EfficientPhys.pure",
    "EfficientPhys.rlap",
]
HR_WINDOW_SECONDS = 10.0
HR_STEP_SECONDS = 1.0
MIN_HR_WINDOW_SECONDS = 2.0


def _suppress_rppg_thread_error(args):
    """Silence the harmless 'cannot join current thread' error from the rppg library."""
    if args.exc_type is RuntimeError and "cannot join current thread" in str(args.exc_value):
        return
    threading.__excepthook__(args)


def _resolve_dataset_root(root_folder):
    """Accept the provided path or common UBFC Dataset_2 naming variants."""
    candidate_paths = [root_folder]

    if root_folder == "DATASET_2":
        candidate_paths.append("Dataset_2")
    elif root_folder == "DATASET_2\\":
        candidate_paths.append("Dataset_2\\")
    elif root_folder == "DATASET_2/":
        candidate_paths.extend(["Dataset_2", "Dataset_2\\", "Dataset_2/"])

    for candidate in candidate_paths:
        if os.path.isdir(candidate):
            return candidate

    return root_folder


def _get_output_filename(model_name):
    return f"{model_name}.txt"


def _estimate_hr_series(model, time):
    """Estimate a time-varying HR signal from overlapping windows of the processed BVP."""
    duration = float(time[-1] - time[0])
    if duration < MIN_HR_WINDOW_SECONDS:
        raise ValueError(
            f"Signal duration ({duration:.2f}s) is too short for HR estimation."
        )

    window_seconds = min(HR_WINDOW_SECONDS, duration)
    start_limit = max(duration - window_seconds, 0.0)

    window_starts = np.arange(0.0, start_limit + HR_STEP_SECONDS, HR_STEP_SECONDS)
    if window_starts.size == 0 or window_starts[-1] < start_limit:
        window_starts = np.append(window_starts, start_limit)

    hr_times = []
    hr_values = []

    for window_start in window_starts:
        window_end = min(window_start + window_seconds, duration)
        hr_result = model.hr(start=float(window_start), end=float(window_end), return_hrv=False)

        if not hr_result or hr_result.get("hr") is None:
            continue

        hr_times.append((window_start + window_end) / 2.0)
        hr_values.append(float(hr_result["hr"]))

    if not hr_values:
        raise ValueError("openRPPG did not return any windowed HR estimates.")

    if len(hr_values) == 1:
        return np.full(time.shape, hr_values[0], dtype=float)

    return np.interp(
        time,
        np.asarray(hr_times, dtype=float),
        np.asarray(hr_values, dtype=float),
    )


def _build_subject_output(model, video_path):
    """Return UBFC-style rows: trace, HR, time."""
    model.process_video(video_path)

    bvp_signal, bvp_timestamp = model.bvp(start=0)

    if len(bvp_signal) == 0 or len(bvp_timestamp) == 0:
        raise ValueError("openRPPG returned no BVP signal.")

    if len(bvp_signal) != len(bvp_timestamp):
        raise ValueError(
            f"Signal/time length mismatch ({len(bvp_signal)} vs {len(bvp_timestamp)})."
        )

    trace = np.asarray(bvp_signal, dtype=float).reshape(-1)
    time = np.asarray(bvp_timestamp, dtype=float).reshape(-1)
    hr = _estimate_hr_series(model, time)

    return np.vstack([trace, hr, time])


def process_ubfc_dataset(root_folder="Dataset_2"):
    """
    Process each UBFC Dataset 2 subject and write openRPPG outputs in UBFC text format.
    """
    threading.excepthook = _suppress_rppg_thread_error
    root_folder = _resolve_dataset_root(root_folder)

    print(f"Processing dataset from: {root_folder}")

    try:
        all_entries = os.listdir(root_folder)
    except FileNotFoundError:
        print(f"Error: Dataset root folder '{root_folder}' not found.")
        return

    dirs = [
        d for d in all_entries
        if os.path.isdir(os.path.join(root_folder, d)) and d not in [".", "..", "desktop.ini"]
    ]
    dirs.sort()

    if not dirs:
        print(f"No subdirectories found in '{root_folder}'. Please check the dataset structure.")
        return

    print(f"Using {len(RPPG_MODELS)} openRPPG models:")
    for model_name in RPPG_MODELS:
        print(f"  - {model_name}")

    for index, dir_name in enumerate(dirs, start=1):
        subject_folder = os.path.join(root_folder, dir_name)
        video_path = os.path.join(subject_folder, "vid.avi")

        print(f"\n--- Processing folder {index}/{len(dirs)}: {subject_folder} ---")

        if not os.path.exists(video_path):
            print(f"  Warning: missing video file '{video_path}', skipping.")
            continue

        for model_name in RPPG_MODELS:
            output_path = os.path.join(subject_folder, _get_output_filename(model_name))
            model = rppg.Model(model_name)

            try:
                output_rows = _build_subject_output(model, video_path)
                np.savetxt(output_path, output_rows, fmt="%.10f")
                print(f"  Saved {model_name} output to: {output_path}")
            except Exception as exc:
                print(f"  Error processing {subject_folder} with {model_name}: {exc}")


if __name__ == "__main__":
    process_ubfc_dataset()
