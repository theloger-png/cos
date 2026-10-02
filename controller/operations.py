"""Operation log: records who did what and when, with optional live progress.

Every state-changing API call is recorded by controller/api/operations_middleware.py
as an Operation row (status "running" while the request is in flight, then
"success" or "failed"). Long-running handlers can report real progress via
report_progress() and override the recorded outcome via set_outcome(); both are
no-ops outside a recorded request, so handlers stay unit-testable on their own.

Recording is strictly best-effort: a failure to write the log is logged and
swallowed, and never affects the request that triggered it.
"""

from __future__ import annotations

import contextvars
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from controller.db import session as db_session_module
from controller.db.models import VM, Network, Node, Operation, Tenant, VMTemplate

logger = logging.getLogger(__name__)

RETENTION_DAYS = 90
STATUS_RUNNING = "running"
STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"
SYSTEM_USER = "system"

_MAX_DESCRIPTION = 512
_MAX_TEXT = 4000


@dataclass
class OperationContext:
    """Mutable handle to the Operation row of the request being served."""

    operation_id: uuid.UUID
    last_progress: int | None = None
    outcome_status: str | None = None
    outcome_message: str | None = None
    outcome_error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


_current: contextvars.ContextVar[OperationContext | None] = contextvars.ContextVar(
    "cos_current_operation", default=None
)


def current_context() -> OperationContext | None:
    """Return the OperationContext of the request being served, if recorded."""
    return _current.get()


def bind_context(ctx: OperationContext | None) -> contextvars.Token:
    """Bind *ctx* as the current operation context; returns a reset token."""
    return _current.set(ctx)


def unbind_context(token: contextvars.Token) -> None:
    """Undo a previous bind_context()."""
    _current.reset(token)


# ---------------------------------------------------------------------------
# Route classification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RouteInfo:
    """What a mutating route means in human terms."""

    action: str
    label: str
    target_type: str | None = None


_ID = r"(?P<id>[0-9a-fA-F-]{36})"

# (HTTP method, path regex, RouteInfo). First match wins.
_ROUTES: list[tuple[str, re.Pattern[str], RouteInfo]] = [
    (m, re.compile(rx + r"/?$"), info)
    for m, rx, info in [
        ("POST", r"^/api/v1/vms", RouteInfo("vm.create", "Create VM", "vm")),
        ("DELETE", rf"^/api/v1/vms/{_ID}", RouteInfo("vm.delete", "Delete VM", "vm")),
        ("POST", rf"^/api/v1/vms/{_ID}/start", RouteInfo("vm.start", "Start VM", "vm")),
        ("POST", rf"^/api/v1/vms/{_ID}/stop", RouteInfo("vm.stop", "Stop VM", "vm")),
        ("POST", rf"^/api/v1/vms/{_ID}/reboot", RouteInfo("vm.reboot", "Reboot VM", "vm")),
        ("POST", rf"^/api/v1/vms/{_ID}/reset-password", RouteInfo("vm.reset_password", "Reset password of VM", "vm")),
        ("POST", rf"^/api/v1/vms/{_ID}/migrate", RouteInfo("vm.migrate", "Migrate VM", "vm")),
        ("PUT", rf"^/api/v1/vms/{_ID}/hardware", RouteInfo("vm.hardware", "Edit hardware of VM", "vm")),
        ("POST", r"^/api/v1/nodes", RouteInfo("node.register", "Register node", "node")),
        ("DELETE", rf"^/api/v1/nodes/{_ID}", RouteInfo("node.delete", "Delete node", "node")),
        ("POST", r"^/api/v1/networks", RouteInfo("network.create", "Create network", "network")),
        ("DELETE", rf"^/api/v1/networks/{_ID}", RouteInfo("network.delete", "Delete network", "network")),
        ("POST", r"^/api/v1/tenants", RouteInfo("tenant.create", "Create tenant", "tenant")),
        ("DELETE", rf"^/api/v1/tenants/{_ID}", RouteInfo("tenant.delete", "Delete tenant", "tenant")),
        ("POST", rf"^/api/v1/tenants/{_ID}/apikeys", RouteInfo("tenant.apikey", "Create API key for tenant", "tenant")),
        ("POST", r"^/api/v1/templates", RouteInfo("template.create", "Create template", "template")),
        ("DELETE", rf"^/api/v1/templates/{_ID}", RouteInfo("template.delete", "Delete template", "template")),
        ("POST", rf"^/api/v1/templates/{_ID}/fetch-image", RouteInfo("template.fetch_image", "Fetch image for template", "template")),
    ]
]

