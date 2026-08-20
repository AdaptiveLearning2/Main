#!/usr/bin/env python3
"""Confirm the face-mesh index table against a real face.

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

Needs both camera extras:

    pip install -e ".[face,gaze]"

It also cross-checks the **Haar cascade** against the mesh on the same frames
before the three steps. `face_roi.FaceLocator` gates the colour sample and the
emotion crop and had no camera check of any kind, while depending on
`cv2.data.haarcascades` shipping inside the wheel -- which a packaging change
can move silently, and one did: `face` was moved from `opencv-python` to
`opencv-contrib-python` so that only one distribution provides `cv2`.

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
import os
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
    later -- so a FAIL cannot distinguish a wrong mapping from someone who
    looked the wrong way, and neither can you. Watching the number move as you
    move collapses that into a single observation.

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
             reasons: dict | None = None, gui=None, **render) -> list:
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
        why = getattr(landmarker, "last_reason", None) or "no_face"
        if not named and reasons is not None:
            reasons[why] = reasons.get(why, 0) + 1
        pose, gz = (head_pose(named), gaze(named)) if named else (None, None)
        if gui is not None:
            # Drawn before the `continue`, so a stretch with no face shows as
            # NO FACE rather than as a frozen window.
            gui.frame(frame, named, pose or head_pose({}), gz or gaze({}),
                      progress=1.0 - (deadline - time.perf_counter()) / seconds,
                      reason=None if named else why, **render)
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
        time.sleep(1.0)
    print(f"   measuring for {seconds:.0f}s...      ")

    empty: dict[str, int] = {}
    samples = _collect(landmarker, source, seconds, w, h, reasons=empty, gui=gui,
                       step=name, instruction=instruction,
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


def _cascade_agrees(source, landmarker, width, height, seconds=2.0) -> dict:
    """Do the Haar cascade and the mesh find a face on the same frames?

    Run against **both** detectors deliberately. A Haar miss on its own is
    ambiguous -- bad light, a turned head, someone out of shot -- so it cannot
    support a verdict. A Haar miss on frames where the mesh *did* find a face is
    not ambiguous at all: the cascade is broken, not the lighting.

    This exists because `face_roi.FaceLocator` gates the colour sample and the
    emotion crop and had no camera check of any kind, while depending on two
    things a packaging change can quietly move: `cv2.data.haarcascades` shipping
    inside the wheel, and the cascade file loading from it. Both were verified
    statically when `face` moved from opencv-python to opencv-contrib-python;
    neither says whether it finds a face.
    """
    from src.app.services.face_roi import FaceLocator      # noqa: PLC0415
    import numpy as np                                     # noqa: PLC0415

    from src.app.services.face_ingestion import LUMA_WEIGHTS  # noqa: PLC0415

    locator = FaceLocator()
    classifier = _emotion_classifier()
    out = {"frames": 0, "mesh": 0, "haar": 0, "crops": 0, "crops_refused": 0,
           "labels": 0, "emotion_refusals": {}, "confidences": [],
           "emotion_available": classifier is not None}
    deadline = time.perf_counter() + seconds
    while time.perf_counter() < deadline:
        frame = source.read()
        if frame is None:
            continue
        out["frames"] += 1
        if landmarker.locate(frame, width, height):
            out["mesh"] += 1
        box = locator.locate(frame.astype(np.float32) @ LUMA_WEIGHTS)
        if box is None:
            continue
        out["haar"] += 1
        if classifier is None:
            continue
        # The emotion path, end to end, on a real box. Everything below here
        # has only ever run against an injected fake session.
        from src.app.services.face_emotion import to_gray64  # noqa: PLC0415

        crop = to_gray64(frame, box)
        if crop is None:
            # `to_gray64` refuses rather than upsampling a box smaller than the
            # model's own input. That refusal has never been observed against
            # real Haar boxes, and if it fired often the channel would be
            # quietly thin rather than obviously broken.
            out["crops_refused"] += 1
            continue
        out["crops"] += 1
        result = classifier.classify(crop)
        if result.label is None:
            reason = result.rejected_by or "unknown"
            out["emotion_refusals"][reason] = out["emotion_refusals"].get(reason, 0) + 1
        else:
            out["labels"] += 1
            if result.confidence is not None:
                out["confidences"].append(result.confidence)
    return out


def _emotion_classifier():
    """A real FER+ classifier, or None if this machine has not provisioned one.

    None rather than a failure: the emotion model is a separate 35 MB download
    and a gaze-only install legitimately lacks it. Skipping says so; failing
    would make the landmark check depend on a channel it is not about.
    """
    from pathlib import Path as _Path                       # noqa: PLC0415

    model = _Path(os.environ.get("FACE_EMOTION_MODEL_PATH")
                  or _Path(__file__).resolve().parents[1]
                  / "models" / "emotion-ferplus-8.onnx")
    if not model.is_file():
        return None
    try:
        from src.app.services.face_emotion import EmotionClassifier  # noqa: PLC0415

        return EmotionClassifier(model)
    except Exception as exc:                                # noqa: BLE001
        print(f"   (emotion model present but would not load: {exc})")
        return None


def _cross_check_verdict(agree: dict) -> int | None:
    """Report the cross-check; an exit code to stop on, or None to carry on.

    Split out of `main()` for the reason `_verdict` already is: these are the
    branches that report a *failure*, and while they lived inline the only way
    to reach one was to have the failure on real hardware. One shipped with a
    `NameError` in it -- a crash in the error path, which reads as the script
    being broken rather than the channel it was trying to report on.
    """
    print(f"   {agree['frames']} frames: mesh found a face on "
          f"{agree['mesh']}, Haar cascade on {agree['haar']}")
    if agree["mesh"] and not agree["haar"]:
        print("   FAIL  the Haar cascade found nothing on frames where the "
              "mesh did.\n         face_roi.FaceLocator gates the colour "
              "sample and the emotion crop, so this is the camera path "
              "broken, not the lighting.")
        return 1
    if not agree["frames"]:
        print("   INCONCLUSIVE  no frames arrived")
        return 2
    if not agree["mesh"]:
        print("   (neither detector saw a face -- check lighting and framing)")
    else:
        print("   OK  both detectors agree there is a face")

    # The emotion path, on the same frames. FER+ is on by default and reaches a
    # parent's report, and until now had no camera check at all while gaze --
    # off by default, rendered nowhere -- had one.
    if not agree["emotion_available"]:
        print("   SKIP  no FER+ model here, so the emotion path is "
              "unchecked (./start.ps1 -Camera provisions it)")
        return None
    if not agree["haar"]:
        return None

    print(f"   emotion: {agree['crops']} crops accepted, "
          f"{agree['crops_refused']} refused as too small; "
          f"{agree['labels']} classified")
    if agree["emotion_refusals"]:
        print("   refusals: " + ", ".join(
            f"{k}={v}" for k, v in sorted(agree["emotion_refusals"].items())))
    if not agree["crops"]:
        print("   FAIL  every crop was refused as too small.\n"
              "         `to_gray64` will not upsample, so the emotion channel "
              "records nothing at this framing.")
        return 1
    if agree["emotion_refusals"].get("inference_failed"):
        print("   FAIL  the model errored on a real crop -- a broken "
              "install, not an unsure classification.")
        return 1
    if agree["confidences"]:
        lo, hi = min(agree["confidences"]), max(agree["confidences"])
        print(f"   OK  the emotion path runs end to end "
              f"(confidence {lo:.2f}-{hi:.2f})")
    else:
        print("   OK  crops reach the model; it declined to label them, "
              "which is a reading it is entitled to make")
    # Deliberately no claim about the label. FER+ has no ground truth you can
    # assert from a chair, and its accuracy on this product's users is the
    # documented weakness no self-check can address. This says the plumbing
    # works and stops there -- a check that looked like it validated emotion
    # would be worse than none.
    print("   (plumbing only -- this says nothing about whether the "
          "label is correct)")
    return None


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
            # rather than refuse: the check is unchanged by the preview, and
            # refusing would make --gui a way to not run it.
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

        # Before the three steps, on the same still, square-on subject.
        print("\n-- detector cross-check --")
        print("   Look at the camera and hold still.")
        agree = _cascade_agrees(source, landmarker, width, height)
        stop = _cross_check_verdict(agree)
        if stop is not None:
            return stop

        square = _step("1/3 square on",
                       "Look straight at the camera and hold still.",
                       landmarker, source, args.seconds, width, height,
                       gui=gui, watch="yaw", target=SQUARE_ON_TOLERANCE)
        eyes = _step("2/3 eyes left",
                     "Keep your head still and look as far LEFT as you can.",
                     landmarker, source, args.seconds, width, height,
                     gui=gui, watch="gaze.x", target=GAZE_THRESHOLD)
        head = _step("3/3 head left",
                     "Turn your HEAD left about 30 deg -- NOT a full profile; "
                     "keep both eyes visible.",
                     landmarker, source, args.seconds, width, height,
                     gui=gui, watch="yaw", target=YAW_THRESHOLD)

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
