"""A diagnostic print on a cp1252 Windows console must not be able to kill question generation."""
import io
import os

os.environ.setdefault("SUPABASE_URL", "http://localhost:54321")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-key")

import pytest  # noqa: E402

import console_encoding  # noqa: E402

# The characters that actually turn up in generated maths and names.
OUTSIDE_CP1252 = "π ≈ 3.14, x² + y², 45° ∠ABC"


def _cp1252_stream():
    """A stand-in for a Windows console: cp1252, strict, like the real one."""
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252", errors="strict")


def test_the_failure_is_real_on_a_cp1252_stream():
    """Pins the bug, so the next test can't pass merely because the chars are representable."""
    stream = _cp1252_stream()
    with pytest.raises(UnicodeEncodeError):
        stream.write(OUTSIDE_CP1252)
        stream.flush()


def test_reconfiguring_the_stream_stops_it_raising():
    stream = _cp1252_stream()
    stream.reconfigure(errors="replace")
    stream.write(OUTSIDE_CP1252)   # must not raise
    stream.flush()


def test_make_console_safe_is_idempotent_and_survives_odd_streams(monkeypatch):
    """pytest capture and redirected pipes substitute streams with no `reconfigure`."""
    monkeypatch.setattr(console_encoding, "_APPLIED", False)
    monkeypatch.setattr("sys.stdout", object())
    monkeypatch.setattr("sys.stderr", object())
    console_encoding.make_console_safe()
    console_encoding.make_console_safe()


def test_importing_llm_client_applies_it():
    """Generators don't call it; importing llm_client is what applies it everywhere."""
    import llm_client  # noqa: F401
    assert console_encoding._APPLIED is True
