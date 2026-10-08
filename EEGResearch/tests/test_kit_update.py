"""The kit's self-updater: the signed feed, what a computer decides from it, the five task actions' files, and the tools."""

from __future__ import annotations

import base64
import codecs
import datetime as dt
import gzip
import hashlib
import http.client
import http.server
import json
import logging
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from launcher_cases import ROOT

from src.kit import config as kit_config
from src.kit import launcher, selftest, update, update_keys, update_settings, winproc
from src.kit.update import Presence

sys.path.insert(0, str(ROOT / "EEGResearch" / "installer"))
import kit_release  # noqa: E402

EEG = ROOT / "EEGResearch"
NOW = dt.datetime(2026, 10, 8, 12, tzinfo=dt.UTC)
KEY = "k" * 43
PROGRAM_FILES = r"C:\Program Files"
APP = rf"{PROGRAM_FILES}\AdaptiveLearning Sensors"
FREE = [Presence(30.0, True, None)]  # just signed in
BUSY = [Presence(900.0, True, None)]  # signed in a while, and no idle report


@pytest.fixture(scope="module")
def signer() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def raw_key(private: Ed25519PrivateKey) -> dict[str, bytes]:
    return update.load_keys({"everyday": kit_release.public_text(private)})


def installer(version: str, body: bytes) -> tuple[dict, bytes]:
    return ({"version": version, "file": f"AdaptiveLearningSensors-Update-{version}.exe",
             "sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}, body)


NEW, NEW_BODY = installer("0.2.1", b"new installer bytes")
OLD, OLD_BODY = installer("0.2.0", b"old installer")


def feed(private, release=NEW, rollout=100, history=(OLD,), published=NOW, name="latest") -> bytes:
    return kit_release.sign(private, release, rollout, list(history), published, name)


def resign(private, change, prefix: bytes = update.FEED_PREFIX) -> bytes:
    """A feed whose manifest change() edited, signed but never read back: refused only for what it says."""
    manifest = {**NEW, "feed": "latest", "published": NOW.isoformat(), "rollout": 100, "history": [OLD]}
    change(manifest)
    signed = json.dumps(manifest).encode()
    return json.dumps({"manifest": base64.b64encode(signed).decode(),
                       "signature": base64.b64encode(private.sign(prefix + signed)).decode()}).encode()


# --- the feed -------------------------------------------------------------------------------------------------

def test_a_signed_feed_reads_back_with_its_signer(signer):
    manifest, by = update.verify_feed(feed(signer), raw_key(signer), "latest")
    assert by == "everyday" and manifest.feed == "latest"
    assert manifest.release == update.Release((0, 2, 1), NEW["file"], NEW["sha256"], NEW["size"])
    assert manifest.history == (update.Release((0, 2, 0), OLD["file"], OLD["sha256"], OLD["size"]),)
    assert manifest.rollout == 100 and manifest.published == NOW


def test_either_baked_key_can_sign(signer):
    other = Ed25519PrivateKey.generate()
    keys = {**update.load_keys({"recovery": kit_release.public_text(other)}), **raw_key(signer)}
    assert update.verify_feed(feed(other), keys)[1] == "recovery"


def test_one_feed_cannot_be_served_as_the_other(signer):
    with pytest.raises(update.FeedError, match="canary"):
        update.verify_feed(feed(signer, name="canary"), raw_key(signer), "latest")
    assert update.verify_feed(feed(signer, name="canary"), raw_key(signer), "canary")[0].feed == "canary"


def _flip_manifest_byte(raw: bytes) -> bytes:
    outer = json.loads(raw)
    signed = bytearray(base64.b64decode(outer["manifest"]))
    signed[-3] ^= 1
    return json.dumps({**outer, "manifest": base64.b64encode(bytes(signed)).decode()}).encode()


@pytest.mark.parametrize("make", [
    pytest.param(lambda p: _flip_manifest_byte(feed(p)), id="tampered manifest"),
    pytest.param(lambda p: feed(Ed25519PrivateKey.generate()), id="unknown key"),
    pytest.param(lambda p: resign(p, lambda m: None, prefix=b""), id="signed without the prefix"),
    pytest.param(lambda p: b"not json", id="not json"),
    pytest.param(lambda p: json.dumps({"manifest": "@@", "signature": "@@"}).encode(), id="not base64"),
])
def test_a_feed_not_signed_by_a_kit_key_is_refused(signer, make):
    raw = make(signer)  # outside the raises: a fixture's own FeedError must not pass for the refusal
    with pytest.raises(update.FeedError):
        update.verify_feed(raw, raw_key(signer))


@pytest.mark.parametrize("change, reason", [
    (lambda m: m.update(file="..\\..\\Windows\\evil.exe"), "file"),
    (lambda m: m.update(file="AdaptiveLearningSensors-Update-0.2.2.exe"), "file"),
    (lambda m: m.update(file="AdaptiveLearningSensors-Setup-0.2.1.exe"), "file"),
    (lambda m: m.update(version="0.2"), "version"),
    (lambda m: m.update(sha256="A" * 64), "sha256"),
    (lambda m: m.update(size=0), "size"),
    (lambda m: m.update(size=update.MAX_INSTALLER_BYTES + 1), "size"),
    (lambda m: m.update(size=True), "size"),
    (lambda m: m.update(rollout=101), "rollout"),
    (lambda m: m.update(rollout=-1), "rollout"),
    (lambda m: m.update(published="2026-10-08T12:00:00"), "time zone"),
    (lambda m: m.update(feed="beta"), "feed"),
    (lambda m: m.pop("feed"), "feed"),
    (lambda m: m.pop("history"), "history"),
    (lambda m: m.update(history=[installer("0.1.3", b"x")[0]]), "history"),
    (lambda m: m.update(history=[installer("0.2.1", b"x")[0]]), "history"),
    (lambda m: m.update(history=[OLD, OLD]), "twice"),
    (lambda m: m.update(history=[{**OLD, "file": "evil.exe"}]), "file"),
])
def test_a_signed_manifest_is_still_refused_for_what_it_says(signer, change, reason):
    raw = resign(signer, change)
    with pytest.raises(update.FeedError, match=reason):
        update.verify_feed(raw, raw_key(signer))


def test_names_a_newer_publisher_adds_are_ignored(signer):
    manifest, _ = update.verify_feed(resign(signer, lambda m: m.update(notes="later field")), raw_key(signer))
    assert manifest.release.version == (0, 2, 1)


def test_the_rollback_floor_itself_is_allowed(signer):
    assert update.verify_feed(feed(signer), raw_key(signer))[0].history[0].version == update.ROLLBACK_FLOOR


# --- what a computer decides ----------------------------------------------------------------------------------

def manifest_of(private, **kwargs) -> update.Manifest:
    return update.verify_feed(feed(private, **kwargs), raw_key(private))[0]


@pytest.mark.parametrize("installed, blocked, bucket, kwargs, state", [
    ((0, 2, 1), set(), 0, {}, "up_to_date"),
    ((0, 3, 0), set(), 0, {}, "up_to_date"),  # never a downgrade
    ((0, 2, 0), {(0, 2, 1)}, 0, {}, "blocked"),
    ((0, 2, 0), {(0, 1, 9)}, 0, {}, "eligible"),
    ((0, 2, 0), set(), 10, {"rollout": 10}, "not_in_rollout"),
    ((0, 2, 0), set(), 9, {"rollout": 10}, "eligible"),
    ((0, 2, 0), set(), 0, {"rollout": 0}, "not_in_rollout"),
    ((0, 2, 0), set(), 99, {"rollout": 100}, "eligible"),
    ((0, 2, 0), set(), 0, {"history": ()}, "no_rollback"),
])
def test_decide(signer, installed, blocked, bucket, kwargs, state):
    decision = update.decide(installed, manifest_of(signer, **kwargs), blocked, bucket)
    assert decision.state == state
    assert bool(decision.stage) == (state == "eligible")


def test_the_way_back_is_the_installed_versions_own_installer(signer):
    middle, _ = installer("0.3.0", b"a version this computer never ran")
    manifest = manifest_of(signer, release=installer("0.4.0", b"newest")[0], history=(OLD, middle))
    assert [r.version for r in update.decide((0, 2, 0), manifest, set(), 0).stage] == [(0, 4, 0), (0, 2, 0)]
    assert [r.version for r in update.decide((0, 3, 0), manifest, set(), 0).stage] == [(0, 4, 0), (0, 3, 0)]
    skipped = manifest_of(signer, release=installer("0.4.0", b"newest")[0], history=(middle,))
    assert update.decide((0, 2, 0), skipped, set(), 0).state == "no_rollback"  # never 0.3.0, which it never ran


@pytest.mark.parametrize("sessions, expected", [
    ([], True),
    ([Presence(5.0, True, None)], True),
    ([Presence(update.QUIET_SESSION_S - 0.1, True, None)], True),
    ([Presence(update.QUIET_SESSION_S, True, None)], False),
    ([Presence(None, True, None)], False),
    ([Presence(-1.0, True, None)], False),
    ([Presence(5.0, True, None), Presence(3600.0, True, None)], False),
    ([Presence(3600.0, True, True)], True),  # the kit reports nobody has touched it for IDLE_FREE_S
    ([Presence(None, True, True)], True),
    ([Presence(3600.0, True, False)], False),
    ([Presence(3600.0, False, None)], True),  # switched away from: nobody is at it
    ([Presence(3600.0, False, None), Presence(3600.0, True, None)], False),
])
def test_quiet_means_no_lesson_can_be_under_way(sessions, expected):
    assert update.quiet(sessions) is expected


def test_the_rollout_bucket_is_fixed_per_computer_and_spread_across_them():
    guid = "5c1b8a0e-1f6c-4a5e-9d3b-0a7e2f4c6b81"
    assert update.rollout_bucket(guid) == update.rollout_bucket(guid.upper())
    buckets = [update.rollout_bucket(f"{i:08x}-0000-4000-8000-000000000000") for i in range(10_000)]
    assert all(0 <= b < 100 for b in buckets)
    assert 0.08 < sum(b < 10 for b in buckets) / len(buckets) < 0.12
    assert update.rollout_bucket(None) == 99  # unknown: only a full rollout reaches it


# --- action 1 ---------------------------------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, body: bytes):
        self.body, self.at, self.asked = body, 0, 0

    def read(self, n: int = -1) -> bytes:
        self.asked += n
        chunk = self.body[self.at:] if n < 0 else self.body[self.at:self.at + n]
        self.at += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeNet:
    """The gate: answers by path, records each request's URL and key."""

    def __init__(self, files: dict[str, bytes]):
        self.files, self.requests, self.responses = files, [], []

    def open(self, request, timeout=None):
        self.requests.append((urlsplit(request.full_url).path, request.get_header("Authorization")))
        path = urlsplit(request.full_url).path
        if path not in self.files:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", None, None)
        self.responses.append(FakeResponse(self.files[path]))
        return self.responses[-1]


def gate(feed_body: bytes, **extra: bytes) -> FakeNet:
    files = {"/v1/feed/latest.json": feed_body, f"/v1/files/{NEW['file']}": NEW_BODY,
             f"/v1/files/{OLD['file']}": OLD_BODY}
    files.update({f"/v1/files/{name}": body for name, body in extra.items()})
    return FakeNet(files)


def kit(tmp_path: Path, version: str = "0.2.0", settings: dict | None = None) -> Path:
    app = tmp_path / "AdaptiveLearning Sensors"
    app.mkdir()
    (app / "version.txt").write_text(version + "\n", encoding="ascii")
    if settings is not False:
        (app / "update.json").write_text(json.dumps(settings or {"feed": "latest", "key": KEY}), encoding="utf-8")
    return app


def machine(net: FakeNet, private, sessions=(), guid="any", now=NOW) -> update.Machine:
    return update.Machine(lambda: list(sessions), lambda: guid, lambda: net, lambda: raw_key(private),
                          now=lambda: now.timestamp())


def status(app: Path) -> dict:
    return json.loads((app / "updates" / "status.json").read_text(encoding="utf-8"))


def attempt_of(app: Path) -> dict:
    return json.loads((app / "updates" / "attempt.json").read_bytes())


def test_a_due_update_is_staged_and_armed_when_nobody_is_signed_in(tmp_path, signer):
    app, net = kit(tmp_path), gate(feed(signer))
    assert update.check_for_update(app, machine(net, signer)) == 0
    updates = app / "updates"
    assert (updates / "apply.exe").read_bytes() == NEW_BODY
    assert (updates / "rollback.exe").read_bytes() == OLD_BODY
    assert attempt_of(app) == {"version": "0.2.1", "way_back": OLD, "at": NOW.timestamp(), "check": "installing"}
    assert status(app)["state"] == "installing" and status(app)["signed_by"] == "everyday"
    assert net.requests == [("/v1/feed/latest.json", f"Bearer {KEY}"), (f"/v1/files/{NEW['file']}", f"Bearer {KEY}"),
                            (f"/v1/files/{OLD['file']}", f"Bearer {KEY}")]


def test_mid_lesson_it_only_stages(tmp_path, signer):
    app = kit(tmp_path)
    assert update.check_for_update(app, machine(gate(feed(signer)), signer, sessions=FREE + BUSY)) == 0
    updates = app / "updates"
    assert not (updates / "apply.exe").exists() and not (updates / "rollback.exe").exists()
    assert not (updates / "attempt.json").exists()
    assert (updates / NEW["file"]).read_bytes() == NEW_BODY and status(app)["state"] == "staged"


def test_an_idle_session_left_signed_in_lets_it_install(tmp_path, signer):
    app = kit(tmp_path)
    update.check_for_update(app, machine(gate(feed(signer)), signer, sessions=[Presence(7200.0, True, True)]))
    assert status(app)["state"] == "installing"


def test_a_staged_installer_is_not_downloaded_again(tmp_path, signer):
    app, net = kit(tmp_path), gate(feed(signer))
    update.check_for_update(app, machine(net, signer, sessions=BUSY))
    net.requests.clear()
    update.check_for_update(app, machine(net, signer))
    assert [path for path, _ in net.requests] == ["/v1/feed/latest.json"]
    assert (app / "updates" / "apply.exe").read_bytes() == NEW_BODY


def test_the_canary_feed_is_the_one_asked_for(tmp_path, signer):
    app = kit(tmp_path, settings={"feed": "canary", "key": KEY})
    net = gate(b"")
    net.files["/v1/feed/canary.json"] = feed(signer, name="canary")
    update.check_for_update(app, machine(net, signer))
    assert net.requests[0][0] == "/v1/feed/canary.json" and status(app)["state"] == "installing"


def test_the_latest_feed_served_at_the_canary_address_is_refused(tmp_path, signer):
    app = kit(tmp_path, settings={"feed": "canary", "key": KEY})
    net = gate(b"")
    net.files["/v1/feed/canary.json"] = feed(signer)
    assert update.check_for_update(app, machine(net, signer)) == 1
    assert "not 'canary'" in status(app)["detail"]


def test_an_older_feed_served_again_is_refused(tmp_path, signer):
    app = kit(tmp_path, version="0.2.1")
    halted = feed(signer, rollout=0, published=NOW)
    update.check_for_update(app, machine(gate(halted), signer))
    assert json.loads((app / "updates" / "seen.json").read_bytes()) == {"latest": NOW.isoformat()}
    replayed = feed(signer, rollout=100, published=NOW - dt.timedelta(days=1))
    assert update.check_for_update(app, machine(gate(replayed), signer)) == 1
    assert "served again" in status(app)["detail"]
    assert update.check_for_update(app, machine(gate(halted), signer)) == 0  # the same feed again is fine


def test_a_blocked_version_is_never_downloaded_or_armed(tmp_path, signer):
    app, net = kit(tmp_path), gate(feed(signer))
    (app / "updates").mkdir()
    (app / "updates" / "blocked.json").write_text('["0.2.1"]', encoding="utf-8")
    update.check_for_update(app, machine(net, signer))
    assert [path for path, _ in net.requests] == ["/v1/feed/latest.json"]
    assert not (app / "updates" / "apply.exe").exists() and status(app)["state"] == "blocked"


def test_a_failed_link_leaves_no_attempt_to_block(tmp_path, signer, monkeypatch):
    app = kit(tmp_path)
    links = []

    def link(src, dst):
        links.append(dst)
        if len(links) == 2:
            raise OSError("no room")
        Path(dst).write_bytes(Path(src).read_bytes())

    monkeypatch.setattr(update.os, "link", link)
    assert update.check_for_update(app, machine(gate(feed(signer)), signer)) == 1
    updates = app / "updates"
    assert not (updates / "attempt.json").exists() and not (updates / "rollback.exe").exists()
    assert not (updates / "apply.exe").exists() and not (updates / "blocked.json").exists()


def test_a_previous_runs_links_are_removed_before_anything_else(tmp_path, signer):
    app = kit(tmp_path, version="0.2.1")
    (app / "updates").mkdir()
    for name in ("apply.exe", "rollback.exe"):
        (app / "updates" / name).write_bytes(b"stale")
    update.check_for_update(app, machine(gate(b"not a feed"), signer))
    assert not (app / "updates" / "apply.exe").exists() and not (app / "updates" / "rollback.exe").exists()


def test_no_update_json_asks_the_gate_nothing(tmp_path, signer):
    app, net = kit(tmp_path, settings=False), gate(feed(signer))
    assert update.check_for_update(app, machine(net, signer)) == 0
    assert net.requests == [] and status(app)["state"] == "not_set_up"


@pytest.mark.parametrize("served, reason", [
    (NEW_BODY + b"extra", "longer"),
    (NEW_BODY[:-1], "ended at"),
    (NEW_BODY[:-1] + b"!", "SHA-256"),
])
def test_a_download_that_is_not_the_feeds_is_refused_and_removed(tmp_path, signer, served, reason):
    app = kit(tmp_path)
    net = gate(feed(signer), **{NEW["file"]: served})
    assert update.check_for_update(app, machine(net, signer)) == 1
    assert status(app)["state"] == "failed" and reason in status(app)["detail"]
    assert sorted(p.name for p in (app / "updates").iterdir()) == ["seen.json", "status.json"]


def test_a_connection_cut_mid_download_is_reported(tmp_path, signer):
    class Cut(FakeNet):
        def open(self, request, timeout=None):
            response = super().open(request, timeout)
            if request.full_url.endswith(".exe"):
                response.read = lambda n=-1: (_ for _ in ()).throw(http.client.IncompleteRead(b"", 5))
            return response

    app = kit(tmp_path)
    assert update.check_for_update(app, machine(Cut(gate(feed(signer)).files), signer)) == 1
    assert status(app)["state"] == "failed" and "IncompleteRead" in status(app)["detail"]


def test_a_download_reads_no_further_than_one_byte_past_its_size(tmp_path, signer):
    app = kit(tmp_path)
    net = gate(feed(signer), **{NEW["file"]: NEW_BODY + b"x" * 10_000_000})
    update.check_for_update(app, machine(net, signer))
    assert net.responses[1].asked == NEW["size"] + 1


def test_a_feed_over_its_cap_is_refused(tmp_path, signer):
    app = kit(tmp_path)
    assert update.check_for_update(app, machine(gate(b"x" * (update.MAX_FEED_BYTES + 1)), signer)) == 1
    assert "over" in status(app)["detail"]


def test_an_old_feed_is_reported(tmp_path, signer):
    app = kit(tmp_path, version="0.2.1")
    stale = NOW - dt.timedelta(days=update.STALE_FEED_DAYS + 1)
    update.check_for_update(app, machine(gate(feed(signer, published=stale)), signer))
    assert status(app)["feed_age_days"] == update.STALE_FEED_DAYS + 1 and "published" in status(app)["warning"]


def test_an_unreadable_version_txt_installs_nothing(tmp_path, signer):
    app, net = kit(tmp_path), gate(feed(signer))
    (app / "version.txt").write_text("unknown", encoding="ascii")
    assert update.check_for_update(app, machine(net, signer)) == 1
    assert net.requests == [] and status(app)["state"] == "failed"


def test_a_redirect_is_refused_rather_than_followed():
    class Redirects(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:9/elsewhere")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Redirects)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        net = urllib.request.build_opener(update._RefuseRedirects())
        with pytest.raises(update.FeedError, match="redirect"):
            net.open(f"http://127.0.0.1:{server.server_port}/v1/feed/latest.json", timeout=10)
    finally:
        server.shutdown()
        server.server_close()


def test_the_real_opener_refuses_redirects_too():
    assert any(isinstance(h, update._RefuseRedirects) for h in update.opener().handlers)
    assert any(isinstance(h, update._RefuseRedirects) for h in update.opener("http://proxy:8080").handlers)


# --- an earlier run's install, finished by action 1 ----------------------------------------------------------------

def left(tmp_path: Path, installed: str, check: str, **extra) -> Path:
    """A kit whose earlier run armed 0.2.1 over 0.2.0 and recorded check; both installers still staged."""
    app = kit(tmp_path, version=installed)
    updates = app / "updates"
    updates.mkdir()
    (updates / NEW["file"]).write_bytes(NEW_BODY)
    (updates / OLD["file"]).write_bytes(OLD_BODY)
    attempt = {"version": "0.2.1", "way_back": OLD, "at": 1000.0, "check": check, **extra}
    (updates / "attempt.json").write_text(json.dumps(attempt), encoding="utf-8")
    return app


@pytest.mark.parametrize("installed, check, sessions, state, armed, blocked", [
    ("0.2.1", "installing", FREE, "checking", True, False),  # cut after setup, before its check
    ("0.2.1", "installing", BUSY, "check_waiting", False, False),
    ("0.2.0", "installing", FREE, "repairing", True, True),  # could not start, or setup was cut short
    ("0.2.0", "installing", BUSY, "repair_waiting", False, True),
    ("0.2.1", "failed", FREE, "rolling_back", True, True),  # the rollback did not happen
    ("0.2.1", "failed", BUSY, "rollback_waiting", False, True),
])
def test_an_unsettled_install_spends_the_next_run_and_only_when_quiet(tmp_path, signer, installed, check, sessions,
                                                                      state, armed, blocked):
    app = left(tmp_path, installed, check)
    if check == "failed":
        update.block(app / "updates", (0, 2, 1))  # as action 3 did
    net = gate(feed(signer))
    update.check_for_update(app, machine(net, signer, sessions=sessions))
    updates = app / "updates"
    assert status(app)["state"] == state and net.requests == []
    assert (updates / "rollback.exe").exists() is armed and not (updates / "apply.exe").exists()
    if armed:
        assert (updates / "rollback.exe").read_bytes() == OLD_BODY
        assert attempt_of(app)["at"] == NOW.timestamp() and attempt_of(app)["check"] == {
            "checking": "installing", "repairing": "repairing", "rolling_back": "failed"}[state]
    assert ((0, 2, 1) in update.read_blocked(updates)) is blocked


@pytest.mark.parametrize("installed, check, note, blocked", [
    ("0.2.1", "passed", "passed its check", False),
    ("0.2.0", "failed", "rolled back to 0.2.0", False),
    ("0.2.0", "repairing", "was reinstalled", False),
    ("0.2.0", "setup_failed", "try 1 of 3", False),
])
def test_a_settled_install_is_noted_and_the_run_goes_on_to_the_feed(tmp_path, signer, installed, check, note,
                                                                    blocked):
    app = left(tmp_path, installed, check)
    net = gate(feed(signer))
    update.check_for_update(app, machine(net, signer, sessions=BUSY))
    assert note in status(app)["last_install"] and net.requests[0][0] == "/v1/feed/latest.json"
    assert not (app / "updates" / "attempt.json").exists()
    assert ((0, 2, 1) in update.read_blocked(app / "updates")) is blocked


def test_a_setup_that_keeps_failing_is_blocked_on_its_third_try(tmp_path, signer):
    app = left(tmp_path, "0.2.0", "setup_failed")
    for tries in (1, 2, 3):
        update.check_for_update(app, machine(gate(feed(signer)), signer, sessions=BUSY))
        assert f"try {tries} of 3" in status(app)["last_install"]
        assert ((0, 2, 1) in update.read_blocked(app / "updates")) is (tries == 3)
        (app / "updates" / "attempt.json").write_text(json.dumps(
            {"version": "0.2.1", "way_back": OLD, "at": 0, "check": "setup_failed"}), encoding="utf-8")


def _rollback_log(app: Path, text: bytes, mtime: float) -> None:
    log = app / "updates" / "rollback.log"
    log.write_bytes(text)
    os.utime(log, (mtime, mtime))


# The head and tail of a real /LOG: Inno Setup 6.7.3 rolling this kit back as SYSTEM, in the Sandbox trial.
REAL_LOG = gzip.decompress((EEG / "tests" / "fixtures" / "inno_setup_completed.log.gz").read_bytes())
REAL_TEXT = REAL_LOG.decode("utf-8-sig")
ENCODINGS = {"as written (UTF-8, BOM)": REAL_LOG, "UTF-8": REAL_TEXT.encode("utf-8"),
             "UTF-16, BOM": REAL_TEXT.encode("utf-16"), "UTF-16LE": REAL_TEXT.encode("utf-16-le"),
             "UTF-16BE, BOM": codecs.BOM_UTF16_BE + REAL_TEXT.encode("utf-16-be")}


def test_the_real_log_is_utf8_with_a_bom_and_ends_a_finished_install():
    assert REAL_LOG.startswith(codecs.BOM_UTF8) and update.SETUP_SUCCEEDED in REAL_TEXT


@pytest.mark.parametrize("raw", ENCODINGS.values(), ids=ENCODINGS.keys())
def test_a_finished_install_reads_in_any_encoding_inno_could_use(raw):
    assert update.log_text(raw) == REAL_TEXT


@pytest.mark.parametrize("raw", ENCODINGS.values(), ids=ENCODINGS.keys())
def test_a_version_that_could_not_start_and_was_rolled_back_is_not_reinstalled_again(tmp_path, signer, raw):
    app = left(tmp_path, "0.2.0", "installing")
    _rollback_log(app, raw, 1000.0 + 60)
    net = gate(feed(signer))
    update.check_for_update(app, machine(net, signer))
    assert "could not run its check and was rolled back" in status(app)["last_install"]
    assert not (app / "updates" / "rollback.exe").exists() and not (app / "updates" / "attempt.json").exists()
    assert (0, 2, 1) in update.read_blocked(app / "updates")
    assert net.requests[0][0] == "/v1/feed/latest.json"  # settled, so the run goes on to the feed


@pytest.mark.parametrize("text, mtime", [
    (REAL_LOG.replace(update.SETUP_SUCCEEDED.encode(), b"Fatal exception"), 1000.0 + 60),  # it ran, and did not finish
    (REAL_LOG, 1000.0 - 60),  # an earlier run's
])
def test_a_rollback_log_that_shows_no_finished_rollback_this_run_still_repairs(tmp_path, signer, text, mtime):
    app = left(tmp_path, "0.2.0", "installing")
    _rollback_log(app, text, mtime)
    update.check_for_update(app, machine(gate(feed(signer)), signer))
    assert status(app)["state"] == "repairing" and (app / "updates" / "rollback.exe").read_bytes() == OLD_BODY
    assert not (app / "updates" / "rollback.log").exists()  # set aside, so the repair's own log is the one read next
    assert (app / "updates" / "rollback.previous.log").read_bytes() == text


@pytest.mark.parametrize("damage", ["missing", "altered"])
def test_a_way_back_that_went_missing_is_fetched_again(tmp_path, signer, damage):
    app = left(tmp_path, "0.2.1", "installing")
    staged = app / "updates" / OLD["file"]
    staged.unlink() if damage == "missing" else staged.write_bytes(b"x" * OLD["size"])
    net = gate(feed(signer))
    update.check_for_update(app, machine(net, signer))
    assert status(app)["state"] == "checking" and (app / "updates" / "rollback.exe").read_bytes() == OLD_BODY
    assert [path for path, _ in net.requests] == [f"/v1/files/{OLD['file']}"]


def test_a_way_back_that_cannot_be_fetched_says_why(tmp_path, signer):
    app = left(tmp_path, "0.2.1", "installing")
    (app / "update.json").unlink()
    (app / "updates" / OLD["file"]).unlink()
    assert update.check_for_update(app, machine(gate(feed(signer)), signer)) == 1
    assert "cannot be fetched again" in status(app)["detail"] and (app / "updates" / "attempt.json").exists()


def test_a_rollback_that_keeps_failing_is_tried_three_times_in_all(tmp_path, signer):
    app = left(tmp_path, "0.2.1", "failed", rollbacks=2)
    update.check_for_update(app, machine(gate(feed(signer)), signer))
    assert status(app)["state"] == "rolling_back" and attempt_of(app)["rollbacks"] == 3
    update.check_for_update(app, machine(gate(feed(signer)), signer))
    assert "3 rollbacks to 0.2.0 did not take" in status(app)["last_install"]
    assert not (app / "updates" / "attempt.json").exists() and not (app / "updates" / "rollback.exe").exists()


# --- actions 3 and 5, and the launcher standing aside -------------------------------------------------------------

def armed(tmp_path: Path, installed: str, at: float | None = None) -> Path:
    app = kit(tmp_path, version=installed)
    updates = app / "updates"
    updates.mkdir()
    (updates / "apply.exe").write_bytes(NEW_BODY)
    (updates / "rollback.exe").write_bytes(OLD_BODY)
    attempt = {"version": "0.2.1", "way_back": OLD, "at": time.time() if at is None else at, "check": "installing"}
    (updates / "attempt.json").write_text(json.dumps(attempt), encoding="utf-8")
    return app


def test_a_passed_check_disarms_the_rollback(tmp_path):
    app = armed(tmp_path, "0.2.1")
    assert update.after_update(app, lambda report, a: 0) == 0
    updates = app / "updates"
    assert not (updates / "apply.exe").exists() and not (updates / "rollback.exe").exists()
    assert attempt_of(app)["check"] == "passed" and not (updates / "blocked.json").exists()


@pytest.mark.parametrize("check", [lambda report, a: 1, lambda report, a: 1 / 0], ids=["fails", "raises"])
def test_a_failed_check_blocks_the_version_and_leaves_the_rollback_armed(tmp_path, check):
    app = armed(tmp_path, "0.2.1")
    assert update.after_update(app, check) == 1
    updates = app / "updates"
    assert not (updates / "apply.exe").exists() and (updates / "rollback.exe").read_bytes() == OLD_BODY
    assert json.loads((updates / "blocked.json").read_bytes()) == ["0.2.1"] and attempt_of(app)["check"] == "failed"


def test_a_setup_that_failed_leaves_nothing_to_roll_back_and_says_so(tmp_path):
    app = armed(tmp_path, "0.2.0")
    ran = []
    assert update.after_update(app, lambda report, a: ran.append(1)) == 1
    assert ran == [] and not (app / "updates" / "rollback.exe").exists()
    assert attempt_of(app)["check"] == "setup_failed" and not (app / "updates" / "blocked.json").exists()


def test_a_check_armed_without_an_install_still_runs(tmp_path):
    app = armed(tmp_path, "0.2.1")
    (app / "updates" / "apply.exe").unlink()  # action 1 armed only rollback.exe: the install was an earlier run's
    assert update.after_update(app, lambda report, a: 0) == 0
    assert attempt_of(app)["check"] == "passed"


@pytest.mark.parametrize("check", ["passed", "failed", "setup_failed", "repairing"])
def test_the_check_runs_only_for_an_unchecked_install(tmp_path, check):
    app = armed(tmp_path, "0.2.1")
    (app / "updates" / "attempt.json").write_text(json.dumps({**attempt_of(app), "check": check}), encoding="utf-8")
    ran = []
    assert update.after_update(app, lambda report, a: ran.append(1)) == 0
    assert ran == [] and (app / "updates" / "rollback.exe").exists()


def test_with_nothing_armed_this_run_the_check_does_not_run(tmp_path):
    app = armed(tmp_path, "0.2.1")
    (app / "updates" / "rollback.exe").unlink()  # not quiet: action 1 left it unarmed
    ran = []
    assert update.after_update(app, lambda report, a: ran.append(1)) == 0
    assert ran == [] and attempt_of(app)["check"] == "installing"


def test_the_check_reports_beside_the_updates_and_the_update_log_survives_it(tmp_path):
    app = armed(tmp_path, "0.2.1")
    seen = []

    def check(report, a):
        seen.append((report, a))
        logging.getLogger().handlers[:] = []  # as selftest.run replaces them
        return 0

    marker = logging.NullHandler()
    logging.getLogger().addHandler(marker)
    try:
        update.after_update(app, check)
        assert marker in logging.getLogger().handlers
    finally:
        logging.getLogger().removeHandler(marker)
    assert seen == [(app / "updates" / "selftest.json", app)]


def test_sessions_are_started_only_after_an_install_this_run(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(winproc, "start_in_sessions", lambda exe, cwd: calls.append((exe, cwd)) or [1])
    app = armed(tmp_path, "0.2.1", at=1000.0)
    update.start_sessions(app, now=1000.0 + 3601)
    assert calls == []
    update.start_sessions(app, now=1000.0 + 60)
    assert calls == [(str(app / update.EXE), str(app))]
    (app / "updates" / "attempt.json").unlink()
    update.start_sessions(app, now=1000.0 + 60)
    assert len(calls) == 1


def test_the_runs_end_unlinks_both_installers_so_a_later_sign_in_starts_the_sensors(tmp_path, monkeypatch):
    monkeypatch.setattr(winproc, "start_in_sessions", lambda exe, cwd: [])
    app = armed(tmp_path, "0.2.0")  # as after a rollback: rollback.exe still linked, attempt.json fresh
    (app / "updates" / "apply.exe").unlink()
    assert update.applying(app)
    update.start_sessions(app)
    assert not (app / "updates" / "rollback.exe").exists() and not update.applying(app)


@pytest.mark.parametrize("link", ["apply.exe", "rollback.exe"])
def test_the_launcher_stands_aside_while_an_install_or_rollback_is_fresh(tmp_path, monkeypatch, link):
    app = armed(tmp_path, "0.2.0")
    (app / "updates" / ({"apply.exe", "rollback.exe"} - {link}).pop()).unlink()
    assert update.applying(app)
    assert not update.applying(app, now=time.time() + update.APPLY_FRESH_S + 1)

    def never(*args, **kwargs):
        raise AssertionError("took the machine during an install")

    monkeypatch.setattr(winproc, "SingleInstance", never)
    assert launcher.serve(app) == 0
    (app / "updates" / link).unlink()
    assert not update.applying(app)


def test_the_kit_reports_its_session_idle_only_with_no_input_and_no_lesson_streaming():
    class Flag:
        def __init__(self):
            self.values = []

        def set(self, idle):
            self.values.append(idle)

    # (seconds since input, streaming): each pair is one report
    readings = iter([(update.IDLE_FREE_S - 1, False), (update.IDLE_FREE_S, False), (update.IDLE_FREE_S, True),
                     (update.IDLE_FREE_S, None), (OSError("no"), False)])
    current = []

    def idle_seconds():
        current[:] = [next(readings)]
        if isinstance(current[0][0], Exception):
            raise current[0][0]
        return current[0][0]

    class Stop:
        calls = 0

        def wait(self, seconds):
            Stop.calls += 1
            return Stop.calls == 5

    flag = Flag()
    launcher.report_idle(Stop(), flag, lambda: current[0][1], idle_seconds)
    # Unknown counts as someone there, whether the input or the stream is what could not be read.
    assert flag.values == [False, True, False, False, False]


@pytest.mark.parametrize("answer, expected", [
    ((200, {"status": "ok", "data": {"enabled": True, "running": True}}), True),
    ((200, {"status": "ok", "data": {"enabled": True, "running": False}}), False),
    ((200, {"status": "ok", "data": {"enabled": False}}), False),
    ((401, {"detail": "Unauthorized"}), None),
    ((200, "not an object"), None),
])
def test_sidecar_streaming_asks_the_push_status_with_the_learner_token(answer, expected):
    seen = []

    class Sidecar(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers.get("Authorization")))
            body = json.dumps(answer[1]).encode()
            self.send_response(answer[0])
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Sidecar)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        assert launcher.sidecar_streaming(server.server_port, KEY) is expected
    finally:
        server.shutdown()
        server.server_close()
    assert seen == [("/api/v1/push/status", f"Bearer {KEY}")]


def test_a_sidecar_that_does_not_answer_is_not_known_to_be_idle():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # bound and not listening: refused
        assert launcher.sidecar_streaming(port, KEY) is None


# --- who may run what --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("mode, app, system, elevated, refused", [
    ("--update", APP, True, False, False),
    ("--update", APP.upper(), True, False, False),
    ("--update", APP, False, True, True),
    ("--after-update", APP, False, False, True),
    ("--start-sessions", APP, True, False, False),
    ("--update", r"C:\Users\s\AppData\Local\Programs\AdaptiveLearning Sensors", True, False, True),
    ("--update", rf"{PROGRAM_FILES}\AdaptiveLearning Sensors\..\Elsewhere", True, False, True),
    ("--register-task", APP, False, True, False),
    ("--register-task", APP, True, False, False),
    ("--register-task", APP, False, False, True),
    ("--register-task", r"D:\Kit\AdaptiveLearning Sensors", False, True, True),
])
def test_refusal(mode, app, system, elevated, refused):
    assert (update.refusal(mode, Path(app), PROGRAM_FILES, system, elevated) is not None) is refused


