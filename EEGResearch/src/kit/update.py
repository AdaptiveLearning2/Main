"""The kit's self-updater: a signed feed through the download gate, a staged Update installer, and a rollback.

A SYSTEM scheduled task runs five actions in order (docs/student-kit.md, "The kit updates itself"); this module
is actions 1, 3 and 5, and registers the task. Standard library, `cryptography` and winproc only: never the sidecar.
"""

from __future__ import annotations

import base64
import codecs
import datetime as dt
import hashlib
import http.client
import json
import logging
import ntpath
import os
import random
import re
import ssl
import subprocess
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import NamedTuple
from xml.sax.saxutils import escape

from src.kit import update_keys, update_settings, winproc

logger = logging.getLogger("src.kit.update")

MODES = ("--update", "--after-update", "--start-sessions", "--register-task")
TASK_NAME = "AdaptiveLearning Sensors Update"
APP_FOLDER = "AdaptiveLearning Sensors"  # the only place the task runs from: under Program Files, students cannot write
EXE = "AdaptiveLearningSensors.exe"
FEED_PREFIX = b"AdaptiveLearningSensors update feed v1\n"  # signed with the manifest, so no other signed blob passes
MAX_FEED_BYTES = 64 * 1024
MAX_INSTALLER_BYTES = 512 * 1024 * 1024
ROLLBACK_FLOOR = (0, 2, 0)  # the first version with --update: a kit rolled back below it could never update again
QUIET_SESSION_S = 180  # a session this new is still signing in, so installing now interrupts no lesson
IDLE_FREE_S = 30 * 60  # no input for this long: nobody is in a lesson, whether the session is locked or not
APPLY_FRESH_S = 15 * 60  # the launcher stands aside while an install this recent may be running
MAX_SETUP_TRIES = 3  # a setup that fails proves nothing about the version, so it is retried before being blocked
STALE_FEED_DAYS = 180
TIMEOUT_S = 60
CHUNK = 1 << 20
SETUP_ARGS = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-"
APPLY, ROLLBACK = "apply.exe", "rollback.exe"  # hard links actions 2 and 4 run when present; action 1 decides which
_VERSION = re.compile(r"(\d{1,4})\.(\d{1,4})\.(\d{1,4})")
_SHA256 = re.compile(r"[0-9a-f]{64}")

Version = tuple[int, int, int]


class FeedError(ValueError):
    """Why a feed or a download was refused."""


@dataclass(frozen=True)
class Release:
    version: Version
    file: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Manifest:
    feed: str  # latest or canary: signed, so one feed cannot be served as the other
    release: Release
    published: dt.datetime  # a computer refuses a feed older than one it has read, so an old one cannot be replayed
    rollout: int  # percent of computers, by rollout_bucket
    history: tuple[Release, ...]  # every earlier published installer: a computer's way back is its own version's


@dataclass(frozen=True)
class Decision:
    state: str  # up_to_date, blocked, not_in_rollout, no_rollback or eligible
    detail: str
    stage: tuple[Release, ...] = ()


class Presence(NamedTuple):
    """One signed-in session, as the updater judges whether a lesson may be under way in it."""
    age: float | None  # seconds since sign-in; None when Windows would not say
    active: bool  # at the keyboard; a session switched away from has nobody in a lesson
    idle: bool | None  # the kit's report of no input for IDLE_FREE_S; None where the kit is not running


def parse_version(text: object) -> Version:
    match = _VERSION.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise FeedError(f"not a version: {text!r}")
    return int(match[1]), int(match[2]), int(match[3])


def version_text(version: Version) -> str:
    return "%d.%d.%d" % version


def installer_name(version: Version) -> str:
    return f"AdaptiveLearningSensors-Update-{version_text(version)}.exe"


