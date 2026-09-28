"""In-memory one-time tickets for the VM serial console WebSocket.

Tickets exist because a browser cannot attach an Authorization or X-API-Key
header to a WebSocket handshake. The REST endpoint that issues a ticket runs
through the normal auth dependency (JWT or X-API-Key) and tenant check; the
WebSocket endpoint then consumes that ticket instead of re-authenticating.

Kept in a process-local dict, not the database. A multi-controller HA
deployment (see STATUS.md "Controller HA") would need a shared store (for
example Redis) instead, since a ticket issued by one controller instance
would not be visible to another instance handling the WebSocket upgrade.
"""

from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass

TICKET_TTL_SECONDS = 30


@dataclass(frozen=True)
class ConsoleTicket:
    """What a ticket resolves to: which VM, and where its agent/domain live."""

    vm_id: uuid.UUID
    node_ip: str
    libvirt_uuid: str
    expires_at: float


_tickets: dict[str, ConsoleTicket] = {}


def issue_ticket(vm_id: uuid.UUID, node_ip: str, libvirt_uuid: str) -> str:
    """Create, store, and return a new one-time ticket valid for TICKET_TTL_SECONDS."""
    _purge_expired()
    token = secrets.token_urlsafe(32)
    _tickets[token] = ConsoleTicket(
        vm_id=vm_id,
        node_ip=node_ip,
        libvirt_uuid=libvirt_uuid,
        expires_at=time.monotonic() + TICKET_TTL_SECONDS,
    )
    return token


def consume_ticket(token: str, vm_id: uuid.UUID) -> ConsoleTicket | None:
    """Validate and single-use-consume *token*, returning its data or None.

    Returns None if the ticket doesn't exist, has expired, or was issued for
    a different VM. The ticket is removed from the store in every case
    (including a vm_id mismatch) so a token can never be presented twice,
    which also stops it being used to probe which vm_id it was issued for.
    """
    ticket = _tickets.pop(token, None)
    if ticket is None:
        return None
    if ticket.expires_at < time.monotonic():
        return None
    if ticket.vm_id != vm_id:
        return None
    return ticket


def _purge_expired() -> None:
    """Drop expired, never-consumed tickets so the store doesn't grow unbounded."""
    now = time.monotonic()
    expired = [tok for tok, t in _tickets.items() if t.expires_at < now]
    for tok in expired:
        _tickets.pop(tok, None)
