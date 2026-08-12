#!/usr/bin/env python3
"""Confirm the face-mesh index table against a real face (Phase 11, step 1).

`face_landmarks.MEDIAPIPE_INDICES` maps mesh indices onto named face parts, and
**it has never been checked against hardware**: MediaPipe 1.0.0 ships no
canonical mesh data file and there is no camera in CI. `check_topology` catches
a grossly wrong index on the first frame, but it cannot catch the one mistake
that matters most -- a left/right swap -- because a mirrored face satisfies
every relation it tests.

That check needs a person, a webcam and about two minutes. This script is that
check, so it is one command rather than twenty minutes of assembling a camera
loop first. A verification step that is expensive to run is a verification step
that does not get run.

Deliberately records **no video**, like `capture_face_rgb.py`. Each frame is
reduced to angles and offsets and dropped. Nothing is written to disk at all.

    python scripts/verify_landmarks.py

Needs the `face` extra plus MediaPipe:

    pip install -e ".[face]" mediapipe

What it decides
---------------
Three prompted steps, each with an automatic verdict, so the outcome is a
sentence rather than a wall of numbers to interpret:

1. **Square on** -- the pose should read near zero on all three angles. Failing
   here means the canonical model or the pose maths is wrong, *or* the index
   table is mirrored: both make the correspondence unfittable by a rotation and
   both surface as `implausible_pose`. Confirmed against hardware 2026-08-12,
   where a wrong-handed model refused 120 frames of 120.
2. **Eyes hard left, head still** -- `gaze.x` must go *positive*. The frame is
   not mirrored, so the subject's own left is the image right, and `gaze.x` is
   measured in image x (`_eye_offset` divides by an absolute width, so there is
   no per-eye sign flip). This checks that the iris tracks and that the sign
   convention holds. **It cannot detect a left/right swap** -- both eyes are
   averaged in image coordinates, so permuting the labels returns the identical
   number. It claimed to, and would have prescribed swapping a correct table.
3. **Turn your head left** -- `yaw` must go *positive*, same reason: turning
   toward your own left is turning toward the image right. This is the step
   that adjudicates the mapping, because yaw comes from a fit against a model
   that has a handedness. A mirrored table makes that fit impossible rather
   than wrong, so it surfaces as a refusal.
   A *modest* turn: the bar is 10 degrees, and near profile the landmark set
   stops being usable. Past roughly 70 degrees the nose tip crosses the far eye
   corner, `check_topology` refuses the frame as `nose_outside_eyes`, and the
   step measures nothing -- correctly, but it is not what the step is asking.

Steps 2 and 3 are separate because they test different things over different
regions of the table -- iris tracking and image-x sign for one, the pose fit's
handedness for the other -- and neither says anything about the other.

**Do not judge direction from another app's camera preview.** Video-call
software usually mirrors the image for the person on screen; this reads the raw
frame, which is not mirrored. "My left" below always means the left side of your
own body.
"""

# ASCII only in everything this prints. A Windows console defaults to cp1252,
# which cannot encode box-drawing characters or arrows at all -- and the failure
# is a UnicodeEncodeError traceback on the first line of output, before the
# check has run. For a script whose entire premise is "a check that is expensive
# to run is a check nobody runs", crashing on the project's first-class dev
# platform would be worse than the docstring it replaces. It also keeps the
# output readable when piped to a file.

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.app.services.face_geometry import gaze, head_pose      # noqa: E402
from src.app.services.face_landmarks import FaceMeshLandmarker  # noqa: E402

# How far a value has to move to count as a deliberate look rather than as
# noise. Gaze is -1..1 across the eye opening and a hard look reaches most of
# the way; 0.15 is comfortably above landmark wobble and far below what the
# instruction asks for, so a *pass* is unambiguous and a *mirror* is too.
GAZE_THRESHOLD = 0.15
YAW_THRESHOLD = 10.0

# Square-on tolerances. Loose: nobody sits perfectly square, and this step is
# checking that the maths is sane, not that the subject is a tripod.
SQUARE_ON_TOLERANCE = 20.0


