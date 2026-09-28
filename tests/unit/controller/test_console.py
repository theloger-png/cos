"""Unit tests for controller/api/routers/console.py (ticket issuance + WS relay)."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controller import console_tickets
from controller.db.models import Node, Tenant, VM


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


def _make_node(ip: str = "10.0.0.1") -> Node:
    n = Node()
    n.id = uuid.uuid4()
    n.hostname = "node1"
    n.ip_address = ip
    n.cpu_total = 16
    n.ram_total_mb = 32768
    n.disk_total_gb = 500
    n.status = "online"
    return n


def _make_vm(tenant: Tenant, node: Node, status: str = "running") -> VM:
    v = VM()
    v.id = uuid.uuid4()
    v.name = "test-vm"
    v.tenant_id = tenant.id
    v.node_id = node.id
    v.cpu_cores = 2
    v.ram_mb = 2048
    v.disk_gb = 20
    v.status = status
    v.libvirt_uuid = str(uuid.uuid4())
    v.created_at = datetime.now(timezone.utc)
    return v


@pytest.fixture(autouse=True)
def _clear_tickets():
    console_tickets._tickets.clear()
    yield
    console_tickets._tickets.clear()


# ---------------------------------------------------------------------------
# POST /{vm_id}/console-ticket
# ---------------------------------------------------------------------------


class TestCreateConsoleTicket:
    def _session(self, vm: VM, node: Node) -> AsyncMock:
        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = vm
        node_result = MagicMock()
        node_result.scalar_one_or_none.return_value = node
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[vm_result, node_result])
        return session

    @pytest.mark.asyncio
    async def test_issues_ticket_for_running_vm(self):
        from controller.api.routers.console import create_console_ticket

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node, status="running")
        session = self._session(vm, node)

        result = await create_console_ticket(vm_id=vm.id, session=session, auth=(None, None))

        assert result.ticket
        assert result.expires_in == console_tickets.TICKET_TTL_SECONDS

    @pytest.mark.asyncio
    async def test_ticket_resolves_to_node_ip_and_libvirt_uuid(self):
        from controller.api.routers.console import create_console_ticket

        tenant = _make_tenant()
        node = _make_node(ip="10.9.9.9")
        vm = _make_vm(tenant, node, status="running")
        session = self._session(vm, node)

        result = await create_console_ticket(vm_id=vm.id, session=session, auth=(None, None))
        ticket = console_tickets.consume_ticket(result.ticket, vm.id)

        assert ticket.node_ip == "10.9.9.9"
        assert ticket.libvirt_uuid == vm.libvirt_uuid

    @pytest.mark.asyncio
    async def test_raises_409_when_vm_stopped(self):
        from fastapi import HTTPException
        from controller.api.routers.console import create_console_ticket

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node, status="stopped")

        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = vm
        session = AsyncMock()
        session.execute = AsyncMock(return_value=vm_result)

        with pytest.raises(HTTPException) as exc_info:
            await create_console_ticket(vm_id=vm.id, session=session, auth=(None, None))
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_409_when_no_libvirt_uuid(self):
        from fastapi import HTTPException
        from controller.api.routers.console import create_console_ticket

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node, status="running")
        vm.libvirt_uuid = None

        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = vm
        session = AsyncMock()
        session.execute = AsyncMock(return_value=vm_result)

        with pytest.raises(HTTPException) as exc_info:
            await create_console_ticket(vm_id=vm.id, session=session, auth=(None, None))
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_raises_404_for_unknown_vm(self):
        from fastapi import HTTPException
        from controller.api.routers.console import create_console_ticket

        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = None
        session = AsyncMock()
        session.execute = AsyncMock(return_value=vm_result)

        with pytest.raises(HTTPException) as exc_info:
            await create_console_ticket(vm_id=uuid.uuid4(), session=session, auth=(None, None))
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_403_for_other_tenants_vm(self):
        from fastapi import HTTPException
        from controller.api.routers.console import create_console_ticket

        tenant = _make_tenant()
        other_tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node, status="running")

        vm_result = MagicMock()
        vm_result.scalar_one_or_none.return_value = vm
        session = AsyncMock()
        session.execute = AsyncMock(return_value=vm_result)

        with pytest.raises(HTTPException) as exc_info:
            await create_console_ticket(vm_id=vm.id, session=session, auth=(None, other_tenant))
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_admin_can_ticket_any_tenants_vm(self):
        from controller.api.routers.console import create_console_ticket

        tenant = _make_tenant()
        node = _make_node()
        vm = _make_vm(tenant, node, status="running")
        session = self._session(vm, node)

        # auth=(None, None) is the admin case (see controller/api/deps.py)
        result = await create_console_ticket(vm_id=vm.id, session=session, auth=(None, None))
        assert result.ticket


# ---------------------------------------------------------------------------
# _relay: byte pumping between the browser WS and the agent WS
# ---------------------------------------------------------------------------


class _FakeAgentWS:
    """Stand-in for a `websockets` client connection used as `async for`."""

    def __init__(self, inbound: list[bytes], close_code: int | None = None, close_reason: str = ""):
        self._inbound = inbound
        self.sent: list[bytes] = []
        self.close_code = close_code
        self.close_reason = close_reason

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for item in self._inbound:
            yield item

    async def send(self, data: bytes) -> None:
        self.sent.append(data)


class _FakeAgentWSBlocking(_FakeAgentWS):
    """An agent WS whose iteration never yields on its own - only cancellation ends it.

    Used where a test needs the *other* side of the relay to be the sole,
    deterministic driver of when _relay() returns (mirrors the approach used
    for the agent's own /console tests, avoiding races between two
    fakes that could otherwise both complete "instantly").
    """

    def __init__(self, close_code: int | None = None, close_reason: str = ""):
        super().__init__(inbound=[], close_code=close_code, close_reason=close_reason)

    async def _gen(self):
        await asyncio.sleep(1000)
        yield b"unreachable"  # pragma: no cover - only for generator syntax


class _FakeClientWS:
    """Stand-in for the browser-side Starlette WebSocket."""

    def __init__(self, inbound: list):
        self._inbound = iter(inbound)
        self.sent: list[bytes] = []
        self.closed_with: tuple[int, str] | None = None

    async def receive_bytes(self) -> bytes:
        item = next(self._inbound)
        if isinstance(item, Exception):
            raise item
        return item

    async def send_bytes(self, data: bytes) -> None:
        self.sent.append(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed_with = (code, reason)


class TestRelay:
    @pytest.mark.asyncio
    async def test_forwards_agent_bytes_to_browser(self):
        from fastapi import WebSocketDisconnect
        from controller.api.routers.console import _relay

        agent_ws = _FakeAgentWS(inbound=[b"console output"])
        client_ws = _FakeClientWS(inbound=[WebSocketDisconnect()])

        await asyncio.wait_for(_relay(client_ws, agent_ws), timeout=5)

        assert b"console output" in client_ws.sent

    @pytest.mark.asyncio
    async def test_forwards_browser_bytes_to_agent(self):
        from fastapi import WebSocketDisconnect
        from controller.api.routers.console import _relay

        agent_ws = _FakeAgentWSBlocking()  # never completes on its own
        client_ws = AsyncMock()
        client_ws.receive_bytes = AsyncMock(side_effect=[b"typed input", WebSocketDisconnect()])

        await asyncio.wait_for(_relay(client_ws, agent_ws), timeout=5)

        assert b"typed input" in agent_ws.sent

    @pytest.mark.asyncio
    async def test_closes_browser_with_agents_close_code_and_reason(self):
        from fastapi import WebSocketDisconnect
        from controller.api.routers.console import _relay

        agent_ws = _FakeAgentWS(inbound=[], close_code=4409, close_reason="VM is not running")
        client_ws = _FakeClientWS(inbound=[WebSocketDisconnect()])

        await asyncio.wait_for(_relay(client_ws, agent_ws), timeout=5)

        assert client_ws.closed_with == (4409, "VM is not running")

    @pytest.mark.asyncio
    async def test_defaults_to_normal_closure_when_agent_gives_no_code(self):
        from fastapi import WebSocketDisconnect
        from controller.api.routers.console import _relay

        agent_ws = _FakeAgentWS(inbound=[], close_code=None)
        client_ws = _FakeClientWS(inbound=[WebSocketDisconnect()])

        await asyncio.wait_for(_relay(client_ws, agent_ws), timeout=5)

        assert client_ws.closed_with == (1000, "")

    @pytest.mark.asyncio
    async def test_agent_ending_first_cancels_the_browser_read(self):
        from controller.api.routers.console import _relay

        agent_ws = _FakeAgentWS(inbound=[b"bye"], close_code=1000, close_reason="")

        async def _never_resolves() -> bytes:
            await asyncio.sleep(1000)
            raise AssertionError("should have been cancelled first")

        client_ws = MagicMock()
        client_ws.receive_bytes = AsyncMock(side_effect=_never_resolves)
        client_ws.send_bytes = AsyncMock()
        client_ws.close = AsyncMock()

        await asyncio.wait_for(_relay(client_ws, agent_ws), timeout=5)

        client_ws.send_bytes.assert_awaited_once_with(b"bye")
        client_ws.close.assert_awaited_once_with(code=1000, reason="")


# ---------------------------------------------------------------------------
# vm_console: ticket validation and agent-unreachable handling
# ---------------------------------------------------------------------------


class TestVmConsoleWebSocket:
    @pytest.mark.asyncio
    async def test_invalid_ticket_closes_4401(self):
        from controller.api.routers.console import vm_console

        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()

        await vm_console(ws, vm_id=uuid.uuid4(), ticket="bogus")

        ws.accept.assert_awaited_once()
        ws.close.assert_awaited_once_with(code=4401, reason="invalid or expired ticket")

    @pytest.mark.asyncio
    async def test_wrong_vm_ticket_closes_4401(self):
        from controller.api.routers.console import vm_console

        vm_id = uuid.uuid4()
        token = console_tickets.issue_ticket(vm_id=vm_id, node_ip="10.0.0.1", libvirt_uuid="dom-1")

        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()

        await vm_console(ws, vm_id=uuid.uuid4(), ticket=token)  # different vm_id

        ws.close.assert_awaited_once_with(code=4401, reason="invalid or expired ticket")

    @pytest.mark.asyncio
    async def test_node_unreachable_closes_4503(self):
        from controller.api.routers.console import vm_console

        vm_id = uuid.uuid4()
        token = console_tickets.issue_ticket(vm_id=vm_id, node_ip="10.0.0.1", libvirt_uuid="dom-1")

        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()

        with patch("controller.api.routers.console.websockets.connect", side_effect=OSError("refused")):
            await vm_console(ws, vm_id=vm_id, ticket=token)

        ws.close.assert_awaited_once_with(code=4503, reason="node offline")

    @pytest.mark.asyncio
    async def test_valid_ticket_connects_to_agent_console_uri(self):
        from controller.api.routers.console import vm_console
        from fastapi import WebSocketDisconnect

        vm_id = uuid.uuid4()
        token = console_tickets.issue_ticket(
            vm_id=vm_id, node_ip="10.5.5.5", libvirt_uuid="dom-xyz"
        )

        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.close = AsyncMock()
        ws.receive_bytes = AsyncMock(side_effect=[WebSocketDisconnect()])
        ws.send_bytes = AsyncMock()

        agent_ws = _FakeAgentWS(inbound=[])

        class _ConnectCM:
            async def __aenter__(self):
                return agent_ws

            async def __aexit__(self, *exc):
                return False

        with patch(
            "controller.api.routers.console.websockets.connect",
            return_value=_ConnectCM(),
        ) as mock_connect:
            await asyncio.wait_for(vm_console(ws, vm_id=vm_id, ticket=token), timeout=5)

        called_uri = mock_connect.call_args[0][0]
        assert called_uri == "ws://10.5.5.5:8091/console?uuid=dom-xyz"