def load_keys(encoded: dict[str, str]) -> dict[str, bytes]:
    """Each named key as its 32 raw bytes; ValueError for one that is not an Ed25519 public key."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: PLC0415

    keys = {}
    for name, text in encoded.items():
        raw = base64.b64decode(text, validate=True)
        Ed25519PublicKey.from_public_bytes(raw)  # raises on a wrong length
        keys[name] = raw
    return keys


def _signed_by(keys: dict[str, bytes], signature: bytes, message: bytes) -> str | None:
    from cryptography.exceptions import InvalidSignature  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: PLC0415

    for name, raw in keys.items():
        try:
            Ed25519PublicKey.from_public_bytes(raw).verify(signature, message)
        except InvalidSignature:
            continue
        return name
    return None


def _release(raw: object) -> Release:
    """Names other than these four are ignored, so a newer publisher's feed still reads here."""
    if not isinstance(raw, dict):
        raise FeedError("a release must be an object")
    version = parse_version(raw.get("version"))
    file, sha256, size = raw.get("file"), raw.get("sha256"), raw.get("size")
    if file != installer_name(version):  # exact: a path or another program's name never reaches the disk
        raise FeedError(f"file {file!r} is not {installer_name(version)}")
    if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
        raise FeedError("sha256 must be 64 lower-case hex digits")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_INSTALLER_BYTES:
        raise FeedError(f"size {size!r} is outside 1 to {MAX_INSTALLER_BYTES} bytes")
    return Release(version, file, sha256, size)


def verify_feed(raw: bytes, keys: dict[str, bytes], feed: str | None = None) -> tuple[Manifest, str]:
    """The manifest and the name of the key that signed it, which must be the named feed's; FeedError otherwise."""
    try:
        outer = json.loads(raw)
        signed = base64.b64decode(outer["manifest"], validate=True)
        signature = base64.b64decode(outer["signature"], validate=True)
    except (ValueError, KeyError, TypeError) as exc:
        raise FeedError(f"the feed is not a signed manifest: {exc}") from exc
    signer = _signed_by(keys, signature, FEED_PREFIX + signed)
    if signer is None:
        raise FeedError("the feed's signature matches none of this kit's keys")
    try:
        body = json.loads(signed)
        published = dt.datetime.fromisoformat(body["published"])
    except (ValueError, KeyError, TypeError) as exc:
        raise FeedError(f"the signed manifest does not read: {exc}") from exc
    if published.tzinfo is None:
        raise FeedError("published must carry its time zone")
    if body.get("feed") not in update_settings.FEEDS or (feed is not None and body["feed"] != feed):
        raise FeedError(f"this is the {body.get('feed')!r} feed, not {feed!r}")
    release = _release(body)
    rollout = body.get("rollout")
    if isinstance(rollout, bool) or not isinstance(rollout, int) or not 0 <= rollout <= 100:
        raise FeedError(f"rollout {rollout!r} is not a percentage")
    if not isinstance(body.get("history"), list):
        raise FeedError("history must be a list of earlier releases")
    history = tuple(_release(entry) for entry in body["history"])
    if len({entry.version for entry in history}) != len(history):
        raise FeedError("history names a version twice")
    for entry in history:
        if not ROLLBACK_FLOOR <= entry.version < release.version:
            raise FeedError(f"history's {version_text(entry.version)} is not older than "
                            f"{version_text(release.version)} and at least {version_text(ROLLBACK_FLOOR)}")
    return Manifest(body["feed"], release, published, rollout, history), signer


def rollout_bucket(machine_guid: str | None) -> int:
    """0-99, fixed for one computer, so a rollout's first computers stay first; unknown is the last bucket."""
    if not machine_guid:
        return 99
    return int.from_bytes(hashlib.sha256(b"rollout\0" + machine_guid.lower().encode()).digest()[:8], "big") % 100


def decide(installed: Version, manifest: Manifest, blocked: set[Version], bucket: int) -> Decision:
    """Whether this computer should stage the feed's release; when to install it is quiet()'s answer."""
    target = manifest.release
    name = version_text(target.version)
    if target.version <= installed:  # never a downgrade: a rollback is only ever the local one, after a failed check
        return Decision("up_to_date", f"{version_text(installed)} is installed; the feed names {name}")
    if target.version in blocked:
        return Decision("blocked", f"{name} failed here before and is skipped until a newer version is published")
    if bucket >= manifest.rollout:
        return Decision("not_in_rollout", f"{name} is out to {manifest.rollout}% of computers; this one is {bucket}")
    # The way back is the version running now, which works here; never one this computer has not run.
    way_back = next((entry for entry in manifest.history if entry.version == installed), None)
    if way_back is None:
        return Decision("no_rollback", f"the feed lists no installer for {version_text(installed)}, the version to "
                                       f"come back to, so {name} is not installed")
    return Decision("eligible", f"{name} is due", (target, way_back))


