"""Make diagnostic printing incapable of raising.

Every generator prints the model's raw reply on its error paths. On a Windows
console the standard streams are cp1252, and `print` on a character outside it
raises `UnicodeEncodeError` -- so a question containing `π`, an em-dash, `×`,
or an accented name killed generation from a *debug* line. Measured against
Haiku 4.5: one geometry generation in three, on a `π` in the question text.

Two things make that worse than a cosmetic bug. The prints sit on the paths
that run when a reply is already suspect, so the failure lands exactly where
the retry logic was supposed to help. And it raises from inside the retry loop,
where nothing catches it -- the loop absorbs bad JSON and bad shapes, then dies
on printing them.

The fix is `errors="replace"` on the streams rather than an edit to 23 print
sites: it cannot be forgotten by the next one, and it covers the prints that
pass model-derived values without looking like they do (`question_data`, a
rejection reason). The console's own encoding is left alone -- forcing UTF-8 on
a cp1252 console trades a crash for mojibake, and the point is only that a
diagnostic must not be able to take down the thing it is describing.
"""
import sys

_APPLIED = False


def make_console_safe():
    """Idempotent. Safe to call from any module's import."""
    global _APPLIED
    if _APPLIED:
        return
    for stream in (sys.stdout, sys.stderr):
        # Not every stream is a TextIOWrapper -- pytest's capture and a
        # redirected pipe both substitute their own object, and neither needs
        # this. `reconfigure` arrived in 3.7; this codebase is on 3.14.
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):
            pass
    _APPLIED = True
