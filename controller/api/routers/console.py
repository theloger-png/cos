"""VM serial console: one-time ticket issuance and WebSocket byte relay.

Flow: browser WebSocket -> nginx -> this WS endpoint -> agent /console
WebSocket -> libvirt console stream. This module never touches libvirt
directly (hard rule); it only proxies raw bytes to/from the agent's own
/console WebSocket, opened via AgentClient.console_uri().
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from common.models import VMStatus
from controller.agent_client.client import AgentClient
from controller.api.deps import current_auth, db_session
from controller.console_tickets import TICKET_TTL_SECONDS, consume_ticket, issue_ticket
from controller.db.models import APIKey, Node, Tenant, VM
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/vms", tags=["console"])

# Close codes in the private-use WebSocket range (4000-4999). 4404/4409/4500
# are mirrored verbatim when the agent applies them (see agent/ws_server.py),
# so the browser sees the real reason regardless of which hop produced it.
_CLOSE_INVALID_TICKET = 4401
_CLOSE_NODE_OFFLINE = 4503


class ConsoleTicketResponse(BaseModel):
    ticket: str
    expires_in: int


async def _get_vm_or_404(session: AsyncSession, vm_id: uuid.UUID) -> VM:
    result = await session.execute(select(VM).where(VM.id == vm_id))
    vm = result.scalar_one_or_none()
    if not vm:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="VM not found")
    return vm


async def _get_node_or_404(session: AsyncSession, node_id: uuid.UUID) -> Node:
    result = await session.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return node


@router.post("/{vm_id}/console-ticket", response_model=ConsoleTicketResponse)
async def create_console_ticket(
    vm_id: uuid.UUID,
    session: AsyncSession = Depends(db_session),
    auth: tuple[APIKey | None, Tenant | None] = Depends(current_auth),
) -> ConsoleTicketResponse:
    """Issue a short-lived, single-use ticket for opening the VM's console WebSocket.

    Normal auth (JWT or X-API-Key) and the usual tenant ownership check
    apply here, exactly like the other /vms endpoints. The ticket exists
    because the browser cannot attach that same credential to a WebSocket
    handshake; the WS endpoint below consumes the ticket instead.
    """
    _, tenant = auth
    vm = await _get_vm_or_404(session, vm_id)
    if tenant is not None and vm.tenant_id != tenant.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if vm.status != VMStatus.running.value or not vm.libvirt_uuid:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="VM is not running")

    node = await _get_node_or_404(session, vm.node_id)
    ticket = issue_ticket(vm_id=vm.id, node_ip=node.ip_address, libvirt_uuid=vm.libvirt_uuid)
    return ConsoleTicketResponse(ticket=ticket, expires_in=TICKET_TTL_SECONDS)


@router.websocket("/{vm_id}/console")
async def vm_console(websocket: WebSocket, vm_id: uuid.UUID, ticket: str) -> None:
    """Relay raw console bytes between the browser and the owning node's agent.

    Path: /api/v1/vms/{vm_id}/console?ticket=<ticket from console-ticket>.
    The ticket is validated and consumed here (never logged). Never calls
    libvirt directly (hard rule) - this only proxies bytes to/from the
    agent's own /console WebSocket.
    """
    await websocket.accept()

    ticket_data = consume_ticket(ticket, vm_id)
    if ticket_data is None:
        await websocket.close(code=_CLOSE_INVALID_TICKET, reason="invalid or expired ticket")
        return

    agent_uri = AgentClient().console_uri(ticket_data.node_ip, ticket_data.libvirt_uuid)
    try:
        async with websockets.connect(agent_uri, open_timeout=10) as agent_ws:
            await _relay(websocket, agent_ws)
    except (OSError, WebSocketException) as exc:
        logger.warning("console relay: agent unreachable for vm %s: %s", vm_id, exc)
        with contextlib.suppress(Exception):
            await websocket.close(code=_CLOSE_NODE_OFFLINE, reason="node offline")
    except Exception:
        logger.exception("console relay: unexpected error for vm %s", vm_id)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="internal error")


async def _relay(client_ws: WebSocket, agent_ws) -> None:
    """Pump raw bytes both ways until either side closes, then close the other.

    The agent's own close code/reason (e.g. 4409 "VM is not running", set
    after the ticket already looked valid) is propagated to the browser
    verbatim, so the frontend can tell a stale ticket from a VM that
    stopped between ticket issuance and connect.
    """

    async def browser_to_agent() -> None:
        try:
            while True:
                data = await client_ws.receive_bytes()
                await agent_ws.send(data)
        except (WebSocketDisconnect, ConnectionClosed):
            pass

    async def agent_to_browser() -> None:
        try:
            async for data in agent_ws:
                await client_ws.send_bytes(data)
        except ConnectionClosed:
            pass

    tasks = [asyncio.create_task(browser_to_agent()), asyncio.create_task(agent_to_browser())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t

    close_code = agent_ws.close_code or 1000
    close_reason = agent_ws.close_reason or ""
    with contextlib.suppress(Exception):
        await client_ws.close(code=close_code, reason=close_reason)