def quiet(sessions: list[Presence]) -> bool:
    """No lesson can be under way: every session at the keyboard is just signed in, or the kit reports it idle.

    A session Windows will not date, with no idle report, counts as a lesson.
    """
    return all(not s.active or s.idle is True or (s.age is not None and 0 <= s.age < QUIET_SESSION_S)
               for s in sessions)


# --- the files under {app}\updates ---------------------------------------------------------------------------

def updates_dir(app: Path) -> Path:
    return app / "updates"


def installed_version(app: Path) -> Version:
    """version.txt, which every installer writes; kit.json's version goes stale after an update."""
    try:
        return parse_version((app / "version.txt").read_text(encoding="ascii").strip())
    except (OSError, UnicodeDecodeError) as exc:
        raise FeedError(f"version.txt does not read: {exc}") from exc


def _read_json(path: Path):
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("ignoring %s: %s", path, exc)
        return None


def _write_json(path: Path, value) -> None:
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(part, path)


def read_blocked(updates: Path) -> set[Version]:
    found = set()
    for text in _read_json(updates / "blocked.json") or []:
        try:
            found.add(parse_version(text))
        except FeedError:
            continue
    return found


def block(updates: Path, version: Version) -> None:
    blocked = read_blocked(updates) | {version}
    _write_json(updates / "blocked.json", sorted(version_text(v) for v in blocked))


def count_setup_failure(updates: Path, version: Version) -> int:
    """How many times setup has now failed to install version here."""
    failures = _read_json(updates / "failures.json")
    failures = failures if isinstance(failures, dict) else {}
    tries = int(failures.get(version_text(version), 0)) + 1
    _write_json(updates / "failures.json", {**failures, version_text(version): tries})
    return tries


ATTEMPT_STATES = ("installing", "passed", "failed", "setup_failed", "repairing")
SETUP_SUCCEEDED = "Installation process succeeded."  # the line Inno Setup's /LOG ends a completed install with


def read_attempt(updates: Path) -> dict | None:
    """attempt.json, the install this task armed: {version, way_back (a release), at, check, rollbacks}."""
    attempt = _read_json(updates / "attempt.json")
    if not isinstance(attempt, dict) or attempt.get("check") not in ATTEMPT_STATES:
        return None
    try:
        parse_version(attempt.get("version"))
        _release(attempt.get("way_back"))
    except FeedError:
        return None
    return attempt


def _retire_logs(updates: Path) -> None:
    """The installers' logs move aside when a run is armed, so a log found later is that run's own."""
    for name in ("apply", "rollback"):
        if (updates / f"{name}.log").exists():
            os.replace(updates / f"{name}.log", updates / f"{name}.previous.log")


def log_text(raw: bytes) -> str:
    """An installer log as text: Inno 6 writes UTF-8 with a BOM (tests/fixtures/inno_setup_completed.log.gz holds a
    real one); UTF-16, with or without its BOM, is read too, so another encoding cannot hide a finished install."""
    if raw.startswith(codecs.BOM_UTF8):
        return raw.decode("utf-8-sig", "replace")
    if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return raw.decode("utf-16", "replace")
    if b"\0" in raw[:256]:
        return raw.decode("utf-16-le", "replace")
    return raw.decode("utf-8", "replace")


def rollback_completed(updates: Path, since: float) -> bool:
    """Whether the rollback installer ran to the end after since: its log is written only once it runs."""
    log = updates / "rollback.log"
    try:
        return log.stat().st_mtime >= since and SETUP_SUCCEEDED in log_text(log.read_bytes())
    except OSError:
        return False


def _arm_rollback(updates: Path, attempt: dict, check: str, now: float, ensure: Callable[[Release], Path]) -> None:
    """rollback.exe for this run's action 4, staged again first if it went missing, and this run's time for action 5."""
    staged = ensure(_release(attempt["way_back"]))
    _retire_logs(updates)
    os.link(staged, updates / ROLLBACK)
    _write_json(updates / "attempt.json", {**attempt, "check": check, "at": now})