def test_no_program_files_folder_refuses():
    assert update.refusal("--update", Path(APP), None, True, True) is not None


def test_a_refused_mode_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(winproc, "running_as_system", lambda: False)
    monkeypatch.setattr(winproc, "running_elevated", lambda: False)
    monkeypatch.setenv("ProgramW6432", str(tmp_path))
    app = tmp_path / update.APP_FOLDER
    app.mkdir()
    for mode in update.MODES:
        assert update.main(mode, app) == 2
    assert list(app.iterdir()) == []


def test_the_launchers_modes_are_the_updaters_and_take_no_arguments(monkeypatch):
    assert launcher.UPDATE_MODES == update.MODES
    called = []
    monkeypatch.setattr(update, "main", lambda mode, app: called.append(mode) or 0)
    monkeypatch.setattr(launcher, "app_dir", lambda: Path(APP))
    assert launcher.main(["--update", "--extra"]) == 2
    assert launcher.main(["--after-update"]) == 0 and called == ["--after-update"]


# --- the task ------------------------------------------------------------------------------------------------------

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


def test_the_task_runs_as_system_with_five_always_launchable_actions_in_order():
    root = ET.fromstring(update.task_xml(Path(APP), 7, r"D:\Win").replace('encoding="UTF-16"', ""))
    assert root.find("t:Principals/t:Principal/t:UserId", NS).text == "S-1-5-18"
    actions = [(e.find("t:Command", NS).text, e.find("t:Arguments", NS).text)
               for e in root.findall("t:Actions/t:Exec", NS)]
    updates = rf"{APP}\updates"
    exe, cmd = f'"{APP}\\{update.EXE}"', '"D:\\Win\\System32\\cmd.exe"'
    assert [c for c, _ in actions] == [exe, cmd, exe, cmd, exe]  # the kit's own exe and cmd: neither can be missing
    assert [actions[0][1], actions[2][1], actions[4][1]] == ["--update", "--after-update", "--start-sessions"]
    for (_, arguments), link, log in [(actions[1], "apply", "apply"), (actions[3], "rollback", "rollback")]:
        target = f"{updates}\\{link}.exe"
        assert arguments == (f'/d /s /c "if exist "{target}" "{target}" {update.SETUP_ARGS} '
                             f'/LOG="{updates}\\{log}.log""')
    triggers = [child.tag.split("}")[1] for child in root.find("t:Triggers", NS)]
    assert triggers == ["BootTrigger", "LogonTrigger", "TimeTrigger"]
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", NS) is None  # any user's sign-in
    time_trigger = root.find("t:Triggers/t:TimeTrigger", NS)
    assert time_trigger.find("t:StartBoundary", NS).text.endswith(":07:00")
    assert time_trigger.find("t:Repetition/t:Interval", NS).text == "PT1H"
    settings = {child.tag.split("}")[1]: child.text for child in root.find("t:Settings", NS)}
    assert settings["MultipleInstancesPolicy"] == "IgnoreNew" and settings["ExecutionTimeLimit"] == "PT1H"
    assert settings["DisallowStartIfOnBatteries"] == settings["StopIfGoingOnBatteries"] == "false"


