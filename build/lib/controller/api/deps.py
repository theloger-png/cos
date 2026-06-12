"""FastAPI dependency helpers."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import datetime, timezone

from common.utils import hash_api_key
from controller.db.models import APIKey, Tenant, User
from controller.db.session import get_session
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

_bearer = HTTPBearer(auto_error=False)
_api_key_header_opt = APIKeyHeader(name="X-API-Key", auto_error=False)


async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session."""
    async for session in get_session():
        yield session


async def current_auth(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    raw_key: str | None = Security(_api_key_header_opt),
    session: AsyncSession = Depends(db_session),
) -> tuple[APIKey | None, Tenant | None]:
    """Authenticate via JWT Bearer token or X-API-Key header.

    JWT path: validates the bearer token, returns (None, None) — full admin access.
    API-key path: validates X-API-Key, returns (APIKey, Tenant|None).
    Raises HTTP 401 if neither credential is present or valid.
    """
    if credentials is not None:
        from jose import JWTError

        from .auth_users import decode_access_token

        try:
            payload = decode_access_token(credentials.credentials)
            user_id_str: str = payload["sub"]
        except (JWTError, KeyError):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

        result = await session.execute(select(User).where(User.id == uuid.UUID(user_id_str)))
        user: User | None = result.scalar_one_or_none()
        if user is None or not user.active:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
        return None, None

    if raw_key is not None:
        key_hash = hash_api_key(raw_key)
        result = await session.execute(select(APIKey).where(APIKey.key_hash == key_hash))
        api_key_obj: APIKey | None = result.scalar_one_or_none()
        if not api_key_obj:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
        api_key_obj.last_used = datetime.now(timezone.utc)
        await session.commit()
        tenant: Tenant | None = None
        if api_key_obj.tenant_id:
            t_result = await session.execute(select(Tenant).where(Tenant.id == api_key_obj.tenant_id))
            tenant = t_result.scalar_one_or_none()
        return api_key_obj, tenant

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")


async def current_tenant(
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> Tenant:
    """Return the authenticated tenant, raising 403 if called by admin."""
    _, tenant = auth
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant context required",
        )
    return tenant


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(db_session),
) -> User:
    """Validate JWT Bearer token and return the corresponding User."""
    from jose import JWTError

    from .auth_users import decode_access_token

    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_access_token(credentials.credentials)
        user_id: str = payload["sub"]
    except (JWTError, KeyError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    result = await session.execute(select(User).where(User.id == uuid.UUID(user_id)))
    user: User | None = result.scalar_one_or_none()

    if user is None or not user.active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    return user
