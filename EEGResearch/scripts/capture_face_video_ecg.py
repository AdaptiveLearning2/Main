#!/usr/bin/env python3
"""Capture face frames for an ECG-referenced rPPG measurement.

**The one script here that writes images of a face to disk.** A learned model
needs the actual pixels, which `capture_face_rgb.py` throws away when it
reduces each frame to three numbers.

The product's rule is that no webcam footage is stored. This doesn't break
that rule -- it's not the product, it's a consenting **adult** recording
themselves for one measurement, kept only as long as the analysis takes and
deleted afterwards. That second half (deleting it) is the part this script is
built to make hard to skip.

What it writes
--------------
Not video. **128x128 face crops** (what RhythmMamba consumes), stored
losslessly as raw uint8:

- `<out>.npy`      -- (N, 128, 128, 3) uint8, N = frames actually captured.
                     Allocated for the worst case and trimmed on close, since
                     an untrimmed tail reads back as black frames
                     indistinguishable from real footage.
- `<out>.jsonl`    -- one line per frame: elapsed seconds, face box, whether the
                     detector found one
- `<out>.json`     -- header: wall-clock start, nominal fps, camera, exposure lock

Lossless because rPPG reads colour variation under 1%, and every lossy codec is
designed to discard exactly that variation. Crops rather than full frames
because that's what the model takes, and because 128x128 is ~1.5 MB/s versus
raw 640x480 at ~27 MB/s (five minutes: 440 MB vs 8 GB).

Storing crops bakes in the ROI choice -- a later analysis wanting a different
region has to re-capture. Accepted trade: the alternative is storing far more
of a person than the measurement needs.

Aligning with the ECG
---------------------
Nothing here talks to a watch. The header records the wall-clock start, and
every frame carries a `perf_counter` offset, so an offset search can line the
two recordings up afterwards (same approach as `test_hrv_against_ecg.py`'s
`PAIRS`). Export the ECG separately and align during analysis.

Uses `perf_counter`, not `time.monotonic()`: on Windows the latter resolves
only 15.625 ms, which can make a steady camera look like it's stuttering.

Usage
-----
    python scripts/capture_face_video_ecg.py --seconds 300 --out D:/rppg/session1

Then, once the analysis is done and written up:

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

# The model's window is 160 frames; anything shorter can't produce even one.
MIN_SECONDS = 30.0

# Seconds discarded before anything is written. A camera's auto-exposure ramps
# for the first few seconds and swamps the pulse signal while it does (mean
# green moved 17% over ~5s against a pulse under 1%; see
# `face_ingestion.WARMUP_SECONDS` for the full measurement). Imported rather
# than restated so this can't drift from what the live adapter does.
from src.app.services.face_ingestion import WARMUP_SECONDS  # noqa: E402


def repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def refuse_if_inside_repo(out: pathlib.Path) -> None:
    """A capture must not land anywhere git can reach it.

    This is the one artefact here that must never be committed, and
    `git add -A` doesn't ask. A path check is the guard that works no matter
    who's running the script.
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

    The array is sized for the worst case, so unwritten tail rows are
    zero-filled and read back as pure black frames -- indistinguishable from a
    covered lens. A windowing script would feed those to the model as real
    data, and a hard step to black is exactly what a frequency-domain
    estimator turns into a confident wrong rate. Trimming makes the file mean
    what it says instead of relying on every reader remembering to slice it.

    Done in place because loading, slicing and re-saving would need a second
    copy of a file that can run to hundreds of megabytes.
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

        # Rewrite the header in place using numpy's existing padding, so the
        # data offset doesn't move and the rows below stay untouched.
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