def test_registering_writes_utf16_and_never_replaces_the_task_it_runs_from(tmp_path, monkeypatch):
    app = tmp_path / update.APP_FOLDER
    (app / "updates").mkdir(parents=True)
    runs = []

    def run(argv, **kwargs):
        runs.append(argv[1:])
        return subprocess.CompletedProcess(argv, 0 if argv[1] == "/Create" or exists else 1, "", "")

    monkeypatch.setattr(update.subprocess, "run", run)
    monkeypatch.setattr(winproc, "running_as_system", lambda: False)
    monkeypatch.setenv("SystemRoot", r"E:\Windows")
    exists = False
    assert update.register_task(app) == 0
    written = (app / "updates" / "task.xml").read_bytes()
    assert written[:2] == b"\xff\xfe" and "S-1-5-18" in written.decode("utf-16")
    assert r"E:\Windows\System32\cmd.exe" in written.decode("utf-16")
    assert runs[-1] == ["/Create", "/TN", update.TASK_NAME, "/XML", str(app / "updates" / "task.xml"), "/F"]
    exists = True
    monkeypatch.setattr(winproc, "running_as_system", lambda: True)
    runs.clear()
    assert update.register_task(app) == 0
    assert runs == [["/Query", "/TN", update.TASK_NAME]]


