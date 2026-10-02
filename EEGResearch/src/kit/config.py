"""kit.json, a student kit's settings: refused for exactly the reasons start.ps1 -Hosted refuses its arguments.

Standard library only, so the build script can check its arguments with any Python before building anything.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import secrets
import sys
from collections.abc import MutableMapping
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from urllib.parse import urlsplit

KIT_FILE = "kit.json"
SIDECAR_PORT = 8001
BRIDGE_PORT = 8765
LANDMARK_MODEL = "face_landmarker.task"
EMOTION_MODEL = "emotion-ferplus-8.onnx"
OPTICS_PRESETS = ("1031", "1032", "1033", "1034", "1035", "1036")
_TOKEN = re.compile(r"[A-Za-z0-9_-]+")
_HOST = re.compile(r"[a-z0-9_.-]+")  # what [Uri]::TryCreate takes in an https host; it refuses *, \, ; and spaces


class KitConfigError(ValueError):
    """Every reason the settings were refused, one per line."""


@dataclass(frozen=True)
class KitConfig:
    backend_url: str
    frontend_origin: str
    learner_token: str
    camera_index: int = 0
    optics_preset: str = ""  # empty: the bridge's own default, 1035
    version: str = ""


def origin_of(url: str) -> str | None:
    """The address as a browser sends an origin (lower case, no default port), or None where -Hosted refuses it."""
    if not isinstance(url, str) or "?" in url or "#" in url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in ("", "/")):
        return None
    if ":" in parts.hostname:
        try:
            ipaddress.IPv6Address(parts.hostname)
        except ValueError:
            return None
    elif not _HOST.fullmatch(parts.hostname):
        return None
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"


def token_ok(token: object) -> bool:
    """token_urlsafe's alphabet, and not .env.example's placeholder, which the sidecar refuses too."""
    return isinstance(token, str) and bool(_TOKEN.fullmatch(token)) and not token.lower().startswith("replace-me")


def check(raw: dict) -> tuple[KitConfig, list[str]]:
    """The normalised settings and any warnings; raises KitConfigError naming every field refused."""
    errors, warnings = [], []
    unknown = sorted(set(raw) - {f.name for f in fields(KitConfig)})
    if unknown:
        errors.append(f"Unknown setting(s) {', '.join(unknown)}: a misspelt name would otherwise be ignored.")
    backend = origin_of(raw.get("backend_url", ""))
    if backend is None:
        errors.append("backend_url must be the hosted backend's https address with no path or user name, "
                      "e.g. https://name.onrender.com.")
    origin = origin_of(raw.get("frontend_origin", ""))
    if origin is None:
        errors.append("frontend_origin must be the site's https origin with no path or user name, "
                      "e.g. https://name.pages.dev.")
    token = raw.get("learner_token", "")
    if not token_ok(token):
        errors.append("learner_token must be the site's VITE_EEG_LOCAL_TOKEN (letters, digits, - and _).")
    camera = raw.get("camera_index", 0)
    if isinstance(camera, bool) or not isinstance(camera, int) or camera < 0:
        errors.append("camera_index must be a whole number, 0 or more.")
    preset = raw.get("optics_preset", "")
    if not isinstance(preset, str) or (preset and preset not in OPTICS_PRESETS):
        errors.append("optics_preset must be empty (the bridge's 1035) or one of 1031-1036.")
    elif preset in ("1031", "1032"):
        warnings.append(f"optics_preset {preset} is 16 CH optics: on hardware the link dropped within ~20 s and "
                        "took EEG with it. 1033-1036 held for minutes.")
    version = raw.get("version", "")
    if not isinstance(version, str):
        errors.append("version must be text.")
    if errors:
        raise KitConfigError("\n".join(errors))
    return KitConfig(backend, origin, token, camera, preset, version), warnings


def load(path: Path) -> tuple[KitConfig, list[str]]:
    """check() on the JSON object in path, saved as UTF-8 or UTF-16, BOM or none: what Windows editors write."""
    try:
        raw = json.loads(Path(path).read_bytes())
    except OSError as exc:
        raise KitConfigError(f"Cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise KitConfigError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise KitConfigError(f"{path} must hold one JSON object.")
    return check(raw)


def write(path: Path, cfg: KitConfig) -> None:
    Path(path).write_text(json.dumps(asdict(cfg), indent=2) + "\n", encoding="utf-8")


def sidecar_env(cfg: KitConfig, app_dir: Path) -> dict[str, str]:
    """The sidecar's settings as start.ps1 -Hosted -Muse -Optics -Camera -Gaze writes them, ADMIN_TOKEN made fresh."""
    models = Path(app_dir) / "models"
    return {
        "API_TOKEN": cfg.learner_token,
        # Fresh each start: under push every endpoint the page calls takes the learner token.
        "ADMIN_TOKEN": secrets.token_urlsafe(32),
        "EEG_SOURCE": "muse",
        "EEG_DEVICES": f"default:muse@{BRIDGE_PORT},camera:face@{cfg.camera_index}",
        "EEG_SPECTRUM_SOURCE": "sdk",
        "FACE_ENABLED": "true",
        "FACE_EMOTION_ENABLED": "true",
        "FACE_GAZE_ENABLED": "true",
        "FACE_CAMERA_INDEX": str(cfg.camera_index),
        "FACE_LANDMARK_MODEL_PATH": str(models / LANDMARK_MODEL),
        "FACE_EMOTION_MODEL_PATH": str(models / EMOTION_MODEL),
        "PUSH_ENABLED": "true",
        "BACKEND_URL": cfg.backend_url,
        "ALLOWED_ORIGINS": cfg.frontend_origin,
    }


