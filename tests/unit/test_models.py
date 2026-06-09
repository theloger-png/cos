"""Unit tests for common/models.py Pydantic model validation."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from common.models import (
    AgentCommand,
    AgentCommandResult,
    HeartbeatPayload,
    NetworkInfo,
    NodeInfo,
    NodeStatus,
    TenantInfo,
    VMInfo,
    VMStatus,
    VMTemplate,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class TestNodeInfo:
    def test_valid(self):
        node = NodeInfo(
            id=_uuid(),
            hostname="node-01",
            ip_address="10.0.0.1",
            cpu_total=16,
            cpu_used=4.5,
            ram_total_mb=32768,
            ram_used_mb=8192,
            disk_total_gb=500.0,
            disk_used_gb=100.0,
            status=NodeStatus.online,
            last_heartbeat=_now(),
        )
        assert node.status == NodeStatus.online
        assert node.cpu_total == 16

    def test_invalid_status(self):
        with pytest.raises(Exception):
            NodeInfo(
                id=_uuid(),
                hostname="x",
                ip_address="1.2.3.4",
                cpu_total=1,
                cpu_used=0.0,
                ram_total_mb=1024,
                ram_used_mb=0,
                disk_total_gb=10.0,
                disk_used_gb=0.0,
                status="bad_status",
                last_heartbeat=_now(),
            )


class TestVMInfo:
    def test_defaults(self):
        vm = VMInfo(
            id=_uuid(),
            name="vm-01",
            tenant_id=_uuid(),
            node_id=_uuid(),
            cpu_cores=2,
            ram_mb=2048,
            disk_gb=20,
            status=VMStatus.stopped,
            created_at=_now(),
        )
        assert vm.ip_addresses == []
        assert vm.template_id is None

    def test_with_template(self):
        tid = _uuid()
        vm = VMInfo(
            id=_uuid(),
            name="vm-02",
            tenant_id=_uuid(),
            node_id=_uuid(),
            cpu_cores=4,
            ram_mb=4096,
            disk_gb=40,
            status=VMStatus.running,
            ip_addresses=["192.168.1.10"],
            template_id=tid,
            created_at=_now(),
        )
        assert vm.template_id == tid
        assert vm.ip_addresses == ["192.168.1.10"]


class TestHeartbeatPayload:
    def test_valid(self):
        hb = HeartbeatPayload(
            node_id=_uuid(),
            timestamp=_now(),
            cpu_used=30.0,
            ram_used_mb=4096,
            disk_used_gb=50.0,
            vm_statuses={"abc-uuid": VMStatus.running},
        )
        assert hb.cpu_used == 30.0

    def test_empty_vm_statuses(self):
        hb = HeartbeatPayload(
            node_id=_uuid(),
            timestamp=_now(),
            cpu_used=0.0,
            ram_used_mb=0,
            disk_used_gb=0.0,
        )
        assert hb.vm_statuses == {}


class TestAgentCommand:
    def test_default_payload(self):
        cmd = AgentCommand(command="vm_list")
        assert cmd.payload == {}

    def test_with_payload(self):
        cmd = AgentCommand(command="vm_start", payload={"libvirt_uuid": "abc"})
        assert cmd.payload["libvirt_uuid"] == "abc"

    def test_roundtrip_json(self):
        cmd = AgentCommand(command="node_stats", payload={})
        restored = AgentCommand.model_validate_json(cmd.model_dump_json())
        assert restored.command == "node_stats"


class TestAgentCommandResult:
    def test_success(self):
        r = AgentCommandResult(success=True, output="done")
        assert r.error is None

    def test_failure(self):
        r = AgentCommandResult(success=False, output="", error="timeout")
        assert not r.success
        assert r.error == "timeout"


class TestVMTemplate:
    def test_valid(self):
        tpl = VMTemplate(
            id=_uuid(),
            name="ubuntu-22.04",
            description="Ubuntu 22.04 LTS",
            cpu_cores=2,
            ram_mb=2048,
            disk_gb=20,
            os_type="linux",
            image_path="/images/ubuntu-22.04.qcow2",
        )
        assert tpl.os_type == "linux"


class TestNetworkInfo:
    def test_valid(self):
        net = NetworkInfo(
            id=_uuid(),
            tenant_id=_uuid(),
            name="prod-net",
            vlan_id=100,
            cidr="10.10.0.0/24",
            gateway="10.10.0.1",
        )
        assert net.vlan_id == 100


class TestTenantInfo:
    def test_valid(self):
        t = TenantInfo(
            id=_uuid(),
            name="acme",
            email="ops@acme.com",
            active=True,
            created_at=_now(),
        )
        assert t.active is True