def _iss() -> str:
    # Inno Setup script, not Python: text is the only way to read it.
    return (EEG / "installer" / "student_kit.iss").read_text("utf-8")


def test_the_installer_names_the_same_task():
    assert f'#define UpdateTask "{update.TASK_NAME}"' in _iss()


def test_the_installer_clears_the_code_folders_before_copying():
    section = _iss().split("[InstallDelete]", 1)[1].split("\n[", 1)[0]
    assert 'Name: "{app}\\_internal"' in section and 'Name: "{app}\\bridge"' in section
    assert "updates" not in section and "kit.json" not in section


# --- settings, kit.json, and what --update imports ----------------------------------------------------------------

def test_update_json_reads_and_ignores_names_it_does_not_know(tmp_path):
    path = tmp_path / "update.json"
    path.write_text(json.dumps({"feed": "canary", "key": KEY, "added_later": 1}), encoding="utf-8")
    assert update_settings.load(path) == update_settings.UpdateSettings("canary", KEY)
    assert update_settings.load(tmp_path / "absent.json") is None


@pytest.mark.parametrize("raw", [{"feed": "beta", "key": KEY}, {"key": "short"}, {"key": KEY + "!"}, {}])
def test_update_json_refuses_a_bad_feed_or_key(raw):
    with pytest.raises(update_settings.UpdateSettingsError):
        update_settings.check(raw)


