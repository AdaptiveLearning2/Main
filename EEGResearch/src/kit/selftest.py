"""`--self-test REPORT`: runs the real sidecar objects and the real bridge from the kit folder, and writes a report.

Each check records {name, ok, detail, seconds} and passes only when its stated criterion holds. The face checks
run on a real portrait, since a blank frame cannot tell a working model from one that never detects.
"""

from __future__ import annotations

import base64
import json
import logging
import ntpath
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from src.app.services.bridge_handshake import authenticate
from src.kit import config as kit_config
from src.kit import winproc
from src.kit.launcher import BRIDGE_EXE, prepare_sidecar_process, sidecar_config

FACE_IMAGE = Path("selftest") / "face.jpg"
STATUS_DLL_NOT_FOUND = 0xC0000135
CRT = re.compile(r"(msvcp140(_\w+)?|vcruntime140(_\w+)?|concrt140)\.dll", re.I)
PYTHON_RUNTIME = re.compile(r"python3\d*\.dll", re.I)
SERVER_START_S = 30.0


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
    work = ctx["tmp"] / "cwd"
    work.mkdir()
    os.chdir(work)  # as the launcher does: a folder with no .env
    kit_config.clear_sidecar_settings(os.environ)
    env = kit_config.sidecar_env(ctx["cfg"], ctx["app"])
    os.environ.update(env)
    prepare_sidecar_process(work)  # as the sidecar process does, before anything imports matplotlib
    ctx["matplotlib"] = work / "matplotlib"
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
    import matplotlib  # noqa: PLC0415 -- mediapipe imported it; frozen, its cache must not be a new temp folder

    cache = matplotlib.get_cachedir()
    require("matplotlib" not in ctx or _norm(cache) == _norm(ctx["matplotlib"]), f"matplotlib caches in {cache}")
    return {"landmarks": sorted(found), "matplotlib_cache": cache}


