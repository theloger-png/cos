"""Unit tests for the VM scheduler node-selection logic."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from controller.scheduler.scheduler import Scheduler


def _make_node(
    *,
    cpu_total: int = 16,
    cpu_used: float = 0.0,
    ram_total_mb: int = 32768,
    ram_used_mb: int = 0,
    disk_total_gb: float = 500.0,
    disk_used_gb: float = 0.0,
    status: str = "online",
    ip_address: str = "10.0.0.1",
) -> MagicMock:
    node = MagicMock()
    node.id = uuid.uuid4()
    node.hostname = "node-test"
    node.ip_address = ip_address
    node.cpu_total = cpu_total
    node.cpu_used = cpu_used
    node.ram_total_mb = ram_total_mb
    node.ram_used_mb = ram_used_mb
    node.disk_total_gb = disk_total_gb
    node.disk_used_gb = disk_used_gb
    node.status = status
    node.last_heartbeat = datetime.now(timezone.utc)
    return node


class TestScheduler:
    def test_returns_none_when_no_nodes(self):
        s = Scheduler([])
        assert s.select_node(2, 1024, 10) is None

    def test_returns_none_when_all_offline(self):
        node = _make_node(status="offline")
        s = Scheduler([node])
        assert s.select_node(2, 1024, 10) is None

    def test_returns_none_when_insufficient_ram(self):
        node = _make_node(ram_total_mb=1024, ram_used_mb=900)
        s = Scheduler([node])
        assert s.select_node(2, 512, 10) is None

    def test_returns_none_when_insufficient_disk(self):
        node = _make_node(disk_total_gb=50.0, disk_used_gb=45.0)
        s = Scheduler([node])
        assert s.select_node(2, 1024, 20) is None

    def test_returns_none_when_insufficient_cpu(self):
        node = _make_node(cpu_total=4, cpu_used=3.5)
        s = Scheduler([node])
        assert s.select_node(2, 1024, 10) is None

    def test_selects_only_available_node(self):
        node = _make_node(ram_total_mb=8192, ram_used_mb=2048)
        s = Scheduler([node])
        result = s.select_node(2, 4096, 10)
        assert result is not None
        assert result.ip_address == node.ip_address

    def test_selects_node_with_most_free_ram_ratio(self):
        # node_a has 50% RAM free, node_b has 80% RAM free → pick node_b
        node_a = _make_node(
            ram_total_mb=8192,
            ram_used_mb=4096,
            ip_address="10.0.0.1",
        )
        node_b = _make_node(
            ram_total_mb=8192,
            ram_used_mb=1638,
            ip_address="10.0.0.2",
        )
        s = Scheduler([node_a, node_b])
        result = s.select_node(1, 512, 5)
        assert result is not None
        assert result.ip_address == "10.0.0.2"

    def test_skips_maintenance_nodes(self):
        node = _make_node(status="maintenance")
        s = Scheduler([node])
        assert s.select_node(1, 512, 5) is None

    def test_returned_node_info_fields(self):
        node = _make_node(cpu_total=32, ram_total_mb=65536, disk_total_gb=1000.0)
        s = Scheduler([node])
        result = s.select_node(4, 4096, 50)
        assert result is not None
        assert result.cpu_total == 32
        assert result.ram_total_mb == 65536
        assert result.disk_total_gb == 1000.0
