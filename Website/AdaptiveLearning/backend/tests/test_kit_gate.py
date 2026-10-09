"""The admin page's kit installer: links the gate Worker honours, and its answers read as three states, never two."""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import httpx  # noqa: E402
import pytest  # noqa: E402

import kit_gate  # noqa: E402
import main  # noqa: E402

GATE = "https://kit-updates.example.workers.dev"
SECRET = "link-secret-for-tests"
NOW = datetime(2026, 10, 10, 2, 30, tzinfo=timezone.utc)  # 1791599400: a link signed now expires at EXP
EXP = 1791600000
# What `kit_release.py setup` writes and the gate passes through unchanged.
CURRENT = (b'{\n  "file": "AdaptiveLearningSensors-Setup-0.2.1.exe",\n  "published": "2026-10-09T19:51:51+00:00",\n'
           b'  "sha256": "8d953f5d18141012ab' + b"0" * 46 + b'",\n  "size": 146618712,\n  "version": "0.2.1"\n}\n')


def gate(current=CURRENT, status=None, seen=None):
    """The Worker's answers to a details link: it checks the signature as the Worker does, then reads the bucket."""
    def handle(request):
        if seen is not None:
            seen.append(request)
        if status is not None:
            return httpx.Response(status, text="whatever answered")
        query = parse_qs(urlsplit(str(request.url)).query)
        exp, sig = query["exp"][0], query["sig"][0]
        expected = hmac.new(SECRET.encode(), f"meta:{exp}".encode(), hashlib.sha256).hexdigest()
        if request.url.path != "/v1/setup/current.json" or not hmac.compare_digest(sig, expected):
            return httpx.Response(403, text="this link has expired or is not valid; ask the admin page for a new one")
        if current is None:
            return httpx.Response(404, text="no installer has been published",
                                  headers={"Cache-Control": "no-store", "X-Kit-Setup": "none"})
        return httpx.Response(200, content=current, headers={"Content-Type": "application/json"})
    return kit_gate.client(httpx.MockTransport(handle))


# ─── the link ─────────────────────────────────────────────────────────────

def test_the_signature_is_the_one_the_gate_checks():
    # The same vector is asserted in update_gate/worker.test.mjs and tests/test_kit_update.py.
    assert kit_gate.signature(SECRET, "setup", EXP) == "c1fc44f6d56f32d734d035082599bead29dffdbe66ddedf9ab53a3c1935631b8"
    assert kit_gate.signature(SECRET, "meta", EXP) == "8e145db21708e6554a4d8c1f5cc471620d424a936d7dec1aa957327f389a498f"


def test_a_link_lasts_ten_minutes_and_names_only_its_expiry_and_signature():
    url, exp = kit_gate.link(GATE, SECRET, "setup", NOW.timestamp() + 0.9)
    assert exp == EXP
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == f"{GATE}/v1/setup/current"
    assert parse_qs(parts.query) == {"exp": [str(EXP)], "sig": [kit_gate.signature(SECRET, "setup", EXP)]}
    assert kit_gate.link(GATE, SECRET, "meta", NOW.timestamp())[0].startswith(f"{GATE}/v1/setup/current.json?")


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

def test_the_details_are_asked_for_with_a_signed_link_and_the_backends_own_user_agent():
    seen = []
    found = kit_gate.current(GATE, SECRET, NOW.timestamp(), gate(seen=seen))
    assert found == {"version": "0.2.1", "file": "AdaptiveLearningSensors-Setup-0.2.1.exe",
                     "sha256": "8d953f5d18141012ab" + "0" * 46, "size": 146618712,
                     "published_at": "2026-10-09T19:51:51+00:00"}
    [request] = seen
    assert request.method == "GET"
    assert str(request.url) == f"{GATE}/v1/setup/current.json?exp={EXP}&sig={kit_gate.signature(SECRET, 'meta', EXP)}"
    assert request.headers["User-Agent"] == "AdaptiveLearningBackend"  # Cloudflare 403s python-httpx's
    assert "Authorization" not in request.headers


def test_nothing_published_is_only_the_gates_own_404():
    assert kit_gate.current(GATE, SECRET, NOW.timestamp(), gate(current=None)) is None
    with pytest.raises(kit_gate.GateError, match="404"):
        kit_gate.current(GATE, SECRET, NOW.timestamp(), gate(status=404))  # a wrong KIT_GATE_URL, not an empty bucket


