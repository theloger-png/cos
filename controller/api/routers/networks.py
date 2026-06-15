"""Network management endpoints — integrates with NOS via agent broadcast."""

from __future__ import annotations

import asyncio
import logging
import uuid

from common.models import NetworkInfo
from controller.agent_client.client import AgentClient
from controller.api.deps import current_auth, db_session
from controller.db.models import APIKey, Network, Node, Tenant
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/networks", tags=["networks"])

_agent_client = AgentClient()


class NetworkCreate(BaseModel):
    name: str
    tenant_id: uuid.UUID | None = None  # required when caller is admin; ignored for tenant-scoped callers
    vlan_id: int
    cidr: str
    gateway: str


class NetworkCreateResponse(NetworkInfo):
    warnings: list[str] = []


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


async def _broadcast_vlan_command(
    command: str,
    vlan_id: int,
    nodes: list[Node],
) -> list[str]:
    """Send *command* with vlan_id to all *nodes* in parallel. Returns a list of warning strings."""
    if not nodes:
        return []

    async def _send(node: Node) -> str | None:
        result = await _agent_client.send_command(
            node_ip=node.ip_address,
            command=command,
            payload={"vlan_id": vlan_id},
        )
        if not result.success:
            msg = f"Node {node.hostname} ({node.ip_address}): {result.error or 'unknown error'}"
            logger.warning("%s vlan_id=%d failed — %s", command, vlan_id, msg)
            return msg
        return None

    results = await asyncio.gather(*(_send(n) for n in nodes))
    return [w for w in results if w is not None]


async def _online_nodes(session: AsyncSession) -> list[Node]:
    result = await session.execute(select(Node).where(Node.status == "online"))
    return list(result.scalars().all())


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


@router.post("", response_model=NetworkCreateResponse, status_code=status.HTTP_201_CREATED)
async def create_network(
    body: NetworkCreate,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> NetworkCreateResponse:
    """Create a network and configure the corresponding VLAN on all online nodes."""
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

    nodes = await _online_nodes(session)
    warnings = await _broadcast_vlan_command("configure_vlan", body.vlan_id, nodes)

    info = _net_to_info(network)
    return NetworkCreateResponse(**info.model_dump(), warnings=warnings)


@router.delete("/{network_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_network(
    network_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> None:
    """Delete a network and remove the corresponding VLAN from all online nodes (best-effort)."""
    _, tenant = auth
    result = await session.execute(select(Network).where(Network.id == network_id))
    network = result.scalar_one_or_none()
    if not network:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Network not found")
    if tenant is not None and network.tenant_id != tenant.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    vlan_id = network.vlan_id
    await session.delete(network)
    await session.commit()

    nodes = await _online_nodes(session)
    await _broadcast_vlan_command("remove_vlan", vlan_id, nodes)