def resume_attempt(updates: Path, installed: Version, free: Callable[[], bool], now: float,
                   ensure: Callable[[Release], Path]) -> tuple[str | None, str | None]:
    """Action 1's first step, finishing an earlier run's install: (None, note) once it is settled, or this run's
    (state, detail) when the run is spent on it. An install with no recorded check is never taken as passed."""
    attempt = read_attempt(updates)
    if attempt is None:
        return None, None
    version, check = parse_version(attempt["version"]), attempt["check"]
    back = attempt["way_back"]["version"]
    name = version_text(version)
    settled = updates / "attempt.json"
    if check == "passed":
        settled.unlink()
        return None, f"{name} installed and passed its check"
    if check == "setup_failed":
        settled.unlink()
        tries = count_setup_failure(updates, version)
        if tries >= MAX_SETUP_TRIES:
            block(updates, version)
        return None, f"setup did not install {name} (try {tries} of {MAX_SETUP_TRIES}; see apply.log)"
    if check == "repairing":
        settled.unlink()
        return None, f"{name} never ran its check, so it is blocked, and {back} was reinstalled"
    if check == "failed" and installed != version:
        settled.unlink()
        return None, f"{name} failed its check and was rolled back to {version_text(installed)}"
    if check == "failed":  # action 4 did not take it back, so it runs again, a few times at most
        rollbacks = int(attempt.get("rollbacks", 1))
        if rollbacks >= MAX_SETUP_TRIES:
            settled.unlink()
            return None, f"{name} failed its check and {rollbacks} rollbacks to {back} did not take; it stays " \
                         "installed, blocked, until a newer version (see rollback.log)"
        if not free():
            return "rollback_waiting", f"{name} failed its check and is still installed; it is rolled back to {back} " \
                                       "once nobody is mid-lesson"
        _arm_rollback(updates, {**attempt, "rollbacks": rollbacks + 1}, "failed", now, ensure)
        return "rolling_back", f"{name} failed its check and is still installed; rolling back to {back}"
    if installed == version:  # installed, then the run was cut short before its check
        if not free():
            return "check_waiting", f"{name} was installed but never checked; it is checked once nobody is mid-lesson"
        _arm_rollback(updates, attempt, "installing", now, ensure)
        return "checking", f"{name} was installed but never checked; checking it now"
    block(updates, version)  # it never ran its check: it could not start, or setup itself was cut short
    if rollback_completed(updates, float(attempt["at"])):  # it could not start, and that run's action 4 took it back
        settled.unlink()
        return None, f"{name} could not run its check and was rolled back to {back}; it is blocked here"
    if not free():
        return "repair_waiting", f"{name} never ran its check and is blocked; {back} is reinstalled once nobody " \
                                 "is mid-lesson"
    _arm_rollback(updates, attempt, "repairing", now, ensure)
    return "repairing", f"{name} never ran its check and is blocked; reinstalling {back} so the install is whole"


def applying(app: Path, now: float | None = None) -> bool:
    """True while an install or rollback the task armed may be running: the launcher stands aside, to be started after."""
    updates = updates_dir(app)
    try:
        started = (updates / "attempt.json").stat().st_mtime
    except OSError:
        return False
    armed = (updates / APPLY).exists() or (updates / ROLLBACK).exists()
    return armed and (time.time() if now is None else now) - started < APPLY_FRESH_S


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


# --- the network -------------------------------------------------------------------------------------------

