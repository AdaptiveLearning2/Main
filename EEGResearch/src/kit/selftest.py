"""`--self-test REPORT`: runs the real sidecar objects and the real bridge from the kit folder, and writes a report.

Each check records {name, ok, detail, seconds} and passes only when its stated criterion holds. The face checks
run on a real portrait, since a blank frame cannot tell a working model from one that never detects.
"""

from __future__ import annotations

import json
import logging
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from pathlib import Path

from src.kit import config as kit_config
from src.kit import winproc
from src.kit.launcher import BRIDGE_EXE, uvicorn_log_config

FACE_IMAGE = Path("selftest") / "face.jpg"
STATUS_DLL_NOT_FOUND = 0xC0000135
CRT = re.compile(r"(msvcp140(_\w+)?|vcruntime140(_\w+)?|concrt140)\.dll", re.I)
PYTHON_RUNTIME = re.compile(r"python3\d*\.dll", re.I)


class CheckFailed(Exception):
    pass


def require(condition: bool, detail: str) -> None:
    if not condition:
        raise CheckFailed(detail)


def _norm(path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _face(ctx) -> tuple:
    """The portrait as BGR and grey, read by Python: OpenCV cannot open a non-ASCII path."""
    import cv2  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    if "face" not in ctx:
        data = np.frombuffer((ctx["app"] / FACE_IMAGE).read_bytes(), dtype=np.uint8)
        bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
        require(bgr is not None, f"{FACE_IMAGE} did not decode")
        ctx["face"] = (bgr, cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    return ctx["face"]


def check_kit(ctx):
    cfg, warnings = kit_config.load(ctx["app"] / kit_config.KIT_FILE)
    ctx["cfg"] = cfg
    return {"backend_url": cfg.backend_url, "frontend_origin": cfg.frontend_origin, "camera_index": cfg.camera_index,
            "optics_preset": cfg.optics_preset or "bridge default", "version": cfg.version, "warnings": warnings}


def check_settings(ctx):
    require("cfg" in ctx, "kit.json did not load")
    work = Path(tempfile.mkdtemp(prefix="kit-cwd-"))
    os.chdir(work)  # as the launcher does: a folder with no .env
    kit_config.clear_sidecar_settings(os.environ)
    env = kit_config.sidecar_env(ctx["cfg"], ctx["app"])
    os.environ.update(env)
    from src.app.config import Settings, get_settings, parse_eeg_devices  # noqa: PLC0415

    get_settings.cache_clear()
    s = Settings()  # its validators refuse a placeholder or shared token, and push in clear text
    require(s.api_token == env["API_TOKEN"] and s.admin_token == env["ADMIN_TOKEN"], "tokens not read from the env")
    require(s.push_enabled and s.backend_url == ctx["cfg"].backend_url, "push or BACKEND_URL")
    require(s.allowed_origins == ctx["cfg"].frontend_origin, "ALLOWED_ORIGINS")
    require(s.eeg_source == "muse" and s.face_gaze_enabled and s.face_emotion_enabled, "EEG source or camera flags")
    devices = {d: c.kind for d, c in parse_eeg_devices(s).items()}
    require(devices == {"default": "muse", "camera": "face"}, f"devices {devices}")
    ctx["settings_ok"] = True
    return {"cwd": str(work), "devices": devices}


def check_haar(ctx):
    from src.app.services.face_roi import FaceLocator  # noqa: PLC0415

    bgr, gray = _face(ctx)
    box = FaceLocator(redetect_every=0).locate(gray)
    require(box is not None and box[2] > gray.shape[1] // 4, f"no face-sized box in the portrait: {box}")
    ctx["box"] = box
    return {"box": [int(v) for v in box]}


def check_emotion(ctx):
    import cv2  # noqa: PLC0415

    from src.app.services import face_emotion as fe  # noqa: PLC0415

    model = ctx["app"] / "models" / kit_config.EMOTION_MODEL
    require(fe.verify(model), f"model missing or unverified: {model}")
    require("box" in ctx, "the face check found no box to classify")
    _, gray = _face(ctx)
    x, y, w, h = ctx["box"]
    crop = cv2.resize(gray[y:y + h, x:x + w], (64, 64)).astype("float32")
    classifier = fe.EmotionClassifier(model)
    result = classifier.classify(crop)
    require(result.label in fe.EMOTION_LABELS and 0.0 < result.confidence <= 1.0, f"bad result {result}")
    require(not classifier.degraded, f"classifier degraded: {classifier.get_meta()}")
    return {"label": result.label, "confidence": round(float(result.confidence), 3),
            "providers": classifier._session.get_providers()}


def check_landmarks(ctx):
    from src.app.services import face_landmarks as fl  # noqa: PLC0415

    model = ctx["app"] / "models" / kit_config.LANDMARK_MODEL
    require(fl.verify(model), f"model missing or unverified: {model}")
    bgr, _ = _face(ctx)
    rgb = bgr[:, :, ::-1].copy()
    found = fl.FaceMeshLandmarker(model_path=str(model)).locate(rgb, rgb.shape[1], rgb.shape[0])
    require(bool(found), "the landmarker found no face in the portrait")
    return {"landmarks": sorted(found)}


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


def check_server(ctx):
    require(ctx.get("settings_ok"), "the settings check did not pass, so the sidecar's env is not set")
    import uvicorn  # noqa: PLC0415

    from src.app.main import app  # noqa: PLC0415

    errors = _Records()
    logging.getLogger("uvicorn").addHandler(errors)  # a failed start is otherwise only a timeout here
    try:
        server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, http="h11", ws="none",
                                               loop="asyncio", lifespan="on", log_config=uvicorn_log_config()))
        thread = threading.Thread(target=server.run, name="selftest-uvicorn")
        thread.start()
        deadline = time.monotonic() + 30
        while not server.started:
            require(thread.is_alive(), f"uvicorn stopped during startup: {errors.lines[-5:]}")
            require(time.monotonic() < deadline, f"uvicorn did not start in 30 s: {errors.lines[-5:]}")
            time.sleep(0.05)
        port = server.servers[0].sockets[0].getsockname()[1]
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=10) as resp:
                status, body = resp.status, json.loads(resp.read())
        finally:
            server.should_exit = True
            thread.join(30)
    finally:
        logging.getLogger("uvicorn").removeHandler(errors)
    require(status == 200 and body == {"status": "ok"}, f"healthz {status} {body}")
    require(not thread.is_alive(), "uvicorn did not exit when asked")
    return {"healthz": status, "warnings": errors.lines[-5:]}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_bridge(ctx):
    """The bridge starts, writes its token, answers as a libMuse build, and loads its C++ runtime from bridge\\."""
    require("cfg" in ctx, "kit.json did not load")
    bridge_dir = ctx["app"] / "bridge"
    home = Path(tempfile.mkdtemp(prefix="kit-bridge-"))
    port = _free_port()
    env = kit_config.bridge_env(ctx["cfg"], dict(os.environ))
    env.update(LOCALAPPDATA=str(home), MUSE_BRIDGE_PORT=str(port))
    token_file = home / "AdaptiveLearning" / f"muse_bridge_{port}.token"
    out = home / "bridge.log"
    with open(out, "wb") as log:
        proc = subprocess.Popen([str(bridge_dir / BRIDGE_EXE)], env=env, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline = time.monotonic() + 20
        while not (token_file.is_file() and token_file.stat().st_size):
            code = proc.poll()
            if code is not None:
                text = out.read_bytes().decode("utf-8", "replace")[-400:]
                require(code & 0xFFFFFFFF != STATUS_DLL_NOT_FOUND, f"a DLL the bridge needs is missing: {text}")
                raise CheckFailed(f"bridge exited with {code}: {text}")
            require(time.monotonic() < deadline, "no token after 20 s")
            time.sleep(0.1)
        token = token_file.read_text(encoding="ascii").strip()
        status = _bridge_status(port, token)
        require(status.get("bridge_mode") == "libmuse",
                f"bridge_mode is {status.get('bridge_mode')!r}: this is not a libMuse build")
        modules = winproc.loaded_modules(proc.pid)
    finally:
        proc.kill()
        proc.wait(10)
    # A working launch proves nothing: the bridge inherits the launcher's DLL folder, _internal\, ahead of System32.
    crt = [m for m in modules if CRT.fullmatch(os.path.basename(m))]
    outside = [m for m in crt if not _norm(m).startswith(_norm(bridge_dir) + os.sep)]
    require(crt and not outside, f"the C++ runtime did not load from bridge\\: {outside or 'none loaded'}")
    return {"bridge_mode": status["bridge_mode"], "crt": crt}


def _bridge_status(port: int, token: str) -> dict:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as conn:
        conn.sendall(f"AUTH {token}\n".encode())
        reader = conn.makefile("rb")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            line = reader.readline()
            require(bool(line), "the bridge closed the connection after AUTH")
            try:
                status = json.loads(line)
            except ValueError:
                continue
            if isinstance(status, dict) and "bridge_mode" in status:
                return status
    raise CheckFailed("no status line from the bridge in 10 s")


def check_modules(ctx):
    """Nothing this process loaded outside the kit except Windows' own DLLs and what Defender injects."""
    require(getattr(sys, "frozen", False), "run from source, where the interpreter is outside the kit by design")
    app, windows = _norm(ctx["app"]), _norm(os.environ.get("SystemRoot", r"C:\Windows"))
    defender = _norm(Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows Defender")
    outside = []
    for path in winproc.loaded_modules():
        n, name = _norm(path), os.path.basename(path)
        if n.startswith(app + os.sep):
            continue
        if CRT.fullmatch(name) or PYTHON_RUNTIME.fullmatch(name) or not (
                n.startswith(windows + os.sep) or n.startswith(defender + os.sep)):
            outside.append(path)
    require(not outside, f"loaded from outside the kit: {outside}")
    return {"outside": outside}


def _versions() -> dict:
    from importlib import metadata  # noqa: PLC0415

    found = {"python": sys.version.split()[0]}
    for dist in ("fastapi", "uvicorn", "pydantic", "numpy", "opencv-contrib-python", "onnxruntime", "mediapipe"):
        try:
            found[dist] = metadata.version(dist)
        except metadata.PackageNotFoundError:
            found[dist] = None  # reported, not checked: --copy-metadata decides what a frozen build carries
    return found


CHECKS = [("kit", check_kit), ("settings", check_settings), ("haar", check_haar), ("emotion", check_emotion),
          ("landmarks", check_landmarks), ("server", check_server), ("bridge", check_bridge),
          ("modules", check_modules)]


def run(report_path: Path, app: Path) -> int:
    """Every check, in order, recorded; 0 only if all passed."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(report_path.with_suffix(".log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().handlers[:] = [handler]
    logging.getLogger().setLevel(logging.INFO)
    ctx = {"app": app}
    results = []
    started = time.monotonic()
    for name, check in CHECKS:
        t = time.monotonic()
        try:
            detail, ok = check(ctx), True
        except BaseException as exc:  # a failure is recorded, never mistaken for a pass
            ok, detail = False, {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}
        results.append({"name": name, "ok": ok, "detail": detail, "seconds": round(time.monotonic() - t, 3)})
        print(f"{'PASS' if ok else 'FAIL'} {name}", flush=True)
    report = {"ok": all(r["ok"] for r in results), "checks": results,
              "total_seconds": round(time.monotonic() - started, 3), "frozen": bool(getattr(sys, "frozen", False)),
              "executable": sys.executable, "app": str(app), "versions": _versions()}
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return 0 if report["ok"] else 1
