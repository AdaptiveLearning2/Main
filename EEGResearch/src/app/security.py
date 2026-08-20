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
    # Starlette decodes headers as latin-1, so a non-ASCII byte becomes a non-ASCII
    # str here, and secrets.compare_digest raises on that -- compare bytes instead.
    # surrogateescape, not utf-8, since the latin-1 decode can produce lone
    # surrogates that plain utf-8 encoding would reject.
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
    """Admin, or -- only under push -- the learner token.

    Under `pull`, the backend drives device lifecycle and pairing on the student's
    behalf, so this stays admin-only there. Under `push` the backend is remote and
    refuses these operations itself (`_refuse_under_push`), so only the page in front
    of the student can reach their headband/camera -- and it only has the learner
    token (`VITE_EEG_LOCAL_TOKEN`). Without this, push has no pairing path and every
    start/scan/connect call answers 401.

    The learner token ships in the client bundle and the sidecar binds to loopback,
    so it separates browser pages, not users -- a page that can already call
    `/api/v1/push/start` can already make this sidecar stream a student's signals,
    so letting it also pair the device is a small addition, not a new exposure.
    """
    settings = get_settings()
    token = _extract_bearer_token(authorization)
    if _matches(token, settings.admin_token):
        return "admin"
    if settings.push_enabled and _matches(token, settings.api_token):
        return "learner"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
