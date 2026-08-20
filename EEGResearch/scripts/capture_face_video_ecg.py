#!/usr/bin/env python3
"""Capture face frames for an ECG-referenced rPPG measurement.

**This is the one script here that writes images of a face to disk**, and it
exists because a video/ECG comparison cannot be answered without them. `capture_face_rgb.py`
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

# Seconds discarded before anything is written, for the reason
# `face_ingestion.WARMUP_SECONDS` states and measures: a camera's auto-exposure
# converges over the first few seconds and does so **enormously** relative to
# the signal -- mean green climbed 17% over ~5 s against a pulse under 1%, and
# the same recording scored confidence 0.05 with that ramp inside the window
# against 0.81 on a clean stretch after it.
#
# The live adapter has always discarded them. This script did not, and it is the
# one whose entire output exists to evaluate an rPPG model -- so a five-minute
# capture began with the single artefact most able to produce a confident wrong
# rate, in the first window or two the model would ever see.
#
# Imported, not restated, so the two cannot drift: if the ramp is ever measured
# again the capture and the live path move together.
from src.app.services.face_ingestion import WARMUP_SECONDS  # noqa: E402


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


class Gui:
    """Live preview for a capture. Opt-in (`--gui`).

    Different constraints from `verify_landmarks`' preview, because this script
    **does** write face images -- so the preview is not a privacy question here,
    it is a correctness one. A five-minute capture is expensive to redo and
    every way it can be wasted is invisible without a window: the face drifting
    out of the crop, a light behind the subject, the detector missing more
    frames than it finds, an exposure lock the driver quietly refused.

    What it must not do is become a second copy. It draws and drops, exactly
    like the capture loop's own frames; the only bytes that reach disk are the
    ones the loop was already writing, and a test asserts no new
    persisting call appears here.

    It also shows the crop the model will actually see, next to the full frame.
    Those are different pictures -- the crop is what gets stored, and judging
    framing from the full frame is how you discover afterwards that the model
    was fed a chin.
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
        """The warm-up, shown rather than a frozen window for 8 seconds.

        Distinct from `frame` on purpose: nothing is being written yet, and a
        preview that looked identical would have the subject believe the capture
        had started.
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

        cv2.circle(img, (18, 20), 7, self.BAD, -1)          # a record dot
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

        # The stored crop, upscaled, beside the frame. Nearest-neighbour so it
        # is obvious this is a 128x128 image rather than a smooth one.
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
        # is a brightness oscillation the model would read as a pulse, so an
        # unlocked capture is worth knowing about before analysing it, not
        # after.
        "exposure_locked": bool(getattr(source, "locked", False)),
        # What was discarded before `wall_start`. Recorded because "the first
        # frame is 8 s after the lens opened" is not recoverable from the data.
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

    # Discarded before anything is written and before the clock the frames are
    # stamped against starts, so `t` in the .jsonl is time since the first
    # *usable* frame rather than since the lens opened. The header carries the
    # warm-up separately, and `wall_start` is stamped after it -- an ECG
    # alignment search keys off that, and an 8 s constant offset silently baked
    # into one side of it is exactly the kind of error that search would absorb
    # as a lag rather than report.
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

    # Checked here rather than only inside the loop: aborting during the warm-up
    # means nothing has been written and nothing should be, so this returns
    # instead of falling through into a recording the subject just asked not to
    # make. `q` is the only interactive control this preview offers and it was
    # ignored for the whole eight seconds it is most likely to be pressed --
    # while the subject is still deciding whether the framing is right.
    if gui is not None and gui.aborted:
        source.release()
        gui.close()
        # The array is allocated *before* the warm-up, deliberately -- a disk
        # too full for the capture should be found before the subject sits
        # through eight seconds, not after. So aborting here leaves a
        # full-capacity zero-filled file behind, and returning without removing
        # it printed "nothing was recorded" over half a gigabyte of black
        # frames with no header to explain them: `truncate_npy`'s own warning,
        # in the one path that skips `truncate_npy`.
        #
        # It compounds, too. The reason `q` matters during warm-up is that the
        # subject is still adjusting the framing, which means aborting two or
        # three times in a row.
        mapping = getattr(frames, "_mmap", None)
        del frames
        if mapping is not None:
            mapping.close()
        pathlib.Path(f"{out}.npy").unlink(missing_ok=True)
        # No header either. `--delete` keeps one because frames existed and were
        # removed, and a cleaned-up capture with no trace is indistinguishable
        # from one nobody cleaned up. Nothing was captured here, so there is
        # nothing to have a record of.
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
                    # Logged, not dropped silently: a stretch with no face is a
                    # gap the analysis has to know about rather than discover as
                    # an unexplained discontinuity.
                    log.write(json.dumps({"t": round(now, 4), "ok": False}) + "\n")
                    missed += 1
                    if gui is not None:
                        # Drawn on the no-face path too, and the abort
                        # checked here. Without it the preview freezes
                        # on the last good frame *and* `q` does nothing
                        # until a face is found again -- which is
                        # exactly the situation someone wants to stop
                        # in, since a stretch with no face is the one
                        # failure they can see and fix.
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
                # Nearest-neighbour would alias; area averaging is what the
                # model's own preprocessing does.
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
        # Release the mapping before truncating — Windows refuses to shorten a
        # file that is still mapped. Nothing touches `frames` after this.
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
