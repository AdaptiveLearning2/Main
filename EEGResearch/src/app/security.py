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
    # compare_digest raises on non-ASCII str, so compare bytes; surrogateescape
    # tolerates lone surrogates from Starlette's latin-1 header decode.
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

    Under push the remote backend refuses lifecycle calls, so the page (learner token)
    is the only pairing path. That token ships in the bundle and only separates pages,
    not users; a page able to call /push/start already can stream, so pairing adds little.
    """
    settings = get_settings()
    token = _extract_bearer_token(authorization)
    if _matches(token, settings.admin_token):
        return "admin"
    if settings.push_enabled and _matches(token, settings.api_token):
        return "learner"
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
