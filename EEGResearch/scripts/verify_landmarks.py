#!/usr/bin/env python3
"""Confirm the face-mesh index table against a real face.

Needs `pip install -e ".[face,gaze]"`, a webcam and a person; records no video. Cross-checks the
Haar cascade against the mesh, then three steps: square on (pose near zero), eyes hard left
(`gaze.x` positive; blind to a left/right swap), head left (`yaw` positive; the step that catches a
mirrored table). The frame is unmirrored: "left" means the subject's own left.
"""

# ASCII only in everything this prints: a cp1252 Windows console crashes on anything else.

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

# Gaze is -1..1; 0.15 is above landmark wobble and well below a hard look.
GAZE_THRESHOLD = 0.15
YAW_THRESHOLD = 10.0

# Degrees; loose on purpose, this checks the maths, not a steady head.
SQUARE_ON_TOLERANCE = 20.0


class Gui:
    """A live preview of the three steps, drawn with OpenCV. Opt-in (`--gui`).

    Never mirrored, and never writes to disk (a test asserts it). A headless OpenCV build
    raises in the constructor and `main` falls back to the terminal flow.
    """

    WINDOW = "verify_landmarks -- NOT MIRRORED"

    # BGR, since that's what OpenCV draws in.
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
        """RGB in (as from `OpenCvFrameSource.read()`), BGR out for `imshow`."""
        return self._cv2.cvtColor(frame, self._cv2.COLOR_RGB2BGR)

    def _landmarks(self, img, named) -> None:
        """Every named point, with the irises picked out."""
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
            # Face found but landmarks refused (e.g. near profile): not "no face".
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
        """Held until a key, so the window doesn't vanish with the answer."""
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
    """Poses and gazes over a few seconds.

    `reasons`, when given, tallies why each empty frame was empty (`no_face` vs a topology refusal).
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
            # Before `continue`, so a no-face stretch doesn't freeze the window.
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
        print("   empty frames: "
              + ", ".join(f"{why}={n}" for why, n in sorted(empty.items())))
    return measured


def _cascade_agrees(source, landmarker, width, height, seconds=2.0) -> dict:
    """Do the Haar cascade and the mesh find a face on the same frames?

    A Haar miss alone is ambiguous; a Haar miss where the mesh found a face means the cascade is broken.
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
        # The emotion path end to end on a real box.
        from src.app.services.face_emotion import to_gray64  # noqa: PLC0415

        crop = to_gray64(frame, box)
        if crop is None:
            # `to_gray64` refuses to upsample a box smaller than the model input.
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
    """A real FER+ classifier, or None if not provisioned (a gaze-only install lacks it)."""
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
    """Report the cross-check; an exit code to stop on, or None to carry on."""
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

    # The emotion path, on the same frames.
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
    # No claim about label accuracy: FER+ on children is a documented weakness.
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

    # A step 1 failure skips step 3 (yaw comes from the pose fit) but not step 2 (gaze doesn't).
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

    # The constructor opens the camera.
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
            # Headless OpenCV has no imshow; the terminal flow still works.
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

        # Before the three steps, while still square-on.
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
