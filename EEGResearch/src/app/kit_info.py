"""Which student kit this sidecar runs inside, for `/healthz`: the lesson page reports it to the admin page."""

from __future__ import annotations

import logging
import os
import re
import sys
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)
_VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}")
_failed_before = False


def for_healthz() -> dict | None:
    """kit(), never raising: the page and the backend read a failed health check as the sidecar being down."""
    global _failed_before
    try:
        return kit()
    except Exception:  # noqa: BLE001
        if not _failed_before:  # the page probes every few seconds
            _failed_before = True
            logger.exception("the kit version could not be read; /healthz reports none")
        return {"version": None}


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