class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """The gate never redirects; following one would send the download key to another host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise FeedError(f"refused a redirect ({code}) to {newurl}")


def opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    """Windows' certificate store and certifi's both, and the WinHTTP proxy when one is set."""
    context = ssl.create_default_context()
    try:
        import certifi  # noqa: PLC0415

        context.load_verify_locations(certifi.where())
    except (ImportError, OSError):
        pass
    proxies = {"https": proxy} if proxy else None  # None: urllib reads the environment and the registry
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxies),
                                       urllib.request.HTTPSHandler(context=context), _RefuseRedirects())


def _get(net: urllib.request.OpenerDirector, path: str, key: str):
    request = urllib.request.Request(f"{update_settings.GATE_URL}{path}",
                                     headers={"Authorization": f"Bearer {key}", "User-Agent": "AdaptiveLearningSensors"})
    return net.open(request, timeout=TIMEOUT_S)


def fetch_feed(net, settings: update_settings.UpdateSettings) -> bytes:
    with _get(net, f"/v1/feed/{settings.feed}.json", settings.key) as response:
        body = response.read(MAX_FEED_BYTES + 1)
    if len(body) > MAX_FEED_BYTES:
        raise FeedError(f"the feed is over {MAX_FEED_BYTES} bytes")
    return body


def remember_feed(updates: Path, manifest: Manifest) -> None:
    """Refuses a feed older than the newest this computer has read of it, which is how a halt stays halted."""
    seen = _read_json(updates / "seen.json")
    seen = seen if isinstance(seen, dict) else {}
    try:
        before = dt.datetime.fromisoformat(seen[manifest.feed])
    except (KeyError, TypeError, ValueError):
        before = None
    if before is not None and manifest.published < before:
        raise FeedError(f"the {manifest.feed} feed from {manifest.published.isoformat()} is older than the one read "
                        f"here from {before.isoformat()}: an old feed served again")
    if before is None or manifest.published > before:
        _write_json(updates / "seen.json", {**seen, manifest.feed: manifest.published.isoformat()})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def download(net, settings: update_settings.UpdateSettings, release: Release, updates: Path) -> Path:
    """The installer at updates/release.file, read no further than its size and checked against its SHA-256."""
    target = updates / release.file
    if target.is_file() and target.stat().st_size == release.size and _sha256(target) == release.sha256:
        return target
    part = target.with_name(target.name + ".part")
    digest, written = hashlib.sha256(), 0
    try:
        with _get(net, f"/v1/files/{release.file}", settings.key) as response, open(part, "wb") as out:
            while chunk := response.read(min(CHUNK, release.size + 1 - written)):
                written += len(chunk)
                if written > release.size:
                    raise FeedError(f"{release.file} is longer than the feed's {release.size} bytes")
                digest.update(chunk)
                out.write(chunk)
        if written != release.size:
            raise FeedError(f"{release.file} ended at {written} bytes; the feed says {release.size}")
        if digest.hexdigest() != release.sha256:
            raise FeedError(f"{release.file} does not match the feed's SHA-256")
        os.replace(part, target)
    finally:
        _unlink(part)
    return target


# --- the task's actions ------------------------------------------------------------------------------------

@dataclass
class Machine:
    """What one run reads from the computer; tests substitute each."""
    sessions: Callable[[], list[Presence]]
    machine_guid: Callable[[], str | None]
    net: Callable[[], urllib.request.OpenerDirector]
    keys: Callable[[], dict[str, bytes]]
    now: Callable[[], float] = time.time


def real_machine() -> Machine:
    def sessions() -> list[Presence]:
        now = time.time()
        return [Presence(None if s.logon is None else now - s.logon, s.active,
                         winproc.session_idle(s.id) if s.active else None) for s in winproc.signed_in_sessions()]

    return Machine(sessions, winproc.machine_guid, lambda: opener(winproc.winhttp_proxy()),
                   lambda: load_keys(update_keys.PUBLIC_KEYS))


def check_for_update(app: Path, machine: Machine) -> int:
    """Action 1: finish an earlier install, else read the feed, stage what is due, and arm actions 2 to 4 when quiet."""
    updates = updates_dir(app)
    updates.mkdir(exist_ok=True)
    for name in (APPLY, ROLLBACK):  # a previous run's; this run arms them afresh or not at all
        _unlink(updates / name)
    status = {"checked_at": dt.datetime.fromtimestamp(machine.now(), dt.UTC).isoformat(timespec="seconds")}
    free = lambda: quiet(machine.sessions())  # noqa: E731 -- asked only where an answer changes what happens

    def ensure(release: Release) -> Path:  # a staged installer, fetched again if something removed or damaged it
        settings = update_settings.load(app / update_settings.UPDATE_FILE)
        if settings is None:
            raise FeedError(f"{release.file} is not staged, and with no update.json it cannot be fetched again")
        return download(machine.net(), settings, release, updates)

    try:
        installed = installed_version(app)
        status["installed"] = version_text(installed)
        state, note = resume_attempt(updates, installed, free, machine.now(), ensure)
        status["last_install"] = note
        if state is not None:
            return _finish(updates, status, state, note)
        settings = update_settings.load(app / update_settings.UPDATE_FILE)
        if settings is None:
            return _finish(updates, status, "not_set_up", "no update.json: this kit was installed without updates")
        status["feed"] = settings.feed
        net = machine.net()
        manifest, signer = verify_feed(fetch_feed(net, settings), machine.keys(), settings.feed)
        remember_feed(updates, manifest)
        age = (dt.datetime.fromtimestamp(machine.now(), dt.UTC) - manifest.published).days
        status.update(feed_version=version_text(manifest.release.version), signed_by=signer,
                      published=manifest.published.isoformat(), feed_age_days=age)
        if age > STALE_FEED_DAYS:
            status["warning"] = f"the feed was published {age} days ago"
        decision = decide(installed, manifest, read_blocked(updates), rollout_bucket(machine.machine_guid()))
        if decision.state != "eligible":
            _prune(updates, ())
            return _finish(updates, status, decision.state, decision.detail)
        staged = [download(net, settings, release, updates) for release in decision.stage]
        _prune(updates, decision.stage)
        if not free():
            return _finish(updates, status, "staged", f"{decision.detail}; it installs once nobody is mid-lesson")
        _arm_install(updates, manifest.release.version, decision.stage[1], staged, machine.now())
        return _finish(updates, status, "installing", decision.detail)
    except (FeedError, update_settings.UpdateSettingsError, OSError, ValueError, http.client.HTTPException) as exc:
        return _finish(updates, status, "failed", f"{type(exc).__name__}: {exc}", code=1)


def _arm_install(updates: Path, version: Version, way_back: Release, staged: list[Path], now: float) -> None:
    """attempt.json, then both links; if a link fails, none of it stays, so no version is blocked for not running."""
    _retire_logs(updates)
    _write_json(updates / "attempt.json", {"version": version_text(version), "at": now, "check": "installing",
                                            "way_back": {"version": version_text(way_back.version), "file": way_back.file,
                                                         "sha256": way_back.sha256, "size": way_back.size}})
    try:
        os.link(staged[1], updates / ROLLBACK)  # before apply.exe: action 2 must never run without its way back
        os.link(staged[0], updates / APPLY)
    except OSError:
        for name in (APPLY, ROLLBACK, "attempt.json"):
            _unlink(updates / name)
        raise


def _prune(updates: Path, keep: tuple[Release, ...]) -> None:
    wanted = {release.file for release in keep}
    for path in updates.glob("AdaptiveLearningSensors-Update-*.exe*"):
        if path.name not in wanted:
            _unlink(path)


def _finish(updates: Path, status: dict, state: str, detail: str, code: int = 0) -> int:
    status.update(state=state, detail=detail)
    (logger.error if code else logger.info)("%s: %s", state, detail)
    _write_json(updates / "status.json", status)
    return code


def after_update(app: Path, run_check: Callable[[Path, Path], int]) -> int:
    """Action 3, run by whichever version is installed now: the self-test, when this run armed one."""
    updates = updates_dir(app)
    _unlink(updates / APPLY)
    attempt = read_attempt(updates)
    # rollback.exe is there only when action 1 armed this run, so a check never runs mid-lesson.
    if attempt is None or attempt["check"] != "installing" or not (updates / ROLLBACK).exists():
        return 0
    version = parse_version(attempt["version"])
    if installed_version(app) != version:
        _unlink(updates / ROLLBACK)  # setup failed, so the previous version is still installed: nothing to undo
        _write_json(updates / "attempt.json", {**attempt, "check": "setup_failed"})
        logger.error("setup did not install %s; see apply.log", version_text(version))
        return 1
    root = logging.getLogger()
    handlers = root.handlers[:]
    try:
        passed = run_check(updates / "selftest.json", app) == 0
    except Exception:
        logger.exception("the check itself failed")
        passed = False
    finally:
        root.handlers[:] = handlers  # the self-test logs to its own file
    _write_json(updates / "attempt.json", {**attempt, "check": "passed" if passed else "failed"})
    if passed:
        _unlink(updates / ROLLBACK)
        logger.info("%s passed its check", version_text(version))
        return 0
    block(updates, version)
    logger.error("%s failed its check (updates\\selftest.json); action 4 rolls it back", version_text(version))
    return 1


def start_sessions(app: Path, now: float | None = None) -> int:
    """Action 5: after an install, check or rollback this run, the sensors again in each session at the keyboard."""
    for name in (APPLY, ROLLBACK):  # the run is over: left linked, they would keep a later sign-in's launcher waiting
        _unlink(updates_dir(app) / name)
    attempt = read_attempt(updates_dir(app))
    if attempt is None or (time.time() if now is None else now) - float(attempt.get("at", 0)) > 3600:
        return 0  # the task's own time limit is an hour, so an older attempt was not this run's
    started = winproc.start_in_sessions(str(app / EXE), str(app))
    logger.info("started the sensors in session(s) %s", started or "none: nobody is signed in")
    return 0


def installer_action(system_root: str, installer: PureWindowsPath, log: PureWindowsPath) -> tuple[str, str]:
    """An installer run through cmd's `if exist`: an action that cannot launch would end the task's run there."""
    return (str(PureWindowsPath(system_root) / "System32" / "cmd.exe"),
            f'/d /s /c "if exist "{installer}" "{installer}" {SETUP_ARGS} /LOG="{log}""')


def task_xml(app: Path, minute: int, system_root: str = r"C:\Windows") -> str:
    """The task's definition: SYSTEM; at boot, at any sign-in and hourly; five actions, each always launchable."""
    app = PureWindowsPath(app)
    updates = app / "updates"
    exe = str(app / EXE)
    actions = [(exe, "--update"),
               installer_action(system_root, updates / APPLY, updates / "apply.log"),
               (exe, "--after-update"),
               installer_action(system_root, updates / ROLLBACK, updates / "rollback.log"),
               (exe, "--start-sessions")]
    execs = "".join("<Exec><Command>%s</Command><Arguments>%s</Arguments><WorkingDirectory>%s</WorkingDirectory></Exec>"
                    % (escape(f'"{command}"'), escape(arguments), escape(str(updates)))
                    for command, arguments in actions)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Keeps AdaptiveLearning Sensors up to date.</Description></RegistrationInfo>
  <Principals><Principal id="Author"><UserId>S-1-5-18</UserId><RunLevel>HighestAvailable</RunLevel></Principal></Principals>
  <Triggers>
    <BootTrigger><Enabled>true</Enabled><Delay>PT2M</Delay></BootTrigger>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
    <TimeTrigger><StartBoundary>2026-01-01T00:{minute:02d}:00</StartBoundary><Enabled>true</Enabled><Repetition><Interval>PT1H</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition></TimeTrigger>
  </Triggers>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">{execs}</Actions>
