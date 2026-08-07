"""Shared test setup for the website backend.

Nothing here is about a specific endpoint -- it exists to stop poller threads
deterministically, see below.
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import eeg_poller  # noqa: E402


def _live_pollers():
    return [t for t in threading.enumerate() if isinstance(t, eeg_poller._Poller)]


@pytest.fixture(autouse=True)
def _join_poller_threads():
    """Stop and join every poller thread a test started, before the next test.

    _Poller is a daemon thread that prints to stdout, including a block of
    teardown lines emitted up to POLL_INTERVAL after stop() signals it. Left to
    daemon-thread teardown, one of those prints can land during interpreter
    shutdown while the stdout BufferedWriter lock is already held, which
    CPython treats as fatal: "_enter_buffered_busy: could not acquire lock for
    <_io.BufferedWriter name='<stdout>'>", exit code 134, after the whole suite
    has passed. Joining here means no poller outlives the test that made it.

    threading.enumerate() rather than eeg_poller._active, deliberately: start()
    pops a same-user poller out of the registry before its thread has finished,
    so the registry does not name every thread that is still running.
    """
    yield
    for p in _live_pollers():
        p.stop()
    for p in _live_pollers():
        p.join(timeout=5)
    still_running = [p.session_id for p in _live_pollers()]
    assert not still_running, f"poller threads did not stop: {still_running}"
