#!/usr/bin/env python3
"""Capture face frames for an ECG-referenced rPPG measurement (Phase 12).

**This is the one script here that writes images of a face to disk**, and it
exists because Phase 12 cannot be answered without them. `capture_face_rgb.py`
reduces each frame to three numbers, which is why its fixture is committable —
and why it cannot evaluate a learned model, which needs the pixels that
reduction throws away.

The standing product rule is that no webcam footage is stored. This does not
break it, because it is not the product: a consenting **adult** recording
themselves for one measurement, held only as long as the analysis takes and
deleted afterwards, is a different activity from a platform retaining footage of
children. The distinction only holds if the second half is actually done, so
this script is built to make that hard to forget rather than easy.

What it writes
--------------
Not video. **128x128 face crops**, which is exactly what RhythmMamba consumes,
stored losslessly as raw uint8:

- `<out>.npy`      — (N, 128, 128, 3) uint8, where N is exactly the number of
                     frames captured. Allocated for the worst case and trimmed
                     on close: unwritten rows are zero-filled, and a run of
                     black frames at the end of a recording is not distinguishable
                     from footage once it is in the file
- `<out>.jsonl`    — one line per frame: elapsed seconds, face box, whether the
                     detector found one
- `<out>.json`     — header: wall-clock start, nominal fps, camera, exposure lock

Lossless on purpose. rPPG reads colour variation of well under one part in a
hundred, and every lossy codec is designed to discard exactly that — an MP4 of
this would look identical and measure nothing. Crops rather than full frames
because it is what the model takes and because 128x128 is ~1.5 MB/s where raw
640x480 is ~27 MB/s; five minutes is 440 MB rather than 8 GB.

The cost of storing crops is that the ROI choice is baked in. A later analysis
wanting a different region has to re-capture. That is the deliberate trade: the
alternative is storing considerably more of a person than the measurement needs.

Aligning with the ECG
---------------------
Nothing here talks to a watch. The header records the wall-clock start and every
frame carries a `perf_counter` offset, which is what lets an offset search line
the two up afterwards — the same approach `test_hrv_against_ecg.py` already uses
with its `PAIRS` offsets. Export the ECG separately and align in analysis.

`perf_counter`, not `time.monotonic()`: on Windows the latter resolves only
15.625 ms, which quantises a 30 fps interval into 2- and 3-tick steps and makes
a steady camera look like it was stuttering.

Usage
-----
    python scripts/capture_face_video_ecg.py --seconds 300 --out D:/rppg/session1

Then, once the analysis is done and its results are written up:

    python scripts/capture_face_video_ecg.py --delete D:/rppg/session1
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

CROP = 128

# The model's window is 160 frames; anything shorter cannot produce even one.
MIN_SECONDS = 30.0


def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def refuse_if_inside_repo(out: pathlib.Path) -> None:
    """A capture must not land anywhere git can reach it.

    Not a stylistic preference. Everything else this project writes is
    committable by design; this is the one artefact that must never be, and
    `git add -A` does not ask. A path check is the only guard that works
    regardless of who is running it or how tired they are.
    """
    root = repo_root().resolve()
    try:
        out.resolve().relative_to(root)
    except ValueError:
        return
    raise SystemExit(
        f"refusing to write inside the repository ({root}).\n"
        f"Face frames must not be committable. Choose a path outside it, on a "
        f"disk you will remember to clear:  --out D:/rppg/session1"
    )


def truncate_npy(path: pathlib.Path, rows: int) -> None:
    """Shrink a preallocated `.npy` to the rows that were actually written.

    The array is sized for the worst case and filled at whatever rate the camera
    manages, so the tail is normally unwritten — a dropped read or a frame with
    no face does not advance the cursor. `open_memmap` zero-fills, so those rows
    read back as **pure black frames**, which is indistinguishable from footage
    of a covered lens. A windowing script iterating the file would hand them to
    the model as captured data, and a step to black is exactly the kind of sharp
    non-physiological edge a frequency-domain estimator converts into a
    confident wrong number.

    The header records `frames_written` separately, so nothing is lost — but
    that leaves correctness resting on every future reader remembering to slice,
    and this is the codebase whose recurring rule is that absence must not be
    representable as data. Trimming makes the file mean what it says.

    In place, because the alternative — load, slice, re-save — needs a second
    copy of a file that runs to hundreds of megabytes.
    """
    import numpy as np

    with open(path, "r+b") as f:
        version = np.lib.format.read_magic(f)
        if version == (1, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_1_0(f)
            len_field = 2
        elif version == (2, 0):
            shape, fortran, dtype = np.lib.format.read_array_header_2_0(f)
            len_field = 4
        else:
            raise SystemExit(f"unexpected .npy version {version} in {path}")
        data_offset = f.tell()

        if rows > shape[0]:
            raise SystemExit(f"cannot grow {path}: {shape[0]} rows on disk, "
                             f"asked for {rows}")
        if rows == shape[0]:
            return

        # Rewrite the header in place. The declared header length stays as it
        # is and the shortfall goes into the padding numpy already allows, so
        # the data offset does not move and the rows below it are untouched.
        dims = ", ".join(str(d) for d in (rows, *shape[1:]))
        body = "{'descr': %s, 'fortran_order': %s, 'shape': (%s%s), }" % (
            repr(np.lib.format.dtype_to_descr(dtype)), fortran, dims,
            "," if len(shape) == 1 else "")
        pad = data_offset - len(np.lib.format.magic(*version)) - len_field \
            - len(body) - 1
        if pad < 0:                       # unreachable: rows <= shape[0]
            raise SystemExit(f"cannot rewrite the header of {path} in place")
        f.seek(len(np.lib.format.magic(*version)) + len_field)
        f.write((body + " " * pad + "\n").encode("latin1"))

        row_bytes = dtype.itemsize * int(np.prod(shape[1:], dtype="int64"))
        f.truncate(data_offset + rows * row_bytes)


def confirm(out: pathlib.Path, seconds: float) -> None:
    frames = int(seconds * 30)
    size_mb = frames * CROP * CROP * 3 / 1e6
    print(f"""
