"""The admin page's way to the student kit's Setup installer, through the gate Worker (EEGResearch/installer/update_gate).

The installer carries this deployment's learner token and download key, so it is never public: the gate serves it only
to a link signed with KIT_LINK_SECRET, its own LINK_SECRET. See docs/student-kit.md, "The admin page's installer".
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime
from urllib.parse import urlsplit

import httpx

LINK_TTL_S = 600
# Cloudflare refuses Python's default User-Agents in front of the gate (403, error 1010).
USER_AGENT = "AdaptiveLearningBackend"
_VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SETUP_FILE = re.compile(r"AdaptiveLearningSensors-Setup-\d{1,4}\.\d{1,4}\.\d{1,4}\.exe")
_REPUBLISH = "publish it again with -Setup"


def client(transport: httpx.BaseTransport | None = None) -> httpx.Client:
    """Never follows a redirect: the link's signature would go with it."""
    return httpx.Client(timeout=5.0, follow_redirects=False, headers={"User-Agent": USER_AGENT}, transport=transport)


_HTTP = client()


class GateError(Exception):
    """The gate could not be asked, or did not answer as set up. Not `retryable`: asking again cannot help."""

    def __init__(self, message: str, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


def settings(environ=os.environ) -> tuple[str, str] | None:
    """(gate URL, link secret), or None unless both are set."""
    gate = (environ.get("KIT_GATE_URL") or "").strip().rstrip("/")
    secret = (environ.get("KIT_LINK_SECRET") or "").strip()
    return (gate, secret) if gate and secret else None


def signature(secret: str, subject: str, exp: int) -> str:
    """HMAC-SHA256 of "subject:exp" in hex. The subject is "meta", or "setup:<file>", so a link opens one file."""
    return hmac.new(secret.encode("utf-8"), f"{subject}:{exp}".encode("ascii"), hashlib.sha256).hexdigest()


def link(gate: str, secret: str, now: float, file: str | None = None) -> tuple[str, int]:
    """A link to one Setup installer, or with no file to setup/current.json; the gate honours it until the unix time."""
    exp = int(now) + LINK_TTL_S
    path, subject = (f"/v1/setup/files/{file}", f"setup:{file}") if file else ("/v1/setup/current.json", "meta")
    return f"{gate}{path}?exp={exp}&sig={signature(secret, subject, exp)}", exp


def _refused(response: httpx.Response) -> GateError:
    """What to fix, for an answer meaning the gate is set up wrong; any other answer may pass, so it is retried."""
    status, mark = response.status_code, response.headers.get("X-Kit-Setup")
    if 300 <= status < 400:
        where = urlsplit(response.headers.get("Location", "")).netloc or "elsewhere"
        return GateError(f"KIT_GATE_URL redirects ({status}, to {where}): set it to the gate's own https address", False)
    if status == 401:
        return GateError("the gate asked for its download key, so it predates the Setup links: redeploy the Worker",
                         False)
    if status == 403 and mark == "refused":
        return GateError("the gate refused the link: KIT_LINK_SECRET is not the gate's LINK_SECRET", False)
    if status == 403:
        return GateError("something in front of the gate refused the request (403)", False)
    if status == 503 and mark == "no-secret":
        return GateError("the gate has no LINK_SECRET set", False)
    if status == 404 and mark is None:
        return GateError("KIT_GATE_URL does not point at the gate: it answered 404", False)
    return GateError(f"the gate answered {status}", True)


def _ask(http: httpx.Client | None, method: str, url: str) -> httpx.Response:
    try:
        return (http or _HTTP).request(method, url)
    except httpx.HTTPError as e:
        raise GateError(f"could not reach the gate: {type(e).__name__}", True) from e


def _is_time(value: object) -> bool:
    """An ISO 8601 time with its offset, as `kit_release.py setup` writes one."""
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _described(response: httpx.Response) -> dict:
    """The installer setup/current.json describes; one describing none is a publish to redo, not an outage."""
    unreadable = GateError(f"setup/current.json does not describe a Setup installer: {_REPUBLISH}", False)
    try:
        data = response.json()
        found = {"version": data["version"], "file": data["file"], "sha256": data["sha256"], "size": data["size"],
                 "published_at": data["published"]}
    except (ValueError, KeyError, TypeError) as e:
        raise unreadable from e
    if not (isinstance(found["version"], str) and _VERSION.fullmatch(found["version"])
            and isinstance(found["file"], str) and _SETUP_FILE.fullmatch(found["file"])
            and isinstance(found["sha256"], str) and _SHA256.fullmatch(found["sha256"])
            and type(found["size"]) is int and found["size"] > 0 and _is_time(found["published_at"])):
        raise unreadable
    return found


def current(gate: str, secret: str, now: float, http: httpx.Client | None = None) -> dict | None:
    """The Setup installer the gate offers, checked downloadable; None when the gate says none is published.

    Raises GateError: retryable for an outage, not for a gate set up wrong or a publish left half done.
    """
    meta = _ask(http, "GET", link(gate, secret, now)[0])
    if meta.status_code == 404 and meta.headers.get("X-Kit-Setup") == "none":
        return None
    if meta.status_code != 200:
        raise _refused(meta)
    found = _described(meta)
    head = _ask(http, "HEAD", link(gate, secret, now, found["file"])[0])
    if head.status_code == 404 and head.headers.get("X-Kit-Setup") == "missing":
        raise GateError(f"setup/current.json names {found['file']}, which the bucket does not hold: {_REPUBLISH}", False)
    if head.status_code != 200:
        raise _refused(head)
    # Only when sent: a HEAD's length is not something every proxy keeps.
    size = head.headers.get("Content-Length")
    if size is not None and size != str(found["size"]):
        raise GateError(f"{found['file']} is not the size setup/current.json gives: {_REPUBLISH}", False)
    return found
