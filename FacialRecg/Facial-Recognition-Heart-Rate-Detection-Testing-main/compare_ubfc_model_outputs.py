import argparse
import csv
import os

import numpy as np


DEFAULT_DATASET_ROOT = "Dataset_2"
DEFAULT_REPORT_PATH = "ubfc_model_comparison_report.txt"
DEFAULT_CSV_PATH = "ubfc_model_comparison_summary.csv"


def load_ubfc_text_file(file_path):
    """Load a UBFC-style text file with rows: trace, HR, time."""
    data = np.loadtxt(file_path, dtype=float)

    if data.ndim == 1:
        data = data.reshape(1, -1)

    if data.shape[0] != 3:
        raise ValueError(f"Expected 3 rows in '{file_path}', found shape {data.shape}.")

    trace = np.asarray(data[0], dtype=float).reshape(-1)
    hr = np.asarray(data[1], dtype=float).reshape(-1)
    time = np.asarray(data[2], dtype=float).reshape(-1)

    if not (len(trace) == len(hr) == len(time)):
        raise ValueError(f"Row lengths do not match in '{file_path}'.")

    return trace, hr, time


def normalize_signal(signal):
    signal = np.asarray(signal, dtype=float)
    std = np.std(signal)
    if std == 0:
        raise ValueError("Cannot normalize a flat signal.")
    return (signal - np.mean(signal)) / std


def get_overlap_mask(time, start_time, end_time):
    return (time >= start_time) & (time <= end_time)


def align_series(reference_time, target_time, target_values):
    return np.interp(reference_time, target_time, target_values)


def pearson_correlation(a, b):
    if len(a) < 2 or len(b) < 2:
        return np.nan
    if np.std(a) == 0 or np.std(b) == 0:
        return np.nan
    return float(np.corrcoef(a, b)[0, 1])


def compute_error_metrics(reference, estimate):
    error = np.asarray(estimate, dtype=float) - np.asarray(reference, dtype=float)
    return {
        "mae": float(np.mean(np.abs(error))),
        "rmse": float(np.sqrt(np.mean(error ** 2))),
        "bias": float(np.mean(error)),
        "corr": pearson_correlation(reference, estimate),
    }


def compute_mean_absolute_percentage_error(reference, estimate):
    reference = np.asarray(reference, dtype=float)
    estimate = np.asarray(estimate, dtype=float)
    valid_mask = np.abs(reference) > 1e-6

    if not np.any(valid_mask):
        return np.nan

    percent_error = np.abs((estimate[valid_mask] - reference[valid_mask]) / reference[valid_mask])
    return float(np.mean(percent_error) * 100.0)


def correlation_to_percent(correlation):
    if np.isnan(correlation):
        return np.nan
    return float(np.clip(correlation, 0.0, 1.0) * 100.0)


def error_to_accuracy_percent(percent_error):
    if np.isnan(percent_error):
        return np.nan
    return float(np.clip(100.0 - percent_error, 0.0, 100.0))


def average_valid(values):
    values = np.asarray(values, dtype=float)
    valid_values = values[~np.isnan(values)]
    return float(np.mean(valid_values)) if valid_values.size else np.nan


def compare_subject_model(subject_folder, model_file_name):
    ground_truth_path = os.path.join(subject_folder, "ground_truth.txt")
    model_path = os.path.join(subject_folder, model_file_name)

    if not os.path.exists(ground_truth_path):
        return None, f"missing '{ground_truth_path}'"

    if not os.path.exists(model_path):
        return None, f"missing '{model_path}'"

    gt_trace, gt_hr, gt_time = load_ubfc_text_file(ground_truth_path)
    model_trace, model_hr, model_time = load_ubfc_text_file(model_path)

    overlap_start = max(float(gt_time[0]), float(model_time[0]))
    overlap_end = min(float(gt_time[-1]), float(model_time[-1]))

    if overlap_end <= overlap_start:
        return None, "no overlapping time range"

    gt_mask = get_overlap_mask(gt_time, overlap_start, overlap_end)
    if np.count_nonzero(gt_mask) < 2:
        return None, "not enough overlapping samples"

    gt_time_overlap = gt_time[gt_mask]
    gt_trace_overlap = gt_trace[gt_mask]
    gt_hr_overlap = gt_hr[gt_mask]

    model_trace_aligned = align_series(gt_time_overlap, model_time, model_trace)
    model_hr_aligned = align_series(gt_time_overlap, model_time, model_hr)

    trace_metrics = compute_error_metrics(
        normalize_signal(gt_trace_overlap),
        normalize_signal(model_trace_aligned),
    )
    hr_metrics = compute_error_metrics(gt_hr_overlap, model_hr_aligned)
    hr_error_percent = compute_mean_absolute_percentage_error(gt_hr_overlap, model_hr_aligned)
    hr_accuracy_percent = error_to_accuracy_percent(hr_error_percent)
    trace_match_percent = correlation_to_percent(trace_metrics["corr"])
    overall_match_percent = average_valid([trace_match_percent, hr_accuracy_percent])

    return {
        "subject": os.path.basename(subject_folder),
        "model_file": model_file_name,
        "model_name": os.path.splitext(model_file_name)[0],
        "samples": int(len(gt_time_overlap)),
        "hr_mae": hr_metrics["mae"],
        "hr_error_percent": hr_error_percent,
        "hr_accuracy_percent": hr_accuracy_percent,
        "trace_match_percent": trace_match_percent,
        "overall_match_percent": overall_match_percent,
    }, None


