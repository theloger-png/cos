"""FastAPI dependency helpers."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from controller.db.models import APIKey, Tenant
from controller.db.session import get_session
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from .auth import get_api_key


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
    from fastapi import HTTPException, status

    _, tenant = auth
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant context required",
        )
    return tenant
