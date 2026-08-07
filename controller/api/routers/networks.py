"""Network management endpoints."""

from __future__ import annotations

import uuid

from common.models import NetworkInfo
from controller.api.deps import current_auth, db_session
from controller.db.models import APIKey, Network, Tenant
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/networks", tags=["networks"])


class NetworkCreate(BaseModel):
    name: str
    tenant_id: uuid.UUID | None = None  # required when caller is admin; ignored for tenant-scoped callers
    vlan_id: int
    cidr: str | None = None
    gateway: str | None = None


def _net_to_info(n: Network) -> NetworkInfo:
    return NetworkInfo(
        id=n.id,
        tenant_id=n.tenant_id,
        name=n.name,
        vlan_id=n.vlan_id,
        cidr=n.cidr,
        gateway=n.gateway,
        created_at=n.created_at,
    )


@router.get("", response_model=list[NetworkInfo])
async def list_networks(
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> list[NetworkInfo]:
    """List all networks owned by the authenticated tenant, or all networks if admin."""
    _, tenant = auth
    if tenant is None:
        result = await session.execute(select(Network))
    else:
        result = await session.execute(select(Network).where(Network.tenant_id == tenant.id))
    return [_net_to_info(n) for n in result.scalars().all()]


@router.post("", response_model=NetworkInfo, status_code=status.HTTP_201_CREATED)
async def create_network(
    body: NetworkCreate,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> NetworkInfo:
    """Create a network. VLAN tagging is applied per-VM by libvirt+OVS at NIC attach time."""
    _, tenant = auth
    if tenant is None:
        if body.tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="tenant_id is required when creating a network as admin",
            )
        t_result = await session.execute(select(Tenant).where(Tenant.id == body.tenant_id))
        tenant = t_result.scalar_one_or_none()
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    effective_tenant_id = tenant.id

    network = Network(
        tenant_id=effective_tenant_id,
        name=body.name,
        vlan_id=body.vlan_id,
        cidr=body.cidr,
        gateway=body.gateway,
    )
    session.add(network)
    await session.commit()
    await session.refresh(network)

    return _net_to_info(network)


@router.delete("/{network_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_network(
    network_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> None:
    """Delete a network."""
    _, tenant = auth
    result = await session.execute(select(Network).where(Network.id == network_id))
    network = result.scalar_one_or_none()
    if not network:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Network not found")
    if tenant is not None and network.tenant_id != tenant.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    await session.delete(network)
    await session.commit()