This records {seconds:.0f}s of 128x128 images of your face to:
    {out}.npy   (~{size_mb:.0f} MB)

Before starting, confirm all of these:
  * you are an adult recording yourself, not a child and not a third party
  * this is for one measurement, and you will delete it afterwards
  * the path above is outside the repository and outside any synced folder
    (OneDrive, Dropbox, iCloud) -- a sync client is a copy you did not decide
    to make

Type 'yes' to record: """, end="")
    if input().strip().lower() != "yes":
        raise SystemExit("not recording")


def capture(args) -> int:
    import numpy as np

    from src.app.services.face_ingestion import OpenCvFrameSource
    from src.app.services.face_roi import FaceLocator

    out = pathlib.Path(args.out)
    refuse_if_inside_repo(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.seconds < MIN_SECONDS:
        raise SystemExit(f"--seconds must be at least {MIN_SECONDS:.0f}; "
                         f"the model's window is 160 frames")
    if not args.yes:
        confirm(out, args.seconds)

    try:
        source = OpenCvFrameSource(camera_index=args.camera, fps=args.fps)
    except Exception as exc:                                   # noqa: BLE001
        print(f"could not open camera {args.camera}: {exc}", file=sys.stderr)
        return 2
    locator = FaceLocator()

    capacity = int(args.seconds * args.fps * 1.2) + 64
    frames = np.lib.format.open_memmap(
        f"{out}.npy", mode="w+", dtype=np.uint8, shape=(capacity, CROP, CROP, 3))

    header = {
        "wall_start": datetime.datetime.now(datetime.timezone.utc)
                              .astimezone().isoformat(timespec="milliseconds"),
        "nominal_fps": args.fps,
        "camera": args.camera,
        "crop": CROP,
        # Whether the driver accepted the exposure lock. Auto-exposure hunting
        # is a brightness oscillation the model would read as a pulse, so an
        # unlocked capture is worth knowing about before analysing it, not
        # after.
        "exposure_locked": bool(getattr(source, "locked", False)),
        "note": "128x128 face crops, lossless. Not video. Delete after analysis.",
    }
    if not header["exposure_locked"]:
        print("WARNING: exposure is not locked; auto-exposure can look like a "
              "pulse. Recording anyway, and the header records it.")

    print(f"recording {args.seconds:.0f}s -- sit still, look at the camera, "
          f"breathe normally")
    written = missed = 0
    t0 = time.perf_counter()
    try:
        with open(f"{out}.jsonl", "w", encoding="utf-8") as log:
            while time.perf_counter() - t0 < args.seconds and written < capacity:
                frame = source.read()
                now = time.perf_counter() - t0
                if frame is None:
                    missed += 1
                    continue
                gray = frame.astype("float32") @ np.array([0.299, 0.587, 0.114])
                box = locator.locate(gray)
                if box is None:
                    # Logged, not dropped silently: a stretch with no face is a
                    # gap the analysis has to know about rather than discover as
                    # an unexplained discontinuity.
                    log.write(json.dumps({"t": round(now, 4), "ok": False}) + "\n")
                    missed += 1
                    continue
                x, y, w, h = box
                crop = frame[y:y + h, x:x + w]
                if crop.size == 0:
                    missed += 1
                    continue
                # Nearest-neighbour would alias; area averaging is what the
                # model's own preprocessing does.
                import cv2                                     # noqa: PLC0415
                frames[written] = cv2.resize(crop, (CROP, CROP),
                                             interpolation=cv2.INTER_AREA)
                log.write(json.dumps({"t": round(now, 4), "ok": True,
                                      "box": [int(v) for v in box]}) + "\n")
                written += 1
                if written % (int(args.fps) * 30) == 0:
                    print(f"  {now:5.0f}s  {written} frames, {missed} without a face")
    finally:
        source.release()
        frames.flush()
        # Release the mapping before truncating — Windows refuses to shorten a
        # file that is still mapped. Nothing touches `frames` after this.
        mapping = getattr(frames, "_mmap", None)
        del frames
        if mapping is not None:
            mapping.close()
        truncate_npy(pathlib.Path(f"{out}.npy"), written)

    header.update(frames_written=written, frames_missed=missed,
                  measured_fps=round(written / max(time.perf_counter() - t0, 1e-9), 3))
    pathlib.Path(f"{out}.json").write_text(json.dumps(header, indent=2), encoding="utf-8")

    print(f"""
