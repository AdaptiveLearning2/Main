"""The admin page's kit installer: links the gate Worker honours, and its answers read as distinct states, never two."""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import httpx  # noqa: E402
import pydantic  # noqa: E402
import pytest  # noqa: E402

import kit_gate  # noqa: E402
import main  # noqa: E402

GATE = "https://kit-updates.example.workers.dev"
SECRET = "link-secret-for-tests"
NOW = datetime(2026, 10, 10, 2, 30, tzinfo=timezone.utc)  # 1791599400: a link signed now expires at EXP
EXP = 1791600000
SETUP = "AdaptiveLearningSensors-Setup-0.2.1.exe"
SHA = "8d953f5d18141012ab" + "0" * 46
SIZE = 146618712
# What `kit_release.py setup` writes and the gate passes through unchanged.
CURRENT = (b'{\n  "file": "AdaptiveLearningSensors-Setup-0.2.1.exe",\n  "published": "2026-10-09T19:51:51+00:00",\n'
           b'  "sha256": "' + SHA.encode() + b'",\n  "size": 146618712,\n  "version": "0.2.1"\n}\n')
OFFERED = {"version": "0.2.1", "file": SETUP, "sha256": SHA, "size": SIZE, "published_at": "2026-10-09T19:51:51+00:00"}