# Mutating requests that are pure noise (periodic agent heartbeats, short-lived
# console tickets) and would drown out real operations.
_IGNORED: list[re.Pattern[str]] = [
    re.compile(r"^/api/v1/nodes/[0-9a-fA-F-]{36}/heartbeat/?$"),
    re.compile(r"^/api/v1/vms/[0-9a-fA-F-]{36}/console-ticket/?$"),
]

LOGIN_PATH = "/api/v1/auth/login"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

_TARGET_MODELS: dict[str, tuple[Any, str]] = {
    "vm": (VM, "name"),
    "node": (Node, "hostname"),
    "network": (Network, "name"),
    "tenant": (Tenant, "name"),
    "template": (VMTemplate, "name"),
}


def is_ignored(method: str, path: str) -> bool:
    """True for requests that must not be recorded (reads and noisy calls)."""
    if method.upper() not in MUTATING_METHODS:
        return True
    return any(rx.match(path) for rx in _IGNORED)


def classify(method: str, path: str) -> tuple[RouteInfo, str | None]:
    """Map a request to (RouteInfo, target id from the path).

    Unknown mutating routes fall back to a generic "METHOD /path" entry so
    nothing is ever silently dropped, including endpoints added later.
    """
    method = method.upper()
    for m, rx, info in _ROUTES:
        if m != method:
            continue
        match = rx.match(path)
        if match:
            return info, match.groupdict().get("id")
    return RouteInfo(f"http.{method.lower()}", f"{method} {path}", None), None


def build_description(info: RouteInfo, target_name: str | None, target_id: str | None) -> str:
    """Compose the one-line human description shown in the console."""
    if info.target_type is None:
        return info.label[:_MAX_DESCRIPTION]
    suffix = target_name or (target_id[:8] if target_id else "")
    return f"{info.label} {suffix}".strip()[:_MAX_DESCRIPTION]


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _trim(text: str | None) -> str | None:
    return text[:_MAX_TEXT] if text else text


async def _lookup_target_name(session, target_type: str | None, target_id: str | None) -> str | None:
    """Best-effort display name of the resource a request targets."""
    if not target_type or not target_id or target_type not in _TARGET_MODELS:
        return None
    model, attr = _TARGET_MODELS[target_type]
    try:
        row = (await session.execute(select(model).where(model.id == uuid.UUID(target_id)))).scalar_one_or_none()
    except Exception:
        return None
    return getattr(row, attr, None) if row is not None else None


async def start_operation(
    *,
    user_label: str,
    user_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
    method: str,
    path: str,
    body_name: str | None,
) -> OperationContext | None:
    """Insert a "running" Operation for an incoming request; None on failure."""
    try:
        info, target_id = classify(method, path)
        async with db_session_module.AsyncSessionLocal() as session:
            target_name = body_name or await _lookup_target_name(session, info.target_type, target_id)
            op = Operation(
                id=uuid.uuid4(),
                user_label=user_label,
                user_id=user_id,
                tenant_id=tenant_id,
                action=info.action,
                description=build_description(info, target_name, target_id),
                target_type=info.target_type,
                target_id=target_id,
                target_name=target_name,
                status=STATUS_RUNNING,
                http_method=method.upper(),
                path=path[:512],
                started_at=datetime.now(timezone.utc),
            )
            session.add(op)
            await session.commit()
            return OperationContext(operation_id=op.id)
    except Exception:
        logger.exception("Could not record start of operation %s %s", method, path)
        return None


async def finish_operation(
    ctx: OperationContext,
    *,
    http_status: int,
    error_detail: str | None,
) -> None:
    """Close a running Operation using the HTTP outcome (or a handler override)."""
    try:
        failed = http_status >= 400
        if ctx.outcome_status is not None:
            failed = ctx.outcome_status == STATUS_FAILED
        async with db_session_module.AsyncSessionLocal() as session:
            op = (await session.execute(select(Operation).where(Operation.id == ctx.operation_id))).scalar_one_or_none()
            if op is None:
                return
            op.status = STATUS_FAILED if failed else STATUS_SUCCESS
            op.finished_at = datetime.now(timezone.utc)
            if not failed:
                op.progress = 100
            if failed:
                op.error = _trim(ctx.outcome_error or error_detail or f"HTTP {http_status}")
            if ctx.outcome_message:
                op.message = _trim(ctx.outcome_message)
            await session.commit()
    except Exception:
        logger.exception("Could not record end of operation %s", ctx.operation_id)


