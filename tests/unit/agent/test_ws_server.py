"""Unit tests for agent/ws_server.py command dispatch."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from common.models import AgentCommand, AgentCommandResult


async def _dispatch(command: AgentCommand) -> AgentCommandResult:
    """Import _dispatch lazily so module-level driver init doesn't run at import."""
    from agent import ws_server
    return await ws_server._dispatch(command)


class TestVmCreateCommand:
    def _libvirt_mock(self, uuid: str = "test-uuid") -> MagicMock:
        m = MagicMock()
        m.create_vm = MagicMock(return_value=uuid)
        return m

    @pytest.mark.asyncio
    async def test_passes_cloud_init_fields(self):
        libvirt = self._libvirt_mock("abc-123")
        payload = {
            "name": "vm1",
            "cpu_cores": 2,
            "ram_mb": 1024,
            "disk_gb": 10,
            "cloud_init_user": "admin",
            "cloud_init_password_hash": "$6$salt$hash",
        }
        with patch("agent.ws_server._libvirt", libvirt):
            result = await _dispatch(AgentCommand(command="vm_create", payload=payload))
        assert result.success is True
        assert result.output == "abc-123"
        libvirt.create_vm.assert_called_once_with(
            name="vm1",
            cpu_cores=2,
            ram_mb=1024,
            disk_gb=10,
            image_path="",
            vlan_id=None,
            cloud_init_user="admin",
            cloud_init_password_hash="$6$salt$hash",
        )

    @pytest.mark.asyncio
    async def test_cloud_init_fields_absent_passes_none(self):
        libvirt = self._libvirt_mock("xyz-456")
        payload = {"name": "vm2", "cpu_cores": 1, "ram_mb": 512, "disk_gb": 5}
        with patch("agent.ws_server._libvirt", libvirt):
            result = await _dispatch(AgentCommand(command="vm_create", payload=payload))
        assert result.success is True
        call_kwargs = libvirt.create_vm.call_args
        assert call_kwargs.kwargs.get("cloud_init_user") is None
        assert call_kwargs.kwargs.get("cloud_init_password_hash") is None


class TestUnknownCommand:
    @pytest.mark.asyncio
    async def test_returns_error(self):
        result = await _dispatch(AgentCommand(command="bogus_cmd", payload={}))
        assert result.success is False
        assert "unknown command" in (result.error or "")


class TestVmGetConfigCommand:
    def _libvirt_with_config(self, config: dict) -> MagicMock:
        m = MagicMock()
        m.get_vm_config = MagicMock(return_value=config)
        return m

    @pytest.mark.asyncio
    async def test_success_returns_json(self):
        import json
        config = {"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []}
        libvirt = self._libvirt_with_config(config)
        with patch("agent.ws_server._libvirt", libvirt):
            result = await _dispatch(AgentCommand(
                command="vm_get_config",
                payload={"libvirt_uuid": "test-uuid"},
            ))
        assert result.success is True
        assert json.loads(result.output) == config
        libvirt.get_vm_config.assert_called_once()

    @pytest.mark.asyncio
    async def test_passes_libvirt_uuid(self):
        config = {"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []}
        libvirt = self._libvirt_with_config(config)
        with patch("agent.ws_server._libvirt", libvirt):
            await _dispatch(AgentCommand(
                command="vm_get_config",
                payload={"libvirt_uuid": "my-vm-uuid"},
            ))
        call_args = libvirt.get_vm_config.call_args
        assert call_args[0][0] == "my-vm-uuid"

    @pytest.mark.asyncio
    async def test_failure_returns_error(self):
        libvirt = MagicMock()
        libvirt.get_vm_config.side_effect = RuntimeError("libvirt not found")
        with patch("agent.ws_server._libvirt", libvirt):
            result = await _dispatch(AgentCommand(
                command="vm_get_config",
                payload={"libvirt_uuid": "bad-uuid"},
            ))
        assert result.success is False
        assert result.error is not None


class TestVmApplyConfigCommand:
    def _libvirt_with_apply(self, new_config: dict) -> MagicMock:
        m = MagicMock()
        m.apply_vm_config = MagicMock(return_value=new_config)
        return m

    @pytest.mark.asyncio
    async def test_success_returns_new_config(self):
        import json
        new_config = {"vcpu": 4, "memory_mb": 4096, "disks": [], "nics": []}
        libvirt = self._libvirt_with_apply(new_config)
        changes = {"vcpu": 4, "memory_mb": 4096}
        with patch("agent.ws_server._libvirt", libvirt):
            result = await _dispatch(AgentCommand(
                command="vm_apply_config",
                payload={"libvirt_uuid": "test-uuid", "changes": changes},
            ))
        assert result.success is True
        assert json.loads(result.output) == new_config

    @pytest.mark.asyncio
    async def test_passes_changes_to_driver(self):
        new_config = {"vcpu": 4, "memory_mb": 2048, "disks": [], "nics": []}
        libvirt = self._libvirt_with_apply(new_config)
        changes = {"vcpu": 4, "add_disks": [{"size_gb": 20}]}
        with patch("agent.ws_server._libvirt", libvirt):
            await _dispatch(AgentCommand(
                command="vm_apply_config",
                payload={"libvirt_uuid": "vm-uuid", "changes": changes},
            ))
        call_args = libvirt.apply_vm_config.call_args
        assert call_args[0][0] == "vm-uuid"
        assert call_args[0][1] == changes

    @pytest.mark.asyncio
    async def test_empty_changes_dict_accepted(self):
        new_config = {"vcpu": 2, "memory_mb": 2048, "disks": [], "nics": []}
        libvirt = self._libvirt_with_apply(new_config)
        with patch("agent.ws_server._libvirt", libvirt):
            result = await _dispatch(AgentCommand(
                command="vm_apply_config",
                payload={"libvirt_uuid": "vm-uuid"},
            ))
        assert result.success is True
        libvirt.apply_vm_config.assert_called_once_with("vm-uuid", {})
