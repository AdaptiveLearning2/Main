"""The kit's self-updater: a signed feed through the download gate, a staged Update installer, and a rollback.

A SYSTEM scheduled task runs five actions in order (docs/student-kit.md, "The kit updates itself"); this module
is actions 1, 3 and 5, and registers the task. Standard library, `cryptography` and winproc only: never the sidecar.
"""

from __future__ import annotations

import base64
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
APPLY_FRESH_S = 15 * 60  # the launcher stands aside while an install this recent may be running
STALE_FEED_DAYS = 180
TIMEOUT_S = 60
CHUNK = 1 << 20
SETUP_ARGS = "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-"
APPLY, ROLLBACK = "apply.exe", "rollback.exe"  # hard links the task's actions 2 and 4 run; absent, those fail harmlessly
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
    release: Release
    published: dt.datetime
    rollout: int  # percent of computers, by rollout_bucket
    rollback_to: Release | None


@dataclass(frozen=True)
class Decision:
    state: str  # up_to_date, blocked, not_in_rollout, no_rollback or eligible
    detail: str
    stage: tuple[Release, ...] = ()


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


def verify_feed(raw: bytes, keys: dict[str, bytes]) -> tuple[Manifest, str]:
    """The manifest and the name of the key that signed it; FeedError for anything else."""
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
    release = _release(body)
    rollout = body.get("rollout")
    if isinstance(rollout, bool) or not isinstance(rollout, int) or not 0 <= rollout <= 100:
        raise FeedError(f"rollout {rollout!r} is not a percentage")
    rollback = body.get("rollback_to")
    if rollback is not None:
        rollback = _release(rollback)
        if not ROLLBACK_FLOOR <= rollback.version < release.version:
            raise FeedError(f"rollback_to {version_text(rollback.version)} is not older than "
                            f"{version_text(release.version)} and at least {version_text(ROLLBACK_FLOOR)}")
    return Manifest(release, published, rollout, rollback), signer


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
    if manifest.rollback_to is None:
        return Decision("no_rollback", f"the feed names no version to roll back to, so {name} is not installed")
    return Decision("eligible", f"{name} is due", (target, manifest.rollback_to))


def quiet(session_ages: list[float | None]) -> bool:
    """Nobody signed in, or everyone only just did: an unknown sign-in time counts as a lesson under way."""
    return all(age is not None and 0 <= age < QUIET_SESSION_S for age in session_ages)


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


def read_attempt(updates: Path) -> dict | None:
    """attempt.json, the install this task is running or last ran: {version, from, at, check}."""
    attempt = _read_json(updates / "attempt.json")
    try:
        return attempt if isinstance(attempt, dict) and parse_version(attempt.get("version")) else None
    except FeedError:
        return None


def settle_attempt(updates: Path, installed: Version) -> str | None:
    """Action 1's first step: an install whose version is not the one installed now did not stick, so it is blocked.

    True even when action 3 never ran, which is the case of a new version whose Python cannot start.
    """
    attempt = read_attempt(updates)
    if attempt is None:
        return None
    (updates / "attempt.json").unlink()
    version = parse_version(attempt["version"])
    if version != installed:
        block(updates, version)
        return (f"{version_text(version)} did not stick (its check failed, or setup did; see apply.log): "
                f"{version_text(installed)} is installed and {version_text(version)} is blocked here")
    if version in read_blocked(updates):
        return f"{version_text(version)} failed its check and the rollback did not happen; it is still installed"
    return f"{version_text(version)} installed and passed its check"


def applying(app: Path, now: float | None = None) -> bool:
    """True while an install the task staged may be running: the launcher then stands aside, to be started after."""
    updates = updates_dir(app)
    try:
        started = (updates / "attempt.json").stat().st_mtime
    except OSError:
        return False
    return (updates / APPLY).exists() and (time.time() if now is None else now) - started < APPLY_FRESH_S


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
    session_ages: Callable[[], list[float | None]]
    machine_guid: Callable[[], str | None]
    net: Callable[[], urllib.request.OpenerDirector]
    keys: Callable[[], dict[str, bytes]]
    now: Callable[[], float] = time.time


