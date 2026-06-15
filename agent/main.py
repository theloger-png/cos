"""COS agent entry point — starts WebSocket server and heartbeat loop."""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import socket
import uuid

import httpx
import uvicorn
from agent.config import settings
from agent.libvirt_driver import LibvirtDriver
from agent.ws_server import app as ws_app
from common.models import VMStatus

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

_libvirt = LibvirtDriver(uri=settings.libvirt_uri, bridge=settings.vm_bridge)
_active_node_id: str | None = None


def _local_hostname() -> str:
    return socket.gethostname()


def _local_ip() -> str:
    """Best-effort detection of the primary outbound IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


async def _register_node(client: httpx.AsyncClient) -> None:
    """Register this node with the controller and persist the assigned node_id."""
    global _active_node_id
    stats = _libvirt.get_node_stats()
    payload = {
        "hostname": _local_hostname(),
        "ip_address": _local_ip(),
        "cpu_total": len(__import__("psutil").cpu_percent(percpu=True)),
        "ram_total_mb": stats["ram_total_mb"],
        "disk_total_gb": stats["disk_total_gb"],
        "nos_api_key": settings.nos_api_key,
    }
    try:
        resp = await client.post(
            f"{settings.controller_url}/api/v1/nodes",
            json=payload,
            headers={"X-API-Key": settings.controller_api_key},
            timeout=10,
        )
        if resp.status_code in (200, 201):
            node_id = resp.json()["id"]
            node_id_path = "/opt/cos/node_id"
            with open(node_id_path, "w") as f:
                f.write(node_id)
            _active_node_id = node_id
            logger.info(
                "Node registered with controller (status %d), node_id=%s",
                resp.status_code,
                node_id,
            )
        else:
            logger.warning("Node registration returned status %d", resp.status_code)
    except Exception as exc:
        logger.error("Node registration failed: %s", exc)


async def _heartbeat_loop(client: httpx.AsyncClient) -> None:
    """Send periodic heartbeats to the controller."""
    while True:
        await asyncio.sleep(settings.heartbeat_interval_seconds)
        try:
            stats = _libvirt.get_node_stats()
            vms = _libvirt.list_vms()

            # Map libvirt domain states to VMStatus
            _STATE_MAP = {
                1: VMStatus.running,  # VIR_DOMAIN_RUNNING
                3: VMStatus.paused,   # VIR_DOMAIN_PAUSED
                5: VMStatus.stopped,  # VIR_DOMAIN_SHUTOFF
            }
            vm_statuses = {
                v["uuid"]: _STATE_MAP.get(v["state"], VMStatus.error).value
                for v in vms
            }

            payload = {
                "node_id": _active_node_id,
                "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                "cpu_used": stats["cpu_percent"],
                "ram_used_mb": stats["ram_used_mb"],
                "disk_used_gb": stats["disk_used_gb"],
                "vm_statuses": vm_statuses,
            }
            resp = await client.post(
                f"{settings.controller_url}/api/v1/nodes/{_active_node_id}/heartbeat",
                json=payload,
                headers={"X-API-Key": settings.controller_api_key},
                timeout=10,
            )
            if resp.status_code not in (200, 201, 204):
                logger.warning("Heartbeat returned status %d", resp.status_code)
            else:
                logger.debug("Heartbeat OK")
        except Exception as exc:
            logger.error("Heartbeat error: %s", exc)


async def _run_server() -> None:
    config = uvicorn.Config(
        ws_app,
        host="0.0.0.0",
        port=settings.ws_port,
        log_level="info",
    )
    server = uvicorn.Server(config)
    await server.serve()


async def main() -> None:
    async with httpx.AsyncClient() as client:
        await _register_node(client)
        await asyncio.gather(
            _run_server(),
            _heartbeat_loop(client),
        )


if __name__ == "__main__":
    asyncio.run(main())
