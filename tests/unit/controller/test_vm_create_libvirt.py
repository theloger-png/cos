"""Unit tests: vm_create persists libvirt_uuid and handles agent failures."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controller.db.models import Node, Tenant, VM


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_tenant() -> Tenant:
    t = Tenant()
    t.id = uuid.uuid4()
    t.name = "acme"
    t.created_at = datetime.now(timezone.utc)
    return t


def _make_node() -> Node:
    n = Node()
    n.id = uuid.uuid4()
    n.hostname = "node1"
    n.ip_address = "10.0.0.1"
    n.cpu_total = 16
    n.ram_total_mb = 32768
    n.disk_total_gb = 500
    return n


def _build_session(tenant: Tenant, node: Node) -> AsyncMock:
    """Session mock wired up for a typical create_vm flow (auto-schedule path)."""
    tenant_result = MagicMock()
    tenant_result.scalar_one_or_none.return_value = tenant

    nodes_result = MagicMock()
    nodes_result.scalars.return_value.all.return_value = [node]

    node_result = MagicMock()
    node_result.scalar_one.return_value = node

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[tenant_result, nodes_result, node_result])
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    async def _fake_refresh(obj):
        obj.id = uuid.uuid4()
        obj.created_at = datetime.now(timezone.utc)

    session.refresh = _fake_refresh
    return session


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestVMCreateLibvirtUUID:

    @pytest.mark.asyncio
    async def test_successful_vm_create_persists_libvirt_uuid_and_running_status(self):
        """When the agent reports success, libvirt_uuid and status=running are saved."""
        from controller.api.routers.vms import create_vm, VMCreate

        tenant = _make_tenant()
        node = _make_node()
        session = _build_session(tenant, node)

        expected_uuid = str(uuid.uuid4())
        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = expected_uuid

        captured_vm: list[VM] = []

        real_add = session.add.side_effect

        def _capture_add(obj):
            if isinstance(obj, VM):
                captured_vm.append(obj)

        session.add.side_effect = _capture_add

        body = VMCreate(
            name="web-01",
            tenant_id=tenant.id,
            cpu_cores=2,
            ram_mb=2048,
            disk_gb=20,
        )

        with patch("controller.api.routers.vms.AgentClient") as MockAgent, \
             patch("controller.api.routers.vms.Scheduler") as MockScheduler:
            mock_instance = MagicMock()
            mock_instance.send_command = AsyncMock(return_value=agent_result)
            MockAgent.return_value = mock_instance

            mock_sched = MagicMock()
            mock_sched.select_node.return_value = node
            MockScheduler.return_value = mock_sched

            await create_vm(body=body, session=session, auth=(None, None))

        assert len(captured_vm) == 1
        vm = captured_vm[0]
        assert vm.libvirt_uuid == expected_uuid
        assert vm.status == "running"

    @pytest.mark.asyncio
    async def test_failed_vm_create_marks_vm_as_error(self):
        """When the agent reports failure, the VM row is saved with status=error."""
        from controller.api.routers.vms import create_vm, VMCreate

        tenant = _make_tenant()
        node = _make_node()
        session = _build_session(tenant, node)

        agent_result = MagicMock()
        agent_result.success = False
        agent_result.output = ""
        agent_result.error = "libvirt: connection refused"

        captured_vm: list[VM] = []

        def _capture_add(obj):
            if isinstance(obj, VM):
                captured_vm.append(obj)

        session.add.side_effect = _capture_add

        body = VMCreate(
            name="web-02",
            tenant_id=tenant.id,
            cpu_cores=2,
            ram_mb=2048,
            disk_gb=20,
        )

        with patch("controller.api.routers.vms.AgentClient") as MockAgent, \
             patch("controller.api.routers.vms.Scheduler") as MockScheduler:
            mock_instance = MagicMock()
            mock_instance.send_command = AsyncMock(return_value=agent_result)
            MockAgent.return_value = mock_instance

            mock_sched = MagicMock()
            mock_sched.select_node.return_value = node
            MockScheduler.return_value = mock_sched

            await create_vm(body=body, session=session, auth=(None, None))

        assert len(captured_vm) == 1
        vm = captured_vm[0]
        assert vm.libvirt_uuid is None
        assert vm.status == "error"

    @pytest.mark.asyncio
    async def test_libvirt_uuid_stripped_of_whitespace(self):
        """Extra whitespace in agent output is stripped before saving."""
        from controller.api.routers.vms import create_vm, VMCreate

        tenant = _make_tenant()
        node = _make_node()
        session = _build_session(tenant, node)

        raw_uuid = str(uuid.uuid4())
        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = f"  {raw_uuid}\n"

        captured_vm: list[VM] = []

        def _capture_add(obj):
            if isinstance(obj, VM):
                captured_vm.append(obj)

        session.add.side_effect = _capture_add

        body = VMCreate(
            name="web-03",
            tenant_id=tenant.id,
            cpu_cores=4,
            ram_mb=4096,
            disk_gb=40,
        )

        with patch("controller.api.routers.vms.AgentClient") as MockAgent, \
             patch("controller.api.routers.vms.Scheduler") as MockScheduler:
            mock_instance = MagicMock()
            mock_instance.send_command = AsyncMock(return_value=agent_result)
            MockAgent.return_value = mock_instance

            mock_sched = MagicMock()
            mock_sched.select_node.return_value = node
            MockScheduler.return_value = mock_sched

            await create_vm(body=body, session=session, auth=(None, None))

        assert captured_vm[0].libvirt_uuid == raw_uuid