def _collect(landmarker, source, seconds: float, width: int, height: int,
             reasons: dict | None = None) -> list:
    """Poses and gazes over a few seconds, with the refusals counted.

    `reasons`, when given, is tallied with why each empty frame was empty --
    `no_face` from the detector versus a `check_topology` refusal. Without it a
    step that measured nothing reports no refusals at all, because the existing
    tallies are built from *usable* samples and a refused frame produces none.
    """
    samples = []
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        frame = source.read()
        if frame is None:
            continue
        named = landmarker.locate(frame, width, height)
        if not named and reasons is not None:
            why = getattr(landmarker, "last_reason", None) or "no_face"
            reasons[why] = reasons.get(why, 0) + 1
        pose, gz = (head_pose(named), gaze(named)) if named else (None, None)
        if not named:
            continue
        samples.append((pose, gz))
    return samples


def _median(values):
    usable = [v for v in values if v is not None]
    return statistics.median(usable) if usable else None


def _step(name: str, instruction: str, landmarker, source, seconds, w, h) -> dict:
    print(f"\n-- {name} --")
    print(f"   {instruction}")
    for count in range(3, 0, -1):
        print(f"   starting in {count}...", end="\r", flush=True)
        time.sleep(1.0)
    print(f"   measuring for {seconds:.0f}s...      ")

    empty: dict[str, int] = {}
    samples = _collect(landmarker, source, seconds, w, h, reasons=empty)
    poses = [p for p, _ in samples]
    gazes = [g for _, g in samples]
    measured = {
        "frames": len(samples),
        "yaw": _median([p.yaw for p in poses]),
        "pitch": _median([p.pitch for p in poses]),
        "roll": _median([p.roll for p in poses]),
        "gaze_x": _median([g.x for g in gazes]),
        "gaze_y": _median([g.y for g in gazes]),
        # Why frames were refused, so a step that measured nothing says which
        # gate stopped it rather than just reporting no data.
        "pose_refusals": sorted({p.rejected_by for p in poses if p.rejected_by}),
        "gaze_refusals": sorted({g.rejected_by for g in gazes if g.rejected_by}),
    }
    print(f"   {measured['frames']} usable frames; "
          f"yaw={measured['yaw']}, pitch={measured['pitch']}, "
          f"roll={measured['roll']}, gaze=({measured['gaze_x']}, {measured['gaze_y']})")
    if measured["pose_refusals"] or measured["gaze_refusals"]:
        print(f"   refusals: pose={measured['pose_refusals'] or '-'} "
              f"gaze={measured['gaze_refusals'] or '-'}")
    if empty:
        # Separately from the two above, which are computed over frames that
        # produced a face. These are the frames that produced none, and why --
        # a detector miss reads very differently from a topology refusal.
        print("   empty frames: "
              + ", ".join(f"{why}={n}" for why, n in sorted(empty.items())))
    return measured


