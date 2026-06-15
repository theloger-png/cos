"""Unit tests for admin-on-behalf-of-tenant create flows (VMs and Networks)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from controller.db.models import Tenant, VM, Network


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tenant(tid: uuid.UUID | None = None) -> Tenant:
    t = Tenant()
    t.id = tid or uuid.uuid4()
    t.name = "acme"
    t.created_at = datetime.now(timezone.utc)
    return t


def _make_node():
    from controller.db.models import Node
    n = Node()
    n.id = uuid.uuid4()
    n.hostname = "node1"
    n.ip_address = "10.0.0.1"
    n.cpu_cores = 16
    n.ram_mb = 32768
    n.disk_gb = 500
    n.active = True
    return n


def _make_vm(tenant_id: uuid.UUID) -> VM:
    v = VM()
    v.id = uuid.uuid4()
    v.name = "test-vm"
    v.tenant_id = tenant_id
    v.node_id = uuid.uuid4()
    v.cpu_cores = 2
    v.ram_mb = 2048
    v.disk_gb = 20
    v.status = "stopped"
    v.libvirt_uuid = None
    v.template_id = None
    v.created_at = datetime.now(timezone.utc)
    return v


def _make_network(tenant_id: uuid.UUID) -> Network:
    n = Network()
    n.id = uuid.uuid4()
    n.tenant_id = tenant_id
    n.name = "net1"
    n.vlan_id = 100
    n.cidr = "10.1.0.0/24"
    n.gateway = "10.1.0.1"
    n.created_at = datetime.now(timezone.utc)
    return n


def _mock_session_multi(returns: list) -> AsyncMock:
    """Return a session mock that cycles through `returns` on successive execute() calls."""
    results = []
    for val in returns:
        r = MagicMock()
        r.scalar_one_or_none.return_value = val
        r.scalar_one.return_value = val
        r.scalars.return_value.all.return_value = [val] if val else []
        results.append(r)

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=results)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    session.delete = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# create_vm
# ---------------------------------------------------------------------------

class TestCreateVMAdmin:
    @pytest.mark.asyncio
    async def test_admin_with_tenant_id_succeeds(self):
        """Admin (tenant=None) can create a VM for an existing tenant via body.tenant_id."""
        from controller.api.routers.vms import create_vm, VMCreate

        tenant = _make_tenant()
        node = _make_node()
        body = VMCreate(
            name="my-vm",
            tenant_id=tenant.id,
            cpu_cores=2,
            ram_mb=2048,
            disk_gb=20,
        )

        nodes_result = MagicMock()
        nodes_result.scalars.return_value.all.return_value = [node]
        tenant_result = MagicMock()
        tenant_result.scalar_one_or_none.return_value = tenant
        node_result = MagicMock()
        node_result.scalar_one.return_value = node

        _created_vm_id = uuid.uuid4()
        _created_at = datetime.now(timezone.utc)

        async def _fake_refresh(obj):
            obj.id = _created_vm_id
            obj.created_at = _created_at

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[tenant_result, nodes_result, node_result])
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = _fake_refresh

        mock_agent_result = MagicMock()
        mock_agent_result.success = True
        mock_agent_result.output = "libvirt-uuid-abc"

        with patch("controller.api.routers.vms.AgentClient") as MockAgent, \
             patch("controller.api.routers.vms.Scheduler") as MockScheduler:
            mock_instance = MagicMock()
            mock_instance.send_command = AsyncMock(return_value=mock_agent_result)
            MockAgent.return_value = mock_instance

            mock_sched = MagicMock()
            mock_sched.select_node.return_value = node
            MockScheduler.return_value = mock_sched

            result = await create_vm(body=body, session=session, auth=(None, None))

        assert result.tenant_id == tenant.id
        assert result.name == "my-vm"

    @pytest.mark.asyncio
    async def test_admin_without_tenant_id_raises_422(self):
        """Admin (tenant=None) calling without tenant_id in body raises 422."""
        from controller.api.routers.vms import create_vm, VMCreate

        body = VMCreate(name="my-vm", cpu_cores=2, ram_mb=2048, disk_gb=20)
        session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await create_vm(body=body, session=session, auth=(None, None))

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_admin_with_nonexistent_tenant_raises_404(self):
        """Admin specifying a non-existent tenant_id gets 404."""
        from controller.api.routers.vms import create_vm, VMCreate

        body = VMCreate(name="vm", tenant_id=uuid.uuid4(), cpu_cores=2, ram_mb=2048, disk_gb=20)
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)

        with pytest.raises(HTTPException) as exc_info:
            await create_vm(body=body, session=session, auth=(None, None))

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_tenant_scoped_ignores_body_tenant_id(self):
        """Tenant-scoped caller's own tenant_id is always used, body.tenant_id is ignored."""
        from controller.api.routers.vms import create_vm, VMCreate

        real_tenant = _make_tenant()
        other_tenant_id = uuid.uuid4()
        node = _make_node()

        body = VMCreate(
            name="vm",
            tenant_id=other_tenant_id,  # should be ignored
            cpu_cores=2,
            ram_mb=2048,
            disk_gb=20,
        )

        nodes_result = MagicMock()
        nodes_result.scalars.return_value.all.return_value = [node]
        node_result = MagicMock()
        node_result.scalar_one.return_value = node

        _vm_id = uuid.uuid4()
        _created_at = datetime.now(timezone.utc)

        async def _fake_refresh(obj):
            obj.id = _vm_id
            obj.created_at = _created_at

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[nodes_result, node_result])
        session.add = MagicMock()
        session.flush = AsyncMock()
        session.commit = AsyncMock()
        session.refresh = _fake_refresh

        mock_agent_result = MagicMock()
        mock_agent_result.success = True
        mock_agent_result.output = "libvirt-uuid-xyz"

        with patch("controller.api.routers.vms.AgentClient") as MockAgent, \
             patch("controller.api.routers.vms.Scheduler") as MockScheduler:
            mock_instance = MagicMock()
            mock_instance.send_command = AsyncMock(return_value=mock_agent_result)
            MockAgent.return_value = mock_instance

            mock_sched = MagicMock()
            mock_sched.select_node.return_value = node
            MockScheduler.return_value = mock_sched

            result = await create_vm(body=body, session=session, auth=(None, real_tenant))

        assert result.tenant_id == real_tenant.id


