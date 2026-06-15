"""Unit tests: vm_create with network_id resolves vlan_id and enforces ownership."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controller.db.models import Network, Node, Tenant, VM


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


def _make_network(tenant_id: uuid.UUID, vlan_id: int = 200) -> Network:
    net = Network()
    net.id = uuid.uuid4()
    net.tenant_id = tenant_id
    net.name = "net-1"
    net.vlan_id = vlan_id
    net.cidr = "10.10.1.0/24"
    net.gateway = "10.10.1.1"
    net.created_at = datetime.now(timezone.utc)
    return net


def _make_session(*execute_returns) -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=list(execute_returns))
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()

    async def _fake_refresh(obj):
        obj.id = uuid.uuid4()
        obj.created_at = datetime.now(timezone.utc)

    session.refresh = _fake_refresh
    return session


def _scalar_one_or_none(obj):
    r = MagicMock()
    r.scalar_one_or_none.return_value = obj
    return r


def _scalars_all(items):
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    return r


def _scalar_one(obj):
    r = MagicMock()
    r.scalar_one.return_value = obj
    return r


def _agent_mock(success: bool = True, output: str = "") -> MagicMock:
    result = MagicMock()
    result.success = success
    result.output = output or str(uuid.uuid4())
    return result


class TestVMCreateWithNetworkId:

    @pytest.mark.asyncio
    async def test_network_id_resolves_vlan_id_in_agent_payload(self):
        """Admin caller with network_id: vlan_id from the network reaches the agent."""
        from controller.api.routers.vms import create_vm, VMCreate

        tenant = _make_tenant()
        node = _make_node()
        network = _make_network(tenant.id, vlan_id=300)

        session = _make_session(
            _scalar_one_or_none(tenant),   # select Tenant (admin path)
            _scalar_one_or_none(network),  # select Network
            _scalars_all([node]),           # select all Nodes (scheduler)
            _scalar_one(node),              # select Node by ip_address
        )

        agent_result = _agent_mock()
        captured_payload: list[dict] = []

        async def _capture_send(ip, cmd, payload):
            captured_payload.append(payload)
            return agent_result

        body = VMCreate(
            name="web-01",
            tenant_id=tenant.id,
            network_id=network.id,
            cpu_cores=2,
            ram_mb=2048,
            disk_gb=20,
        )

        with patch("controller.api.routers.vms.AgentClient") as MockAgent, \
             patch("controller.api.routers.vms.Scheduler") as MockScheduler:
            mock_instance = MagicMock()
            mock_instance.send_command = AsyncMock(side_effect=_capture_send)
            MockAgent.return_value = mock_instance

            mock_sched = MagicMock()
            mock_sched.select_node.return_value = node
            MockScheduler.return_value = mock_sched

            await create_vm(body=body, session=session, auth=(None, None))

        assert len(captured_payload) == 1
        assert captured_payload[0].get("vlan_id") == 300

    @pytest.mark.asyncio
    async def test_no_network_id_sends_no_vlan_id(self):
        """Without network_id, vlan_id is absent from the agent payload."""
        from controller.api.routers.vms import create_vm, VMCreate

        tenant = _make_tenant()
        node = _make_node()

        session = _make_session(
            _scalar_one_or_none(tenant),  # select Tenant (admin path)
            _scalars_all([node]),          # select all Nodes
            _scalar_one(node),             # select Node by ip_address
        )

        agent_result = _agent_mock()
        captured_payload: list[dict] = []

        async def _capture_send(ip, cmd, payload):
            captured_payload.append(payload)
            return agent_result

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
            mock_instance.send_command = AsyncMock(side_effect=_capture_send)
            MockAgent.return_value = mock_instance

            mock_sched = MagicMock()
            mock_sched.select_node.return_value = node
            MockScheduler.return_value = mock_sched

            await create_vm(body=body, session=session, auth=(None, None))

        assert len(captured_payload) == 1
        assert "vlan_id" not in captured_payload[0]

    @pytest.mark.asyncio
    async def test_network_id_wrong_tenant_raises_404(self):
        """Tenant caller with a network_id belonging to another tenant gets 404."""
        from fastapi import HTTPException
        from controller.api.routers.vms import create_vm, VMCreate

        tenant_a = _make_tenant()
        node = _make_node()

        # Session: network lookup returns None (ownership filter excludes it)
        session = _make_session(
            _scalar_one_or_none(None),  # select Network → not found for this tenant
        )

        other_network_id = uuid.uuid4()
        body = VMCreate(
            name="web-03",
            network_id=other_network_id,
            cpu_cores=2,
            ram_mb=2048,
            disk_gb=20,
        )

        with pytest.raises(HTTPException) as exc_info:
            await create_vm(body=body, session=session, auth=(None, tenant_a))

        assert exc_info.value.status_code == 404
        assert "Network not found" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_admin_can_use_any_tenant_network(self):
        """Admin caller can attach a VM to any network regardless of tenant ownership."""
        from controller.api.routers.vms import create_vm, VMCreate

        tenant = _make_tenant()
        other_tenant = _make_tenant()
        node = _make_node()
        # Network belongs to other_tenant, but admin caller should still resolve it
        network = _make_network(other_tenant.id, vlan_id=150)

        session = _make_session(
            _scalar_one_or_none(tenant),   # select Tenant (admin path uses body.tenant_id)
            _scalar_one_or_none(network),  # select Network — no tenant filter for admin
            _scalars_all([node]),
            _scalar_one(node),
        )

        agent_result = _agent_mock()
        captured_payload: list[dict] = []

        async def _capture_send(ip, cmd, payload):
            captured_payload.append(payload)
            return agent_result

        body = VMCreate(
            name="web-04",
            tenant_id=tenant.id,
            network_id=network.id,
            cpu_cores=2,
            ram_mb=2048,
            disk_gb=20,
        )

        with patch("controller.api.routers.vms.AgentClient") as MockAgent, \
             patch("controller.api.routers.vms.Scheduler") as MockScheduler:
            mock_instance = MagicMock()
            mock_instance.send_command = AsyncMock(side_effect=_capture_send)
            MockAgent.return_value = mock_instance

            mock_sched = MagicMock()
            mock_sched.select_node.return_value = node
            MockScheduler.return_value = mock_sched

            await create_vm(body=body, session=session, auth=(None, None))

        assert captured_payload[0].get("vlan_id") == 150