</Task>
"""


def register_task(app: Path) -> int:
    """Run by the installer: the task, written as UTF-16 and handed to schtasks."""
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    schtasks = str(Path(system_root) / "System32" / "schtasks.exe")
    exists = subprocess.run([schtasks, "/Query", "/TN", TASK_NAME], capture_output=True).returncode == 0
    if exists and winproc.running_as_system():
        logger.info("kept the existing task: an install the task itself is running must not replace it")
        return 0
    definition = updates_dir(app) / "task.xml"
    # The hour's minute is drawn per install, so a fleet's checks spread across the hour.
    definition.write_text(task_xml(app, random.randrange(60), system_root), encoding="utf-16")
    result = subprocess.run([schtasks, "/Create", "/TN", TASK_NAME, "/XML", str(definition), "/F"],
                            capture_output=True, text=True)
    (logger.info if result.returncode == 0 else logger.error)("schtasks /Create: %s %s", result.returncode,
                                                              (result.stdout + result.stderr).strip())
    return result.returncode


def refusal(mode: str, app: Path, program_files: str | None, system: bool, elevated: bool) -> str | None:
    """Why this mode may not run here: only from the Program Files install, and only as SYSTEM (or, to register, an admin)."""
    expected = ntpath.normcase(ntpath.join(program_files or "", APP_FOLDER))
    if not program_files or ntpath.normcase(ntpath.normpath(str(app))) != expected:
        return f"{mode} runs only from {expected}"
    if mode == "--register-task":
        return None if system or elevated else "--register-task needs an administrator"
    return None if system else f"{mode} runs only as SYSTEM, from the update task"


def main(mode: str, app: Path) -> int:
    """The launcher's update modes: refused with exit 2 before anything is written, so a student's run leaves no trace."""
    from src.kit import launcher  # noqa: PLC0415 -- its imports are as light as this module's

    try:
        reason = refusal(mode, app, os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles"),
                         winproc.running_as_system(), winproc.running_elevated())
    except OSError as exc:
        reason = f"cannot tell who this is: {exc}"
    if reason:
        return 2
    updates = updates_dir(app)
    updates.mkdir(exist_ok=True)
    launcher._ensure_stdio(updates / "console.log")
    launcher.configure_logging(updates, "update.log")
    os.environ["PATH"] = winproc.system_path()  # as SYSTEM, a folder a user can write must not be searched for a DLL
    logger.info("%s", mode)
    try:
        if mode == "--update":
            return check_for_update(app, real_machine())
        if mode == "--after-update":
            from src.kit import selftest  # noqa: PLC0415

            return after_update(app, selftest.run)
        if mode == "--start-sessions":
            return start_sessions(app)
        return register_task(app)
    except Exception:
        logger.exception("%s failed", mode)
        return 1
