"""Shared Pydantic v2 models for COS controller and agent."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class NodeStatus(str, Enum):
    online = "online"
    offline = "offline"
    maintenance = "maintenance"


class VMStatus(str, Enum):
    running = "running"
    stopped = "stopped"
    paused = "paused"
    migrating = "migrating"
    error = "error"


class NodeInfo(BaseModel):
    id: uuid.UUID
    hostname: str
    ip_address: str
    cpu_total: int
    cpu_used: float
    ram_total_mb: int
    ram_used_mb: int
    disk_total_gb: float
    disk_used_gb: float
    status: NodeStatus
    last_heartbeat: datetime


class VMInfo(BaseModel):
    id: uuid.UUID
    name: str
    tenant_id: uuid.UUID
    node_id: uuid.UUID
    cpu_cores: int
    ram_mb: int
    disk_gb: int
    status: VMStatus
    ip_addresses: list[str] = Field(default_factory=list)
    template_id: Optional[uuid.UUID] = None
    created_at: datetime


class TenantInfo(BaseModel):
    id: uuid.UUID
    name: str
    email: str
    active: bool
    created_at: datetime


class VMTemplate(BaseModel):
    id: uuid.UUID
    name: str
    description: str
    cpu_cores: int
    ram_mb: int
    disk_gb: int
    os_type: str
    image_path: str
    cloud_init_user: str = "ubuntu"
    created_at: datetime


class NetworkInfo(BaseModel):
    id: uuid.UUID
    tenant_id: uuid.UUID
    name: str
    vlan_id: int
    cidr: str | None = None
    gateway: str | None = None
    created_at: datetime


class HeartbeatPayload(BaseModel):
    node_id: uuid.UUID
    timestamp: datetime
    cpu_used: float
    ram_used_mb: int
    disk_used_gb: float
    vm_statuses: dict[str, VMStatus] = Field(default_factory=dict)


class DiskInfo(BaseModel):
    """Disk device attached to a VM as reported by the agent."""

    target: str
    size_gb: float
    path: str
    device: str  # "disk" or "cdrom"


class NICInfo(BaseModel):
    """Network interface attached to a VM.

    vlan_id is populated by the agent via NOS config lookup.
    network_id / network_name are optionally enriched by the controller.
    """

    target: str
    mac: str
    bridge: str
    vlan_id: Optional[int] = None
    network_id: Optional[uuid.UUID] = None
    network_name: Optional[str] = None


class VMHardwareConfig(BaseModel):
    """Current hardware configuration of a VM as reported by the agent."""

    vcpu: int
    memory_mb: int
    disks: list[DiskInfo]
    nics: list[NICInfo]


class AgentCommand(BaseModel):
    """Command sent from controller to agent over WebSocket.

    vm_create payload keys: name, cpu_cores, ram_mb, disk_gb, image_path,
    vlan_id (optional int — omit to let nos-libvirt-hook default to VLAN 115).
    """

    command: str
    payload: dict = Field(default_factory=dict)


class AgentCommandResult(BaseModel):
    success: bool
    output: str
    error: Optional[str] = None
