"""Unit tests for GET/PUT /api/v1/vms/{id}/hardware endpoints."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controller.db.models import Network, Node, Tenant, VM


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tenant() -> Tenant:
    t = Tenant()
    t.id = uuid.uuid4()
    t.name = "acme"
    t.email = "acme@example.com"
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
    n.status = "online"
    return n


def _make_vm(tenant: Tenant, node: Node) -> VM:
    v = VM()
    v.id = uuid.uuid4()
    v.name = "test-vm"
    v.tenant_id = tenant.id
    v.node_id = node.id
    v.cpu_cores = 2
    v.ram_mb = 2048
    v.disk_gb = 20
    v.status = "running"
    v.libvirt_uuid = str(uuid.uuid4())
    v.created_at = datetime.now(timezone.utc)
    return v


def _make_network(tenant: Tenant, vlan_id: int = 101) -> Network:
    net = Network()
    net.id = uuid.uuid4()
    net.tenant_id = tenant.id
    net.name = "net-101"
    net.vlan_id = vlan_id
    net.cidr = None
    net.gateway = None
    net.created_at = datetime.now(timezone.utc)
    return net


def _hardware_dict(vlan_id: int | None = 101) -> dict:
    return {
        "vcpu": 2,
        "memory_mb": 2048,
        "disks": [{"target": "vda", "size_gb": 20.0, "path": "/var/lib/cos/vms/x.qcow2", "device": "disk"}],
        "nics": [{"target": "vnet0", "mac": "52:54:00:11:22:33", "bridge": "nos-br", "vlan_id": vlan_id}],
    }


# ---------------------------------------------------------------------------
# GET /hardware tests
# ---------------------------------------------------------------------------


class TestGetVmHardware:
    def _build_session(self, vm: VM, node: Node, networks: list[Network]) -> AsyncMock:
        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = vm

        node_result = MagicMock()
        node_result.scalar_one_or_none.return_value = node

        nets_result = MagicMock()
        nets_result.scalars.return_value.all.return_value = networks

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[vm_result, node_result, nets_result])
        return session

    @pytest.mark.asyncio
    async def test_returns_hardware_config(self):
        from controller.api.routers.vms import get_vm_hardware

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        session = self._build_session(vm, node, [])

        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = json.dumps(_hardware_dict(vlan_id=None))

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            MockAgent.return_value.send_command = AsyncMock(return_value=agent_result)
            result = await get_vm_hardware(vm_id=vm.id, session=session, auth=(None, None))

        assert result.vcpu == 2
        assert result.memory_mb == 2048
        assert len(result.disks) == 1
        assert len(result.nics) == 1

    @pytest.mark.asyncio
    async def test_enriches_nic_with_network_info(self):
        from controller.api.routers.vms import get_vm_hardware

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        network = _make_network(tenant, vlan_id=101)
        session = self._build_session(vm, node, [network])

        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = json.dumps(_hardware_dict(vlan_id=101))

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            MockAgent.return_value.send_command = AsyncMock(return_value=agent_result)
            result = await get_vm_hardware(vm_id=vm.id, session=session, auth=(None, None))

        nic = result.nics[0]
        assert nic.vlan_id == 101
        assert nic.network_id == network.id
        assert nic.network_name == "net-101"

    @pytest.mark.asyncio
    async def test_nic_without_matching_network_has_no_network_id(self):
        from controller.api.routers.vms import get_vm_hardware

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        session = self._build_session(vm, node, [])  # no networks

        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = json.dumps(_hardware_dict(vlan_id=999))

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            MockAgent.return_value.send_command = AsyncMock(return_value=agent_result)
            result = await get_vm_hardware(vm_id=vm.id, session=session, auth=(None, None))

        assert result.nics[0].network_id is None
        assert result.nics[0].network_name is None

    @pytest.mark.asyncio
    async def test_raises_409_when_no_libvirt_uuid(self):
        from fastapi import HTTPException
        from controller.api.routers.vms import get_vm_hardware

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        vm.libvirt_uuid = None

        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = vm
        session = AsyncMock()
        session.execute = AsyncMock(return_value=vm_result)

        with pytest.raises(HTTPException) as exc_info:
            await get_vm_hardware(vm_id=vm.id, session=session, auth=(None, None))
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_502_when_agent_fails(self):
        from fastapi import HTTPException
        from controller.api.routers.vms import get_vm_hardware

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        session = self._build_session(vm, node, [])

        agent_result = MagicMock()
        agent_result.success = False
        agent_result.error = "connection refused"

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            MockAgent.return_value.send_command = AsyncMock(return_value=agent_result)
            with pytest.raises(HTTPException) as exc_info:
                await get_vm_hardware(vm_id=vm.id, session=session, auth=(None, None))
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_sends_vm_get_config_command(self):
        from controller.api.routers.vms import get_vm_hardware

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        session = self._build_session(vm, node, [])

        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = json.dumps(_hardware_dict(vlan_id=None))

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            mock_send = AsyncMock(return_value=agent_result)
            MockAgent.return_value.send_command = mock_send
            await get_vm_hardware(vm_id=vm.id, session=session, auth=(None, None))

        mock_send.assert_awaited_once()
        call_args = mock_send.call_args
        assert call_args[0][1] == "vm_get_config"
        assert call_args[0][2]["libvirt_uuid"] == vm.libvirt_uuid


# ---------------------------------------------------------------------------
# PUT /hardware tests
# ---------------------------------------------------------------------------


class TestPutVmHardware:
    def _build_session(
        self,
        vm: VM,
        node: Node,
        network: Network | None = None,
        enrich_networks: list[Network] | None = None,
    ) -> AsyncMock:
        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = vm

        side_effects = [vm_result]

        if network is not None:
            net_result = MagicMock()
            net_result.scalar_one_or_none.return_value = network
            side_effects.append(net_result)

        node_result = MagicMock()
        node_result.scalar_one_or_none.return_value = node
        side_effects.append(node_result)

        enrich_result = MagicMock()
        enrich_result.scalars.return_value.all.return_value = enrich_networks or []
        side_effects.append(enrich_result)

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=side_effects)
        return session

    @pytest.mark.asyncio
    async def test_sends_vm_apply_config_command(self):
        from controller.api.routers.vms import put_vm_hardware, VMHardwareChanges

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        session = self._build_session(vm, node)

        new_config = _hardware_dict(vlan_id=None)
        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = json.dumps(new_config)

        body = VMHardwareChanges(vcpu=4)

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            mock_send = AsyncMock(return_value=agent_result)
            MockAgent.return_value.send_command = mock_send
            await put_vm_hardware(vm_id=vm.id, body=body, session=session, auth=(None, None))

        mock_send.assert_awaited_once()
        call_args = mock_send.call_args
        assert call_args[0][1] == "vm_apply_config"
        payload = call_args[0][2]
        assert payload["libvirt_uuid"] == vm.libvirt_uuid
        assert payload["changes"]["vcpu"] == 4

    @pytest.mark.asyncio
    async def test_resolves_network_id_to_vlan_id(self):
        from controller.api.routers.vms import put_vm_hardware, VMHardwareChanges, AddNICRequest

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        network = _make_network(tenant, vlan_id=202)
        session = self._build_session(vm, node, network=network)

        new_config = _hardware_dict(vlan_id=202)
        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = json.dumps(new_config)

        body = VMHardwareChanges(add_nics=[AddNICRequest(network_id=network.id)])

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            mock_send = AsyncMock(return_value=agent_result)
            MockAgent.return_value.send_command = mock_send
            await put_vm_hardware(vm_id=vm.id, body=body, session=session, auth=(None, None))

        call_args = mock_send.call_args
        add_nics = call_args[0][2]["changes"]["add_nics"]
        assert len(add_nics) == 1
        assert add_nics[0]["vlan_id"] == 202

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_network(self):
        from fastapi import HTTPException
        from controller.api.routers.vms import put_vm_hardware, VMHardwareChanges, AddNICRequest

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)

        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = vm
        net_result = MagicMock()
        net_result.scalar_one_or_none.return_value = None  # not found

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[vm_result, net_result])

        body = VMHardwareChanges(add_nics=[AddNICRequest(network_id=uuid.uuid4())])

        with pytest.raises(HTTPException) as exc_info:
            await put_vm_hardware(vm_id=vm.id, body=body, session=session, auth=(None, None))
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_enriched_hardware_config(self):
        from controller.api.routers.vms import put_vm_hardware, VMHardwareChanges

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        network = _make_network(tenant, vlan_id=101)
        session = self._build_session(vm, node, enrich_networks=[network])

        new_config = _hardware_dict(vlan_id=101)
        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = json.dumps(new_config)

        body = VMHardwareChanges(memory_mb=4096)

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            MockAgent.return_value.send_command = AsyncMock(return_value=agent_result)
            result = await put_vm_hardware(vm_id=vm.id, body=body, session=session, auth=(None, None))

        assert result.nics[0].network_id == network.id
        assert result.nics[0].network_name == "net-101"

    @pytest.mark.asyncio
    async def test_raises_502_when_agent_fails(self):
        from fastapi import HTTPException
        from controller.api.routers.vms import put_vm_hardware, VMHardwareChanges

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        session = self._build_session(vm, node)

        agent_result = MagicMock()
        agent_result.success = False
        agent_result.error = "apply failed"

        body = VMHardwareChanges(vcpu=4)

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            MockAgent.return_value.send_command = AsyncMock(return_value=agent_result)
            with pytest.raises(HTTPException) as exc_info:
                await put_vm_hardware(vm_id=vm.id, body=body, session=session, auth=(None, None))
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_memory_mb_included_in_changes(self):
        from controller.api.routers.vms import put_vm_hardware, VMHardwareChanges

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        session = self._build_session(vm, node)

        new_config = _hardware_dict(vlan_id=None)
        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = json.dumps(new_config)

        body = VMHardwareChanges(memory_mb=8192)

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            mock_send = AsyncMock(return_value=agent_result)
            MockAgent.return_value.send_command = mock_send
            await put_vm_hardware(vm_id=vm.id, body=body, session=session, auth=(None, None))

        changes = mock_send.call_args[0][2]["changes"]
        assert changes["memory_mb"] == 8192
        assert "vcpu" not in changes

    @pytest.mark.asyncio
    async def test_remove_nics_passed_to_agent(self):
        from controller.api.routers.vms import put_vm_hardware, VMHardwareChanges, RemoveNICRequest

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node)
        session = self._build_session(vm, node)

        new_config = _hardware_dict(vlan_id=None)
        agent_result = MagicMock()
        agent_result.success = True
        agent_result.output = json.dumps(new_config)

        body = VMHardwareChanges(remove_nics=[RemoveNICRequest(target="vnet1")])

        with patch("controller.api.routers.vms.AgentClient") as MockAgent:
            mock_send = AsyncMock(return_value=agent_result)
            MockAgent.return_value.send_command = mock_send
            await put_vm_hardware(vm_id=vm.id, body=body, session=session, auth=(None, None))

        changes = mock_send.call_args[0][2]["changes"]
        assert changes["remove_nics"] == [{"target": "vnet1"}]
