"""FastAPI dependency helpers."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from controller.db.models import APIKey, Tenant, User
from controller.db.session import get_session
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_api_key

_bearer = HTTPBearer(auto_error=False)


async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session."""
    async for session in get_session():
        yield session


async def current_auth(
    auth: tuple[APIKey, Tenant | None] = Depends(get_api_key),
) -> tuple[APIKey, Tenant | None]:
    """Return the authenticated (APIKey, Tenant|None) pair."""
    return auth


async def current_tenant(
    auth: tuple[APIKey, Tenant | None] = Depends(get_api_key),
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