def bridge_env(cfg: KitConfig, base: MutableMapping[str, str]) -> dict[str, str]:
    """base without any MUSE_* name, then the optics flags; LOCALAPPDATA stays, since the token is written under it."""
    env = {k: v for k, v in base.items() if not k.upper().startswith("MUSE_")}
    env["MUSE_ENABLE_OPTICS"] = "1"
    if cfg.optics_preset:
        env["MUSE_OPTICS_PRESET"] = cfg.optics_preset
    return env


def settings_from_environment_only() -> None:
    """The sidecar's Settings read no .env from here on, so the environment the kit set is all they see."""
    from src.app.config import Settings  # noqa: PLC0415 -- pydantic only once the kit is launching

    Settings.model_config["env_file"] = None


def clear_sidecar_settings(environ: MutableMapping[str, str]) -> list[str]:
    """Removes every name the sidecar's Settings reads from environ, and returns those that were set."""
    from src.app.config import Settings  # noqa: PLC0415 -- pydantic only once the kit is launching

    names = {f.alias.upper() for f in Settings.model_fields.values() if f.alias}
    found = sorted(k for k in environ if k.upper() in names)
    for key in found:
        del environ[key]
    return found


def main(argv: list[str] | None = None) -> int:
    """`check` prints every refusal; `write PATH` writes the normalised kit.json as well."""
    parser = argparse.ArgumentParser(prog="python -m src.kit.config")
    parser.add_argument("action", choices=["check", "write"])
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--backend-url", required=True)
    parser.add_argument("--frontend-origin", required=True)
    parser.add_argument("--learner-token", required=True)
    parser.add_argument("--camera-index", type=int, default=0)
    parser.add_argument("--optics-preset", default="")
    parser.add_argument("--version", default="")
    args = parser.parse_args(argv)
    if args.action == "write" and args.path is None:
        parser.error("write needs the path of the kit.json to write")
    raw = {"backend_url": args.backend_url, "frontend_origin": args.frontend_origin,
           "learner_token": args.learner_token, "camera_index": args.camera_index,
           "optics_preset": args.optics_preset, "version": args.version}
    try:
        cfg, warnings = check(raw)
    except KitConfigError as exc:
        print(exc, file=sys.stderr)
        return 1
    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if args.action == "write":
        write(args.path, cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
