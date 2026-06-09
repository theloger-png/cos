"""Simple resource-aware VM scheduler."""

from __future__ import annotations

from common.models import NodeInfo, NodeStatus
from controller.db.models import Node


class Scheduler:
    """Selects the best-fit node for a VM placement request."""

    def __init__(self, nodes: list[Node]) -> None:
        self._nodes = nodes

    def select_node(self, cpu_cores: int, ram_mb: int, disk_gb: int) -> NodeInfo | None:
        """Return the node with the most available RAM ratio that can fit the VM.

        Nodes are ranked by (available_ram / total_ram) descending.  The first
        node that satisfies all three resource requirements is returned, or None
        if no suitable node exists.
        """
        candidates: list[tuple[float, Node]] = []

        for node in self._nodes:
            if node.status != NodeStatus.online.value:
                continue

            available_ram = node.ram_total_mb - node.ram_used_mb
            available_disk = node.disk_total_gb - node.disk_used_gb
            available_cpu = node.cpu_total - node.cpu_used

            if available_ram < ram_mb:
                continue
            if available_disk < disk_gb:
                continue
            if available_cpu < cpu_cores:
                continue

            ram_ratio = available_ram / node.ram_total_mb if node.ram_total_mb else 0.0
            candidates.append((ram_ratio, node))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1]

        return NodeInfo(
            id=best.id,
            hostname=best.hostname,
            ip_address=best.ip_address,
            cpu_total=best.cpu_total,
            cpu_used=best.cpu_used,
            ram_total_mb=best.ram_total_mb,
            ram_used_mb=best.ram_used_mb,
            disk_total_gb=best.disk_total_gb,
            disk_used_gb=best.disk_used_gb,
            status=NodeStatus(best.status),
            last_heartbeat=best.last_heartbeat,
        )