def real_machine() -> Machine:
    def ages() -> list[float | None]:
        now = time.time()
        return [None if logon is None else now - logon for _, _, logon in winproc.signed_in_sessions()]

    return Machine(ages, winproc.machine_guid, lambda: opener(winproc.winhttp_proxy()),
                   lambda: load_keys(update_keys.PUBLIC_KEYS))


def check_for_update(app: Path, machine: Machine) -> int:
    """Action 1: settle the last install, read the feed, stage what is due, and arm actions 2 and 4 when quiet."""
    updates = updates_dir(app)
    updates.mkdir(exist_ok=True)
    for name in (APPLY, ROLLBACK):  # a previous run's; this run arms them afresh or not at all
        _unlink(updates / name)
    status = {"checked_at": dt.datetime.fromtimestamp(machine.now(), dt.UTC).isoformat(timespec="seconds")}
    try:
        installed = installed_version(app)
        status["installed"] = version_text(installed)
        status["last_install"] = settle_attempt(updates, installed)
        settings = update_settings.load(app / update_settings.UPDATE_FILE)
        if settings is None:
            return _finish(updates, status, "not_set_up", "no update.json: this kit was installed without updates")
        status["feed"] = settings.feed
        net = machine.net()
        manifest, signer = verify_feed(fetch_feed(net, settings), machine.keys())
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
        if not quiet(machine.session_ages()):
            return _finish(updates, status, "staged", f"{decision.detail}; it installs once nobody is mid-lesson")
        _write_json(updates / "attempt.json", {"version": version_text(manifest.release.version),
                                                "from": version_text(installed), "at": machine.now()})
        os.link(staged[1], updates / ROLLBACK)  # before apply.exe: action 2 must never run without its way back
        os.link(staged[0], updates / APPLY)
        return _finish(updates, status, "installing", decision.detail)
    except (FeedError, update_settings.UpdateSettingsError, OSError, ValueError, http.client.HTTPException) as exc:
        return _finish(updates, status, "failed", f"{type(exc).__name__}: {exc}", code=1)


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
    """Action 3, run by whichever version is installed now: the kit's self-test, which decides about action 4."""
    updates = updates_dir(app)
    attempt = read_attempt(updates)
    if attempt is None or not (updates / APPLY).exists():
        return 0  # nothing was installed this run
    _unlink(updates / APPLY)
    version = parse_version(attempt["version"])
    if installed_version(app) != version:
        _unlink(updates / ROLLBACK)  # setup failed, so the previous version is still installed: nothing to undo
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
    attempt["check"] = "passed" if passed else "failed"
    _write_json(updates / "attempt.json", attempt)
    if passed:
        _unlink(updates / ROLLBACK)
        logger.info("%s passed its check", version_text(version))
        return 0
    block(updates, version)
    logger.error("%s failed its check (updates\\selftest.json); action 4 rolls it back", version_text(version))
    return 1


def start_sessions(app: Path, now: float | None = None) -> int:
    """Action 5: after an install this run, the sensors again in each signed-in session, as that session's user."""
    attempt = read_attempt(updates_dir(app))
    if attempt is None or (time.time() if now is None else now) - float(attempt.get("at", 0)) > 3600:
        return 0  # the task's own time limit is an hour, so an older attempt was not this run's
    started = winproc.start_in_sessions(str(app / EXE), str(app))
    logger.info("started the sensors in session(s) %s", started or "none: nobody is signed in")
    return 0


def task_xml(app: Path, minute: int) -> str:
    """The task's definition: SYSTEM; at boot, at any sign-in and hourly; the five actions in order."""
    app = PureWindowsPath(app)
    updates = app / "updates"
    exe = app / EXE
    actions = [(exe, "--update"),
               (updates / APPLY, f'{SETUP_ARGS} /LOG="{updates / "apply.log"}"'),
               (exe, "--after-update"),
               (updates / ROLLBACK, f'{SETUP_ARGS} /LOG="{updates / "rollback.log"}"'),
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
    schtasks = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "schtasks.exe")
    exists = subprocess.run([schtasks, "/Query", "/TN", TASK_NAME], capture_output=True).returncode == 0
    if exists and winproc.running_as_system():
        logger.info("kept the existing task: an install the task itself is running must not replace it")
        return 0
    definition = updates_dir(app) / "task.xml"
    definition.write_text(task_xml(app, random.randrange(60)), encoding="utf-16")  # the hour's minute spreads the load
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
