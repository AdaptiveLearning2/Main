"""update.json, the self-updater's settings: apart from kit.json, whose check refuses any name it does not know.

Unknown names here are ignored, not refused: after a rollback an older updater reads the newer kit's file.
Standard library only, so the build script can check its arguments with any Python.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

UPDATE_FILE = "update.json"
GATE_URL = "https://kit-updates.akashravi04.workers.dev"  # the download gate (installer/update_gate)
FEEDS = ("latest", "canary")
_KEY = re.compile(r"[A-Za-z0-9_-]{32,128}")


class UpdateSettingsError(ValueError):
    """Every reason update.json was refused, one per line."""


@dataclass(frozen=True)
class UpdateSettings:
    feed: str
    key: str  # the gate's download key: it lets a kit download, never makes one install anything


def check(raw: dict) -> UpdateSettings:
    errors = []
    feed = raw.get("feed", "latest")
    if feed not in FEEDS:
        errors.append(f"feed must be one of {', '.join(FEEDS)}.")
    key = raw.get("key", "")
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        errors.append("key must be the download key: 32 to 128 letters, digits, - and _.")
    if errors:
        raise UpdateSettingsError("\n".join(errors))
    return UpdateSettings(feed, key)


def load(path: Path) -> UpdateSettings | None:
    """None when the file is absent (a kit with no updater set up); UpdateSettingsError when it is unusable."""
    try:
        raw = json.loads(Path(path).read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise UpdateSettingsError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise UpdateSettingsError(f"{path} must hold one JSON object.")
    return check(raw)


def write(path: Path, settings: UpdateSettings) -> None:
    Path(path).write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """`check` prints every refusal; `write PATH` writes update.json as well."""
    parser = argparse.ArgumentParser(prog="python -m src.kit.update_settings")
    parser.add_argument("action", choices=["check", "write"])
    parser.add_argument("path", nargs="?", type=Path)
    parser.add_argument("--feed", default="latest")
    parser.add_argument("--key", required=True)
    args = parser.parse_args(argv)
    if args.action == "write" and args.path is None:
        parser.error("write needs the path of the update.json to write")
    try:
        settings = check({"feed": args.feed, "key": args.key})
    except UpdateSettingsError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.action == "write":
        write(args.path, settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
