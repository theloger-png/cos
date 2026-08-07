"""Unit tests for network create/delete endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from controller.db.models import Network, Tenant


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


def _create_session() -> AsyncMock:
    """Build a session mock sufficient for create_network's persistence calls."""
    session = AsyncMock()
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
# create_network tests
# ---------------------------------------------------------------------------

class TestNetworkCidrGatewayOptional:
    @pytest.mark.asyncio
    async def test_create_network_without_cidr_gateway_returns_201_with_nulls(self):
        from controller.api.routers.networks import create_network, NetworkCreate

        tenant = _make_tenant()
        session = _create_session()

        body = NetworkCreate(name="l2-net", vlan_id=200)

        result = await create_network(body=body, session=session, auth=(None, tenant))

        assert result.vlan_id == 200
        assert result.cidr is None
        assert result.gateway is None

    @pytest.mark.asyncio
    async def test_create_network_with_cidr_gateway_unchanged(self):
        from controller.api.routers.networks import create_network, NetworkCreate

        tenant = _make_tenant()
        session = _create_session()

        body = NetworkCreate(name="full-net", vlan_id=100, cidr="10.0.0.0/24", gateway="10.0.0.1")

        result = await create_network(body=body, session=session, auth=(None, tenant))

        assert result.vlan_id == 100
        assert result.cidr == "10.0.0.0/24"
        assert result.gateway == "10.0.0.1"


# ---------------------------------------------------------------------------
# delete_network tests
# ---------------------------------------------------------------------------

class TestDeleteNetwork:
    @pytest.mark.asyncio
    async def test_successful_delete_removes_network(self):
        from controller.api.routers.networks import delete_network

        tenant = _make_tenant()
        network = _make_network(tenant.id)

        net_result = MagicMock()
        net_result.scalar_one_or_none.return_value = network

        session = AsyncMock()
        session.execute = AsyncMock(return_value=net_result)
        session.delete = AsyncMock()
        session.commit = AsyncMock()

        await delete_network(network_id=network.id, session=session, auth=(None, None))

        session.delete.assert_awaited_once_with(network)
        session.commit.assert_awaited_once()

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
