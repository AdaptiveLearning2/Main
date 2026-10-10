"""`/healthz` says which kit the sidecar runs in: none outside one, its version, or a version that did not read."""

import sys

import pytest
from fastapi.testclient import TestClient

from src.app import kit_info
from src.app.main import app


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
