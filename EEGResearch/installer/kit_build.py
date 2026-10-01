"""The student kit's build steps that need Python, run by build_student_kit.ps1 with the build venv's interpreter.

models: fetch and verify both camera models. freeze: PyInstaller onedir. stage: assemble the installed folder.
audit: every DLL each binary imports is bundled beside it or ships with Windows. See DEVELOPER_SETUP_WINDOWS.md.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

EEG = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(EEG))

from src.kit import config as kit_config  # noqa: E402

NAME = "AdaptiveLearningSensors"
CRT_FILES = ["msvcp140.dll", "msvcp140_1.dll", "vcruntime140.dll", "vcruntime140_1.dll"]
# Found by the trial build: these packages read their own metadata at run time.
METADATA = ["fastapi", "starlette", "pydantic", "pydantic-settings", "pydantic-core", "uvicorn", "numpy", "scipy",
            "h11", "httpx", "onnxruntime", "mediapipe", "opencv-contrib-python"]
EXCLUDE = ["tkinter", "_tkinter", "sounddevice", "mediapipe.tasks.python.audio", "pytest", "IPython", "uvloop",
           "httptools", "watchfiles", "websockets"]


def models(target: Path) -> int:
    from src.app.services import face_emotion, face_landmarks  # noqa: PLC0415

    target.mkdir(parents=True, exist_ok=True)
    face_emotion.ensure_model(target / kit_config.EMOTION_MODEL)
    face_landmarks.ensure_model(target / kit_config.LANDMARK_MODEL)
    return 0


def freeze(work: Path, dist: Path) -> int:
    argv = [sys.executable, "-m", "PyInstaller", str(EEG / "src" / "kit" / "__main__.py"), "--name", NAME,
            "--onedir", "--windowed", "--noconfirm", "--clean", "--noupx", "--distpath", str(dist),
            "--workpath", str(work / "pyinstaller"), "--specpath", str(work / "spec"), "--paths", str(EEG),
            "--collect-submodules", "src.app", "--collect-submodules", "src.kit", "--hidden-import", "src.app.main",
            # uvicorn picks its protocol, lifespan and loop modules by name.
            "--collect-submodules", "uvicorn", "--hidden-import", "uvicorn.protocols.http.h11_impl",
            "--hidden-import", "uvicorn.lifespan.on", "--hidden-import", "uvicorn.loops.asyncio",
            # The cv2 hook leaves out cv2/data (the Haar cascade); MediaPipe has no hook at all.
            "--collect-data", "cv2", "--collect-all", "mediapipe", "--hidden-import", "mediapipe.tasks.c",
            "--collect-binaries", "onnxruntime"]
    for dist_name in METADATA:
        argv += ["--copy-metadata", dist_name]
    for module in EXCLUDE:
        argv += ["--exclude-module", module]
    # PyInstaller finds DLL dependencies through PATH; a JDK on it once put its own C++ runtime in the bundle.
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("PATH", "PYTHONPATH", "PYTHONHOME")}
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    env["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), rf"{system_root}\System32", system_root])
    return subprocess.run(argv, env=env).returncode


def _version(path: Path) -> tuple[int, ...]:
    import pefile  # noqa: PLC0415

    pe = pefile.PE(str(path), fast_load=True)
    try:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_RESOURCE"]])
        info = pe.VS_FIXEDFILEINFO[0]
        return info.FileVersionMS >> 16, info.FileVersionMS & 0xFFFF, info.FileVersionLS >> 16, info.FileVersionLS & 0xFFFF
    finally:
        pe.close()


def find_crt() -> Path:
    """The newest x64 C++ runtime redistributable of the newest Visual Studio with the C++ tools."""
    vswhere = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Microsoft Visual Studio",
                   "Installer", "vswhere.exe")
    install = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                              "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                             capture_output=True, text=True, check=True).stdout.strip()
    found = [p for p in Path(install, "VC", "Redist", "MSVC").glob("*/x64/Microsoft.VC*.CRT")
             if re.fullmatch(r"[\d.]+", p.parent.parent.name)]
    if not install or not found:
        raise SystemExit(f"no x64 C++ runtime redistributable under {install or 'any Visual Studio'}")
    return max(found, key=lambda p: tuple(int(n) for n in p.parent.parent.name.split(".")))


def stage(dist: Path, bridge_exe: Path, model_dir: Path, out: Path) -> int:
    """out/NAME: the frozen app, bridge\\ with its own C++ runtime, models\\, and the self-test's portrait."""
    import matplotlib  # noqa: PLC0415 -- mediapipe needs it, so the build venv always has it

    crt = find_crt()
    app = out / NAME
    if app.exists():
        shutil.rmtree(app)
    shutil.copytree(dist / NAME, app)
    bridge = app / "bridge"
    bridge.mkdir()
    shutil.copy2(bridge_exe, bridge / bridge_exe.name)
    for name in CRT_FILES:
        shutil.copy2(crt / name, bridge / name)
    internal = app / "_internal"
    present = {p.name.lower(): p for p in internal.iterdir() if p.is_file()}
    for name in CRT_FILES:
        old = present.get(name.lower())
        if old is None or _version(old) < _version(crt / name):  # a stale copy PyInstaller found is replaced
            if old is not None:
                old.unlink()
            shutil.copy2(crt / name, internal / name)
    (app / "models").mkdir()
    for model in (kit_config.EMOTION_MODEL, kit_config.LANDMARK_MODEL):
        shutil.copy2(model_dir / model, app / "models" / model)
    (app / "selftest").mkdir()
    shutil.copy2(Path(matplotlib.get_data_path()) / "sample_data" / "grace_hopper.jpg", app / "selftest" / "face.jpg")
    print(f"staged {app} with the C++ runtime from {crt}")
    return 0


