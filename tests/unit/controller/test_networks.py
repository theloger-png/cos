"""Unit tests for network create/delete VLAN broadcast logic."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controller.db.models import Network, Node, Tenant
from common.models import AgentCommandResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tenant() -> Tenant:
    t = Tenant()
    t.id = uuid.uuid4()
    t.name = "acme"
    t.email = "acme@example.com"
    t.created_at = datetime.now(timezone.utc)
    return t


def _make_network(tenant_id: uuid.UUID, vlan_id: int = 100) -> Network:
    n = Network()
    n.id = uuid.uuid4()
    n.tenant_id = tenant_id
    n.name = "net1"
    n.vlan_id = vlan_id
    n.cidr = "10.0.0.0/24"
    n.gateway = "10.0.0.1"
    n.created_at = datetime.now(timezone.utc)
    return n


def _make_node(ip: str = "10.0.0.10", hostname: str = "node1") -> Node:
    n = Node()
    n.id = uuid.uuid4()
    n.hostname = hostname
    n.ip_address = ip
    n.cpu_total = 8
    n.cpu_used = 0.0
    n.ram_total_mb = 16384
    n.ram_used_mb = 0
    n.disk_total_gb = 500.0
    n.disk_used_gb = 0.0
    n.status = "online"
    n.last_heartbeat = datetime.now(timezone.utc)
    n.nos_api_key = ""
    n.created_at = datetime.now(timezone.utc)
    return n


def _ok_result() -> AgentCommandResult:
    return AgentCommandResult(success=True, output="configured")


def _fail_result(error: str = "timeout") -> AgentCommandResult:
    return AgentCommandResult(success=False, output="", error=error)


def _session_with_nodes(nodes: list[Node], *, tenant: Tenant | None = None) -> AsyncMock:
    """Build a session mock that returns *nodes* for the online-nodes query."""
    nodes_result = MagicMock()
    nodes_result.scalars.return_value.all.return_value = nodes

    if tenant is not None:
        tenant_result = MagicMock()
        tenant_result.scalar_one_or_none.return_value = tenant
        execute_results = [tenant_result, nodes_result]
    else:
        execute_results = [nodes_result]

    session = AsyncMock()
    session.execute = AsyncMock(side_effect=execute_results)
    session.add = MagicMock()
    session.commit = AsyncMock()

    async def _fake_refresh(obj):
        if not obj.id:
            obj.id = uuid.uuid4()
        if not obj.created_at:
            obj.created_at = datetime.now(timezone.utc)

    session.refresh = _fake_refresh
    return session


# ---------------------------------------------------------------------------
# create_network broadcast tests
# ---------------------------------------------------------------------------

class TestCreateNetworkBroadcast:
    @pytest.mark.asyncio
    async def test_broadcasts_configure_vlan_to_online_nodes(self):
        from controller.api.routers.networks import create_network, NetworkCreate

        tenant = _make_tenant()
        node1 = _make_node("10.0.0.1", "node1")
        node2 = _make_node("10.0.0.2", "node2")
        session = _session_with_nodes([node1, node2])

        body = NetworkCreate(name="net", vlan_id=100, cidr="10.0.0.0/24", gateway="10.0.0.1")

        with patch("controller.api.routers.networks._agent_client") as mock_agent:
            mock_agent.send_command = AsyncMock(return_value=_ok_result())
            result = await create_network(body=body, session=session, auth=(None, tenant))

        assert result.vlan_id == 100
        assert mock_agent.send_command.call_count == 2
        calls = {c.kwargs["node_ip"] for c in mock_agent.send_command.call_args_list}
        assert calls == {"10.0.0.1", "10.0.0.2"}
        for c in mock_agent.send_command.call_args_list:
            assert c.kwargs["command"] == "configure_vlan"
            assert c.kwargs["payload"] == {"vlan_id": 100}

    @pytest.mark.asyncio
    async def test_no_nodes_online_succeeds_with_no_broadcast(self):
        from controller.api.routers.networks import create_network, NetworkCreate

        tenant = _make_tenant()
        session = _session_with_nodes([])

        body = NetworkCreate(name="net", vlan_id=50, cidr="10.5.0.0/24", gateway="10.5.0.1")

        with patch("controller.api.routers.networks._agent_client") as mock_agent:
            mock_agent.send_command = AsyncMock(return_value=_ok_result())
            result = await create_network(body=body, session=session, auth=(None, tenant))

        assert result.vlan_id == 50
        mock_agent.send_command.assert_not_called()
        assert result.warnings == []

    @pytest.mark.asyncio
    async def test_partial_node_failure_returns_warnings_not_exception(self):
        from controller.api.routers.networks import create_network, NetworkCreate

        tenant = _make_tenant()
        node1 = _make_node("10.0.0.1", "node1")
        node2 = _make_node("10.0.0.2", "node2")
        session = _session_with_nodes([node1, node2])

        body = NetworkCreate(name="net", vlan_id=200, cidr="10.20.0.0/24", gateway="10.20.0.1")

        with patch("controller.api.routers.networks._agent_client") as mock_agent:
            mock_agent.send_command = AsyncMock(
                side_effect=[_ok_result(), _fail_result("timeout")]
            )
            result = await create_network(body=body, session=session, auth=(None, tenant))

        assert result.vlan_id == 200
        assert len(result.warnings) == 1
        assert "node2" in result.warnings[0] or "10.0.0.2" in result.warnings[0]

    @pytest.mark.asyncio
    async def test_all_nodes_fail_returns_all_warnings(self):
        from controller.api.routers.networks import create_network, NetworkCreate

        tenant = _make_tenant()
        node1 = _make_node("10.0.0.1", "node1")
        node2 = _make_node("10.0.0.2", "node2")
        session = _session_with_nodes([node1, node2])

        body = NetworkCreate(name="net", vlan_id=300, cidr="10.30.0.0/24", gateway="10.30.0.1")

        with patch("controller.api.routers.networks._agent_client") as mock_agent:
            mock_agent.send_command = AsyncMock(return_value=_fail_result("refused"))
            result = await create_network(body=body, session=session, auth=(None, tenant))

        assert len(result.warnings) == 2

    @pytest.mark.asyncio
    async def test_db_insert_happens_before_broadcast(self):
        """The network row must be committed before we send to agents."""
        from controller.api.routers.networks import create_network, NetworkCreate

        tenant = _make_tenant()
        call_order: list[str] = []

        node = _make_node()
        nodes_result = MagicMock()
        nodes_result.scalars.return_value.all.return_value = [node]

        session = AsyncMock()
        session.execute = AsyncMock(return_value=nodes_result)
        session.add = MagicMock(side_effect=lambda _: call_order.append("add"))
        session.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

        async def _fake_refresh(obj):
            obj.id = uuid.uuid4()
            obj.created_at = datetime.now(timezone.utc)

        session.refresh = _fake_refresh

        body = NetworkCreate(name="net", vlan_id=10, cidr="10.1.0.0/24", gateway="10.1.0.1")

        with patch("controller.api.routers.networks._agent_client") as mock_agent:
            mock_agent.send_command = AsyncMock(
                side_effect=lambda **_: call_order.append("broadcast") or _ok_result()
            )
            await create_network(body=body, session=session, auth=(None, tenant))

        assert call_order.index("commit") < call_order.index("broadcast")


# ---------------------------------------------------------------------------
# delete_network broadcast tests
# ---------------------------------------------------------------------------

class TestDeleteNetworkBroadcast:
    @pytest.mark.asyncio
    async def test_broadcasts_remove_vlan_to_online_nodes(self):
        from controller.api.routers.networks import delete_network

        tenant = _make_tenant()
        network = _make_network(tenant.id, vlan_id=100)

        net_result = MagicMock()
        net_result.scalar_one_or_none.return_value = network
        nodes_result = MagicMock()
        node1 = _make_node("10.0.0.1", "node1")
        nodes_result.scalars.return_value.all.return_value = [node1]

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[net_result, nodes_result])
        session.delete = AsyncMock()
        session.commit = AsyncMock()

        with patch("controller.api.routers.networks._agent_client") as mock_agent:
            mock_agent.send_command = AsyncMock(return_value=_ok_result())
            await delete_network(network_id=network.id, session=session, auth=(None, None))

        mock_agent.send_command.assert_awaited_once()
        call = mock_agent.send_command.call_args
        assert call.kwargs["command"] == "remove_vlan"
        assert call.kwargs["payload"] == {"vlan_id": 100}

    @pytest.mark.asyncio
    async def test_delete_best_effort_does_not_raise_on_node_failure(self):
        from controller.api.routers.networks import delete_network

        tenant = _make_tenant()
        network = _make_network(tenant.id)

        net_result = MagicMock()
        net_result.scalar_one_or_none.return_value = network
        nodes_result = MagicMock()
        nodes_result.scalars.return_value.all.return_value = [_make_node()]

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[net_result, nodes_result])
        session.delete = AsyncMock()
        session.commit = AsyncMock()

        with patch("controller.api.routers.networks._agent_client") as mock_agent:
            mock_agent.send_command = AsyncMock(return_value=_fail_result("timeout"))
            # Should not raise
            await delete_network(network_id=network.id, session=session, auth=(None, None))

        session.delete.assert_awaited_once_with(network)

    @pytest.mark.asyncio
    async def test_offline_nodes_not_queried(self):
        """Only nodes with status='online' get the remove_vlan command."""
        from controller.api.routers.networks import delete_network

        tenant = _make_tenant()
        network = _make_network(tenant.id)

        net_result = MagicMock()
        net_result.scalar_one_or_none.return_value = network
        nodes_result = MagicMock()
        nodes_result.scalars.return_value.all.return_value = []  # no online nodes

        session = AsyncMock()
        session.execute = AsyncMock(side_effect=[net_result, nodes_result])
        session.delete = AsyncMock()
        session.commit = AsyncMock()

        with patch("controller.api.routers.networks._agent_client") as mock_agent:
            mock_agent.send_command = AsyncMock(return_value=_ok_result())
            await delete_network(network_id=network.id, session=session, auth=(None, None))

        mock_agent.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_delete_nonexistent_network_raises_404(self):
        from controller.api.routers.networks import delete_network
        from fastapi import HTTPException

        net_result = MagicMock()
        net_result.scalar_one_or_none.return_value = None
        session = AsyncMock()
        session.execute = AsyncMock(return_value=net_result)

        with pytest.raises(HTTPException) as exc_info:
            await delete_network(network_id=uuid.uuid4(), session=session, auth=(None, None))

        assert exc_info.value.status_code == 404
