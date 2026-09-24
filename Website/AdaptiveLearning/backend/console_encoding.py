"""Make diagnostic printing incapable of raising.

On a cp1252 Windows console, printing a model reply containing e.g. `π` raises
UnicodeEncodeError inside the generators' retry loops. `errors="replace"` on the
streams covers every print site; the console encoding itself is left alone.
"""
import sys

_APPLIED = False


def make_console_safe():
    """Idempotent. Safe to call from any module's import."""
    global _APPLIED
    if _APPLIED:
        return
    for stream in (sys.stdout, sys.stderr):
        # pytest capture and redirected pipes are not TextIOWrappers.
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    _APPLIED = True