wrote {written} frames ({header['measured_fps']} fps measured), {missed} without a face
    {out}.npy    {os.path.getsize(f'{out}.npy')/1e6:.0f} MB
    {out}.jsonl
    {out}.json

The .npy is images of your face. When the analysis is written up, delete it:
    python {pathlib.Path(__file__).name} --delete {out}

Keep the derived per-window results and the ECG reference, not the frames --
the way tests/fixtures/FACE_RPPG_ECG.md does, and say where the source went.""")
    return 0


def delete(target: str) -> int:
    out = pathlib.Path(target)
    gone = []
    for suffix in (".npy", ".jsonl"):
        p = pathlib.Path(f"{out}{suffix}")
        if p.exists():
            p.unlink()
            gone.append(p.name)
    # The header stays. It is metadata with no face in it, and it is the record
    # that a capture happened and was cleaned up -- which is worth more than the
    # tidiness of removing it.
    print(f"deleted: {', '.join(gone) if gone else 'nothing found'}")
    header = pathlib.Path(f"{out}.json")
    if header.exists():
        meta = json.loads(header.read_text(encoding="utf-8"))
        meta["frames_deleted_at"] = datetime.datetime.now(
            datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
        header.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        print(f"kept {header.name}, stamped with the deletion time")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", help="path prefix, OUTSIDE the repository")
    ap.add_argument("--seconds", type=float, default=300.0)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--yes", action="store_true", help="skip the confirmation")
    ap.add_argument("--delete", metavar="PREFIX",
                    help="delete a capture's frames, keeping its header")
    args = ap.parse_args()

    if args.delete:
        return delete(args.delete)
    if not args.out:
        ap.error("--out is required (or --delete)")
    return capture(args)


if __name__ == "__main__":
    raise SystemExit(main())
