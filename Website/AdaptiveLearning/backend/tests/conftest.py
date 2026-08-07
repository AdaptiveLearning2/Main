"""Shared test setup for the website backend.

Nothing here is about a specific endpoint -- it puts the backend package on
sys.path for every test module, and stops poller threads deterministically.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import eeg_poller  # noqa: E402


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
