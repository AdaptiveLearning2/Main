"""Shared test setup for the website backend.

Puts the backend package on sys.path for every test module, and stops poller
threads deterministically after each test.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import eeg_poller  # noqa: E402

# The close sequence lives in `_close_session`, and `_claim_session_close` is
# the half of it that writes the stamp. Neither is a *site*; every other
# function that ends a session is one.
_CLOSE_HELPERS = ("_close_session", "_claim_session_close")


def close_sites():
    """Every function that ends a session, found by scanning the source.

    A site is a function that calls `_close_session(` **or** writes its own
    `"ended_at":`. Both checks matter: one catches a site drifting away from
    the helper, the other catches a new closer that hand-rolls the stamp
    without ever calling the helper.
    """
    import inspect

    import main

    found = []
    for name, obj in vars(main).items():
        if not inspect.isfunction(obj) or obj.__module__ != "main":
            continue
        if name in _CLOSE_HELPERS:
            continue
        try:
            source = inspect.getsource(obj)
        except OSError:                      # pragma: no cover -- defensive
            continue
        if "_close_session(" in source or '"ended_at":' in source:
            found.append((name, source))
    return found


@pytest.fixture(autouse=True)
def _join_poller_threads():
    """Stop and join every poller thread a test started, before the next test.

    A poller thread prints to stdout as it shuts down. Left to daemon-thread
    teardown, one of those prints can land during interpreter shutdown while
    the stdout lock is already held, which CPython treats as a fatal crash
    after the whole suite has passed. Joining here stops that.

    Uses the same helper the server's own shutdown uses, so tests exercise the
    real production path.
    """
    yield
    eeg_poller.stop_all()
    # The stale sweeper is the same kind of thread with the same crash-on-exit
    # risk. Nothing here starts it -- it starts from `_lifespan`, which tests
    # do not run -- but a test that exercises it directly must not leave it
    # running into the next one, where it would close sessions out from under
    # a fake client.
    import main
    main.stop_stale_sweeper()
    still_running = [p.session_id for p in eeg_poller.live_pollers()]
    assert not still_running, f"poller threads did not stop: {still_running}"


@pytest.fixture(autouse=True)
def _consent_allows_polling():
    """Wire a permissive consent check for tests that are not about consent.

    `eeg_poller.start` refuses outright when no check is wired, on purpose:
    defaulting to "assume yes" would make an unwired deployment record without
    real consent. The refused and unwired cases are tested for real in
    test_consent_gates_polling.py instead of relying on this fixture.
    """
    eeg_poller.set_consent_check(lambda _student_id: True)
    yield
    eeg_poller.set_consent_check(None)


@pytest.fixture(autouse=True)
def _school_year_is_open(monkeypatch):
    """An open retention window for tests that are not about the window.

    Recording is gated on the school year as well as consent, and fails closed
    the same way: no configured year means nothing records. Most tests drive a
    fake Supabase with no `retention_window` table, so without this they would
    pass while testing nothing -- an ingest assertion satisfied by a refusal
    unrelated to what the test is checking.

    Same reasoning as `_consent_allows_polling` above. `test_retention_window.py`
    overrides this fixture to exercise each state directly.
    """
    import main
    # Clear the cache on both sides: a row cached by one test must not decide
    # another test's answer.
    main._retention_cache_clear()
    monkeypatch.setattr(main, "_retention_window",
                        lambda: {"state": main.WINDOW_OPEN,
                                 "starts_on": "2000-01-01",
                                 "ends_on": "2099-12-31",
                                 "timezone": "UTC"})
    yield
    main._retention_cache_clear()


def _default_flags():
    import main
    return {k: {"enabled": v, "bypass_until": None}
            for k, v in main._FEATURE_FLAG_DEFAULTS.items()}


@pytest.fixture(autouse=True)
def _feature_flags_are_default():
    """The declared flag defaults for tests that are not about the flags.

    Same reasoning as `_school_year_is_open` above: without this, the real
    reader would fall back to defaults anyway, but only after a caught
    exception and a printed error per call. Pinning the defaults here avoids
    that noise and makes clear the flags are not what the test is checking.

    Cache is cleared on both sides so one test's successful read can't decide
    another test's answer.

    Deliberately does not take `monkeypatch`, unlike its neighbour above:
    requesting it here would hoist `monkeypatch`'s setup ahead of
    `_join_poller_threads` in fixture ordering, inverting their teardown and
    breaking tests that patch `live_pollers` for reasons invisible in their own
    bodies. Patching and restoring by hand avoids that.
    """
    import main
    main._feature_flags_cache_clear()
    original = main._feature_flags
    main._feature_flags = _default_flags
    yield
    main._feature_flags = original
    main._feature_flags_cache_clear()


@pytest.fixture
def set_flag(monkeypatch):
    """Override one feature flag, leaving the rest at their declared defaults.

    Returns a setter rather than taking the flag as a parameter so a test can
    change one and still name the others by their absence.
    """
    def _set(key, enabled, bypass_until=None):
        import main
        assert key in main._FEATURE_FLAG_DEFAULTS, f"unknown flag {key!r}"
        flags = _default_flags()
        flags[key] = {"enabled": enabled, "bypass_until": bypass_until}
        monkeypatch.setattr(main, "_feature_flags", lambda: flags)
        return flags
    return _set
