"""A diagnostic print must not be able to kill question generation.

Every generator prints the model's raw reply on its error paths. On a Windows
console those streams are cp1252, so a reply containing `π`, an em-dash, `×` or
an accented name raised `UnicodeEncodeError` from a *debug* line -- measured
against Haiku 4.5 at one geometry generation in three, on a `π` in the question
text.

Two things made it worse than cosmetic: the prints sit on the paths that run
when a reply is already suspect, so it failed exactly where the retry logic was
meant to help; and it raised from inside the retry loop, which absorbs bad JSON
and bad shapes and then died on printing them.
"""
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
    """Pins the bug itself. Without this, the test below could pass because
    the characters are representable rather than because anything was fixed."""
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
    """pytest's capture and a redirected pipe both substitute their own stream
    object, and neither needs reconfiguring -- so this must not raise on a
    stream that has no `reconfigure`."""
    monkeypatch.setattr(console_encoding, "_APPLIED", False)
    monkeypatch.setattr("sys.stdout", object())
    monkeypatch.setattr("sys.stderr", object())
    console_encoding.make_console_safe()
    console_encoding.make_console_safe()


def test_importing_llm_client_applies_it():
    """The generators do not call this themselves; they all import llm_client,
    which is what makes it reach the app, the scripts and a direct call alike."""
    import llm_client  # noqa: F401
    assert console_encoding._APPLIED is True
