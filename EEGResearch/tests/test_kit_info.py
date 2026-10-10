"""`/healthz` says which kit the sidecar runs in (none, its version, or one that did not read); the self-test holds it."""

import sys

import pytest
from fastapi.testclient import TestClient

from src.app import kit_info
from src.app.main import app
from src.kit import selftest, update


def _installed(tmp_path, version_txt: bytes | None):
    if version_txt is not None:
        (tmp_path / "version.txt").write_bytes(version_txt)
    return str(tmp_path / "AdaptiveLearningSensors.exe")


@pytest.fixture
def not_a_kit(monkeypatch):
    monkeypatch.delenv("KIT_APP_DIR", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)


def test_outside_a_kit_healthz_says_so(not_a_kit):
    assert kit_info.kit() is None
    body = TestClient(app).get("/healthz").json()
    assert body == {"status": "ok", "kit": None}


def test_in_the_frozen_kit_healthz_reports_the_installed_version(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", _installed(tmp_path, b"0.2.1\r\n"))
    assert TestClient(app).get("/healthz").json() == {"status": "ok", "kit": {"version": "0.2.1"}}


def test_a_kit_run_from_source_reads_its_folders_version(tmp_path):
    _installed(tmp_path, b"0.3.10\n")
    assert kit_info.kit(frozen=False, environ={"KIT_APP_DIR": str(tmp_path)}) == {"version": "0.3.10"}


def test_a_kit_folder_that_cannot_be_read_still_answers_healthz_and_logs_it_once(monkeypatch, caplog):
    # A path no file system takes: read_text raises ValueError, which kit() does not expect.
    real = kit_info.kit
    monkeypatch.setattr(kit_info, "kit", lambda: real(frozen=False, environ={"KIT_APP_DIR": "C:\\kit\0dir"}))
    monkeypatch.setattr(kit_info, "_failed_before", False)
    client = TestClient(app)
    for _ in range(3):
        response = client.get("/healthz")
        assert response.status_code == 200 and response.json() == {"status": "ok", "kit": {"version": None}}
    assert [r.levelname for r in caplog.records if r.name == "src.app.kit_info"] == ["ERROR"]


@pytest.mark.parametrize("version_txt", [
    None, b"", b"0.2", b"0.2.1-beta", b"v0.2.1", b"0.2.1.0", b"12345.0.0",
    "٠.٢.١".encode("utf-8"),  # digits to \d, but not ASCII: the ASCII read refuses them first
    "0.2.1".encode("utf-16"),
])
def test_a_kit_whose_version_does_not_read_says_so_rather_than_guessing(tmp_path, version_txt):
    assert kit_info.kit(frozen=True, executable=_installed(tmp_path, version_txt)) == {"version": None}


def _check_server(monkeypatch, tmp_path, answer=None):
    """The self-test's server check on the kit in tmp_path; answer stands in for /healthz, else the real app's."""
    def serve(served, path):
        if answer is not None:
            return answer
        response = TestClient(served).get(path)
        return response.status_code, response.json(), []

    monkeypatch.setattr(selftest, "_serve_once", serve)
    monkeypatch.setenv("KIT_APP_DIR", str(tmp_path))
    monkeypatch.delattr(sys, "frozen", raising=False)
    return selftest.check_server({"settings_ok": True, "app": tmp_path})


def test_the_self_test_passes_a_sidecar_reporting_its_kits_own_version(monkeypatch, tmp_path):
    _installed(tmp_path, b"0.2.3\r\n")
    assert _check_server(monkeypatch, tmp_path) == {"healthz": 200, "warnings": []}


@pytest.mark.parametrize("status, body", [
    (200, {"status": "ok"}),
    (200, {"status": "ok", "kit": None}),
    (200, {"status": "ok", "kit": {"version": None}}),
    (200, {"status": "ok", "kit": {"version": "0.2.2"}}),
    (200, {"status": "degraded", "kit": {"version": "0.2.3"}}),
    (503, {"status": "ok", "kit": {"version": "0.2.3"}}),
])
def test_the_self_test_fails_a_sidecar_naming_another_kit_or_none(monkeypatch, tmp_path, status, body):
    _installed(tmp_path, b"0.2.3")
    with pytest.raises(selftest.CheckFailed):
        _check_server(monkeypatch, tmp_path, (status, body, []))


def test_the_self_test_fails_a_kit_with_no_version_txt(monkeypatch, tmp_path):
    with pytest.raises(update.FeedError):
        _check_server(monkeypatch, tmp_path, (200, {"status": "ok", "kit": {"version": None}}, []))
