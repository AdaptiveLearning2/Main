"""Which student kit this sidecar runs inside, for `/healthz`: the lesson page reports it to the admin page."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

_VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")


def kit(frozen: bool | None = None, executable: str | None = None,
        environ: Mapping[str, str] = os.environ) -> dict | None:
    """None outside a kit; else {"version": version.txt's}, or {"version": None} when it does not read as one.

    Inside a kit is what the launcher means by it: the frozen exe, or a run from source with KIT_APP_DIR.
    """
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    if frozen:
        app = Path(executable or sys.executable).resolve().parent
    elif environ.get("KIT_APP_DIR"):
        app = Path(environ["KIT_APP_DIR"])
    else:
        return None
    try:
        text = (app / "version.txt").read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return {"version": None}
    return {"version": text if _VERSION.fullmatch(text) else None}
