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


def test_a_short_token_warns_but_boots(caplog):
    with caplog.at_level("WARNING"):
        _settings(API_TOKEN="short", ADMIN_TOKEN="other")
    assert "at least 32" in caplog.text