def _verdict(square, eyes, head) -> int:
    print("\n-- verdict --")
    failures = 0

    def report(ok: bool, line: str) -> None:
        nonlocal failures
        print(f"   {'PASS' if ok else 'FAIL'}  {line}")
        failures += 0 if ok else 1

    if not square["frames"]:
        print("   INCONCLUSIVE  no face was measured at all - check the camera, "
              "the lighting, and that MediaPipe is installed")
        return 2

    square_ok = all(v is not None and abs(v) <= SQUARE_ON_TOLERANCE
                    for v in (square["yaw"], square["pitch"], square["roll"]))
    report(square_ok, f"square on reads near zero "
                      f"(yaw={square['yaw']}, pitch={square['pitch']}, roll={square['roll']})")
    if not square_ok:
        print("         -> the canonical model or the pose maths is wrong, not "
              "the index table. The two steps below cannot be trusted until "
              "this passes.")

    gx = eyes["gaze_x"]
    if gx is None:
        report(False, "looking left produced no gaze reading")
    elif gx >= GAZE_THRESHOLD:
        report(True, f"looking left drives gaze.x positive ({gx})")
    elif gx <= -GAZE_THRESHOLD:
        report(False, f"looking left drives gaze.x NEGATIVE ({gx})")
        print("         -> the iris is tracking the wrong way in image x. Not "
              "a left/right label swap: gaze averages both eyes in image "
              "coordinates and cannot see one. Suspect the iris indices, or a "
              "frame that arrived mirrored.")
    else:
        report(False, f"gaze.x barely moved ({gx}) - look harder, or the iris "
                      f"landmarks are not tracking")

    # Step 3 is skipped when step 1 failed, and step 2 is not. That is not an
    # inconsistency: `gaze` is computed from the eye and iris landmarks alone
    # and never touches the rotation fit, so a wrong canonical model or a wrong
    # Euler extraction cannot flip its sign. `yaw` comes straight out of that
    # fit, so it is meaningless until step 1 passes -- and printing PASS beside
    # a warning that says not to trust it is how someone reads the wrong half
    # of a failed run.
    if not square_ok:
        print("   SKIP  turning left: yaw comes from the pose fit, which step 1 "
              "says is wrong")
        return 1

    yaw = head["yaw"]
    if yaw is None:
        report(False, "turning left produced no pose reading")
    elif yaw >= YAW_THRESHOLD:
        report(True, f"turning left drives yaw positive ({yaw})")
    elif yaw <= -YAW_THRESHOLD:
        report(False, f"turning left drives yaw NEGATIVE ({yaw})")
        print("         -> the pose fit is inverted about the vertical axis. "
              "A mirrored index table would refuse at step 1 rather than reach "
              "here, so suspect CANONICAL_FACE's x signs or the Euler "
              "recovery, not the mapping.")
    else:
        report(False, f"yaw barely moved ({yaw}) - turn further, or the "
                      f"outline landmarks are not tracking")

    print()
    if failures == 0:
        print("   The index table is confirmed against a real face. Record the "
              "date in CLAUDE.md and the landmark path can be wired into the "
              "capture loop.")
    else:
        print(f"   {failures} check(s) failed. Do not wire this into the "
              f"capture loop: gaze and pose reach face_signals and four "
              f"surfaces render them, so a mirrored mapping would be published "
              f"as fact.")
    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--camera", type=int, default=0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--seconds", type=float, default=4.0,
                    help="measurement window per step")
    args = ap.parse_args()

    try:
        from src.app.services.face_ingestion import OpenCvFrameSource
    except Exception as exc:                                   # noqa: BLE001
        print(f"could not import the camera source: {exc}\n"
              f"install the face extra:  pip install -e \".[face]\"", file=sys.stderr)
        return 2

    try:
        landmarker = FaceMeshLandmarker()
    except Exception as exc:                                   # noqa: BLE001
        print(f"could not start MediaPipe Face Mesh: {exc}\n"
              f"install it:  pip install mediapipe", file=sys.stderr)
        return 2

    # The constructor opens the camera; there is no separate open().
    # Wrapped like the two setup steps above it. This is the likeliest failure
    # of the three in practice -- no webcam, permission denied, or the camera
    # held by a video call -- and it was the only one that produced a raw
    # traceback instead of a sentence saying what to do.
    try:
        source = OpenCvFrameSource(camera_index=args.camera, fps=args.fps)
    except Exception as exc:                                   # noqa: BLE001
        print(f"could not open camera {args.camera}: {exc}",
              "check it is connected, not in use by another app, and that this "
              "terminal has camera permission; --camera N selects another",
              sep="\n", file=sys.stderr)
        return 2
    try:
        first = None
        deadline = time.perf_counter() + 5.0
        while first is None and time.perf_counter() < deadline:
            first = source.read()
        if first is None:
            print("no frames from the camera", file=sys.stderr)
            return 2
        height, width = first.shape[0], first.shape[1]
        print(f"camera {args.camera}: {width}x{height}; "
              f"exposure locked: {source.locked}")
        print("No video is recorded. Each frame becomes angles and is dropped.")
        print("Ignore any mirrored preview in other apps - this reads the raw "
              "frame.\n'left' below always means the left side of YOUR body.")

        square = _step("1/3 square on",
                       "Look straight at the camera and hold still.",
                       landmarker, source, args.seconds, width, height)
        eyes = _step("2/3 eyes left",
                     "Keep your head still and look as far LEFT as you can.",
                     landmarker, source, args.seconds, width, height)
        head = _step("3/3 head left",
                     "Turn your HEAD left about 30 deg -- NOT a full profile; "
                     "keep both eyes visible.",
                     landmarker, source, args.seconds, width, height)

        rejected = getattr(landmarker, "rejections", 0)
        if rejected:
            print(f"\n   note: {rejected} frame(s) were refused by the topology "
                  f"check - see the logged reason; that means an index is "
                  f"grossly wrong, not merely mirrored.")

        return _verdict(square, eyes, head)
    finally:
        source.release()


if __name__ == "__main__":
    raise SystemExit(main())
