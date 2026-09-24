"""Shared backend test setup: sys.path, and deterministic poller-thread shutdown."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

import eeg_poller  # noqa: E402

# Helpers inside the close sequence; not sites themselves.
_CLOSE_HELPERS = ("_close_session", "_claim_session_close")


def close_sites():
    """Every function that ends a session, found by scanning the source.

    A site calls `_close_session(` **or** writes its own `"ended_at":` -- the
    first catches drift from the helper, the second a hand-rolled stamp.
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

    A print during interpreter shutdown is a fatal stdout-lock abort; joining
    through the production `stop_all()` prevents it.
    """
    yield
    eeg_poller.stop_all()
    # The stale sweeper has the same crash-on-exit risk, and left running it
    # would close sessions under the next test's fake client.
    import main
    main.stop_stale_sweeper()
    still_running = [p.session_id for p in eeg_poller.live_pollers()]
    assert not still_running, f"poller threads did not stop: {still_running}"


@pytest.fixture(autouse=True)
def _consent_allows_polling():
    """Wire a permissive consent check; `start` refuses when none is wired.

    The refused and unwired cases live in test_consent_gates_polling.py.
    """
    eeg_poller.set_consent_check(lambda _student_id: True)
    yield
    eeg_poller.set_consent_check(None)


@pytest.fixture(autouse=True)
def _school_year_is_open(monkeypatch):
    """An open retention window, or tests would pass by recording nothing.

    `test_retention_window.py` overrides this to exercise each state.
    """
    import main
    # Cleared on both sides so one test's cached row can't decide another's.
    main._retention_cache_clear()
    monkeypatch.setattr(main, "_retention_window",
                        lambda: {"state": main.WINDOW_OPEN,
                                 "starts_on": "2000-01-01",
                                 "ends_on": "2099-12-31",
                                 "timezone": "UTC"})
    yield
    main._retention_cache_clear()


@pytest.fixture(autouse=True)
def _limiters_start_empty():
    """Every sliding window is empty at the start of every test.

    Found by walking the module, so a new limiter is covered automatically.
    Takes no fixtures, for the ordering reason in `_feature_flags_are_default`.
    """
    import main
    cls = type(main._STRATEGY_LIMITER)
    for value in list(vars(main).values()):
        if isinstance(value, cls):
            value.reset()
        elif isinstance(value, dict):
            # Copied first: poller and prefetch threads mutate module-level dicts.
            for member in list(value.values()):
                if isinstance(member, cls):
                    member.reset()
    yield


def _default_flags():
    import main
    return {k: {"enabled": v, "bypass_until": None}
            for k, v in main._FEATURE_FLAG_DEFAULTS.items()}


@pytest.fixture(autouse=True)
def _feature_flags_are_default():
    """The declared flag defaults, without a caught exception per read.

    Deliberately takes no `monkeypatch`: requesting it would hoist its setup
    ahead of `_join_poller_threads` and invert their teardown.
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
    """Override one feature flag, leaving the rest at their declared defaults."""
    def _set(key, enabled, bypass_until=None):
        import main
        assert key in main._FEATURE_FLAG_DEFAULTS, f"unknown flag {key!r}"
        flags = _default_flags()
        flags[key] = {"enabled": enabled, "bypass_until": bypass_until}
        monkeypatch.setattr(main, "_feature_flags", lambda: flags)
        return flags
    return _set


def tighten(monkeypatch, limiter, *, limit=None, window=None):
    """Shrink one `_SlidingWindowLimiter`'s budget for a test, and reset it.

    Patch the limiter, never `main._X_RATE_LIMIT`: those constants are read
    once at import, so patching one changes nothing.
    """
    if limit is not None:
        monkeypatch.setattr(limiter, "limit", limit)
    if window is not None:
        monkeypatch.setattr(limiter, "window", window)
    limiter.reset()