# ---------------------------------------------------------------------------
# create_network
# ---------------------------------------------------------------------------

class TestCreateNetworkAdmin:
    @pytest.mark.asyncio
    async def test_admin_without_tenant_id_raises_422(self):
        """Admin calling create_network without tenant_id raises 422."""
        from controller.api.routers.networks import create_network, NetworkCreate

        body = NetworkCreate(name="net", vlan_id=10, cidr="10.0.0.0/24", gateway="10.0.0.1")
        session = AsyncMock()

        with pytest.raises(HTTPException) as exc_info:
            await create_network(body=body, session=session, auth=(None, None))

        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_admin_with_nonexistent_tenant_raises_404(self):
        """Admin specifying non-existent tenant_id in create_network gets 404."""
        from controller.api.routers.networks import create_network, NetworkCreate

        body = NetworkCreate(
            name="net", tenant_id=uuid.uuid4(), vlan_id=10, cidr="10.0.0.0/24", gateway="10.0.0.1"
        )
        result = MagicMock()
        result.scalar_one_or_none.return_value = None
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)

        with pytest.raises(HTTPException) as exc_info:
            await create_network(body=body, session=session, auth=(None, None))

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_admin_with_tenant_id_succeeds(self):
        """Admin can create a network for an existing tenant."""
        from controller.api.routers.networks import create_network, NetworkCreate

        tenant = _make_tenant()
        body = NetworkCreate(
            name="net1",
            tenant_id=tenant.id,
            vlan_id=100,
            cidr="10.1.0.0/24",
            gateway="10.1.0.1",
        )
        tenant_result = MagicMock()
        tenant_result.scalar_one_or_none.return_value = tenant

        _net_id = uuid.uuid4()
        _created_at = datetime.now(timezone.utc)

        async def _fake_refresh(obj):
            obj.id = _net_id
            obj.created_at = _created_at

        session = AsyncMock()
        session.execute = AsyncMock(return_value=tenant_result)
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.refresh = _fake_refresh

        with patch("controller.api.routers.networks.NOSClient") as MockNOS:
            nos_instance = MagicMock()
            nos_instance.configure_vlan = AsyncMock(return_value=True)
            nos_instance.commit = AsyncMock(return_value=True)
            MockNOS.return_value = nos_instance

            result = await create_network(body=body, session=session, auth=(None, None))

        assert result.tenant_id == tenant.id

    @pytest.mark.asyncio
    async def test_tenant_scoped_ignores_body_tenant_id(self):
        """Tenant-scoped caller's own tenant is used; body.tenant_id is ignored."""
        from controller.api.routers.networks import create_network, NetworkCreate

        real_tenant = _make_tenant()
        body = NetworkCreate(
            name="net",
            tenant_id=uuid.uuid4(),  # should be ignored
            vlan_id=200,
            cidr="10.2.0.0/24",
            gateway="10.2.0.1",
        )

        _net_id = uuid.uuid4()
        _created_at = datetime.now(timezone.utc)

        async def _fake_refresh(obj):
            obj.id = _net_id
            obj.created_at = _created_at

        session = AsyncMock()
        session.add = MagicMock()
        session.commit = AsyncMock()
        session.refresh = _fake_refresh

        with patch("controller.api.routers.networks.NOSClient") as MockNOS:
            nos_instance = MagicMock()
            nos_instance.configure_vlan = AsyncMock(return_value=True)
            nos_instance.commit = AsyncMock(return_value=True)
            MockNOS.return_value = nos_instance

            result = await create_network(body=body, session=session, auth=(None, real_tenant))

        assert result.tenant_id == real_tenant.id


