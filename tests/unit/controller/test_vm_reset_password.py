"""Unit tests: POST /api/v1/vms/{id}/reset-password."""

from __future__ import annotations

import crypt
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from controller.api.routers.vms import (
    VMPasswordResetRequest,
    reset_vm_password,
)
from controller.db.models import Node, Tenant, VM, VMTemplate


def _tenant() -> Tenant:
    t = Tenant()
    t.id = uuid.uuid4()
    t.name = "acme"
    return t


def _node() -> Node:
    n = Node()
    n.id = uuid.uuid4()
    n.hostname = "node1"
    n.ip_address = "10.0.0.1"
    return n


def _vm(tenant: Tenant, node: Node, *, template_id=None, libvirt_uuid: str | None = "lv-uuid") -> VM:
    v = VM()
    v.id = uuid.uuid4()
    v.name = "web-01"
    v.tenant_id = tenant.id
    v.node_id = node.id
    v.template_id = template_id
    v.libvirt_uuid = libvirt_uuid
    v.status = "running"
    return v


def _template(user: str) -> VMTemplate:
    t = VMTemplate()
    t.id = uuid.uuid4()
    t.cloud_init_user = user
    return t


def _result(value) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _session(*values) -> AsyncMock:
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[_result(v) for v in values])
    return session


def _agent(success: bool = True, error: str | None = None) -> MagicMock:
    """Patchable AgentClient class whose instance returns a canned result."""
    result = MagicMock()
    result.success = success
    result.error = error
    instance = MagicMock()
    instance.send_command = AsyncMock(return_value=result)
    cls = MagicMock(return_value=instance)
    cls.instance = instance
    return cls


async def _call(session, agent_cls, *, body=None, tenant=None, vm_id=None):
    with patch("controller.api.routers.vms.AgentClient", agent_cls):
        return await reset_vm_password(
            vm_id=vm_id or uuid.uuid4(), body=body, session=session, auth=(None, tenant)
        )


class TestResetPassword:
    @pytest.mark.asyncio
    async def test_uses_template_user_and_sends_only_the_hash_to_the_agent(self):
        tenant, node = _tenant(), _node()
        tpl = _template("debian")
        vm = _vm(tenant, node, template_id=tpl.id)
        agent = _agent()

        response = await _call(_session(vm, tpl, node), agent)

        assert response.user == "debian"
        assert len(response.password) == 16

        ip, command, payload = agent.instance.send_command.call_args[0]
        assert ip == "10.0.0.1"
        assert command == "vm_set_password"
        assert payload["libvirt_uuid"] == "lv-uuid"
        assert payload["user"] == "debian"
        # Only a SHA-512 crypt hash of the returned password is sent, never the plaintext.
        assert payload["password_hash"].startswith("$6$")
        assert response.password not in payload.values()
        assert crypt.crypt(response.password, payload["password_hash"]) == payload["password_hash"]

    @pytest.mark.asyncio
    async def test_defaults_to_ubuntu_without_a_template(self):
        tenant, node = _tenant(), _node()
        vm = _vm(tenant, node, template_id=None)

        response = await _call(_session(vm, node), _agent())

        assert response.user == "ubuntu"

    @pytest.mark.asyncio
    async def test_explicit_user_overrides_template_and_skips_template_lookup(self):
        tenant, node = _tenant(), _node()
        vm = _vm(tenant, node, template_id=uuid.uuid4())
        session = _session(vm, node)  # only VM and node lookups are expected
        agent = _agent()

        response = await _call(session, agent, body=VMPasswordResetRequest(user="root"))

        assert response.user == "root"
        assert agent.instance.send_command.call_args[0][2]["user"] == "root"
        assert session.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_each_reset_generates_a_new_password(self):
        tenant, node = _tenant(), _node()
        first = await _call(_session(_vm(tenant, node), node), _agent())
        second = await _call(_session(_vm(tenant, node), node), _agent())
        assert first.password != second.password

    @pytest.mark.asyncio
    async def test_password_is_never_logged(self, caplog):
        tenant, node = _tenant(), _node()
        with caplog.at_level("DEBUG"):
            response = await _call(_session(_vm(tenant, node), node), _agent())
        assert response.password not in caplog.text


class TestResetPasswordErrors:
    @pytest.mark.asyncio
    async def test_unknown_vm_is_404(self):
        agent = _agent()
        with pytest.raises(HTTPException) as exc:
            await _call(_session(None), agent)
        assert exc.value.status_code == 404
        agent.instance.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_other_tenants_vm_is_403_and_agent_is_not_called(self):
        owner, node = _tenant(), _node()
        agent = _agent()
        with pytest.raises(HTTPException) as exc:
            await _call(_session(_vm(owner, node)), agent, tenant=_tenant())
        assert exc.value.status_code == 403
        agent.instance.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_vm_without_libvirt_uuid_is_409(self):
        tenant, node = _tenant(), _node()
        agent = _agent()
        with pytest.raises(HTTPException) as exc:
            await _call(_session(_vm(tenant, node, libvirt_uuid=None)), agent)
        assert exc.value.status_code == 409
        agent.instance.send_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_agent_failure_is_502_with_reason_and_without_the_new_password(self):
        tenant, node = _tenant(), _node()
        agent = _agent(success=False, error="The guest agent is not responding")
        with pytest.raises(HTTPException) as exc:
            await _call(_session(_vm(tenant, node), node), agent)
        assert exc.value.status_code == 502
        assert "The guest agent is not responding" in exc.value.detail
        sent_hash = agent.instance.send_command.call_args[0][2]["password_hash"]
        assert sent_hash not in exc.value.detail


class TestRequestValidation:
    @pytest.mark.parametrize("user", ["root", "ubuntu", "svc_user-1", "_apt"])
    def test_valid_usernames_are_accepted(self, user):
        assert VMPasswordResetRequest(user=user).user == user

    @pytest.mark.parametrize(
        "user", ["Root", "a b", "x; rm -rf /", "1user", "", "a" * 33, "user\n"]
    )
    def test_invalid_usernames_are_rejected(self, user):
        with pytest.raises(ValidationError):
            VMPasswordResetRequest(user=user)

    def test_user_is_optional(self):
        assert VMPasswordResetRequest().user is None
