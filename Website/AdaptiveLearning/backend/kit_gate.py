"""The admin page's way to the student kit's Setup installer, through the gate Worker (EEGResearch/installer/update_gate).

The installer carries this deployment's learner token and download key, so it is never public: the gate serves it only
to a link signed with KIT_LINK_SECRET, its own LINK_SECRET. See docs/student-kit.md, "The admin page's installer".
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re

import httpx

LINK_TTL_S = 600
# Cloudflare refuses Python's default User-Agents in front of the gate (403, error 1010).
USER_AGENT = "AdaptiveLearningBackend"
_VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SETUP_FILE = re.compile(r"AdaptiveLearningSensors-Setup-\d{1,4}\.\d{1,4}\.\d{1,4}\.exe")

def client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Never follows a redirect: the link's signature would go with it."""
    return httpx.Client(timeout=5.0, follow_redirects=False, headers={"User-Agent": USER_AGENT}, transport=transport)


_HTTP = client()


class GateError(Exception):
    """The gate could not be asked, or did not answer as the gate answers."""


def settings(environ=os.environ) -> tuple[str, str] | None:
    """(gate URL, link secret), or None unless both are set."""
    gate = (environ.get("KIT_GATE_URL") or "").strip().rstrip("/")
    secret = (environ.get("KIT_LINK_SECRET") or "").strip()
    return (gate, secret) if gate and secret else None


def signature(secret: str, kind: str, exp: int) -> str:
    """HMAC-SHA256 of "kind:exp" in hex; kind is "setup" (the installer) or "meta" (its details)."""
    return hmac.new(secret.encode("utf-8"), f"{kind}:{exp}".encode("ascii"), hashlib.sha256).hexdigest()


def link(gate: str, secret: str, kind: str, now: float) -> tuple[str, int]:
    """A link the gate honours until the returned unix time."""
    exp = int(now) + LINK_TTL_S
    path = "/v1/setup/current.json" if kind == "meta" else "/v1/setup/current"
    return f"{gate}{path}?exp={exp}&sig={signature(secret, kind, exp)}", exp


def current(gate: str, secret: str, now: float, http: httpx.Client | None = None) -> dict | None:
    """The Setup installer the gate offers; None when the gate says none is published. Raises GateError."""
    url, _ = link(gate, secret, "meta", now)
    try:
        response = (http or _HTTP).get(url)
    except httpx.HTTPError as e:
        raise GateError(f"could not reach the gate: {type(e).__name__}") from e
    # Only the gate's own marker means "none": any other 404 is a wrong KIT_GATE_URL, not an empty bucket.
    if response.status_code == 404 and response.headers.get("X-Kit-Setup") == "none":
        return None
    if response.status_code != 200:
        hint = "; does KIT_LINK_SECRET match the gate's LINK_SECRET?" if response.status_code == 403 else ""
        raise GateError(f"the gate answered {response.status_code}{hint}")
    try:
        data = response.json()
        found = {"version": data["version"], "file": data["file"], "sha256": data["sha256"], "size": data["size"],
                 "published_at": data["published"]}
    except (ValueError, KeyError, TypeError) as e:
        raise GateError("the gate's setup/current.json is not readable") from e
    if not (isinstance(found["version"], str) and _VERSION.fullmatch(found["version"])
            and isinstance(found["file"], str) and _SETUP_FILE.fullmatch(found["file"])
            and isinstance(found["sha256"], str) and _SHA256.fullmatch(found["sha256"])
            and type(found["size"]) is int and found["size"] > 0 and isinstance(found["published_at"], str)):
        raise GateError("the gate's setup/current.json does not describe a Setup installer")
    return found
