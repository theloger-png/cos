"""Node management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from common.models import HeartbeatPayload, NodeInfo, NodeStatus, VMInfo, VMStatus
from controller.api.deps import current_auth, db_session
from controller.db.models import APIKey, Node, Tenant, VM
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/nodes", tags=["nodes"])


class NodeCreate(BaseModel):
    hostname: str
    ip_address: str
    cpu_total: int
    ram_total_mb: int
    disk_total_gb: float
    nos_api_key: str = ""




def _node_to_info(n: Node) -> NodeInfo:
    return NodeInfo(
        id=n.id,
        hostname=n.hostname,
        ip_address=n.ip_address,
        cpu_total=n.cpu_total,
        cpu_used=n.cpu_used,
        ram_total_mb=n.ram_total_mb,
        ram_used_mb=n.ram_used_mb,
        disk_total_gb=n.disk_total_gb,
        disk_used_gb=n.disk_used_gb,
        status=NodeStatus(n.status),
        last_heartbeat=n.last_heartbeat or datetime.now(timezone.utc),
    )


@router.get("", response_model=list[NodeInfo])
async def list_nodes(
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> list[NodeInfo]:
    """List all registered nodes with their current status."""
    result = await session.execute(select(Node))
    return [_node_to_info(n) for n in result.scalars().all()]


@router.get("/{node_id}", response_model=NodeInfo)
async def get_node(
    node_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> NodeInfo:
    """Return details and current resource usage for a single node."""
    result = await session.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return _node_to_info(node)


@router.post("", response_model=NodeInfo)
async def register_node(
    body: NodeCreate,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> NodeInfo:
    """Register or update a physical node, keyed by ip_address."""
    from fastapi.responses import JSONResponse

    result = await session.execute(select(Node).where(Node.ip_address == body.ip_address))
    existing = result.scalar_one_or_none()

    if existing:
        existing.hostname = body.hostname
        existing.cpu_total = body.cpu_total
        existing.ram_total_mb = body.ram_total_mb
        existing.disk_total_gb = body.disk_total_gb
        existing.nos_api_key = body.nos_api_key
        existing.status = NodeStatus.offline.value
        existing.last_heartbeat = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(existing)
        return JSONResponse(
            content=_node_to_info(existing).model_dump(mode="json"),
            status_code=status.HTTP_200_OK,
        )

    node = Node(
        hostname=body.hostname,
        ip_address=body.ip_address,
        cpu_total=body.cpu_total,
        ram_total_mb=body.ram_total_mb,
        disk_total_gb=body.disk_total_gb,
        nos_api_key=body.nos_api_key,
        status=NodeStatus.offline.value,
    )
    session.add(node)
    await session.commit()
    await session.refresh(node)
    return JSONResponse(
        content=_node_to_info(node).model_dump(mode="json"),
        status_code=status.HTTP_201_CREATED,
    )


@router.delete("/{node_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_node(
    node_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> None:
    """Remove a node from the controller."""
    result = await session.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    await session.delete(node)
    await session.commit()


@router.get("/{node_id}/vms", response_model=list[VMInfo])
async def list_node_vms(
    node_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> list[VMInfo]:
    """List all VMs currently assigned to the given node."""
    result = await session.execute(select(Node).where(Node.id == node_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    vms_result = await session.execute(select(VM).where(VM.node_id == node_id))
    vms = vms_result.scalars().all()
    return [
        VMInfo(
            id=v.id,
            name=v.name,
            tenant_id=v.tenant_id,
            node_id=v.node_id,
            cpu_cores=v.cpu_cores,
            ram_mb=v.ram_mb,
            disk_gb=v.disk_gb,
            status=VMStatus(v.status),
            created_at=v.created_at,
            template_id=v.template_id,
        )
        for v in vms
    ]


@router.post("/{node_id}/heartbeat", response_model=NodeInfo, status_code=status.HTTP_200_OK)
async def receive_heartbeat(
    node_id: uuid.UUID,
    body: HeartbeatPayload,
    session: AsyncSession = Depends(db_session),
) -> NodeInfo:
    """Accept a heartbeat from an agent and update node resource usage."""
    result = await session.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    node.cpu_used = body.cpu_used
    node.ram_used_mb = body.ram_used_mb
    node.disk_used_gb = body.disk_used_gb
    node.status = NodeStatus.online.value
    node.last_heartbeat = datetime.now(timezone.utc)

    for libvirt_uuid, vm_status in body.vm_statuses.items():
        vm_result = await session.execute(select(VM).where(VM.libvirt_uuid == libvirt_uuid))
        vm = vm_result.scalar_one_or_none()
        if vm:
            vm.status = vm_status.value

    await session.commit()
    await session.refresh(node)
    return _node_to_info(node)