def test_a_secret_the_gate_does_not_share_is_named_as_the_likely_cause():
    with pytest.raises(kit_gate.GateError, match="KIT_LINK_SECRET"):
        kit_gate.current(GATE, "another-secret", NOW.timestamp(), gate())


@pytest.mark.parametrize("status", [302, 401, 500, 503])
def test_any_other_answer_is_a_failed_read(status):
    with pytest.raises(kit_gate.GateError, match=str(status)):
        kit_gate.current(GATE, SECRET, NOW.timestamp(), gate(status=status))


def test_a_redirect_is_not_followed():
    def redirect(request):
        return httpx.Response(302, headers={"Location": "https://elsewhere.example/steal"})
    asked = []

    def record(request):
        asked.append(str(request.url))
        return redirect(request)
    with pytest.raises(kit_gate.GateError, match="302"):
        kit_gate.current(GATE, SECRET, NOW.timestamp(), kit_gate.client(httpx.MockTransport(record)))
    assert len(asked) == 1 and asked[0].startswith(GATE)


def test_an_unreachable_gate_is_a_failed_read():
    def down(request):
        raise httpx.ConnectError("no route", request=request)
    with pytest.raises(kit_gate.GateError, match="ConnectError"):
        kit_gate.current(GATE, SECRET, NOW.timestamp(), kit_gate.client(httpx.MockTransport(down)))


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
    _with(published=None),
])
def test_details_that_do_not_describe_a_setup_installer_are_a_failed_read(body):
    with pytest.raises(kit_gate.GateError):
        kit_gate.current(GATE, SECRET, NOW.timestamp(), gate(current=body))


# ─── the admin routes ─────────────────────────────────────────────────────

ADMIN = {"id": "a1b2c3d4-admin-user-id"}


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setattr(main, "_require_admin", lambda _r: ADMIN)
    monkeypatch.setattr(main, "_utc_now", lambda: NOW)
    monkeypatch.setattr(main, "_KIT_GATE", (GATE, SECRET))


def test_the_page_reads_what_the_gate_offers(monkeypatch, admin):
    seen = []
    monkeypatch.setattr(kit_gate, "_HTTP", gate(seen=seen))
    assert main.admin_kit(None) == {"configured": True, "published": True, "version": "0.2.1",
                                    "file": "AdaptiveLearningSensors-Setup-0.2.1.exe",
                                    "sha256": "8d953f5d18141012ab" + "0" * 46, "size": 146618712,
                                    "published_at": "2026-10-09T19:51:51+00:00"}
    assert f"exp={EXP}&" in str(seen[0].url)


def test_the_page_says_when_nothing_is_published(monkeypatch, admin):
    monkeypatch.setattr(kit_gate, "_HTTP", gate(current=None))
    assert main.admin_kit(None) == {"configured": True, "published": False}


def test_a_gate_that_cannot_be_read_is_a_503_never_nothing_published(monkeypatch, admin):
    monkeypatch.setattr(kit_gate, "_HTTP", gate(status=404))
    with pytest.raises(main.HTTPException) as e:
        main.admin_kit(None)
    assert e.value.status_code == 503 and "Retry-After" in e.value.headers


def test_without_the_gate_settings_the_page_says_so_and_asks_nothing(monkeypatch, admin):
    monkeypatch.setattr(main, "_KIT_GATE", None)
    seen = []
    monkeypatch.setattr(kit_gate, "_HTTP", gate(seen=seen))
    assert main.admin_kit(None) == {"configured": False}
    assert seen == []
    with pytest.raises(main.HTTPException) as e:
        main.admin_kit_download_link(None)
    assert e.value.status_code == 503


def test_a_download_link_lasts_ten_minutes_names_no_user_and_is_never_cached(admin):
    response = main.admin_kit_download_link(None)
    body = json.loads(response.body)
    assert response.headers["Cache-Control"] == "no-store"
    assert body["expires_at"] == "2026-10-10T02:40:00+00:00"
    assert body["url"] == f"{GATE}/v1/setup/current?exp={EXP}&sig={kit_gate.signature(SECRET, 'setup', EXP)}"
    assert ADMIN["id"] not in body["url"] and "a1b2c3d4" not in body["url"]
