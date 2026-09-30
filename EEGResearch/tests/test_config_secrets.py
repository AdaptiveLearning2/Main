"""The sidecar refuses to boot on a published token, a shared token, or a cleartext remote backend."""

import pytest
from pydantic import ValidationError

from src.app.config import Settings

REAL = "k" * 43


def _settings(**over):
    base = {"API_TOKEN": REAL, "ADMIN_TOKEN": "a" * 43, "_env_file": None}
    base.update(over)
    return Settings(**base)


@pytest.mark.parametrize("field", ["API_TOKEN", "ADMIN_TOKEN"])
def test_the_published_placeholder_is_refused(field):
    """A copied .env.example boots with tokens anyone can read in the repository."""
    with pytest.raises(ValidationError, match="placeholder"):
        _settings(**{field: "replace-me-learner-token"})


def test_one_token_for_both_roles_is_refused():
    """The learner token ships in the page, so sharing it makes the admin token public."""
    with pytest.raises(ValidationError, match="same"):
        _settings(API_TOKEN=REAL, ADMIN_TOKEN=REAL)


@pytest.mark.parametrize("url", ["http://192.168.1.20:8000", "http://backend.school.example"])
def test_push_to_a_remote_backend_over_http_is_refused(url):
    with pytest.raises(ValidationError, match="cleartext"):
        _settings(PUSH_ENABLED=True, BACKEND_URL=url)


@pytest.mark.parametrize("url", ["http://127.0.0.1:8000", "http://localhost:8000",
                                 "https://api.school.example"])
def test_push_to_this_machine_or_over_https_boots(url):
    assert _settings(PUSH_ENABLED=True, BACKEND_URL=url).backend_url == url


def test_a_remote_http_url_is_fine_while_push_is_off():
    """Nothing is sent to it: the backend pulls."""
    assert _settings(PUSH_ENABLED=False, BACKEND_URL="http://192.168.1.20:8000")


@pytest.mark.parametrize("hz, push, warns", [(4, True, False), (10, True, False),
                                              (11, True, True), (30, False, False)])
def test_a_push_rate_the_backend_would_refuse_warns(hz, push, warns, caplog):
    """The backend cannot see EEG_SAMPLE_HZ, so its ceiling is checked here."""
    with caplog.at_level("WARNING"):
        _settings(EEG_SAMPLE_HZ=hz, PUSH_ENABLED=push)
    assert ("INGEST_MAX_ROWS_PER_MINUTE" in caplog.text) is warns


def test_a_short_token_warns_but_boots(caplog):
    with caplog.at_level("WARNING"):
        _settings(API_TOKEN="short", ADMIN_TOKEN="other")
    assert "at least 32" in caplog.text