class _Records(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(self.format(record))


@contextmanager
def _server_warnings() -> Iterator[list[str]]:
    """Warnings and errors logged while the block runs: on the root logger, which uvicorn's dictConfig leaves alone."""
    records = _Records()
    root = logging.getLogger()
    root.addHandler(records)
    try:
        yield records.lines
    finally:
        root.removeHandler(records)


def _serve_once(app, path: str) -> tuple[int, dict, list[str]]:
    """uvicorn with app on a free port, one GET of path, then stopped; CheckFailed if it did not start in time."""
    import uvicorn  # noqa: PLC0415

    with _server_warnings() as warnings:  # a failed start is otherwise only a timeout here
        server = uvicorn.Server(sidecar_config(app, 0))  # the sidecar's own, so the session check is checked too
        thread = threading.Thread(target=server.run, name="selftest-uvicorn", daemon=True)  # never holds up the exit
        thread.start()
        try:
            deadline = time.monotonic() + SERVER_START_S
            while not server.started:
                require(thread.is_alive(), f"uvicorn stopped during startup: {warnings[-5:]}")
                require(time.monotonic() < deadline,
                        f"uvicorn did not start in {SERVER_START_S:.0f} s: {warnings[-5:]}")
                time.sleep(0.05)
            port = server.servers[0].sockets[0].getsockname()[1]
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
                status, body = resp.status, json.loads(resp.read())
        finally:
            server.should_exit = True  # on a slow start too, or it serves on once started
            thread.join(30)
        require(not thread.is_alive(), "uvicorn did not exit when asked")
        return status, body, warnings[-5:]


def check_server(ctx):
    require(ctx.get("settings_ok"), "the settings check did not pass, so the sidecar's env is not set")
    from src.app.main import app  # noqa: PLC0415
    from src.kit import update  # noqa: PLC0415

    # the kit must report its own version.txt, or the admin page counts it as naming none
    want = {"status": "ok", "kit": {"version": update.version_text(update.installed_version(ctx["app"]))}}
    status, body, warnings = _serve_once(app, "/healthz")
    require(status == 200 and body == want, f"healthz {status} {body}, not {want}")
    return {"healthz": status, "warnings": warnings}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_bridge(ctx):
    """The bridge starts, writes its token, answers as a libMuse build, and loads its C++ runtime from bridge\\."""
    require("cfg" in ctx, "kit.json did not load")
    bridge_dir = ctx["app"] / "bridge"
    home = ctx["tmp"] / "bridge"
    home.mkdir()
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
        refused = authenticate(conn, token)  # the sidecar's handshake, so the bridge's proof is checked too
        require(refused is None, f"the bridge {refused}")
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


def _outside_kit(loaded, app: str, windows: str, kit_names: set[str]) -> tuple[list[str], list[str]]:
    """(failing, foreign) among loaded paths, read as Windows paths on any platform. The runtimes from outside the
    kit fail, and so does a DLL the kit ships found outside Windows; any other DLL outside Windows, such as an
    antivirus hook, is only reported."""
    app, windows = ntpath.normcase(app).rstrip("\\") + "\\", ntpath.normcase(windows).rstrip("\\") + "\\"
    failing, foreign = [], []
    for path in loaded:
        n, name = ntpath.normcase(path), ntpath.basename(path).lower()
        if n.startswith(app):
            continue
        if CRT.fullmatch(name) or PYTHON_RUNTIME.fullmatch(name):
            failing.append(path)
        elif not n.startswith(windows):
            (failing if name in kit_names else foreign).append(path)
    return failing, foreign


def check_modules(ctx):
    """No runtime or DLL the kit ships came from elsewhere; with PATH cut to Windows', nothing else could stand in."""
    require(getattr(sys, "frozen", False), "run from source, where the interpreter is outside the kit by design")
    kit_names = {p.name.lower() for p in ctx["app"].rglob("*") if p.suffix.lower() in (".dll", ".pyd")}
    failing, foreign = _outside_kit(winproc.loaded_modules(), str(ctx["app"]),
                                    os.environ.get("SystemRoot", r"C:\Windows"), kit_names)
    require(not failing, f"loaded from outside the kit: {failing}")
    return {"outside": failing, "foreign": foreign}


def check_update(ctx):
    """The updater can verify a feed in this build, refuses a tampered one, and has keys to verify real ones with."""
    from src.kit import update, update_keys  # noqa: PLC0415

    test_key = update.load_keys({"test": update_keys.TEST_SIGNER})
    manifest, _ = update.verify_feed(update_keys.TEST_FEED, test_key)
    outer = json.loads(update_keys.TEST_FEED)
    signature = bytearray(base64.b64decode(outer["signature"]))
    signature[0] ^= 1
    tampered = json.dumps({**outer, "signature": base64.b64encode(signature).decode("ascii")}).encode("ascii")
    try:
        update.verify_feed(tampered, test_key)
    except update.FeedError:
        pass
    else:
        raise CheckFailed("a feed with a changed signature was accepted")
    keys = update.load_keys(update_keys.PUBLIC_KEYS)
    require(bool(keys), "no signing keys in update_keys.PUBLIC_KEYS: this kit could never verify an update")
    return {"keys": sorted(keys), "test_feed": update.version_text(manifest.release.version)}


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
          ("update", check_update), ("modules", check_modules)]


def run(report_path: Path, app: Path) -> int:
    """Every check, in order, recorded; 0 only if all passed."""
    report_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(report_path.with_suffix(".log"), encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().handlers[:] = [handler]
    logging.getLogger().setLevel(logging.INFO)
    ctx = {"app": app, "tmp": Path(tempfile.mkdtemp(prefix="kit-selftest-"))}
    results = []
    started = time.monotonic()
    home = os.getcwd()
    try:
        for name, check in CHECKS:
            t = time.monotonic()
            try:
                detail, ok = check(ctx), True
            except BaseException as exc:  # a failure is recorded, never mistaken for a pass
                ok, detail = False, {"error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc()[-1500:]}
            results.append({"name": name, "ok": ok, "detail": detail, "seconds": round(time.monotonic() - t, 3)})
            print(f"{'PASS' if ok else 'FAIL'} {name}", flush=True)
    finally:
        os.chdir(home)  # the settings check works inside ctx["tmp"], and Windows keeps a working folder
        shutil.rmtree(ctx["tmp"], ignore_errors=True)
    report = {"ok": all(r["ok"] for r in results), "checks": results,
              "total_seconds": round(time.monotonic() - started, 3), "frozen": bool(getattr(sys, "frozen", False)),
              "executable": sys.executable, "app": str(app), "versions": _versions()}
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return 0 if report["ok"] else 1
