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

Add `--gui` for a live preview with the readings drawn on it. Worth it: this
check hunts a **sign** error, and without a preview you perform each movement
blind and are told the verdict afterwards, so a FAIL cannot separate a mirrored
table from someone who looked the wrong way. The preview is deliberately **not
mirrored**, for the reason in the last paragraph. It still records nothing.

Needs the `face` extra plus MediaPipe:

    pip install -e ".[face]" mediapipe

What it decides
---------------
Three prompted steps, each with an automatic verdict, so the outcome is a
sentence rather than a wall of numbers to interpret:

1. **Square on** -- the pose should read near zero on all three angles. Failing
   here means the canonical model or the pose maths is wrong, not the mapping.
2. **Eyes hard left, head still** -- `gaze.x` must go *negative*. Positive means
   the eye landmarks are mirrored, which is the failure this exists to find.
3. **Turn your head left** -- `yaw` must go negative, on the same convention.
   A *modest* turn: the bar is 10 degrees, and near profile the landmark set
   stops being usable. Past roughly 70 degrees the nose tip crosses the far eye
   corner, `check_topology` refuses the frame as `nose_outside_eyes`, and the
   step measures nothing -- correctly, but it is not what the step is asking.

Steps 2 and 3 are separate because they can fail independently: the eye/iris
indices and the face-outline indices are different parts of the table, and one
being mirrored says nothing about the other.

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


