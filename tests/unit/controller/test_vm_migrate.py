"""Unit tests: POST /api/v1/vms/{id}/migrate (timeout and reconciliation)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from controller.api.routers.vms import MigrateRequest, migrate_vm
from controller.db.models import Node, Tenant, VM


def _node(ip: str) -> Node:
    n = Node()
    n.id = uuid.uuid4()
    n.hostname = f"node-{ip}"
    n.ip_address = ip
    return n


def _vm(tenant: Tenant, node: Node, *, libvirt_uuid: str | None = "lv-uuid") -> VM:
    v = VM()
    v.id = uuid.uuid4()
    v.name = "web-01"
    v.tenant_id = tenant.id
    v.node_id = node.id
    v.libvirt_uuid = libvirt_uuid
    v.status = "running"
    return v


def _result(value) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _session(*values) -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[_result(v) for v in values])
    return session


def _reply(success: bool = True, output: str = "", error: str | None = None) -> MagicMock:
    r = MagicMock()
    r.success = success
    r.output = output
    r.error = error
    return r


def _agent(*replies) -> MagicMock:
    """Patchable AgentClient class; send_command returns *replies* in order."""
    instance = MagicMock()
    instance.send_command = AsyncMock(side_effect=list(replies))
    cls = MagicMock(return_value=instance)
    cls.instance = instance
    return cls


def _domains(state: int, libvirt_uuid: str = "lv-uuid") -> str:
    return json.dumps([{"uuid": libvirt_uuid, "name": "web-01", "state": state}])


async def _call(session, agent_cls, vm, dst, *, tenant=None):
    with patch("controller.api.routers.vms.AgentClient", agent_cls):
        return await migrate_vm(
            vm_id=vm.id,
            body=MigrateRequest(target_node_id=dst.id),
            session=session,
            auth=(None, tenant),
        )


@pytest.fixture
def setup():
    tenant = Tenant()
    tenant.id = uuid.uuid4()
    src, dst = _node("10.0.0.1"), _node("10.0.0.2")
    return tenant, src, dst, _vm(tenant, src)


class TestMigrateVm:
    @pytest.mark.asyncio
    async def test_success_waits_long_and_updates_node(self, setup):
        tenant, src, dst, vm = setup
        agent = _agent(_reply(True, "migrated"))

        result = await _call(_session(vm, src, dst), agent, vm, dst)

        assert result == {"ok": True}
        assert vm.node_id == dst.id
        assert vm.status == "running"
        (call,) = agent.instance.send_command.call_args_list
        assert call.args[:2] == (src.ip_address, "vm_migrate")
        assert call.kwargs["timeout_seconds"] == 3600

    @pytest.mark.asyncio
    async def test_failed_reply_but_running_on_destination_is_recorded(self, setup):
        tenant, src, dst, vm = setup
        agent = _agent(_reply(False, error="timeout"), _reply(True, _domains(1)))
        session = _session(vm, src, dst)

        result = await _call(session, agent, vm, dst)

        assert result == {"ok": True}
        assert vm.node_id == dst.id
        session.commit.assert_awaited()
        check = agent.instance.send_command.call_args_list[1]
        assert check.args[:3] == (dst.ip_address, "vm_list", {})

    @pytest.mark.asyncio
    async def test_retry_after_completed_migration_succeeds(self, setup):
        tenant, src, dst, vm = setup  # DB still says src, domain already on dst
        agent = _agent(
            _reply(False, error="Domain not found: no domain with matching uuid 'lv-uuid'"),
            _reply(True, _domains(1)),
        )

        assert await _call(_session(vm, src, dst), agent, vm, dst) == {"ok": True}
        assert vm.node_id == dst.id

    @pytest.mark.asyncio
    @pytest.mark.parametrize("check_reply", [
        _reply(True, "[]"),                       # domain not on destination
        _reply(True, _domains(5)),                # defined there but shut off
        _reply(True, _domains(1, "other-uuid")),  # a different domain is running
        _reply(False, error="timeout"),           # destination agent unreachable
        _reply(True, "not json"),                 # garbage output
        _reply(True, '{"uuid": "lv-uuid"}'),      # wrong shape
    ])
    async def test_failed_migration_not_on_destination_stays_502(self, setup, check_reply):
        tenant, src, dst, vm = setup
        agent = _agent(_reply(False, error="boom"), check_reply)
        session = _session(vm, src, dst)

        with pytest.raises(HTTPException) as exc:
            await _call(session, agent, vm, dst)

        assert exc.value.status_code == 502
        assert "Migration failed: boom" in exc.value.detail
        assert vm.node_id == src.id
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_other_tenant_is_403(self, setup):
        tenant, src, dst, vm = setup
        other = Tenant()
        other.id = uuid.uuid4()

        with pytest.raises(HTTPException) as exc:
            await _call(_session(vm), _agent(), vm, dst, tenant=other)

        assert exc.value.status_code == 403

    @pytest.mark.asyncio
    async def test_vm_without_libvirt_uuid_is_409(self, setup):
        tenant, src, dst, vm = setup
        vm.libvirt_uuid = None

        with pytest.raises(HTTPException) as exc:
            await _call(_session(vm), _agent(), vm, dst)

        assert exc.value.status_code == 409