def gate(current=CURRENT, files=None, status=None, headers=None, seen=None):
    """The Worker's answers, marks included: it checks a link's signature as the Worker does, then reads its bucket."""
    files = {SETUP: str(SIZE)} if files is None else files

    def handle(request):
        if seen is not None:
            seen.append(request)
        if status is not None:
            return httpx.Response(status, text="whatever answered", headers=headers or {})
        query = parse_qs(urlsplit(str(request.url)).query)
        path = request.url.path
        file = path.removeprefix("/v1/setup/files/") if path.startswith("/v1/setup/files/") else None
        if path != "/v1/setup/current.json" and file is None:
            return httpx.Response(401, text="unauthorized")
        subject = f"setup:{file}" if file else "meta"
        expected = hmac.new(SECRET.encode(), f"{subject}:{query['exp'][0]}".encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(query["sig"][0], expected):
            return httpx.Response(403, text="this link has expired or is not valid", headers={"X-Kit-Setup": "refused"})
        if file is None:
            if current is None:
                return httpx.Response(404, text="no installer has been published", headers={"X-Kit-Setup": "none"})
            return httpx.Response(200, content=current, headers={"Content-Type": "application/json"})
        if file not in files:
            return httpx.Response(404, text=f"{file} is not in the bucket", headers={"X-Kit-Setup": "missing"})
        return httpx.Response(200, headers={} if files[file] is None else {"Content-Length": files[file]})
    return kit_gate.client(httpx.MockTransport(handle))


def offered(http):
    return kit_gate.current(GATE, SECRET, NOW.timestamp(), http)


# ─── the link ─────────────────────────────────────────────────────────────

def test_the_signature_is_the_one_the_gate_checks():
    # The same vectors are asserted in update_gate/worker.test.mjs and EEGResearch/tests/test_kit_update.py.
    assert kit_gate.signature(SECRET, f"setup:{SETUP}", EXP) == (
        "823bd77fcb80967df8ccf2f4d7a12d7fe0fb1aa5f236b703d29829a90f4c3e88")
    assert kit_gate.signature(SECRET, "meta", EXP) == "8e145db21708e6554a4d8c1f5cc471620d424a936d7dec1aa957327f389a498f"


def test_a_link_lasts_ten_minutes_opens_one_file_and_names_only_its_expiry_and_signature():
    url, exp = kit_gate.link(GATE, SECRET, NOW.timestamp() + 0.9, SETUP)
    assert exp == EXP
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{GATE}/v1/setup/files/{SETUP}"
    assert parse_qs(parts.query) == {"exp": [str(EXP)], "sig": [kit_gate.signature(SECRET, f"setup:{SETUP}", EXP)]}
    assert kit_gate.link(GATE, SECRET, NOW.timestamp())[0] == (
        f"{GATE}/v1/setup/current.json?exp={EXP}&sig={kit_gate.signature(SECRET, 'meta', EXP)}")


@pytest.mark.parametrize("env,expected", [
    ({"KIT_GATE_URL": f"{GATE}/", "KIT_LINK_SECRET": f" {SECRET}\n"}, (GATE, SECRET)),
    ({"KIT_GATE_URL": GATE}, None),
    ({"KIT_LINK_SECRET": SECRET}, None),
    ({"KIT_GATE_URL": " ", "KIT_LINK_SECRET": SECRET}, None),
    ({"KIT_GATE_URL": GATE, "KIT_LINK_SECRET": "  "}, None),
])
def test_the_gate_is_set_up_only_with_both_settings(env, expected):
    assert kit_gate.settings(env) == expected


# ─── what the gate answers ────────────────────────────────────────────────

def test_the_details_then_the_file_are_asked_for_with_signed_links_and_the_backends_own_user_agent():
    seen = []
    assert offered(gate(seen=seen)) == OFFERED
    meta, head = seen
    assert (meta.method, str(meta.url)) == (
        "GET", f"{GATE}/v1/setup/current.json?exp={EXP}&sig={kit_gate.signature(SECRET, 'meta', EXP)}")
    # Published means downloadable: the file the details name is asked for too, without its bytes.
    assert (head.method, str(head.url)) == (
        "HEAD", f"{GATE}/v1/setup/files/{SETUP}?exp={EXP}&sig={kit_gate.signature(SECRET, f'setup:{SETUP}', EXP)}")
    for request in seen:
        assert request.headers["User-Agent"] == "AdaptiveLearningBackend"  # Cloudflare 403s python-httpx's
        assert "Authorization" not in request.headers


def test_nothing_published_is_only_the_gates_own_404():
    seen = []
    assert offered(gate(current=None, seen=seen)) is None
    assert len(seen) == 1


@pytest.mark.parametrize("status,headers,names", [
    (401, {}, "redeploy the Worker"),
    (403, {"X-Kit-Setup": "refused"}, "KIT_LINK_SECRET is not the gate's LINK_SECRET"),
    (403, {"X-Kit-Setup": "expired"}, "this server's clock and Cloudflare's disagree"),
    (403, {}, "in front of the gate"),
    (503, {"X-Kit-Setup": "no-secret"}, "no LINK_SECRET"),
    (302, {"Location": "https://elsewhere.example/v1"}, "redirects (302, to elsewhere.example)"),
    (301, {}, "redirects (301, to elsewhere)"),
    (404, {}, "does not point at the gate"),
])
def test_a_gate_set_up_wrong_is_named_and_never_retried(status, headers, names):
    with pytest.raises(kit_gate.GateError, match=names.replace("(", r"\(").replace(")", r"\)")) as e:
        offered(gate(status=status, headers=headers))
    assert (e.value.kind, e.value.retryable) == ("setup", False)


def test_a_secret_the_gate_does_not_share_is_named_as_the_cause_and_skew_is_not():
    with pytest.raises(kit_gate.GateError, match="KIT_LINK_SECRET") as e:
        kit_gate.current(GATE, "another-secret", NOW.timestamp(), gate())
    assert e.value.kind == "setup"
    with pytest.raises(kit_gate.GateError, match="clock") as e:
        offered(gate(status=403, headers={"X-Kit-Setup": "expired"}))
    assert "KIT_LINK_SECRET" not in str(e.value)


@pytest.mark.parametrize("status,headers", [(500, {}), (502, {}), (503, {}), (429, {}), (503, {"X-Kit-Setup": "x"})])
def test_an_answer_that_may_pass_is_a_failed_read_to_retry(status, headers):
    with pytest.raises(kit_gate.GateError, match=str(status)) as e:
        offered(gate(status=status, headers=headers))
    assert (e.value.kind, e.value.retryable) == ("outage", True)


def test_a_redirect_is_not_followed():
    asked = []

    def redirect(request):
        asked.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://elsewhere.example/steal"})
    with pytest.raises(kit_gate.GateError, match="302"):
        offered(kit_gate.client(httpx.MockTransport(redirect)))
    assert len(asked) == 1 and asked[0].startswith(GATE)


def test_an_unreachable_gate_is_a_failed_read_to_retry():
    def down(request):
        raise httpx.ConnectError("no route", request=request)
    with pytest.raises(kit_gate.GateError, match="ConnectError") as e:
        offered(kit_gate.client(httpx.MockTransport(down)))
    assert e.value.retryable is True


def test_details_naming_a_file_the_bucket_lacks_are_a_publish_to_redo():
    with pytest.raises(kit_gate.GateError, match=f"names {SETUP}, which the bucket does not hold") as e:
        offered(gate(files={}))
    assert (e.value.kind, e.value.retryable) == ("publish", False)


def test_a_file_of_another_size_is_a_publish_to_redo_and_an_unsent_or_zero_size_is_not_judged():
    with pytest.raises(kit_gate.GateError, match="not the size") as e:
        offered(gate(files={SETUP: str(SIZE - 1)}))
    assert (e.value.kind, e.value.retryable) == ("publish", False)
    # A proxy may drop or zero a HEAD's length; judging it would block every download with no republish to fix it.
    assert offered(gate(files={SETUP: None})) == OFFERED
    assert offered(gate(files={SETUP: "0"})) == OFFERED


def _with(**changes):
    data = json.loads(CURRENT)
    data.update(changes)
    return json.dumps({k: v for k, v in data.items() if v is not ...}).encode()


@pytest.mark.parametrize("body", [
    b"not json", b"[]", b'"text"',
    _with(version=...), _with(file=...), _with(sha256=...), _with(size=...), _with(published=...),
    _with(version="0.2"), _with(version=21),
    _with(file="AdaptiveLearningSensors-Update-0.2.1.exe"), _with(file="../feed/latest.json"),
    _with(sha256="8D953F5D" + "0" * 56), _with(sha256="ab" * 31),
    _with(size=0), _with(size=True), _with(size="146618712"), _with(size=1.5),
    _with(published=None), _with(published="yesterday"), _with(published="2026-10-09"),
    _with(published="2026-10-09T19:51:51"),
])
def test_details_that_do_not_describe_a_setup_installer_are_a_publish_to_redo(body):
    with pytest.raises(kit_gate.GateError, match="does not describe a Setup installer") as e:
        offered(gate(current=body))
    assert (e.value.kind, e.value.retryable) == ("publish", False)


# ─── the admin routes ─────────────────────────────────────────────────────

ADMIN = {"id": "a1b2c3d4-admin-user-id"}


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda _r: ADMIN)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    monkeypatch.setattr(main, "_KIT_GATE", (GATE, SECRET))


