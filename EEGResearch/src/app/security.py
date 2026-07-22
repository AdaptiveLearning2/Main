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
