from fastapi import Header, HTTPException, status

from src.app.config import get_settings


def _extract_bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def require_learner_token(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    if _extract_bearer_token(authorization) != settings.api_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return "learner"


def require_admin_token(authorization: str | None = Header(default=None)) -> str:
    settings = get_settings()
    if _extract_bearer_token(authorization) != settings.admin_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    return "admin"