async def record_completed(
    *,
    user_label: str,
    user_id: uuid.UUID | None,
    tenant_id: uuid.UUID | None,
    action: str,
    description: str,
    success: bool,
    error: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    target_name: str | None = None,
    http_method: str | None = None,
    path: str | None = None,
) -> None:
    """Insert an already-finished Operation (login attempts, system events)."""
    try:
        now = datetime.now(timezone.utc)
        async with db_session_module.AsyncSessionLocal() as session:
            session.add(
                Operation(
                    id=uuid.uuid4(),
                    user_label=user_label,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    action=action,
                    description=description[:_MAX_DESCRIPTION],
                    target_type=target_type,
                    target_id=target_id,
                    target_name=target_name,
                    status=STATUS_SUCCESS if success else STATUS_FAILED,
                    progress=100 if success else None,
                    error=_trim(error),
                    http_method=http_method,
                    path=path,
                    started_at=now,
                    finished_at=now,
                )
            )
            await session.commit()
    except Exception:
        logger.exception("Could not record operation %s", action)


async def record_system_event(
    action: str,
    description: str,
    *,
    success: bool = True,
    tenant_id: uuid.UUID | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    target_name: str | None = None,
) -> None:
    """Record an automatic (non user-initiated) event under the user "system"."""
    await record_completed(
        user_label=SYSTEM_USER,
        user_id=None,
        tenant_id=tenant_id,
        action=action,
        description=description,
        success=success,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
    )


async def report_progress(percent: int | None, message: str | None = None) -> None:
    """Update the current request's operation with real progress (0-100).

    No-op when the request is not being recorded, and when the value has not
    changed since the last report (so callers can report freely).
    """
    ctx = current_context()
    if ctx is None:
        return
    percent = None if percent is None else max(0, min(100, int(percent)))
    if percent == ctx.last_progress and message is None:
        return
    ctx.last_progress = percent
    try:
        async with db_session_module.AsyncSessionLocal() as session:
            op = (await session.execute(select(Operation).where(Operation.id == ctx.operation_id))).scalar_one_or_none()
            if op is None:
                return
            op.progress = percent
            if message is not None:
                op.message = _trim(message)
            await session.commit()
    except Exception:
        logger.exception("Could not update progress of operation %s", ctx.operation_id)


def set_outcome(*, failed: bool, message: str | None = None, error: str | None = None) -> None:
    """Override the recorded outcome for requests that succeed at HTTP level.

    For example fetch-image answers 200 even when every node failed; the
    handler calls this so the console still shows the operation as failed.
    """
    ctx = current_context()
    if ctx is None:
        return
    ctx.outcome_status = STATUS_FAILED if failed else STATUS_SUCCESS
    ctx.outcome_message = message
    ctx.outcome_error = error


# ---------------------------------------------------------------------------
# Maintenance
# ---------------------------------------------------------------------------

async def purge_old_operations(retention_days: int = RETENTION_DAYS) -> int:
    """Delete finished operations older than *retention_days*; returns the count."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    async with db_session_module.AsyncSessionLocal() as session:
        result = await session.execute(
            delete(Operation).where(Operation.started_at < cutoff, Operation.status != STATUS_RUNNING)
        )
        await session.commit()
        return result.rowcount or 0


async def fail_stale_running_operations() -> int:
    """Mark operations left "running" (controller restarted mid-request) as failed.

    Called once at controller start-up: any request in flight at that moment
    died with the old process, so it can never finish on its own.
    """
    async with db_session_module.AsyncSessionLocal() as session:
        rows = (await session.execute(select(Operation).where(Operation.status == STATUS_RUNNING))).scalars().all()
        now = datetime.now(timezone.utc)
        for op in rows:
            op.status = STATUS_FAILED
            op.error = "Interrupted: controller restarted before the operation finished"
            op.finished_at = now
        await session.commit()
        return len(rows)