class Gui:
    """A live preview of the three steps, drawn with OpenCV. Opt-in (`--gui`).

    Not decoration. This check hunts a **sign** error, and the headless version
    asks you to perform a movement blind and reads you the verdict four seconds
    later -- so a FAIL cannot distinguish a mirrored table from someone who
    looked the wrong way, and neither can you. Watching `gaze.x` go negative
    while you look left collapses that into a single observation.

    Two properties it must not break:

    * **The preview is never mirrored.** The entire question is which way is
      left. Every video-call app mirrors its preview, which is why the docstring
      already warns against judging direction from one; a mirrored preview here
      would be that same trap wearing this script's authority. The window title
      and an on-frame banner both say so.
    * **Nothing is written to disk.** Frames are drawn and dropped, exactly as
      on the headless path. No call anywhere in this file persists one, and a
      test asserts that none appears -- a decoded frame with a window already
      open is one line away from being saved.

    Degrades rather than fails: a headless OpenCV build has no `imshow`, so the
    constructor raises and `main` falls back to the terminal flow.
    """

    WINDOW = "verify_landmarks -- NOT MIRRORED"

    # BGR, because that is what OpenCV draws in.
    OK = (80, 200, 80)
    BAD = (60, 60, 235)
    WATCH = (40, 200, 235)
    DIM = (190, 190, 190)

    def __init__(self) -> None:
        import cv2                                             # noqa: PLC0415
        self._cv2 = cv2
        cv2.namedWindow(self.WINDOW, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(self.WINDOW, 960, 720)
        self.aborted = False

    def close(self) -> None:
        try:
            self._cv2.destroyWindow(self.WINDOW)
        except Exception:                                      # noqa: BLE001
            pass

    # -- drawing -------------------------------------------------------------

    def _panel(self, img, x, y, w, h) -> None:
        cv2 = self._cv2
        over = img.copy()
        cv2.rectangle(over, (x, y), (x + w, y + h), (0, 0, 0), -1)
        cv2.addWeighted(over, 0.55, img, 0.45, 0, img)

    def _text(self, img, s, org, colour=(255, 255, 255), scale=0.55, weight=1):
        self._cv2.putText(img, s, org, self._cv2.FONT_HERSHEY_SIMPLEX,
                          scale, colour, weight, self._cv2.LINE_AA)

    def _canvas(self, frame):
        """RGB in, BGR out.

        `OpenCvFrameSource.read()` converts to RGB at its boundary because every
        layer above it assumes RGB. `imshow` wants BGR, so handing it the frame
        unconverted gives a blue-tinted preview -- directionally correct and
        obviously wrong-looking, which is the kind of thing that gets blamed on
        the camera.
        """
        return self._cv2.cvtColor(frame, self._cv2.COLOR_RGB2BGR)

    def _landmarks(self, img, named) -> None:
        """Every named point, with the irises picked out.

        "the iris landmarks are not tracking" is one of the verdicts this script
        can reach, and it is the one a reader cannot act on without seeing
        whether the dots are on the eyes.
        """
        for name, (x, y) in named.items():
            iris = name.endswith("_iris")
            self._cv2.circle(img, (int(x), int(y)), 4 if iris else 2,
                             self.WATCH if iris else self.OK, -1)

    def _readout(self, img, pose, gz, watch: str, target: float) -> None:
        h = img.shape[0]
        self._panel(img, 0, h - 96, img.shape[1], 96)
        rows = [("yaw", pose.yaw, "deg"), ("pitch", pose.pitch, "deg"),
                ("roll", pose.roll, "deg"),
                ("gaze.x", gz.x, ""), ("gaze.y", gz.y, "")]
        for i, (name, value, unit) in enumerate(rows):
            col, row = i % 3, i // 3
            x, y = 14 + col * 210, h - 66 + row * 26
            watched = name == watch
            if value is None:
                self._text(img, f"{name:7} --", (x, y), self.DIM)
                continue
            hit = (value <= target) if target < 0 else (abs(value) <= target)
            colour = (self.OK if hit else self.WATCH) if watched else self.DIM
            self._text(img, f"{name:7}{value:+7.2f}{unit}", (x, y), colour,
                       weight=2 if watched else 1)
        reason = (pose.rejected_by or gz.rejected_by)
        if reason:
            self._text(img, f"refused: {reason}", (14, h - 12), self.BAD, 0.5)

    def frame(self, frame, named, pose, gz, *, step: str, instruction: str,
              watch: str, target: float, progress: float | None,
              reason: str | None = None) -> None:
        """One frame. Returns nothing; sets `aborted` if the user pressed q."""
        cv2 = self._cv2
        img = self._canvas(frame)
        w = img.shape[1]

        self._panel(img, 0, 0, w, 76)
        self._text(img, step, (14, 26), (255, 255, 255), 0.7, 2)
        self._text(img, instruction, (14, 50), self.WATCH, 0.6, 1)
        self._text(img, "NOT MIRRORED -- 'left' means YOUR left. q to abort.",
                   (14, 68), self.DIM, 0.45)

        if named:
            self._landmarks(img, named)
        elif reason in (None, "no_face"):
            self._text(img, "NO FACE", (w // 2 - 60, img.shape[0] // 2),
                       self.BAD, 1.0, 2)
        else:
            # A face WAS found and the landmark set was refused. Rendering that
            # as "no face" sends someone to check their lighting when the real
            # answer is that a named point is somewhere a face cannot put it --
            # and near profile that is routine rather than a fault, because
            # `nose_outside_eyes` becomes true once the nose crosses the far
            # eye corner. Two different events, two different sentences.
            self._text(img, "FACE FOUND, LANDMARKS REFUSED",
                       (w // 2 - 240, img.shape[0] // 2 - 16), self.BAD, 0.85, 2)
            self._text(img, reason, (w // 2 - 100, img.shape[0] // 2 + 14),
                       self.WATCH, 0.7, 2)
            if reason == "nose_outside_eyes":
                self._text(img, "turned too far -- come back toward the camera",
                           (w // 2 - 210, img.shape[0] // 2 + 44), self.DIM, 0.6)
        self._readout(img, pose, gz, watch, target)

        if progress is not None:
            bar = int(w * max(0.0, min(1.0, progress)))
            cv2.rectangle(img, (0, 78), (bar, 84), self.WATCH, -1)

        cv2.imshow(self.WINDOW, img)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            self.aborted = True

    def countdown(self, frame, step: str, instruction: str, remaining: int) -> None:
        cv2 = self._cv2
        img = self._canvas(frame)
        w, h = img.shape[1], img.shape[0]
        self._panel(img, 0, 0, w, 76)
        self._text(img, step, (14, 26), (255, 255, 255), 0.7, 2)
        self._text(img, instruction, (14, 50), self.WATCH, 0.6, 1)
        self._text(img, "NOT MIRRORED -- 'left' means YOUR left.", (14, 68),
                   self.DIM, 0.45)
        self._text(img, str(remaining), (w // 2 - 20, h // 2), (255, 255, 255),
                   3.0, 6)
        cv2.imshow(self.WINDOW, img)
        if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
            self.aborted = True

    def verdict(self, frame, code: int) -> None:
        """Held until a key, so the window does not vanish with the answer."""
        cv2 = self._cv2
        label, colour = {0: ("PASS", self.OK), 1: ("FAIL", self.BAD)}.get(
            code, ("INCONCLUSIVE", self.WATCH))
        img = self._canvas(frame)
        w, h = img.shape[1], img.shape[0]
        self._panel(img, 0, h // 2 - 60, w, 120)
        self._text(img, label, (w // 2 - 90, h // 2), colour, 1.6, 4)
        self._text(img, "detail is in the terminal -- press any key",
                   (w // 2 - 170, h // 2 + 34), self.DIM, 0.55)
        cv2.imshow(self.WINDOW, img)
        cv2.waitKey(0)


def _collect(landmarker, source, seconds: float, width: int, height: int,
             gui=None, reasons: dict | None = None, **render) -> list:
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
        if gui is not None:
            # Drawn before the `continue` below, so a stretch with no face shows
            # as NO FACE rather than as a frozen window -- which is the state
            # someone needs to see, since it is usually them leaving the frame.
            gui.frame(frame, named, pose or head_pose({}), gz or gaze({}),
                      progress=1.0 - (deadline - time.perf_counter()) / seconds,
                      reason=getattr(landmarker, "last_reason", None),
                      **render)
            if gui.aborted:
                break
        if not named:
            continue
        samples.append((pose, gz))
    return samples


def _median(values):
    usable = [v for v in values if v is not None]
    return statistics.median(usable) if usable else None


def _step(name: str, instruction: str, landmarker, source, seconds, w, h,
          gui=None, watch: str = "", target: float = 0.0) -> dict:
    print(f"\n-- {name} --")
    print(f"   {instruction}")
    for count in range(3, 0, -1):
        print(f"   starting in {count}...", end="\r", flush=True)
        if gui is None:
            time.sleep(1.0)
        else:
            # Keep reading during the countdown, or the preview freezes for
            # three seconds at exactly the moment someone is trying to get
            # themselves into position.
            until = time.perf_counter() + 1.0
            while time.perf_counter() < until:
                frame = source.read()
                if frame is not None:
                    gui.countdown(frame, name, instruction, count)
    print(f"   measuring for {seconds:.0f}s...      ")

    empty: dict[str, int] = {}
    samples = _collect(landmarker, source, seconds, w, h, gui=gui,
                       reasons=empty, step=name, instruction=instruction,
                       watch=watch, target=target)
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
    elif gx <= -GAZE_THRESHOLD:
        report(True, f"looking left drives gaze.x negative ({gx})")
    elif gx >= GAZE_THRESHOLD:
        report(False, f"looking left drives gaze.x POSITIVE ({gx})")
        print("         -> the eye and iris indices are MIRRORED. Swap the "
              "`left_*` and `right_*` eye entries in "
              "face_landmarks.MEDIAPIPE_INDICES.")
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
    elif yaw <= -YAW_THRESHOLD:
        report(True, f"turning left drives yaw negative ({yaw})")
    elif yaw >= YAW_THRESHOLD:
        report(False, f"turning left drives yaw POSITIVE ({yaw})")
        print("         -> the outline indices are MIRRORED, or the canonical "
              "model's x axis is flipped. Note this is a *different* table "
              "region from the eyes above; check which of the two failed.")
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
    ap.add_argument("--gui", action="store_true",
                    help="live preview with the readings drawn on it "
                         "(needs a desktop OpenCV build; falls back if absent)")
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
    gui = None
    if args.gui:
        try:
            gui = Gui()
        except Exception as exc:                               # noqa: BLE001
            # A headless OpenCV build has no imshow. Degrade to the terminal
            # flow rather than refuse: the check itself is unchanged by the
            # preview, and refusing would make --gui a way to not run it.
            print(f"no GUI available ({exc}); continuing without the preview",
                  file=sys.stderr)

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
                       landmarker, source, args.seconds, width, height,
                       gui=gui, watch="yaw", target=SQUARE_ON_TOLERANCE)
        eyes = _step("2/3 eyes left",
                     "Keep your head still and look as far LEFT as you can.",
                     landmarker, source, args.seconds, width, height,
                     gui=gui, watch="gaze.x", target=-GAZE_THRESHOLD)
        head = _step("3/3 head left",
                     "Turn your HEAD left about 30 deg -- NOT a full profile; "
                     "keep both eyes visible.",
                     landmarker, source, args.seconds, width, height,
                     gui=gui, watch="yaw", target=-YAW_THRESHOLD)

        if gui is not None and gui.aborted:
            print("\naborted", file=sys.stderr)
            return 2

        rejected = getattr(landmarker, "rejections", 0)
        if rejected:
            print(f"\n   note: {rejected} frame(s) were refused by the topology "
                  f"check - see the logged reason; that means an index is "
                  f"grossly wrong, not merely mirrored.")

        code = _verdict(square, eyes, head)
        if gui is not None:
            frame = source.read()
            if frame is not None:
                gui.verdict(frame, code)
        return code
    finally:
        source.release()
        if gui is not None:
            gui.close()


if __name__ == "__main__":
    raise SystemExit(main())
