import secrets

from fastapi import Header, HTTPException, status

from src.app.config import get_settings


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def _matches(token: str | None, expected: str) -> bool:
    if token is None:
        return False
    # Header values are latin-1 on the wire; Starlette decodes them as such, so
    # a non-ASCII byte in the Authorization header becomes a non-ASCII str here.
    # secrets.compare_digest raises TypeError on non-ASCII str input, so compare
    # bytes instead. surrogateescape (not utf-8) because the latin-1 decode can
    # produce lone surrogates that plain utf-8 encoding would also reject.
    return secrets.compare_digest(
        token.encode("utf-8", "surrogateescape"), expected.encode("utf-8")
    )


def require_learner_token(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    if not _matches(_extract_bearer_token(authorization), settings.api_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return "learner"


def require_admin_token(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    if not _matches(_extract_bearer_token(authorization), settings.admin_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return "admin"


def require_local_controller(authorization: str | None = Header(default=None)) -> str:
    """Admin, or -- **only under push** -- the learner token.

    Device lifecycle and headband pairing were admin-only because the backend
    was the only caller: under `pull` it polls this sidecar and drives the
    hardware on the student's behalf. Push inverts that. The backend is remote
    by definition there -- that is *why* push exists -- so it refuses these
    operations (`_refuse_under_push`), and the only thing that can reach a
    student's own headband and camera is the page in front of them.

    Without this, push has no pairing path at all: the browser holds
    `VITE_EEG_LOCAL_TOKEN`, which is the learner token, and every start/scan/
    connect endpoint answered 401. That is the gap that made push unusable for
    the channel it was built for.

    Scoped to the mode rather than granted outright, which is the whole point:

    - under `pull` this is admin-only, exactly as before. A pull deployment's
      browser gains nothing, because the backend is the legitimate controller.
    - under `push` the learner token is accepted for these operations only.
      The admin token keeps working in both.

    What that grants is bounded by what the learner token already was. It ships
    in the client bundle and the sidecar binds to loopback, so it separates one
    page in this browser from another, not one user from another -- and any page
    that could already call `/api/v1/push/start` could already make this sidecar
    stream a student's signals to the backend. Being able to also *pair* the
    headband it streams from is a smaller step than it looks, and it is the
    difference between push working and push being a mode nobody can use.
    """
    settings = get_settings()
    token = _extract_bearer_token(authorization)
    if _matches(token, settings.admin_token):
        return "admin"
    if settings.push_enabled and _matches(token, settings.api_token):
        return "learner"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