# ---------------------------------------------------------------------------
# Admin bypass of ownership checks on action/delete endpoints
# ---------------------------------------------------------------------------

class TestAdminOwnershipBypass:
    @pytest.mark.asyncio
    async def test_admin_can_destroy_any_vm(self):
        """Admin (tenant=None) can destroy a VM belonging to any tenant."""
        from controller.api.routers.vms import destroy_vm

        tenant_id = uuid.uuid4()
        vm = _make_vm(tenant_id)
        vm.libvirt_uuid = None

        node = _make_node()
        node.id = vm.node_id

        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = vm
        node_result = MagicMock()
        node_result.scalar_one_or_none.return_value = node
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[vm_result, node_result])
        session.delete = AsyncMock()
        session.commit = AsyncMock()

        # Should not raise
        await destroy_vm(vm_id=vm.id, session=session, auth=(None, None))
        session.delete.assert_awaited_once_with(vm)

    @pytest.mark.asyncio
    async def test_tenant_scoped_cannot_destroy_other_tenants_vm(self):
        """Tenant-scoped caller gets 403 when trying to destroy another tenant's VM."""
        from controller.api.routers.vms import destroy_vm

        owner_tenant = _make_tenant()
        caller_tenant = _make_tenant()
        vm = _make_vm(owner_tenant.id)

        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = vm
        session = AsyncMock()
        session.execute = AsyncMock(return_value=vm_result)

        with pytest.raises(HTTPException) as exc_info:
            await destroy_vm(vm_id=vm.id, session=session, auth=(None, caller_tenant))

        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_delete_any_network(self):
        """Admin (tenant=None) can delete a network belonging to any tenant."""
        from controller.api.routers.networks import delete_network

        tenant_id = uuid.uuid4()
        network = _make_network(tenant_id)

        net_result = MagicMock()
        net_result.scalar_one_or_none.return_value = network
        session = AsyncMock()
        session.execute = AsyncMock(return_value=net_result)
        session.delete = AsyncMock()
        session.commit = AsyncMock()

        with patch("controller.api.routers.networks.NOSClient") as MockNOS:
            nos_instance = MagicMock()
            nos_instance.delete_vlan = AsyncMock(return_value=True)
            nos_instance.commit = AsyncMock(return_value=True)
            MockNOS.return_value = nos_instance

            await delete_network(network_id=network.id, session=session, auth=(None, None))

        session.delete.assert_awaited_once_with(network)