def test_kit_json_stays_free_of_updater_names():
    good = {"backend_url": "https://a.example", "frontend_origin": "https://b.example", "learner_token": KEY}
    assert kit_config.check(good)[0].learner_token == KEY
    with pytest.raises(kit_config.KitConfigError, match="Unknown setting"):
        kit_config.check({**good, "feed": "latest"})  # why update.json is a file of its own


def test_update_settings_cli_takes_the_key_from_the_environment_only(tmp_path):
    out = tmp_path / "update.json"
    assert update_settings.main(["write", str(out), "--feed", "canary"], {update_settings.KEY_VAR: KEY}) == 0
    assert json.loads(out.read_bytes()) == {"feed": "canary", "key": KEY}
    assert update_settings.main(["check"], {}) == 1
    with pytest.raises(SystemExit):
        update_settings.main(["check", f"--key={KEY}"], {update_settings.KEY_VAR: KEY})  # no such argument


def test_the_update_mode_imports_nothing_of_the_sidecar(tmp_path, signer):
    app = kit(tmp_path)
    (tmp_path / "feed.json").write_bytes(feed(signer))
    (tmp_path / "key.txt").write_text(kit_release.public_text(signer), encoding="ascii")
    script = textwrap.dedent(f"""
        import sys
        before = set(sys.modules)
        import json, urllib.error
        from pathlib import Path
        from src.kit import launcher, update
        tmp = Path({str(tmp_path)!r})
        files = {{"/v1/feed/latest.json": (tmp / "feed.json").read_bytes(),
                  "/v1/files/{NEW['file']}": {NEW_BODY!r}, "/v1/files/{OLD['file']}": {OLD_BODY!r}}}
        class R:
            def __init__(self, b): self.b = b
            def read(self, n=-1): b, self.b = (self.b, b"") if n < 0 else (self.b[:n], self.b[n:]); return b
            def __enter__(self): return self
            def __exit__(self, *e): return False
        class N:
            def open(self, req, timeout=None): return R(files[req.full_url.split(".dev", 1)[1]])
        key = update.load_keys({{"k": (tmp / "key.txt").read_text()}})
        m = update.Machine(lambda: [], lambda: "g", lambda: N(), lambda: key)
        assert update.check_for_update(Path({str(app)!r}), m) == 0, (Path({str(app)!r}) / "updates/status.json").read_text()
        added = sorted(set(sys.modules) - before)
        allowed = set(sys.stdlib_module_names) | {{"src", "cryptography", "_openssl", "_cffi_backend", "certifi"}}
        print(json.dumps([n for n in added if n.split(".")[0] not in allowed or n.startswith("src.app")]))
    """)
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, cwd=EEG, timeout=120)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []
    assert (app / "updates" / "apply.exe").read_bytes() == NEW_BODY  # the run it measured was a whole one


