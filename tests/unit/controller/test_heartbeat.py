"""Unit tests for POST /api/v1/nodes/{node_id}/heartbeat."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from common.models import HeartbeatPayload, VMStatus
from controller.api.routers.nodes import receive_heartbeat
from controller.db.models import Node, VM


def _make_node(node_id: uuid.UUID) -> Node:
    n = Node()
    n.id = node_id
    n.hostname = "node1"
    n.ip_address = "10.0.0.1"
    n.cpu_total = 16
    n.ram_total_mb = 32768
    n.disk_total_gb = 500
    n.cpu_used = 0.0
    n.ram_used_mb = 0
    n.disk_used_gb = 0.0
    n.status = "online"
    return n


def _make_vm(node_id: uuid.UUID, libvirt_uuid: str, status: str = "running") -> VM:
    v = VM()
    v.id = uuid.uuid4()
    v.name = "test-vm"
    v.tenant_id = uuid.uuid4()
    v.node_id = node_id
    v.cpu_cores = 2
    v.ram_mb = 2048
    v.disk_gb = 20
    v.status = status
    v.libvirt_uuid = libvirt_uuid
    v.created_at = datetime.now(timezone.utc)
    return v


def _payload(node_id: uuid.UUID, vm_statuses: dict[str, VMStatus]) -> HeartbeatPayload:
    return HeartbeatPayload(
        node_id=node_id,
        timestamp=datetime.now(timezone.utc),
        cpu_used=1.0,
        ram_used_mb=100,
        disk_used_gb=1.0,
        vm_statuses=vm_statuses,
    )


def _build_session(node: Node, vm_lookup_results: list[VM | None]) -> AsyncMock:
    node_result = MagicMock()
    node_result.scalar_one_or_none.return_value = node

    vm_results = []
    for vm in vm_lookup_results:
        r = MagicMock()
        r.scalar_one_or_none.return_value = vm
        vm_results.append(r)

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[node_result, *vm_results])
    session.refresh = AsyncMock()
    return session


class TestReceiveHeartbeat:
    @pytest.mark.asyncio
    async def test_updates_status_for_vm_on_this_node(self):
        node_id = uuid.uuid4()
        node = _make_node(node_id)
        libvirt_uuid = str(uuid.uuid4())
        vm = _make_vm(node_id, libvirt_uuid, status="stopped")
        session = _build_session(node, [vm])

        await receive_heartbeat(
            node_id,
            _payload(node_id, {libvirt_uuid: VMStatus.running}),
            session=session,
        )

        assert vm.status == VMStatus.running.value

    @pytest.mark.asyncio
    async def test_ignores_status_for_vm_migrated_to_another_node(self):
        """A stale heartbeat from a VM's old node must not clobber the
        correct status set by the migrate endpoint on its new node - this
        is exactly the bug seen in production: node2's heartbeat kept
        reporting a migrated-away VM as stopped (leftover defined-but-off
        domain) after the controller had already marked it running on
        node1.
        """
        old_node_id = uuid.uuid4()
        new_node_id = uuid.uuid4()
        old_node = _make_node(old_node_id)
        libvirt_uuid = str(uuid.uuid4())
        # VM now lives on new_node_id, but old_node still reports it.
        vm = _make_vm(new_node_id, libvirt_uuid, status="running")
        session = _build_session(old_node, [vm])

        await receive_heartbeat(
            old_node_id,
            _payload(old_node_id, {libvirt_uuid: VMStatus.stopped}),
            session=session,
        )

        assert vm.status == "running"

    @pytest.mark.asyncio
    async def test_unknown_libvirt_uuid_is_ignored(self):
        node_id = uuid.uuid4()
        node = _make_node(node_id)
        session = _build_session(node, [None])

        await receive_heartbeat(
            node_id,
            _payload(node_id, {"unknown-uuid": VMStatus.running}),
            session=session,
        )

        session.commit.assert_awaited_once()


class TestHeartbeatOperationEvents:
    """State changes reported by a heartbeat are logged as 'system' operations."""

    @pytest.mark.asyncio
    async def test_vm_status_change_and_node_coming_online_are_logged(self):
        from unittest.mock import patch

        node_id = uuid.uuid4()
        node = _make_node(node_id)
        node.status = "offline"
        libvirt_uuid = str(uuid.uuid4())
        vm = _make_vm(node_id, libvirt_uuid, status="running")
        session = _build_session(node, [vm])

        with patch("controller.api.routers.nodes.operations.record_system_event", new=AsyncMock()) as rec:
            await receive_heartbeat(
                node_id, _payload(node_id, {libvirt_uuid: VMStatus.stopped}), session=session
            )

        actions = [c.args[0] for c in rec.await_args_list]
        assert actions == ["node.online", "vm.status"]
        assert "running -> stopped" in rec.await_args_list[1].args[1]
        assert rec.await_args_list[1].kwargs["tenant_id"] == vm.tenant_id

    @pytest.mark.asyncio
    async def test_steady_state_heartbeat_logs_nothing(self):
        from unittest.mock import patch

        node_id = uuid.uuid4()
        node = _make_node(node_id)  # already online
        libvirt_uuid = str(uuid.uuid4())
        vm = _make_vm(node_id, libvirt_uuid, status="running")
        session = _build_session(node, [vm])

        with patch("controller.api.routers.nodes.operations.record_system_event", new=AsyncMock()) as rec:
            await receive_heartbeat(
                node_id, _payload(node_id, {libvirt_uuid: VMStatus.running}), session=session
            )

        rec.assert_not_awaited()
