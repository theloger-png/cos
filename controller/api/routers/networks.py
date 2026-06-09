"""Network management endpoints — integrates with NOS via REST API."""

from __future__ import annotations

import logging
import uuid

from common.models import NetworkInfo
from controller.api.deps import current_tenant, db_session
from controller.db.models import Network, Tenant
from controller.nos_client.client import NOSClient
from controller.config import settings
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/networks", tags=["networks"])


class NetworkCreate(BaseModel):
    name: str
    vlan_id: int
    cidr: str
    gateway: str


def _net_to_info(n: Network) -> NetworkInfo:
    return NetworkInfo(
        id=n.id,
        tenant_id=n.tenant_id,
        name=n.name,
        vlan_id=n.vlan_id,
        cidr=n.cidr,
        gateway=n.gateway,
    )


@router.get("", response_model=list[NetworkInfo])
async def list_networks(
    session: AsyncSession = Depends(db_session),
    tenant: Tenant = Depends(current_tenant),
) -> list[NetworkInfo]:
    """List all networks owned by the authenticated tenant."""
    result = await session.execute(select(Network).where(Network.tenant_id == tenant.id))
    return [_net_to_info(n) for n in result.scalars().all()]


@router.post("", response_model=NetworkInfo, status_code=status.HTTP_201_CREATED)
async def create_network(
    body: NetworkCreate,
    session: AsyncSession = Depends(db_session),
    tenant: Tenant = Depends(current_tenant),
) -> NetworkInfo:
    """Create a network and configure the corresponding VLAN on NOS."""
    nos = NOSClient(base_url=settings.nos_api_url, api_key=settings.nos_api_key)
    ok = await nos.configure_vlan(body.vlan_id, body.name)
    if not ok:
        logger.warning("NOS VLAN configuration failed for vlan_id=%d", body.vlan_id)

    committed = await nos.commit()
    if not committed:
        logger.warning("NOS commit failed after VLAN %d creation", body.vlan_id)

    network = Network(
        tenant_id=tenant.id,
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
    tenant: Tenant = Depends(current_tenant),
) -> None:
    """Delete a network and remove the corresponding VLAN from NOS."""
    result = await session.execute(select(Network).where(Network.id == network_id))
    network = result.scalar_one_or_none()
    if not network:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Network not found")
    if network.tenant_id != tenant.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    nos = NOSClient(base_url=settings.nos_api_url, api_key=settings.nos_api_key)
    await nos.delete_vlan(network.vlan_id)
    await nos.commit()

    await session.delete(network)
    await session.commit()