# --- the publisher's tool and the self-test -------------------------------------------------------------------------

def test_newkey_writes_an_encrypted_key_and_never_over_another(tmp_path):
    path = tmp_path / "everyday.pem"
    public = kit_release.new_key(path, b"correct horse battery")
    assert b"ENCRYPTED" in path.read_bytes()
    assert kit_release.public_text(kit_release.load_key(path, b"correct horse battery")) == public
    with pytest.raises(FileExistsError):
        kit_release.new_key(path, b"correct horse battery")


def test_newdownloadkey_writes_a_key_the_settings_accept_and_never_over_another(tmp_path):
    path = tmp_path / "download.key"
    kit_release.new_download_key(path)
    key = path.read_text(encoding="ascii")
    assert update_settings.check({"key": key}).key == key and key == key.strip()
    with pytest.raises(FileExistsError):
        kit_release.new_download_key(path)


def test_release_of_reads_an_update_installer_only(tmp_path):
    good = tmp_path / NEW["file"]
    good.write_bytes(NEW_BODY)
    assert kit_release.release_of(good) == NEW
    setup = tmp_path / "AdaptiveLearningSensors-Setup-0.2.1.exe"
    setup.write_bytes(NEW_BODY)
    with pytest.raises(update.FeedError):
        kit_release.release_of(setup)