def asking(monkeypatch, http):
    monkeypatch.setattr(kit_gate, "_HTTP", http)


def link_for(sha256=SHA):
    return main.admin_kit_download_link(None, main.KitLinkRequest(sha256=sha256))


def test_the_page_reads_what_the_gate_offers(monkeypatch, admin):
    asking(monkeypatch, gate())
    assert main.admin_kit(None) == {"configured": True, "published": True, **OFFERED}


def test_the_page_says_when_nothing_is_published(monkeypatch, admin):
    asking(monkeypatch, gate(current=None))
    assert main.admin_kit(None) == {"configured": True, "published": False}


def test_a_gate_set_up_wrong_is_a_problem_to_fix_not_a_retry(monkeypatch, admin):
    asking(monkeypatch, gate(status=401))
    answer = main.admin_kit(None)
    assert answer["configured"] is True and "redeploy the Worker" in answer["problem"]
    assert answer["problem_is"] == "setup" and "published" not in answer


def test_a_half_finished_publish_is_a_problem_of_its_own_kind(monkeypatch, admin):
    asking(monkeypatch, gate(files={}))
    answer = main.admin_kit(None)
    assert answer["problem_is"] == "publish" and "publish it again with -Setup" in answer["problem"]


def test_an_outage_is_a_503_to_retry_never_nothing_published(monkeypatch, admin):
    asking(monkeypatch, gate(status=502))
    with pytest.raises(main.HTTPException) as e:
        main.admin_kit(None)
    assert e.value.status_code == 503 and "Retry-After" in e.value.headers


