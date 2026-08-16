"""Shared test setup for the website backend.

Nothing here is about a specific endpoint -- it puts the backend package on
sys.path for every test module, and stops poller threads deterministically.
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
    """Every function that ends a session, derived from the source.

    Three test modules each carried their own copy of this loop -- the archive
    check, the rollup check and the credit check -- which is the duplication the
    close sequence itself was consolidated to remove, reproduced in the tests
    that verify the consolidation.

    A site is a function that calls `_close_session(` **or** writes an
    `"ended_at":` of its own. Both halves matter: the first catches an existing
    site drifting away from the helper, and the second catches a *new* closer
    that hand-rolls the stamp and never reaches the helper at all -- which is
    what all three of these tests were originally written to find, and what
    matching on the helper call alone would go blind to.
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

    _Poller is a daemon thread that prints to stdout, including a block of
    teardown lines emitted after its loop ends. Left to daemon-thread teardown,
    one of those prints can land during interpreter shutdown while the stdout
    BufferedWriter lock is already held, which CPython treats as fatal:
    "_enter_buffered_busy: could not acquire lock for <_io.BufferedWriter
    name='<stdout>'>", exit code 134, after the whole suite has passed. Joining
    here means no poller outlives the test that made it.

    Same helper the server's own shutdown uses, so the tests exercise the path
    that protects production rather than a parallel copy of it.
    """
    yield
    eeg_poller.stop_all()
    still_running = [p.session_id for p in eeg_poller.live_pollers()]
    assert not still_running, f"poller threads did not stop: {still_running}"


@pytest.fixture(autouse=True)
def _consent_allows_polling():
    """Wire a permissive consent check for tests that are not about consent.

    `eeg_poller.start` refuses outright when none is wired, which is deliberate:
    a default of "assume yes" in the module would make an unwired deployment
    record against a refusal and look identical to a wired one. Tests need a
    wired one, and the two that matter -- refused, and unwired -- assert the
    real behaviour explicitly in test_consent_gates_polling.py rather than
    relying on this.
    """
    eeg_poller.set_consent_check(lambda _student_id: True)
    yield
    eeg_poller.set_consent_check(None)


@pytest.fixture(autouse=True)
def _school_year_is_open(monkeypatch):
    """An open retention window for tests that are not about the window.

    Recording is gated on the school year as well as consent (Phase 9), and it
    fails closed the same way: no configured year means nothing records. Most
    of these tests drive a fake Supabase with no `retention_window` table, so
    without this they would keep passing while testing nothing -- every ingest
    assertion satisfied by a refusal that had no connection to the thing under
    test.

    The same reasoning as `_consent_allows_polling` above, and the same escape
    hatch: `test_retention_window.py` overrides this to exercise each state,
    because a default that cannot be turned off is a rule nothing tests.
    """
    import main
    # Cleared both sides of the test: the real reader caches successful reads,
    # and a row cached by one test must not decide another's answer.
    main._retention_cache_clear()
    monkeypatch.setattr(main, "_retention_window",
                        lambda: {"state": main.WINDOW_OPEN,
                                 "starts_on": "2000-01-01",
                                 "ends_on": "2099-12-31",
                                 "timezone": "UTC"})
    yield
    main._retention_cache_clear()