def test_history_gathers_every_published_installer_older_than_this_one(signer):
    v030, _ = installer("0.3.0", b"three")
    v031, _ = installer("0.3.1", b"canary three one")
    latest = manifest_of(signer, release=v030, history=(OLD, NEW))
    canary = manifest_of(signer, release=v031, history=(OLD, NEW, v030), name="canary")
    this, _ = installer("0.3.1", b"canary three one")
    assert [e["version"] for e in kit_release.history_from([latest, canary], this)] == ["0.2.0", "0.2.1", "0.3.0"]
    assert kit_release.history_from([], this) == []
    with pytest.raises(SystemExit, match="other bytes"):
        kit_release.history_from([canary], installer("0.3.1", b"rebuilt")[0])
    clash = manifest_of(signer, release=v031, history=(installer("0.2.0", b"another 0.2.0")[0],), name="canary")
    with pytest.raises(SystemExit, match="two installers"):
        kit_release.history_from([latest, clash], installer("0.4.0", b"x")[0])


def test_signing_refuses_a_key_the_kits_do_not_trust(tmp_path, monkeypatch):
    path = tmp_path / "stray.pem"
    kit_release.new_key(path, b"correct horse battery")
    (tmp_path / NEW["file"]).write_bytes(NEW_BODY)
    monkeypatch.setattr(kit_release.getpass, "getpass", lambda prompt="": "correct horse battery")
    with pytest.raises(SystemExit, match="not in update_keys"):
        kit_release.main(["sign", "--key", str(path), "--installer", str(tmp_path / NEW["file"]), "--feed", "canary",
                          "--rollout", "10", "--out", str(tmp_path / "feed.json")])
    assert not (tmp_path / "feed.json").exists()