def iter_subject_folders(dataset_root, excluded_subjects=None):
    excluded = set(excluded_subjects) if excluded_subjects else set()
    for name in sorted(os.listdir(dataset_root)):
        if name in excluded:
            continue
        folder = os.path.join(dataset_root, name)
        if os.path.isdir(folder) and name not in {".", "..", "desktop.ini"}:
            yield folder


def iter_model_files(subject_folder, model_name=None):
    if model_name:
        if not model_name.endswith(".txt"):
            model_name = f"{model_name}.txt"
        candidate = os.path.join(subject_folder, model_name)
        if os.path.exists(candidate):
            return [model_name]
        return []

    model_files = []
    for entry in sorted(os.listdir(subject_folder)):
        entry_path = os.path.join(subject_folder, entry)
        if not os.path.isfile(entry_path):
            continue
        if not entry.lower().endswith(".txt"):
            continue
        if entry == "ground_truth.txt":
            continue
        model_files.append(entry)

    return model_files


def summarize_results(results):
    keys = [
        "hr_mae",
        "hr_error_percent",
        "hr_accuracy_percent",
        "trace_match_percent",
        "overall_match_percent",
    ]
    summary = {}
    for key in keys:
        summary[key] = average_valid([row[key] for row in results])
    return summary


def aggregate_by_model(results):
    grouped = {}
    for row in results:
        grouped.setdefault(row["model_name"], []).append(row)

    summaries = []
    for model_name, model_results in grouped.items():
        summary = summarize_results(model_results)
        summary["model_name"] = model_name
        summary["subjects_compared"] = len(model_results)
        summaries.append(summary)

    summaries.sort(
        key=lambda item: (-item["overall_match_percent"], -item["hr_accuracy_percent"], item["hr_mae"])
    )
    return summaries


def format_percent(value):
    return "n/a" if np.isnan(value) else f"{value:.2f}%"


def format_subject_section(subject_name, subject_results):
    lines = [f"Subject {subject_name}", "Ground truth is the reference for every comparison in this section."]

    ranked_results = sorted(
        subject_results,
        key=lambda item: (-item["overall_match_percent"], -item["hr_accuracy_percent"], item["hr_mae"]),
    )

    for result in ranked_results:
        lines.append(
            (
                f"- {result['model_name']}: compared with ground truth, "
                f"heart-rate error was {result['hr_mae']:.4f} BPM, "
                f"heart-rate accuracy was {format_percent(result['hr_accuracy_percent'])}, "
                f"trace match was {format_percent(result['trace_match_percent'])}, "
                f"and overall match was {format_percent(result['overall_match_percent'])}."
            )
        )

    return "\n".join(lines)


def format_overall_section(model_summaries):
    lines = [
        "Overall model comparison",
        "Each summary below is averaged over the subjects that had both ground truth and the model output file.",
    ]

    for summary in model_summaries:
        lines.append(
            (
                f"- {summary['model_name']}: across {summary['subjects_compared']} subjects, "
                f"heart-rate error averaged {summary['hr_mae']:.4f} BPM, "
                f"heart-rate accuracy averaged {format_percent(summary['hr_accuracy_percent'])}, "
                f"trace match averaged {format_percent(summary['trace_match_percent'])}, "
                f"and overall match averaged {format_percent(summary['overall_match_percent'])}."
            )
        )

    return "\n".join(lines)


