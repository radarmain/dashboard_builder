"""Cognito JWT verification and authorization dependencies."""

from functools import lru_cache
from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, Request
from jwt import PyJWKClient
from pydantic import BaseModel

from app import store
from app.config import get_settings

BUILDER_GROUPS = {"admin", "analyst"}


class User(BaseModel):
    sub: str
    email: str | None = None
    groups: list[str] = []

    @property
    def is_admin(self) -> bool:
        return "admin" in self.groups

    @property
    def is_builder(self) -> bool:
        return bool(BUILDER_GROUPS & set(self.groups))


@lru_cache
def _jwks() -> PyJWKClient:
    s = get_settings()
    return PyJWKClient(f"{s.issuer}/.well-known/jwks.json", cache_keys=True)


def verify_token(token: str) -> dict[str, Any]:
    """Verify signature + issuer; then check token_use and audience/client_id by hand
    (ID tokens carry `aud`, access tokens carry `client_id`)."""
    s = get_settings()
    key = _jwks().get_signing_key_from_jwt(token).key
    claims = jwt.decode(
        token,
        key,
        algorithms=["RS256"],
        issuer=s.issuer,
        options={"verify_aud": False},
    )
    use = claims.get("token_use")
    if use == "id":
        ok = claims.get("aud") == s.app_client_id
    elif use == "access":
        ok = claims.get("client_id") == s.app_client_id
    else:
        ok = False
    if not ok:
        raise jwt.InvalidTokenError("bad token_use/audience")
    return claims


def current_user(request: Request) -> User:
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        raise HTTPException(401, "missing bearer token")
    try:
        claims = verify_token(header[7:].strip())
    except Exception:  # noqa: BLE001 - any verification failure is a 401; don't leak detail
        raise HTTPException(401, "invalid token") from None
    return User(
        sub=claims["sub"],
        email=claims.get("email"),
        groups=claims.get("cognito:groups", []),
    )


CurrentUser = Annotated[User, Depends(current_user)]


def require_builder(user: CurrentUser) -> User:
    if not user.is_builder:
        raise HTTPException(403, "builder access required")
    return user


def require_admin(user: CurrentUser) -> User:
    if not user.is_admin:
        raise HTTPException(403, "admin required")
    return user


def require_client_access(client_slug: str, user: CurrentUser) -> User:
    """Dependency (client_slug comes from the path). admin/analyst always; client users only
    with an Access item. Unknown/unauthorized -> 404 so existence isn't revealed."""
    if user.is_builder:
        return user
    if "client" in user.groups and store.has_access(user.sub, client_slug):
        return user
    raise HTTPException(404, "not found")


Builder = Annotated[User, Depends(require_builder)]
Admin = Annotated[User, Depends(require_admin)]
ClientAccess = Annotated[User, Depends(require_client_access)]
