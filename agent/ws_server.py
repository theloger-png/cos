"""WebSocket server that receives and dispatches agent commands."""

from __future__ import annotations

import json
import logging

from agent.libvirt_driver import LibvirtDriver
from agent.nos_driver import load_nos_driver
from agent.config import settings
from common.models import AgentCommand, AgentCommandResult
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

app = FastAPI(title="COS Agent")

_libvirt = LibvirtDriver(uri=settings.libvirt_uri, bridge=settings.vm_bridge)
_nos = load_nos_driver(
    base_url=settings.nos_api_url,
    api_key=settings.nos_api_key,
    api_key_file=settings.nos_api_key_file,
)


async def _dispatch(command: AgentCommand) -> AgentCommandResult:
    """Route a command to the appropriate driver and return the result."""
    cmd = command.command
    p = command.payload

    try:
        if cmd == "vm_create":
            libvirt_uuid = _libvirt.create_vm(
                name=p["name"],
                cpu_cores=p["cpu_cores"],
                ram_mb=p["ram_mb"],
                disk_gb=p["disk_gb"],
                image_path=p.get("image_path", ""),
                vlan_id=p.get("vlan_id"),
            )
            return AgentCommandResult(success=True, output=libvirt_uuid)

        elif cmd == "vm_start":
            ok = _libvirt.start_vm(p["libvirt_uuid"])
            return AgentCommandResult(success=ok, output="started" if ok else "", error=None if ok else "start failed")

        elif cmd == "vm_stop":
            ok = _libvirt.stop_vm(p["libvirt_uuid"])
            return AgentCommandResult(success=ok, output="stopped" if ok else "", error=None if ok else "stop failed")

        elif cmd == "vm_reboot":
            ok = _libvirt.reboot_vm(p["libvirt_uuid"])
            return AgentCommandResult(success=ok, output="rebooted" if ok else "", error=None if ok else "reboot failed")

        elif cmd == "vm_destroy":
            ok = _libvirt.destroy_vm(p["libvirt_uuid"])
            return AgentCommandResult(success=ok, output="destroyed" if ok else "", error=None if ok else "destroy failed")

        elif cmd == "vm_migrate":
            ok = _libvirt.migrate_vm(p["libvirt_uuid"], p["target_uri"])
            return AgentCommandResult(success=ok, output="migrated" if ok else "", error=None if ok else "migration failed")

        elif cmd == "vm_list":
            vms = _libvirt.list_vms()
            return AgentCommandResult(success=True, output=json.dumps(vms))

        elif cmd == "node_stats":
            stats = _libvirt.get_node_stats()
            return AgentCommandResult(success=True, output=json.dumps(stats))

        elif cmd == "configure_vlan":
            ok = await _nos.configure_vlan(p["vlan_id"])
            return AgentCommandResult(success=ok, output="configured" if ok else "", error=None if ok else "configure_vlan failed")

        elif cmd == "remove_vlan":
            ok = await _nos.remove_vlan(p["vlan_id"])
            return AgentCommandResult(success=ok, output="removed" if ok else "", error=None if ok else "remove_vlan failed")

        else:
            return AgentCommandResult(success=False, output="", error=f"unknown command: {cmd}")

    except Exception as exc:
        logger.exception("Error dispatching command %s", cmd)
        return AgentCommandResult(success=False, output="", error=str(exc))


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Accept a WebSocket connection, process a single AgentCommand, then close."""
    await websocket.accept()
    try:
        raw = await websocket.receive_text()
        command = AgentCommand.model_validate_json(raw)
        result = await _dispatch(command)
        await websocket.send_text(result.model_dump_json())
    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected before command was received")
    except Exception as exc:
        logger.error("WebSocket handler error: %s", exc)
        error_result = AgentCommandResult(success=False, output="", error=str(exc))
        try:
            await websocket.send_text(error_result.model_dump_json())
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