def write_report(report_path, subject_sections, model_summaries, skipped_items):
    blocks = [
        "UBFC Model Comparison Report",
        "All differences are described relative to the ground truth.",
    ]

    if model_summaries:
        blocks.append(format_overall_section(model_summaries))

    if subject_sections:
        blocks.append("Per-subject comparison\n\n" + "\n\n".join(subject_sections))

    if skipped_items:
        blocks.append("Skipped items\n" + "\n".join(f"- {skipped}" for skipped in skipped_items))

    with open(report_path, "w", encoding="utf-8") as report_file:
        report_file.write("\n\n".join(blocks).rstrip() + "\n")


def write_csv_summary(csv_path, model_summaries):
    fieldnames = [
        "model_name",
        "subjects_compared",
        "hr_mae_bpm",
        "hr_error_percent",
        "hr_accuracy_percent",
        "trace_match_percent",
        "overall_match_percent",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
        writer.writeheader()

        for summary in model_summaries:
            writer.writerow(
                {
                    "model_name": summary["model_name"],
                    "subjects_compared": summary["subjects_compared"],
                    "hr_mae_bpm": f"{summary['hr_mae']:.4f}",
                    "hr_error_percent": "" if np.isnan(summary["hr_error_percent"]) else f"{summary['hr_error_percent']:.2f}",
                    "hr_accuracy_percent": "" if np.isnan(summary["hr_accuracy_percent"]) else f"{summary['hr_accuracy_percent']:.2f}",
                    "trace_match_percent": "" if np.isnan(summary["trace_match_percent"]) else f"{summary['trace_match_percent']:.2f}",
                    "overall_match_percent": "" if np.isnan(summary["overall_match_percent"]) else f"{summary['overall_match_percent']:.2f}",
                }
            )


def main():
    parser = argparse.ArgumentParser(
        description="Compare UBFC ground-truth files against all model output files in each subject folder."
    )
    parser.add_argument(
        "--dataset-root",
        default=DEFAULT_DATASET_ROOT,
        help=f"Path to the UBFC subject folders (default: {DEFAULT_DATASET_ROOT})",
    )
    parser.add_argument(
        "--model-name",
        default=None,
        help="Optional single model filename without .txt to compare instead of scanning all model files.",
    )
    parser.add_argument(
        "--report-path",
        default=DEFAULT_REPORT_PATH,
        help=f"Path to the output report text file (default: {DEFAULT_REPORT_PATH})",
    )
    parser.add_argument(
        "--csv-path",
        default=DEFAULT_CSV_PATH,
        help=f"Path to the output CSV summary file (default: {DEFAULT_CSV_PATH})",
    )
    parser.add_argument(
        "--exclude-subjects",
        nargs="+",
        default=[],
        metavar="SUBJECT",
        help="One or more subject folder names to exclude from comparison (e.g. --exclude-subjects subject20 subject24).",
    )
    args = parser.parse_args()

    if not os.path.isdir(args.dataset_root):
        raise FileNotFoundError(f"Dataset root '{args.dataset_root}' was not found.")

    results = []
    subject_sections = []
    skipped_items = []

    for subject_folder in iter_subject_folders(args.dataset_root, args.exclude_subjects):
        subject_name = os.path.basename(subject_folder)
        model_files = iter_model_files(subject_folder, args.model_name)

        if not model_files:
            skipped_items.append(f"{subject_name}: no model .txt files found to compare")
            continue

        subject_results = []
        for model_file_name in model_files:
            metrics, error = compare_subject_model(subject_folder, model_file_name)
            if error:
                skipped_items.append(f"{subject_name} / {model_file_name}: {error}")
                continue

            print(
                f"{subject_name} / {metrics['model_name']}: "
                f"hr error={metrics['hr_mae']:.4f} BPM, "
                f"hr accuracy={format_percent(metrics['hr_accuracy_percent'])}, "
                f"overall match={format_percent(metrics['overall_match_percent'])}"
            )
            subject_results.append(metrics)
            results.append(metrics)

        if subject_results:
            subject_sections.append(format_subject_section(subject_name, subject_results))
        else:
            skipped_items.append(f"{subject_name}: all model comparisons failed")

    model_summaries = aggregate_by_model(results)
    write_report(args.report_path, subject_sections, model_summaries, skipped_items)
    write_csv_summary(args.csv_path, model_summaries)

    print(f"\nWrote report to: {args.report_path}")
    print(f"Wrote CSV summary to: {args.csv_path}")
    if not results:
        print("No successful comparisons were found.")


if __name__ == "__main__":
    main()
