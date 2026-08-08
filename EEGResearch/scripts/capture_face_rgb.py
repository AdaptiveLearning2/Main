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

    {"t": 12.34, "rgb": [181.2, 120.7, 110.4], "q": 0.93}

`t` is seconds since capture start, from the same monotonic clock the adapter
uses, and it is the whole point of the format. A webcam asked for 30 fps does
not deliver 30 evenly spaced frames — measured here, intervals were bimodal at
31 ms and 47 ms — so the samples are placed by these stamps rather than by their
index, both in analysis and in the shipped path.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.app.services.face_ingestion import OpenCvFrameSource  # noqa: E402
from src.app.services.face_roi import FaceLocator, mean_rgb  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    opener = gzip.open if out.suffix == ".gz" else open

    source = OpenCvFrameSource(camera_index=args.camera, fps=args.fps)
    locator = FaceLocator()
    print(f"camera {args.camera} open; locked: {source.locked}", flush=True)

    frames = faces = written = 0
    started = time.monotonic()
    last_report = started

    # Written incrementally rather than buffered to the end, so a capture
    # interrupted at minute four still leaves four usable minutes on disk.
    with opener(out, "wt", encoding="utf-8", newline="\n") as fh:
        try:
            while time.monotonic() - started < args.seconds:
                tick = time.monotonic()
                frame = source.read()
                if frame is None:
                    continue
                frames += 1

                gray = frame.mean(axis=2).astype("uint8")
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

                now = time.monotonic()
                if now - last_report >= 10.0:
                    elapsed = now - started
                    print(f"{elapsed:6.0f}s  {written} samples  "
                          f"{written / elapsed:.1f}/s measured  "
                          f"face {faces}/{frames}", flush=True)
                    last_report = now

                # Deliberately no sleep. Pacing the loop to a nominal 33.3 ms
                # made things worse, not better: against this camera's native
                # ~31 ms cadence it produced a beat pattern -- 78% of intervals
                # at 31 ms and 21% at 47 ms, one skipped frame each time the
                # sleep overshot the next exposure. `read()` already blocks
                # until a frame is ready, so the camera paces the loop, and the
                # frames we would have skipped are free signal.
        except KeyboardInterrupt:
            print("interrupted", flush=True)
        finally:
            source.release()

    elapsed = time.monotonic() - started
    print(f"wrote {written} samples to {out} "
          f"({elapsed:.1f}s span, {written / elapsed:.2f} Hz measured, "
          f"face found on {faces}/{frames} frames)", flush=True)
    if frames and faces / frames < 0.9:
        print("WARNING: the face was lost on more than 10% of frames", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