def test_without_the_gate_settings_the_page_says_so_and_asks_nothing(monkeypatch, admin):
    monkeypatch.setattr(main, "_KIT_GATE", None)
    seen = []
    asking(monkeypatch, gate(seen=seen))
    assert main.admin_kit(None) == {"configured": False}
    with pytest.raises(main.HTTPException) as e:
        link_for()
    assert e.value.status_code == 503
    assert seen == []


def test_a_download_link_opens_the_file_the_page_showed_for_ten_minutes_names_no_user_and_is_never_cached(
        monkeypatch, admin):
    asking(monkeypatch, gate())
    response = link_for()
    body = json.loads(response.body)
    assert response.headers["Cache-Control"] == "no-store"
    assert body["expires_at"] == "2026-10-10T02:40:00+00:00"
    assert body["url"] == (
        f"{GATE}/v1/setup/files/{SETUP}?exp={EXP}&sig={kit_gate.signature(SECRET, f'setup:{SETUP}', EXP)}")
    assert ADMIN["id"] not in body["url"] and "a1b2c3d4" not in body["url"]


@pytest.mark.parametrize("http,sha256,says", [
    (lambda: gate(), "ab" * 32, "A different installer was published"),
    (lambda: gate(current=None), SHA, "No installer is published any more"),
    (lambda: gate(files={}), SHA, "The last publish did not finish: setup/current.json names"),
    (lambda: gate(status=401), SHA, "The download gate is set up wrong: the gate asked for its download key"),
])
def test_no_link_is_given_for_an_installer_other_than_the_one_shown(monkeypatch, admin, http, sha256, says):
    asking(monkeypatch, http())
    with pytest.raises(main.HTTPException) as e:
        link_for(sha256)
    assert e.value.status_code == 409 and e.value.detail.startswith(says)


def test_a_link_asked_for_naming_no_installer_is_to_the_current_one(monkeypatch, admin):
    """A caller that sends no hash, such as a page loaded before the hash was sent, still gets a working link."""
    asking(monkeypatch, gate())
    body = json.loads(main.admin_kit_download_link(None, None).body)
    assert body["url"].startswith(f"{GATE}/v1/setup/files/{SETUP}?exp={EXP}&")


def test_through_the_app_a_post_with_no_body_gets_a_link_and_a_malformed_hash_a_422(monkeypatch, admin):
    from fastapi.testclient import TestClient
    asking(monkeypatch, gate())
    client = TestClient(main.app)
    path = "/api/admin/kit/download-link"
    assert client.post(path).status_code == 200
    assert client.post(path, json={"sha256": SHA}).status_code == 200
    assert client.post(path, json={"sha256": SHA.upper()}).status_code == 422


def test_a_link_asked_for_during_an_outage_is_a_503_to_retry(monkeypatch, admin):
    asking(monkeypatch, gate(status=503))
    with pytest.raises(main.HTTPException) as e:
        link_for()
    assert e.value.status_code == 503


@pytest.mark.parametrize("sha256", ["AB" * 32, "ab" * 31, "ab" * 33, "", "z" * 64])
def test_the_page_must_name_the_installer_by_its_hash(sha256):
    with pytest.raises(pydantic.ValidationError):
        main.KitLinkRequest(sha256=sha256)
