#!/usr/bin/env python3
"""Capture mean face colour to a fixture, for validating POS against ECG.

Deliberately records **no video**. Each frame is reduced to three numbers and a
quality figure and then dropped, exactly as the live adapter does — so this
script cannot become a way to accumulate footage of a face, and the fixture it
writes is safe to commit and to read in a test.

That also makes it the honest thing to validate against: it exercises the same
`FaceLocator` and `mean_rgb` the product uses, so a result here is a statement
about the shipped path rather than about an offline reimplementation of it.

Usage, with a camera attached:

    python scripts/capture_face_rgb.py --seconds 300 --out tests/fixtures/face_rgb_ecg.jsonl.gz

One JSON object per line:

    {"wall_start": "2026-08-07T15:04:11.882+01:00", "nominal_fps": 30.0}
    {"t": 12.34, "rgb": [181.2, 120.7, 110.4], "q": 0.93}

The first line is a header carrying the absolute start; every line after it is a
sample.

`t` is seconds since capture start, from the same `perf_counter` clock the
adapter stamps samples with, and it is the whole point of the format: samples
are placed by these stamps rather than by their index, in analysis and in the
shipped path alike.

`perf_counter` specifically. `time.monotonic()` on Windows resolves only
15.625 ms, which is coarse enough to quantise a 30 fps interval into 2- and
3-tick steps and to make an evenly-running camera look like it was stuttering.
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
    # Defaulted from the adapter's constant rather than repeating the number.
    # The warm-up, the unpaced loop and the luma weights all landed in this
    # script before the shipped path, and each divergence costs the script the
    # one property that makes it worth running: that a result here describes
    # production. Sharing the constant means this one cannot drift.
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
    # FaceCaptureAdapter._capture_loop does. See WARMUP_SECONDS for the
    # measurement and why the exposure cannot simply be locked instead.
    if args.warmup > 0:
        warm_until = time.perf_counter() + args.warmup
        while time.perf_counter() < warm_until:
            source.read()
        print(f"discarded {args.warmup:.0f}s of exposure warm-up", flush=True)

    frames = faces = written = 0
    started = time.perf_counter()

    # Wall clock as well as the perf_counter base, written as the first line.
    #
    # This is what makes the ECG alignment self-contained. The watch's export
    # carries an absolute `Created time`, so with an absolute start here the
    # offset between the two recordings is arithmetic -- nobody has to mark the
    # moment a reading began, and the alignment stops depending on how quickly
    # someone typed. `perf_counter` stamps the samples because it is monotonic
    # and fine-grained; the wall clock appears once, only to anchor it.
    wall_start = datetime.now().astimezone()
    last_report = started

    # Written incrementally rather than buffered to the end, so a capture
    # interrupted at minute four still leaves four usable minutes on disk.
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

                # Luma, matching the adapter exactly. A flat channel mean is a
                # different image -- brighter in red, which is most of a face --
                # and detection is precisely where the two would diverge. The
                # value of this script is that it runs the shipped path; a
                # different grayscale quietly makes that untrue.
                gray = frame.astype(np.float32) @ LUMA_WEIGHTS
                box = locator.locate(gray)
                if box is None:
                    continue
                faces += 1

                sample = mean_rgb(frame, box)
                # `frame` is not referenced again. Nothing below this line has
                # access to an image.
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
                # sensor has a frame, so the camera is the clock and a second
                # one on top only discards frames it overshoots.
                #
                # (An earlier revision blamed the sleep for a bimodal 31/47 ms
                # interval pattern. It was not the cause -- those are 2 and 3
                # ticks of `monotonic`'s 15.625 ms resolution, and the pattern
                # went away with `perf_counter`, not with the sleep. The reason
                # above is the real one and it stands on its own.)
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