# Windows' own DLLs beyond KnownDLLs, on every Windows 10/11 x64 desktop; the trial build's audit added the last two.
CORE_OS = {
    "ntdll.dll", "kernel32.dll", "kernelbase.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "ws2_32.dll",
    "bcrypt.dll", "bcryptprimitives.dll", "ncrypt.dll", "crypt32.dll", "secur32.dll", "ole32.dll", "oleaut32.dll",
    "combase.dll", "shell32.dll", "shlwapi.dll", "setupapi.dll", "cfgmgr32.dll", "dwmapi.dll", "comctl32.dll",
    "comdlg32.dll", "winmm.dll", "iphlpapi.dll", "version.dll", "imm32.dll", "opengl32.dll", "glu32.dll", "d3d9.dll",
    "d3d11.dll", "d3d12.dll", "dxgi.dll", "d3dcompiler_47.dll", "dxcore.dll", "mf.dll", "mfplat.dll",
    "mfreadwrite.dll", "mfuuid.dll", "evr.dll", "ksuser.dll", "dbghelp.dll", "psapi.dll", "rpcrt4.dll",
    "sechost.dll", "userenv.dll", "wtsapi32.dll", "powrprof.dll", "winhttp.dll", "wininet.dll", "dnsapi.dll",
    "mswsock.dll", "netapi32.dll", "normaliz.dll", "wldap32.dll", "msvcrt.dll", "ucrtbase.dll", "uxtheme.dll",
    "propsys.dll", "shcore.dll", "dwrite.dll", "d2d1.dll", "gdiplus.dll", "bluetoothapis.dll", "hid.dll",
    "windowscodecs.dll", "mfsensorgroup.dll", "dxva2.dll", "avrt.dll", "mmdevapi.dll", "winusb.dll", "wintrust.dll",
    "imagehlp.dll", "msimg32.dll", "usp10.dll", "oleacc.dll", "urlmon.dll", "credui.dll", "cryptbase.dll", "nsi.dll",
    "wsock32.dll", "avicap32.dll",
}
MUST_BUNDLE = re.compile(r"(msvcp140(_\w+)?|vcruntime140(_\w+)?|concrt140|vcomp140|vccorlib140|python3\d*)\.dll")


def _known_dlls() -> set[str]:
    key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\KnownDLLs")
    names, i = set(), 0
    while True:
        try:
            _, value, _ = winreg.EnumValue(key, i)
        except OSError:
            return names
        if isinstance(value, str) and value.lower().endswith(".dll"):
            names.add(value.lower())
        i += 1


def _imports(path: Path) -> list[str]:
    import pefile  # noqa: PLC0415

    pe = pefile.PE(str(path), fast_load=True)
    try:
        pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                                               pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT"]])
        return [e.dll.decode("ascii", "replace").lower()
                for attr in ("DIRECTORY_ENTRY_IMPORT", "DIRECTORY_ENTRY_DELAY_IMPORT") for e in getattr(pe, attr, [])]
    finally:
        pe.close()


def audit(app: Path) -> int:
    """Each import is an API set, Windows' own, or bundled where that binary's loader looks: bridge\\ for the bridge."""
    known = _known_dlls()
    binaries = [p for p in app.rglob("*") if p.suffix.lower() in (".exe", ".dll", ".pyd")]
    bridge = app / "bridge"
    in_bridge = {p.name.lower() for p in binaries if p.parent == bridge}
    elsewhere = {p.name.lower() for p in binaries if bridge not in p.parents}
    problems = []
    for binary in binaries:
        bundled = in_bridge if binary.parent == bridge else elsewhere
        for name in _imports(binary):
            if name.startswith(("api-ms-win-", "ext-ms-")) or name in bundled:
                continue
            if MUST_BUNDLE.fullmatch(name):
                problems.append(f"{binary.relative_to(app)}\t{name}\tnot bundled beside it")
            elif name not in known and name not in CORE_OS:
                problems.append(f"{binary.relative_to(app)}\t{name}\tnot bundled, not part of Windows")
    print(f"audited {len(binaries)} binaries; {len(problems)} problem(s)")
    for line in problems:
        print(line)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kit_build.py")
    sub = parser.add_subparsers(dest="step", required=True)
    sub.add_parser("models").add_argument("target", type=Path)
    p = sub.add_parser("freeze")
    p.add_argument("work", type=Path)
    p.add_argument("dist", type=Path)
    p = sub.add_parser("stage")
    for name in ("dist", "bridge_exe", "model_dir", "out"):
        p.add_argument(name, type=Path)
    sub.add_parser("audit").add_argument("app", type=Path)
    args = parser.parse_args(argv)
    if args.step == "models":
        return models(args.target)
    if args.step == "freeze":
        return freeze(args.work, args.dist)
    if args.step == "stage":
        return stage(args.dist, args.bridge_exe, args.model_dir, args.out)
    return audit(args.app)


if __name__ == "__main__":
    sys.exit(main())
