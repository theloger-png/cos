"""Unit tests for controller/api/routers/templates.py."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from controller.api.routers.templates import (
    FetchImageRequest,
    TemplateCreate,
    create_template,
    fetch_template_image,
)
from controller.db.models import Node, VMTemplate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _node(hostname: str, ip: str, status: str = "online") -> Node:
    n = Node()
    n.id = uuid.uuid4()
    n.hostname = hostname
    n.ip_address = ip
    n.status = status
    return n


def _template(
    *,
    image_path: str = "/var/lib/libvirt/images/old.img",
    image_url: str | None = None,
) -> VMTemplate:
    t = VMTemplate()
    t.id = uuid.uuid4()
    t.name = "ubuntu1"
    t.description = ""
    t.cpu_cores = 2
    t.ram_mb = 2048
    t.disk_gb = 20
    t.os_type = "ubuntu24.04"
    t.image_path = image_path
    t.image_url = image_url
    t.cloud_init_user = "ubuntu"
    t.created_at = datetime.now(timezone.utc)
    return t


def _result_single(value):
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _result_list(values):
    r = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = values
    r.scalars.return_value = scalars
    return r


def _agent_cls(per_ip: dict[str, tuple[bool, str | None]]):
    """Patchable AgentClient whose send_command result depends on node_ip."""
    instance = MagicMock()

    async def _send(node_ip, command, payload, timeout_seconds=30):
        success, error = per_ip.get(node_ip, (False, "no mock configured for this IP"))
        result = MagicMock()
        result.success = success
        result.error = error
        return result

    instance.send_command = AsyncMock(side_effect=_send)
    cls = MagicMock(return_value=instance)
    return cls


def _create_session_for_create_template() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    async def _fake_refresh(obj):
        if not obj.id:
            obj.id = uuid.uuid4()
        if not obj.created_at:
            obj.created_at = datetime.now(timezone.utc)

    session.refresh = AsyncMock(side_effect=_fake_refresh)
    return session


# ---------------------------------------------------------------------------
# create_template: image_path / image_url handling
# ---------------------------------------------------------------------------

class TestCreateTemplateImageFields:
    @pytest.mark.asyncio
    async def test_image_path_given_directly_is_used_as_is(self):
        session = _create_session_for_create_template()
        body = TemplateCreate(
            name="t1", cpu_cores=1, ram_mb=512, disk_gb=10, os_type="ubuntu24.04",
            image_path="/var/lib/libvirt/images/manual.img",
        )
        result = await create_template(body, session=session, auth=(None, None))
        assert result.image_path == "/var/lib/libvirt/images/manual.img"
        assert result.image_url is None

    @pytest.mark.asyncio
    async def test_image_path_derived_from_image_url_when_not_given(self):
        session = _create_session_for_create_template()
        body = TemplateCreate(
            name="t1", cpu_cores=1, ram_mb=512, disk_gb=10, os_type="ubuntu24.04",
            image_url="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        )
        result = await create_template(body, session=session, auth=(None, None))
        assert result.image_path == "/var/lib/libvirt/images/noble-server-cloudimg-amd64.img"
        assert result.image_url == body.image_url

    @pytest.mark.asyncio
    async def test_neither_image_path_nor_image_url_is_rejected(self):
        session = _create_session_for_create_template()
        body = TemplateCreate(name="t1", cpu_cores=1, ram_mb=512, disk_gb=10, os_type="ubuntu24.04")
        with pytest.raises(HTTPException) as exc_info:
            await create_template(body, session=session, auth=(None, None))
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_image_url_with_no_path_segment_is_rejected(self):
        session = _create_session_for_create_template()
        body = TemplateCreate(
            name="t1", cpu_cores=1, ram_mb=512, disk_gb=10, os_type="ubuntu24.04",
            image_url="https://example.com/",
        )
        with pytest.raises(HTTPException) as exc_info:
            await create_template(body, session=session, auth=(None, None))
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# fetch_template_image endpoint
# ---------------------------------------------------------------------------

class TestFetchTemplateImageEndpoint:
    @pytest.mark.asyncio
    async def test_template_not_found_is_404(self):
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_result_single(None))
        with pytest.raises(HTTPException) as exc_info:
            await fetch_template_image(
                uuid.uuid4(), body=None, session=session, auth=(None, None)
            )
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_no_url_anywhere_is_400(self):
        tpl = _template(image_url=None)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_result_single(tpl))
        with pytest.raises(HTTPException) as exc_info:
            await fetch_template_image(
                tpl.id, body=None, session=session, auth=(None, None)
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_non_http_url_is_400(self):
        tpl = _template(image_url=None)
        session = AsyncMock()
        session.execute = AsyncMock(return_value=_result_single(tpl))
        with pytest.raises(HTTPException) as exc_info:
            await fetch_template_image(
                tpl.id,
                body=FetchImageRequest(url="ftp://example.com/x.img"),
                session=session,
                auth=(None, None),
            )
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_no_online_nodes_is_409(self):
        tpl = _template(image_url="https://example.com/x.img")
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[_result_single(tpl), _result_list([])]
        )
        with pytest.raises(HTTPException) as exc_info:
            await fetch_template_image(
                tpl.id, body=None, session=session, auth=(None, None)
            )
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_all_nodes_succeed_updates_template_and_returns_results(self):
        tpl = _template(image_path="/var/lib/libvirt/images/old.img", image_url=None)
        n1, n2 = _node("node1", "10.0.0.1"), _node("node2", "10.0.0.2")
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[_result_single(tpl), _result_list([n1, n2])]
        )
        session.commit = AsyncMock()

        agent_cls = _agent_cls({
            "10.0.0.1": (True, None),
            "10.0.0.2": (True, None),
        })
        url = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
        with patch("controller.api.routers.templates.AgentClient", agent_cls):
            result = await fetch_template_image(
                tpl.id,
                body=FetchImageRequest(url=url),
                session=session,
                auth=(None, None),
            )

        assert tpl.image_url == url
        assert tpl.image_path == "/var/lib/libvirt/images/noble-server-cloudimg-amd64.img"
        assert result.image_path == tpl.image_path
        assert len(result.results) == 2
        assert all(r.success for r in result.results)
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_partial_success_still_updates_template(self):
        tpl = _template(image_path="/var/lib/libvirt/images/old.img", image_url=None)
        n1, n2 = _node("node1", "10.0.0.1"), _node("node2", "10.0.0.2")
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[_result_single(tpl), _result_list([n1, n2])]
        )
        session.commit = AsyncMock()

        agent_cls = _agent_cls({
            "10.0.0.1": (True, None),
            "10.0.0.2": (False, "no write permission on /var/lib/libvirt/images"),
        })
        url = "https://example.com/img.qcow2"
        with patch("controller.api.routers.templates.AgentClient", agent_cls):
            result = await fetch_template_image(
                tpl.id,
                body=FetchImageRequest(url=url),
                session=session,
                auth=(None, None),
            )

        assert tpl.image_url == url
        assert tpl.image_path == "/var/lib/libvirt/images/img.qcow2"
        successes = [r for r in result.results if r.success]
        failures = [r for r in result.results if not r.success]
        assert len(successes) == 1
        assert len(failures) == 1
        assert failures[0].error == "no write permission on /var/lib/libvirt/images"
        session.commit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_nodes_fail_does_not_update_template(self):
        tpl = _template(image_path="/var/lib/libvirt/images/old.img", image_url="https://old.example.com/old.img")
        n1 = _node("node1", "10.0.0.1")
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[_result_single(tpl), _result_list([n1])]
        )
        session.commit = AsyncMock()

        agent_cls = _agent_cls({"10.0.0.1": (False, "timeout")})
        with patch("controller.api.routers.templates.AgentClient", agent_cls):
            result = await fetch_template_image(
                tpl.id, body=None, session=session, auth=(None, None)
            )

        # Falls back to the template's own stored image_url since body had none.
        assert tpl.image_url == "https://old.example.com/old.img"
        assert tpl.image_path == "/var/lib/libvirt/images/old.img"  # unchanged
        assert all(not r.success for r in result.results)
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_falls_back_to_stored_image_url_when_body_has_none(self):
        stored_url = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
        tpl = _template(image_path="/var/lib/libvirt/images/noble-server-cloudimg-amd64.img", image_url=stored_url)
        n1 = _node("node1", "10.0.0.1")
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[_result_single(tpl), _result_list([n1])]
        )
        session.commit = AsyncMock()

        agent_cls = _agent_cls({"10.0.0.1": (True, None)})
        with patch("controller.api.routers.templates.AgentClient", agent_cls):
            result = await fetch_template_image(
                tpl.id, body=None, session=session, auth=(None, None)
            )

        assert result.results[0].success
        # Confirm the agent was actually called with the template's stored URL.
        agent_cls.return_value.send_command.assert_awaited_once()
        call_kwargs = agent_cls.return_value.send_command.call_args
        assert call_kwargs.args[2]["url"] == stored_url

    @pytest.mark.asyncio
    async def test_body_url_overrides_stored_image_url(self):
        tpl = _template(image_path="/var/lib/libvirt/images/old.img", image_url="https://old.example.com/old.img")
        n1 = _node("node1", "10.0.0.1")
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[_result_single(tpl), _result_list([n1])]
        )
        session.commit = AsyncMock()

        new_url = "https://new.example.com/new.img"
        agent_cls = _agent_cls({"10.0.0.1": (True, None)})
        with patch("controller.api.routers.templates.AgentClient", agent_cls):
            await fetch_template_image(
                tpl.id,
                body=FetchImageRequest(url=new_url),
                session=session,
                auth=(None, None),
            )

        assert tpl.image_url == new_url
        assert tpl.image_path == "/var/lib/libvirt/images/new.img"

    @pytest.mark.asyncio
    async def test_offline_nodes_are_excluded_from_fan_out(self):
        tpl = _template(image_path="/var/lib/libvirt/images/old.img", image_url=None)
        online = _node("node1", "10.0.0.1", status="online")
        # Only the online node should ever be queried for by the select(); the
        # session mock enforces this implicitly by only returning `online` -
        # this test mainly documents/locks in that behavior via the query
        # itself rather than needing to simulate an offline node object.
        session = AsyncMock()
        session.execute = AsyncMock(
            side_effect=[_result_single(tpl), _result_list([online])]
        )
        session.commit = AsyncMock()

        agent_cls = _agent_cls({"10.0.0.1": (True, None)})
        url = "https://example.com/img.qcow2"
        with patch("controller.api.routers.templates.AgentClient", agent_cls):
            result = await fetch_template_image(
                tpl.id,
                body=FetchImageRequest(url=url),
                session=session,
                auth=(None, None),
            )

        assert len(result.results) == 1
        assert result.results[0].hostname == "node1"