def test_signing_lists_the_published_ways_back(tmp_path, monkeypatch):
    path = tmp_path / "everyday.pem"
    public = kit_release.new_key(path, b"correct horse battery")
    monkeypatch.setattr(update_keys, "PUBLIC_KEYS", {"everyday": public})
    monkeypatch.setattr(kit_release.getpass, "getpass", lambda prompt="": "correct horse battery")
    private = kit_release.load_key(path, b"correct horse battery")
    (tmp_path / "old.json").write_bytes(kit_release.sign(private, OLD, 100, [], NOW, "latest"))
    (tmp_path / NEW["file"]).write_bytes(NEW_BODY)
    assert kit_release.main(["sign", "--key", str(path), "--installer", str(tmp_path / NEW["file"]), "--feed", "latest",
                             "--rollout", "10", "--history-from", str(tmp_path / "old.json"),
                             "--out", str(tmp_path / "f.json")]) == 0
    manifest, by = kit_release.verify((tmp_path / "f.json").read_bytes())
    assert by == "everyday" and manifest.rollout == 10 and manifest.feed == "latest"
    assert [entry.sha256 for entry in manifest.history] == [OLD["sha256"]]


def test_the_self_test_fails_a_build_with_no_keys(monkeypatch, signer):
    monkeypatch.setattr(update_keys, "PUBLIC_KEYS", {})
    with pytest.raises(selftest.CheckFailed, match="no signing keys"):
        selftest.check_update({})
    monkeypatch.setattr(update_keys, "PUBLIC_KEYS", {"everyday": kit_release.public_text(signer)})
    assert selftest.check_update({}) == {"keys": ["everyday"], "test_feed": "1.0.0"}


def test_the_self_test_fails_a_verifier_that_accepts_anything(monkeypatch, signer):
    monkeypatch.setattr(update_keys, "PUBLIC_KEYS", {"everyday": kit_release.public_text(signer)})
    monkeypatch.setattr(update, "_signed_by", lambda keys, signature, message: "test")
    with pytest.raises(selftest.CheckFailed, match="changed signature"):
        selftest.check_update({})


@pytest.mark.parametrize("spec, expected", [
    ("proxy.school:8080", "http://proxy.school:8080"),
    ("http=a:1;https=b:2", "http://b:2"),
    ("http=a:1 https=b:2", "http://b:2"),
    ("http=a:1", None),
    ("http://p:3128", "http://p:3128"),
    ("", None),
    (None, None),
])
def test_https_proxy(spec, expected):
    assert winproc.https_proxy(spec) == expected


def test_system_path_is_windows_own_folders():
    assert winproc.system_path({"SystemRoot": r"D:\Win"}) == r"D:\Win\System32;D:\Win;D:\Win\System32\Wbem"
    assert winproc.system_path({}).startswith(r"C:\Windows\System32;")
