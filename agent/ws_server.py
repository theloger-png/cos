"""WebSocket server that receives and dispatches agent commands.

Authentication note: this server has no authentication of its own (neither
/ws nor /console below check any credential). It relies entirely on network
reachability - the agent's WS port (8091) is expected to only be reachable
from the controller's network, the same trust model the existing /ws command
channel already uses. The console endpoint reuses that same (lack of)
authentication rather than inventing a new scheme for just this one path.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from agent.libvirt_driver import (
    ConsoleUnavailableError,
    DomainNotFoundError,
    DomainNotRunningError,
    LibvirtDriver,
    PasswordResetError,
)
from agent.config import settings
from common.models import AgentCommand, AgentCommandResult
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

app = FastAPI(title="COS Agent")

_libvirt = LibvirtDriver(uri=settings.libvirt_uri, bridge=settings.vm_bridge)


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
                cloud_init_user=p.get("cloud_init_user"),
                cloud_init_password_hash=p.get("cloud_init_password_hash"),
                ip_cidr=p.get("ip_cidr"),
                gateway=p.get("gateway"),
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

        elif cmd == "vm_set_password":
            # Raises PasswordResetError (message safe to show) on failure; the
            # generic handler below turns it into an error result.
            _libvirt.set_user_password(p["libvirt_uuid"], p["user"], p["password_hash"])
            return AgentCommandResult(success=True, output="password reset")

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

        elif cmd == "vm_get_config":
            config = _libvirt.get_vm_config(p["libvirt_uuid"])
            return AgentCommandResult(success=True, output=json.dumps(config))

        elif cmd == "vm_apply_config":
            new_config = _libvirt.apply_vm_config(
                p["libvirt_uuid"], p.get("changes", {})
            )
            return AgentCommandResult(success=True, output=json.dumps(new_config))

        else:
            return AgentCommandResult(success=False, output="", error=f"unknown command: {cmd}")

    except PasswordResetError as exc:
        # Expected, user-presentable failure (VM stopped, no guest agent...):
        # no traceback, and the payload (password hash) is never logged.
        logger.warning("Command %s failed: %s", cmd, exc)
        return AgentCommandResult(success=False, output="", error=str(exc))

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


# ---------------------------------------------------------------------------
# Serial console relay
# ---------------------------------------------------------------------------

# Close codes in the private-use WebSocket range (4000-4999), mirrored
# verbatim by the controller's relay so the browser sees the real reason.
_CLOSE_VM_NOT_FOUND = 4404
_CLOSE_VM_NOT_RUNNING = 4409
_CLOSE_CONSOLE_UNAVAILABLE = 4500


@app.websocket("/console")
async def console_endpoint(websocket: WebSocket, uuid: str) -> None:
    """Relay a running domain's pty console as raw binary WebSocket frames.

    Path: /console?uuid=<libvirt_uuid>. Only the controller is expected to
    connect here (see module docstring for the auth/trust model). Rejects
    with a 4xxx close code if the domain doesn't exist, isn't running, or
    has no usable console; otherwise proxies bytes in both directions until
    either side disconnects or the domain stops.
    """
    await websocket.accept()
    loop = asyncio.get_running_loop()

    try:
        session = await loop.run_in_executor(None, _libvirt.open_console, uuid, loop)
    except DomainNotFoundError:
        await websocket.close(code=_CLOSE_VM_NOT_FOUND, reason="VM not found")
        return
    except DomainNotRunningError:
        await websocket.close(code=_CLOSE_VM_NOT_RUNNING, reason="VM is not running")
        return
    except ConsoleUnavailableError as exc:
        logger.warning("console unavailable for %s: %s", uuid, exc)
        await websocket.close(code=_CLOSE_CONSOLE_UNAVAILABLE, reason="console unavailable")
        return
    except Exception:
        logger.exception("Unexpected error opening console for %s", uuid)
        await websocket.close(code=_CLOSE_CONSOLE_UNAVAILABLE, reason="internal error")
        return

    async def _browser_to_console() -> None:
        try:
            while True:
                data = await websocket.receive_bytes()
                session.write(data)
        except WebSocketDisconnect:
            pass

    async def _console_to_browser() -> None:
        while True:
            data = await session.inbound_queue.get()
            if data is None:
                return  # console closed (domain stopped, stream error, ...)
            await websocket.send_bytes(data)

    tasks = [asyncio.create_task(_browser_to_console()), asyncio.create_task(_console_to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        # session.close() joins the reader thread; keep it off the event loop.
        await loop.run_in_executor(None, session.close)
        with contextlib.suppress(Exception):
            await websocket.close()
