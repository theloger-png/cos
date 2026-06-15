"""VM lifecycle management endpoints."""

from __future__ import annotations

import logging
import uuid

from common.models import VMInfo, VMStatus
from controller.agent_client.client import AgentClient
from controller.api.deps import current_auth, db_session
from controller.db.models import APIKey, Node, Tenant, VM, VMTemplate
from controller.scheduler.scheduler import Scheduler
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/vms", tags=["vms"])


class VMCreate(BaseModel):
    name: str
    tenant_id: uuid.UUID | None = None  # required when caller is admin; ignored for tenant-scoped callers
    template_id: uuid.UUID | None = None
    node_id: uuid.UUID | None = None
    cpu_cores: int
    ram_mb: int
    disk_gb: int


class MigrateRequest(BaseModel):
    target_node_id: uuid.UUID


def _vm_to_info(v: VM) -> VMInfo:
    return VMInfo(
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


async def _get_vm_or_404(session: AsyncSession, vm_id: uuid.UUID) -> VM:
    result = await session.execute(select(VM).where(VM.id == vm_id))
    vm = result.scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM not found")
    return vm


async def _get_node_or_404(session: AsyncSession, node_id: uuid.UUID) -> Node:
    result = await session.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return node


@router.get("", response_model=list[VMInfo])
async def list_vms(
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> list[VMInfo]:
    """List all VMs belonging to the authenticated tenant, or all VMs if admin."""
    _, tenant = auth
    if tenant is None:
        result = await session.execute(select(VM))
    else:
        result = await session.execute(select(VM).where(VM.tenant_id == tenant.id))
    return [_vm_to_info(v) for v in result.scalars().all()]


@router.get("/{vm_id}", response_model=VMInfo)
async def get_vm(
    vm_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> VMInfo:
    """Return details for a specific VM."""
    _, tenant = auth
    vm = await _get_vm_or_404(session, vm_id)
    if tenant is not None and vm.tenant_id != tenant.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return _vm_to_info(vm)


@router.post("", response_model=VMInfo, status_code=status.HTTP_201_CREATED)
async def create_vm(
    body: VMCreate,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> VMInfo:
    """Create and provision a new VM on a selected or auto-scheduled node."""
    _, tenant = auth
    if tenant is None:
        # Admin caller: tenant_id must be provided in the request body
        if body.tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="tenant_id is required when creating a VM as admin",
            )
        t_result = await session.execute(select(Tenant).where(Tenant.id == body.tenant_id))
        tenant = t_result.scalar_one_or_none()
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    effective_tenant_id = tenant.id

    image_path = ""
    if body.template_id:
        tpl_result = await session.execute(select(VMTemplate).where(VMTemplate.id == body.template_id))
        tpl = tpl_result.scalar_one_or_none()
        if not tpl:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
        image_path = tpl.image_path

    if body.node_id:
        node = await _get_node_or_404(session, body.node_id)
    else:
        nodes_result = await session.execute(select(Node))
        all_nodes = nodes_result.scalars().all()
        scheduler = Scheduler(all_nodes)
        selected = scheduler.select_node(body.cpu_cores, body.ram_mb, body.disk_gb)
        if not selected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No node has sufficient resources",
            )
        node_result = await session.execute(select(Node).where(Node.ip_address == selected.ip_address))
        node = node_result.scalar_one()

    vm = VM(
        name=body.name,
        tenant_id=effective_tenant_id,
        node_id=node.id,
        cpu_cores=body.cpu_cores,
        ram_mb=body.ram_mb,
        disk_gb=body.disk_gb,
        status=VMStatus.stopped.value,
        template_id=body.template_id,
    )
    session.add(vm)
    await session.flush()

    agent = AgentClient()
    result = await agent.send_command(
        node.ip_address,
        "vm_create",
        {
            "name": vm.name,
            "cpu_cores": vm.cpu_cores,
            "ram_mb": vm.ram_mb,
            "disk_gb": vm.disk_gb,
            "image_path": image_path,
        },
    )
    if result.success:
        vm.libvirt_uuid = result.output.strip()
        vm.status = VMStatus.running.value
    else:
        logger.warning("vm_create agent command failed: %s", result.error)
        vm.status = VMStatus.error.value

    await session.commit()
    await session.refresh(vm)
    return _vm_to_info(vm)


@router.delete("/{vm_id}", status_code=status.HTTP_204_NO_CONTENT)
async def destroy_vm(
    vm_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> None:
    """Destroy a VM and release its resources."""
    _, tenant = auth
    vm = await _get_vm_or_404(session, vm_id)
    if tenant is not None and vm.tenant_id != tenant.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    node = await _get_node_or_404(session, vm.node_id)
    if vm.libvirt_uuid:
        agent = AgentClient()
        await agent.send_command(node.ip_address, "vm_destroy", {"libvirt_uuid": vm.libvirt_uuid})

    await session.delete(vm)
    await session.commit()


async def _vm_action(
    vm_id: uuid.UUID,
    command: str,
    session: AsyncSession,
    tenant: Tenant | None,
) -> dict:
    vm = await _get_vm_or_404(session, vm_id)
    if tenant is not None and vm.tenant_id != tenant.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if not vm.libvirt_uuid:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="VM has no libvirt UUID")

    node = await _get_node_or_404(session, vm.node_id)
    agent = AgentClient()
    result = await agent.send_command(node.ip_address, command, {"libvirt_uuid": vm.libvirt_uuid})
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Agent command failed: {result.error}",
        )
    return {"ok": True}


@router.post("/{vm_id}/start")
async def start_vm(
    vm_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> dict:
    """Start a stopped VM."""
    _, tenant = auth
    return await _vm_action(vm_id, "vm_start", session, tenant)


@router.post("/{vm_id}/stop")
async def stop_vm(
    vm_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> dict:
    """Gracefully shut down a running VM."""
    _, tenant = auth
    return await _vm_action(vm_id, "vm_stop", session, tenant)


@router.post("/{vm_id}/reboot")
async def reboot_vm(
    vm_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> dict:
    """Reboot a running VM."""
    _, tenant = auth
    return await _vm_action(vm_id, "vm_reboot", session, tenant)


@router.post("/{vm_id}/migrate")
async def migrate_vm(
    vm_id: uuid.UUID,
    body: MigrateRequest,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> dict:
    """Live-migrate a VM to a different node."""
    _, tenant = auth
    vm = await _get_vm_or_404(session, vm_id)
    if tenant is not None and vm.tenant_id != tenant.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if not vm.libvirt_uuid:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="VM has no libvirt UUID")

    src_node = await _get_node_or_404(session, vm.node_id)
    dst_node = await _get_node_or_404(session, body.target_node_id)

    target_uri = f"qemu+ssh://{dst_node.ip_address}/system"
    agent = AgentClient()
    result = await agent.send_command(
        src_node.ip_address,
        "vm_migrate",
        {"libvirt_uuid": vm.libvirt_uuid, "target_uri": target_uri},
    )
    if not result.success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Migration failed: {result.error}",
        )

    vm.node_id = dst_node.id
    vm.status = VMStatus.running.value
    await session.commit()
    return {"ok": True}
