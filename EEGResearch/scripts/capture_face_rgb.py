#!/usr/bin/env python3
"""Capture mean face colour to a fixture, for validating POS against ECG.

Records **no video**. Each frame is reduced to three numbers and a quality
figure, then dropped, exactly as the live adapter does -- so the fixture is
safe to commit and read in a test.

Uses the product's own `FaceLocator` and `mean_rgb`, so a result here is a
statement about the shipped path, not a separate reimplementation of it.

Usage, with a camera attached:

    python scripts/capture_face_rgb.py --seconds 300 --out tests/fixtures/face_rgb_ecg.jsonl.gz

One JSON object per line:

    {"wall_start": "2026-08-07T15:04:11.882+01:00", "nominal_fps": 30.0}
    {"t": 12.34, "rgb": [181.2, 120.7, 110.4], "q": 0.93}

The first line is a header carrying the absolute start; every line after it is
a sample.

`t` is seconds since capture start, from the same `perf_counter` clock the
adapter stamps samples with, so samples are placed by timestamp rather than by
index -- matching how the shipped path works.

Uses `perf_counter`, not `time.monotonic()`: on Windows the latter resolves
only 15.625 ms, which can make an evenly-running camera look like it stutters.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402

from src.app.services.face_ingestion import (  # noqa: E402
    LUMA_WEIGHTS,
    WARMUP_SECONDS,
    OpenCvFrameSource,
)
from src.app.services.face_roi import FaceLocator, mean_rgb  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--fps", type=float, default=30.0)
    # Defaults from the adapter's own constant, not a repeated number, so this
    # script cannot drift from what production actually does.
    ap.add_argument("--warmup", type=float, default=WARMUP_SECONDS,
                    help="seconds of frames to discard before recording")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if out.suffix == ".gz" else open

    source = OpenCvFrameSource(camera_index=args.camera, fps=args.fps)
    locator = FaceLocator()
    print(f"camera {args.camera} open; locked: {source.locked}", flush=True)

    # Discard the camera's auto-exposure convergence, exactly as
    # FaceCaptureAdapter._capture_loop does. See WARMUP_SECONDS for why the
    # exposure can't just be locked instead.
    if args.warmup > 0:
        warm_until = time.perf_counter() + args.warmup
        while time.perf_counter() < warm_until:
            source.read()
        print(f"discarded {args.warmup:.0f}s of exposure warm-up", flush=True)

    frames = faces = written = 0
    started = time.perf_counter()

    # Wall clock, written once as the header. The watch's ECG export carries
    # an absolute start time too, so aligning the two recordings later is
    # arithmetic instead of manually marking when a reading began.
    wall_start = datetime.now().astimezone()
    last_report = started

    # Written incrementally, not buffered to the end, so an interrupted
    # capture still leaves the minutes recorded so far on disk.
    with opener(out, "wt", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps({"wall_start": wall_start.isoformat(),
                             "nominal_fps": args.fps}) + "\n")
        print(f"capture started at {wall_start.isoformat()}", flush=True)
        try:
            while time.perf_counter() - started < args.seconds:
                tick = time.perf_counter()
                frame = source.read()
                if frame is None:
                    continue
                frames += 1

                # Luma weights, matching the adapter exactly -- a flat channel
                # mean would be a different (redder) image and would break
                # detection the same way it would in production.
                gray = frame.astype(np.float32) @ LUMA_WEIGHTS
                box = locator.locate(gray)
                if box is None:
                    continue
                faces += 1

                sample = mean_rgb(frame, box)
                # `frame` is not referenced again below this line.
                if not sample.ok:
                    continue

                fh.write(json.dumps({
                    "t": round(tick - started, 4),
                    "rgb": [round(c, 4) for c in sample.rgb],
                    "q": round(sample.usable_fraction, 4),
                }) + "\n")
                written += 1

                now = time.perf_counter()
                if now - last_report >= 10.0:
                    elapsed = now - started
                    print(f"{elapsed:6.0f}s  {written} samples  "
                          f"{written / elapsed:.1f}/s measured  "
                          f"face {faces}/{frames}", flush=True)
                    last_report = now

                # Deliberately no sleep: `read()` already blocks until the
                # sensor has a frame, so the camera is the clock and a sleep on
                # top would only discard frames it overshoots.
        except KeyboardInterrupt:
            print("interrupted", flush=True)
        finally:
            source.release()

    elapsed = time.perf_counter() - started
    print(f"wrote {written} samples to {out} "
          f"({elapsed:.1f}s span, {written / elapsed:.2f} Hz measured, "
          f"face found on {faces}/{frames} frames)", flush=True)
    if frames and faces / frames < 0.9:
        print("WARNING: the face was lost on more than 10% of frames", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
