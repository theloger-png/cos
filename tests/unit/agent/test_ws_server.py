"""Unit tests for agent/ws_server.py command dispatch."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch, MagicMock

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
            ip_cidr=None,
            gateway=None,
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

    @pytest.mark.asyncio
    async def test_passes_static_ip_fields(self):
        libvirt = self._libvirt_mock("static-ip-uuid")
        payload = {
            "name": "vm3",
            "cpu_cores": 2,
            "ram_mb": 1024,
            "disk_gb": 10,
            "cloud_init_user": "admin",
            "cloud_init_password_hash": "$6$salt$hash",
            "ip_cidr": "192.168.1.50/24",
            "gateway": "192.168.1.1",
        }
        with patch("agent.ws_server._libvirt", libvirt):
            result = await _dispatch(AgentCommand(command="vm_create", payload=payload))
        assert result.success is True
        call_kwargs = libvirt.create_vm.call_args
        assert call_kwargs.kwargs.get("ip_cidr") == "192.168.1.50/24"
        assert call_kwargs.kwargs.get("gateway") == "192.168.1.1"


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


# ---------------------------------------------------------------------------
# /console WebSocket endpoint
# ---------------------------------------------------------------------------


class TestConsoleEndpoint:
    def _websocket(self, receive_side_effect: list) -> AsyncMock:
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.send_bytes = AsyncMock()
        ws.receive_bytes = AsyncMock(side_effect=receive_side_effect)
        return ws

    @pytest.mark.asyncio
    async def test_domain_not_found_closes_4404(self):
        from agent import ws_server
        from agent.libvirt_driver import DomainNotFoundError
        from fastapi import WebSocketDisconnect

        ws = self._websocket([WebSocketDisconnect()])
        libvirt_mock = MagicMock()
        libvirt_mock.open_console.side_effect = DomainNotFoundError("nope")

        with patch("agent.ws_server._libvirt", libvirt_mock):
            await ws_server.console_endpoint(ws, "missing-uuid")

        ws.accept.assert_awaited_once()
        ws.close.assert_awaited_once_with(code=4404, reason="VM not found")

    @pytest.mark.asyncio
    async def test_domain_not_running_closes_4409(self):
        from agent import ws_server
        from agent.libvirt_driver import DomainNotRunningError

        ws = self._websocket([])
        libvirt_mock = MagicMock()
        libvirt_mock.open_console.side_effect = DomainNotRunningError("stopped")

        with patch("agent.ws_server._libvirt", libvirt_mock):
            await ws_server.console_endpoint(ws, "stopped-uuid")

        ws.close.assert_awaited_once_with(code=4409, reason="VM is not running")

    @pytest.mark.asyncio
    async def test_console_unavailable_closes_4500(self):
        from agent import ws_server
        from agent.libvirt_driver import ConsoleUnavailableError

        ws = self._websocket([])
        libvirt_mock = MagicMock()
        libvirt_mock.open_console.side_effect = ConsoleUnavailableError("no pty console")

        with patch("agent.ws_server._libvirt", libvirt_mock):
            await ws_server.console_endpoint(ws, "no-console-uuid")

        ws.close.assert_awaited_once_with(code=4500, reason="console unavailable")

    @pytest.mark.asyncio
    async def test_unexpected_error_opening_console_closes_4500(self):
        from agent import ws_server

        ws = self._websocket([])
        libvirt_mock = MagicMock()
        libvirt_mock.open_console.side_effect = RuntimeError("boom")

        with patch("agent.ws_server._libvirt", libvirt_mock):
            await ws_server.console_endpoint(ws, "bad-uuid")

        ws.close.assert_awaited_once_with(code=4500, reason="internal error")

    @pytest.mark.asyncio
    async def test_browser_disconnect_relays_input_and_cleans_up_session(self):
        """Bytes typed in the browser reach session.write(); disconnect ends the relay."""
        from agent import ws_server
        from fastapi import WebSocketDisconnect

        session = MagicMock()
        session.inbound_queue = asyncio.Queue()  # left empty: never resolves on its own
        session.write = MagicMock()
        session.close = MagicMock()

        ws = self._websocket([b"keystrokes", WebSocketDisconnect()])
        libvirt_mock = MagicMock()
        libvirt_mock.open_console.return_value = session

        with patch("agent.ws_server._libvirt", libvirt_mock):
            await asyncio.wait_for(ws_server.console_endpoint(ws, "running-uuid"), timeout=5)

        session.write.assert_called_once_with(b"keystrokes")
        session.close.assert_called_once()
        ws.close.assert_awaited_once_with()

    @pytest.mark.asyncio
    async def test_console_eof_ends_relay_and_cancels_browser_read(self):
        """A None on inbound_queue (domain stopped) ends the relay even if the browser is silent."""
        from agent import ws_server

        session = MagicMock()
        session.inbound_queue = asyncio.Queue()
        await session.inbound_queue.put(b"boot message")
        await session.inbound_queue.put(None)
        session.write = MagicMock()
        session.close = MagicMock()

        async def _never_resolves() -> bytes:
            # A lambda returning asyncio.sleep(...) would NOT actually block here:
            # AsyncMock only awaits a side_effect that is itself a coroutine
            # function, so a plain lambda's returned-but-unawaited coroutine
            # would come straight back, spinning the loop instead of blocking.
            await asyncio.sleep(1000)
            raise AssertionError("should have been cancelled first")

        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.send_bytes = AsyncMock()
        # Never resolves on its own; only cancellation ends this task.
        ws.receive_bytes = AsyncMock(side_effect=_never_resolves)

        libvirt_mock = MagicMock()
        libvirt_mock.open_console.return_value = session

        with patch("agent.ws_server._libvirt", libvirt_mock):
            await asyncio.wait_for(ws_server.console_endpoint(ws, "running-uuid"), timeout=5)

        ws.send_bytes.assert_awaited_once_with(b"boot message")
        session.close.assert_called_once()
        ws.close.assert_awaited_once_with()


class TestVmSetPasswordCommand:
    _PAYLOAD = {"libvirt_uuid": "abc-123", "user": "ubuntu", "password_hash": "$6$salt$secrethash"}

    @pytest.mark.asyncio
    async def test_dispatches_to_driver(self):
        libvirt = MagicMock()
        with patch("agent.ws_server._libvirt", libvirt):
            result = await _dispatch(AgentCommand(command="vm_set_password", payload=self._PAYLOAD))
        assert result.success is True
        libvirt.set_user_password.assert_called_once_with("abc-123", "ubuntu", "$6$salt$secrethash")

    @pytest.mark.asyncio
    async def test_driver_error_message_is_returned_without_traceback_or_hash(self, caplog):
        from agent.libvirt_driver import PasswordResetError

        libvirt = MagicMock()
        libvirt.set_user_password.side_effect = PasswordResetError("VM is not running")
        with patch("agent.ws_server._libvirt", libvirt), caplog.at_level("DEBUG"):
            result = await _dispatch(AgentCommand(command="vm_set_password", payload=self._PAYLOAD))
        assert result.success is False
        assert result.error == "VM is not running"
        assert "secrethash" not in caplog.text
        assert "Traceback" not in caplog.text

    @pytest.mark.asyncio
    async def test_unexpected_error_is_still_reported(self):
        libvirt = MagicMock()
        libvirt.set_user_password.side_effect = RuntimeError("boom")
        with patch("agent.ws_server._libvirt", libvirt):
            result = await _dispatch(AgentCommand(command="vm_set_password", payload=self._PAYLOAD))
        assert result.success is False
        assert result.error == "boom"
