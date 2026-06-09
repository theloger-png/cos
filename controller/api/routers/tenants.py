"""Tenant and API key management endpoints (admin-only)."""

from __future__ import annotations

import uuid

from common.models import TenantInfo
from common.utils import generate_api_key, hash_api_key
from controller.api.auth import require_admin
from controller.api.deps import current_auth, db_session
from controller.db.models import APIKey, Tenant
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/tenants", tags=["tenants"])


class TenantCreate(BaseModel):
    name: str
    email: str


class APIKeyCreate(BaseModel):
    description: str = ""


class APIKeyResponse(BaseModel):
    id: uuid.UUID
    key: str
    description: str


def _tenant_to_info(t: Tenant) -> TenantInfo:
    return TenantInfo(
        id=t.id,
        name=t.name,
        email=t.email,
        active=t.active,
        created_at=t.created_at,
    )


@router.get("", response_model=list[TenantInfo])
async def list_tenants(
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> list[TenantInfo]:
    """List all tenants. Admin access required."""
    require_admin(auth)
    result = await session.execute(select(Tenant))
    return [_tenant_to_info(t) for t in result.scalars().all()]


@router.post("", response_model=TenantInfo, status_code=status.HTTP_201_CREATED)
async def create_tenant(
    body: TenantCreate,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> TenantInfo:
    """Create a new tenant. Admin access required."""
    require_admin(auth)
    tenant = Tenant(name=body.name, email=body.email)
    session.add(tenant)
    await session.commit()
    await session.refresh(tenant)
    return _tenant_to_info(tenant)


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> None:
    """Delete a tenant and all associated resources. Admin access required."""
    require_admin(auth)
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    await session.delete(tenant)
    await session.commit()


@router.post("/{tenant_id}/apikeys", response_model=APIKeyResponse, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    tenant_id: uuid.UUID,
    body: APIKeyCreate,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> APIKeyResponse:
    """Generate a new API key for the given tenant. Admin access required."""
    require_admin(auth)
    result = await session.execute(select(Tenant).where(Tenant.id == tenant_id))
    tenant = result.scalar_one_or_none()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    raw_key = generate_api_key()
    api_key = APIKey(
        tenant_id=tenant_id,
        key_hash=hash_api_key(raw_key),
        description=body.description,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)
    return APIKeyResponse(id=api_key.id, key=raw_key, description=api_key.description)
