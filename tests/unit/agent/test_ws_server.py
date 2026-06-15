"""Unit tests for new VLAN commands in agent/ws_server.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from common.models import AgentCommand, AgentCommandResult


async def _dispatch(command: AgentCommand) -> AgentCommandResult:
    """Import _dispatch lazily so module-level NOSDriver init doesn't run at import."""
    from agent import ws_server
    return await ws_server._dispatch(command)


def _nos_mock(ok: bool) -> MagicMock:
    m = MagicMock()
    m.configure_vlan = AsyncMock(return_value=ok)
    m.remove_vlan = AsyncMock(return_value=ok)
    return m


class TestConfigureVlanCommand:
    @pytest.mark.asyncio
    async def test_success(self):
        nos = _nos_mock(True)
        with patch("agent.ws_server._nos", nos):
            result = await _dispatch(AgentCommand(command="configure_vlan", payload={"vlan_id": 100}))
        assert result.success is True
        assert result.output == "configured"
        nos.configure_vlan.assert_awaited_once_with(100)

    @pytest.mark.asyncio
    async def test_failure(self):
        nos = _nos_mock(False)
        with patch("agent.ws_server._nos", nos):
            result = await _dispatch(AgentCommand(command="configure_vlan", payload={"vlan_id": 200}))
        assert result.success is False
        assert result.error == "configure_vlan failed"

    @pytest.mark.asyncio
    async def test_passes_vlan_id(self):
        nos = _nos_mock(True)
        with patch("agent.ws_server._nos", nos):
            await _dispatch(AgentCommand(command="configure_vlan", payload={"vlan_id": 42}))
        nos.configure_vlan.assert_awaited_once_with(42)


class TestRemoveVlanCommand:
    @pytest.mark.asyncio
    async def test_success(self):
        nos = _nos_mock(True)
        with patch("agent.ws_server._nos", nos):
            result = await _dispatch(AgentCommand(command="remove_vlan", payload={"vlan_id": 100}))
        assert result.success is True
        assert result.output == "removed"
        nos.remove_vlan.assert_awaited_once_with(100)

    @pytest.mark.asyncio
    async def test_failure(self):
        nos = _nos_mock(False)
        with patch("agent.ws_server._nos", nos):
            result = await _dispatch(AgentCommand(command="remove_vlan", payload={"vlan_id": 99}))
        assert result.success is False
        assert result.error == "remove_vlan failed"

    @pytest.mark.asyncio
    async def test_passes_vlan_id(self):
        nos = _nos_mock(True)
        with patch("agent.ws_server._nos", nos):
            await _dispatch(AgentCommand(command="remove_vlan", payload={"vlan_id": 77}))
        nos.remove_vlan.assert_awaited_once_with(77)


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
        with patch("agent.ws_server._libvirt", libvirt), patch("agent.ws_server._nos", _nos_mock(True)):
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
        with patch("agent.ws_server._libvirt", libvirt), patch("agent.ws_server._nos", _nos_mock(True)):
            result = await _dispatch(AgentCommand(command="vm_create", payload=payload))
        assert result.success is True
        call_kwargs = libvirt.create_vm.call_args
        assert call_kwargs.kwargs.get("cloud_init_user") is None
        assert call_kwargs.kwargs.get("cloud_init_password_hash") is None


class TestUnknownCommand:
    @pytest.mark.asyncio
    async def test_returns_error(self):
        with patch("agent.ws_server._nos", _nos_mock(True)):
            result = await _dispatch(AgentCommand(command="bogus_cmd", payload={}))
        assert result.success is False
        assert "unknown command" in (result.error or "")
