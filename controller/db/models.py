"""SQLAlchemy ORM models (SQLAlchemy 2.0 mapped_column style)."""

from __future__ import annotations

import uuid
from datetime import datetime

from controller.db.base import Base
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hostname: Mapped[str] = mapped_column(String(255), nullable=False)
    ip_address: Mapped[str] = mapped_column(String(45), nullable=False, unique=True)
    cpu_total: Mapped[int] = mapped_column(Integer, nullable=False)
    cpu_used: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    ram_total_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    ram_used_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    disk_total_gb: Mapped[float] = mapped_column(Float, nullable=False)
    disk_used_gb: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="offline")
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    nos_api_key: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    vms: Mapped[list[VM]] = relationship("VM", back_populates="node", lazy="select")


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    hashed_password: Mapped[str] = mapped_column(String(256), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    vms: Mapped[list[VM]] = relationship("VM", back_populates="tenant", lazy="select")
    networks: Mapped[list[Network]] = relationship("Network", back_populates="tenant", lazy="select")
    api_keys: Mapped[list[APIKey]] = relationship("APIKey", back_populates="tenant", lazy="select")


class VMTemplate(Base):
    __tablename__ = "vm_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    os_type: Mapped[str] = mapped_column(String(64), nullable=False)
    image_path: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class VM(Base):
    __tablename__ = "vms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    node_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("nodes.id"), nullable=False)
    cpu_cores: Mapped[int] = mapped_column(Integer, nullable=False)
    ram_mb: Mapped[int] = mapped_column(Integer, nullable=False)
    disk_gb: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="stopped")
    libvirt_uuid: Mapped[str | None] = mapped_column(String(36), nullable=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("vm_templates.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="vms")
    node: Mapped[Node] = relationship("Node", back_populates="vms")
    template: Mapped[VMTemplate | None] = relationship("VMTemplate")


class Network(Base):
    __tablename__ = "networks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    vlan_id: Mapped[int] = mapped_column(Integer, nullable=False)
    cidr: Mapped[str] = mapped_column(String(18), nullable=False)
    gateway: Mapped[str] = mapped_column(String(45), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="networks")


class APIKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=True)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[Tenant | None] = relationship("Tenant", back_populates="api_keys")