class Gui:
    """Live preview for a capture. Opt-in (`--gui`).

    Here the preview is a correctness aid, not a privacy question, since this
    script already writes face images. A five-minute capture is expensive to
    redo, and problems like the face drifting out of frame or exposure lock
    failing are otherwise invisible until it's too late.

    Draws and drops frames like the capture loop does -- never a second
    persisting call, which a test asserts. It also shows the actual stored
    crop next to the full frame, since judging framing from the full frame can
    hide that the model was fed a chin.
    """

    WINDOW = "capture_face_video_ecg -- RECORDING"

    OK = (80, 200, 80)
    BAD = (60, 60, 235)
    WATCH = (40, 200, 235)
    DIM = (190, 190, 190)

    def __init__(self) -> None:
        import cv2                                             # noqa: PLC0415
        self._cv2 = cv2
        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW, 900, 560)
        self.aborted = False

    def close(self) -> None:
        try:
            self._cv2.destroyWindow(self.WINDOW)
        except Exception:                                      # noqa: BLE001
            pass

    def _text(self, img, s, org, colour=(255, 255, 255), scale=0.55, weight=1):
        self._cv2.putText(img, s, org, self._cv2.FONT_HERSHEY_SIMPLEX,
                          scale, colour, weight, self._cv2.LINE_AA)

    def warming(self, frame, *, remaining: float) -> None:
        """The warm-up, shown instead of a frozen window for 8 seconds.

        Drawn differently from `frame` so the subject doesn't mistake this for
        the capture starting -- nothing is being written yet.
        """
        cv2 = self._cv2
        img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        h, w = img.shape[0], img.shape[1]
        over = img.copy()
        cv2.rectangle(over, (0, 0), (w, 58), (0, 0, 0), -1)
        cv2.addWeighted(over, 0.55, img, 0.45, 0, img)
        self._text(img, f"WARMING UP  {max(0.0, remaining):.0f}s",
                   (14, 26), self.WATCH, 0.7, 2)
        self._text(img, "auto-exposure settling; nothing is being written yet",
                   (14, 48), self.DIM, 0.5)
        cv2.imshow(self.WINDOW, img)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            self.aborted = True

    def frame(self, frame, box, crop, *, elapsed, total, written, missed,
              exposure_locked) -> None:
        """One frame. RGB in; `imshow` wants BGR, so it is converted here."""
        import numpy as np                                     # noqa: PLC0415
        cv2 = self._cv2
        img = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        h, w = img.shape[0], img.shape[1]

        if box is not None:
            x, y, bw, bh = box
            cv2.rectangle(img, (x, y), (x + bw, y + bh), self.OK, 2)
        else:
            self._text(img, "NO FACE", (w // 2 - 60, h // 2), self.BAD, 1.0, 2)

        over = img.copy()
        cv2.rectangle(over, (0, 0), (w, 58), (0, 0, 0), -1)
        cv2.rectangle(over, (0, h - 30), (w, h), (0, 0, 0), -1)
        cv2.addWeighted(over, 0.55, img, 0.45, 0, img)

        cv2.circle(img, (18, 20), 7, self.BAD, -1)          # record dot
        self._text(img, f"RECORDING  {elapsed:5.1f} / {total:.0f}s",
                   (34, 26), (255, 255, 255), 0.6, 2)
        self._text(img, f"{written} frames written, {missed} without a face",
                   (34, 48), self.DIM, 0.5)
        if not exposure_locked:
            self._text(img, "exposure NOT locked -- may look like a pulse",
                       (14, h - 10), self.WATCH, 0.5)
        else:
            self._text(img, "q to stop early (the capture is kept)",
                       (14, h - 10), self.DIM, 0.5)

        bar = int(w * max(0.0, min(1.0, elapsed / total))) if total else 0
        cv2.rectangle(img, (0, 58), (bar, 63), self.BAD, -1)

        # The stored crop, upscaled beside the frame. Nearest-neighbour on
        # purpose, so it looks like the 128x128 image it actually is.
        if crop is not None:
            shown = cv2.resize(cv2.cvtColor(crop, cv2.COLOR_RGB2BGR),
                               (h // 2, h // 2), interpolation=cv2.INTER_NEAREST)
            panel = np.zeros((h, shown.shape[1], 3), dtype=img.dtype)
            panel[0:shown.shape[0], 0:shown.shape[1]] = shown
            self._text(panel, "what is stored", (8, shown.shape[0] + 22),
                       self.DIM, 0.5)
            self._text(panel, f"{CROP}x{CROP}", (8, shown.shape[0] + 42),
                       self.DIM, 0.5)
            img = np.hstack([img, panel])

        cv2.imshow(self.WINDOW, img)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            self.aborted = True


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
        # looks like a pulse to the model, so this is worth knowing before
        # analysis, not after.
        "exposure_locked": bool(getattr(source, "locked", False)),
        # Seconds discarded before `wall_start`. Recorded because that offset
        # isn't recoverable from the data itself.
        "warmup_seconds": 0.0,
        "note": "128x128 face crops, lossless. Not video. Delete after analysis.",
    }
    if not header["exposure_locked"]:
        print("WARNING: exposure is not locked; auto-exposure can look like a "
              "pulse. Recording anyway, and the header records it.")

    gui = None
    if getattr(args, "gui", False):
        try:
            gui = Gui()
        except Exception as exc:                               # noqa: BLE001
            print(f"no GUI available ({exc}); recording without the preview",
                  file=sys.stderr)

    # Warm-up happens before the clock starts, so `t` in the .jsonl is time
    # since the first usable frame, not since the lens opened. `wall_start` is
    # stamped after warm-up for the same reason -- an 8s offset baked silently
    # into one side would show up as alignment error against the ECG, not as
    # a reported bug.
    if not args.no_warmup:
        print(f"warming up {WARMUP_SECONDS:.0f}s (auto-exposure ramp -- these "
              f"frames are read and dropped)")
        warm_until = time.perf_counter() + WARMUP_SECONDS
        while time.perf_counter() < warm_until:
            frame = source.read()
            if gui is not None and frame is not None:
                gui.warming(frame, remaining=warm_until - time.perf_counter())
                if gui.aborted:
                    break

    # Checked here, not just inside the loop: aborting during warm-up means
    # nothing should be recorded, so this must return instead of falling
    # through into a capture the subject just declined.
    if gui is not None and gui.aborted:
        source.release()
        gui.close()
        # The array is allocated before warm-up (so a full disk is caught
        # before the subject waits through it), which means an abort here
        # would otherwise leave a full-capacity zero-filled file -- half a
        # gigabyte of black frames with no header. Delete it instead of
        # relying on `truncate_npy`, which this path skips.
        mapping = getattr(frames, "_mmap", None)
        del frames
        if mapping is not None:
            mapping.close()
        pathlib.Path(f"{out}.npy").unlink(missing_ok=True)
        # No header either -- nothing was captured, so there's nothing to
        # record. `--delete` keeps a header because it removes real frames.
        print("aborted during warm-up; nothing was recorded")
        return 1

    header["wall_start"] = (datetime.datetime.now(datetime.timezone.utc)
                            .astimezone().isoformat(timespec="milliseconds"))
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
                    # Logged, not dropped silently, so a no-face stretch is a
                    # known gap in analysis rather than an unexplained one.
                    log.write(json.dumps({"t": round(now, 4), "ok": False}) + "\n")
                    missed += 1
                    if gui is not None:
                        # Drawn on the no-face path too, with the abort check,
                        # or the preview freezes and `q` stops working exactly
                        # when someone most wants to stop.
                        gui.frame(frame, None, None, elapsed=now,
                                  total=args.seconds, written=written,
                                  missed=missed,
                                  exposure_locked=header["exposure_locked"])
                        if gui.aborted:
                            print("\nstopped early; keeping the frames captured so far")
                            break
                    continue
                x, y, w, h = box
                crop = frame[y:y + h, x:x + w]
                if crop.size == 0:
                    missed += 1
                    continue
                # Area averaging, matching the model's own preprocessing;
                # nearest-neighbour would alias.
                import cv2                                     # noqa: PLC0415
                frames[written] = cv2.resize(crop, (CROP, CROP),
                                             interpolation=cv2.INTER_AREA)
                log.write(json.dumps({"t": round(now, 4), "ok": True,
                                      "box": [int(v) for v in box]}) + "\n")
                written += 1
                if gui is not None:
                    gui.frame(frame, box, frames[written - 1], elapsed=now,
                              total=args.seconds, written=written,
                              missed=missed,
                              exposure_locked=header["exposure_locked"])
                    if gui.aborted:
                        print("\nstopped early; keeping the frames captured "
                              "so far")
                        break
                if written % (int(args.fps) * 30) == 0:
                    print(f"  {now:5.0f}s  {written} frames, {missed} without a face")
    finally:
        source.release()
        if gui is not None:
            gui.close()
        frames.flush()
        # Release the mapping before truncating -- Windows refuses to shorten
        # a file that's still mapped.
        mapping = getattr(frames, "_mmap", None)
        del frames
        if mapping is not None:
            mapping.close()
        truncate_npy(pathlib.Path(f"{out}.npy"), written)

    header["warmup_seconds"] = 0.0 if args.no_warmup else WARMUP_SECONDS
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
    # The header stays: it has no face in it, and it's the record that a
    # capture happened and was cleaned up.
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
    ap.add_argument("--no-warmup", action="store_true",
                    help="skip the auto-exposure discard. Only for a camera "
                         "whose exposure is genuinely fixed -- the ramp is "
                         "17%% of mean green against a pulse under 1%%.")
    ap.add_argument("--gui", action="store_true",
                    help="live preview: the face box, the stored crop, and the "
                         "counters. Draws and drops; writes nothing extra.")
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
