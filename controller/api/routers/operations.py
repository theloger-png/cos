"""Operations console endpoint: recent and in-progress state-changing actions."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from controller.api.deps import db_session
from controller.api.operations_middleware import Actor, resolve_actor
from controller.db.models import Operation

router = APIRouter(prefix="/api/v1/operations", tags=["operations"])

_MAX_LIMIT = 500


class OperationInfo(BaseModel):
    id: uuid.UUID
    user: str
    action: str
    description: str
    target_type: str | None
    target_id: str | None
    target_name: str | None
    status: str
    progress: int | None
    message: str | None
    error: str | None
    started_at: datetime
    finished_at: datetime | None


async def current_actor(request: Request) -> Actor:
    """Authenticate the caller (JWT or X-API-Key) and return who they are.

    Unlike current_auth this keeps the caller's tenant, which decides what
    they are allowed to see in the console.
    """
    actor = await resolve_actor(
        request.headers.get("authorization"),
        request.headers.get("x-api-key"),
    )
    if actor is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return actor


def _to_info(op: Operation) -> OperationInfo:
    return OperationInfo(
        id=op.id,
        user=op.user_label,
        action=op.action,
        description=op.description,
        target_type=op.target_type,
        target_id=op.target_id,
        target_name=op.target_name,
        status=op.status,
        progress=op.progress,
        message=op.message,
        error=op.error,
        started_at=op.started_at,
        finished_at=op.finished_at,
    )


@router.get("", response_model=list[OperationInfo])
async def list_operations(
    limit: int = Query(200, ge=1, le=_MAX_LIMIT),
    before: datetime | None = Query(None, description="Only operations started before this time (for paging)"),
    op_status: str | None = Query(None, alias="status", pattern="^(running|success|failed)$"),
    user: str | None = Query(None, description="Exact user name"),
    action: str | None = Query(None, description="Action prefix, e.g. 'vm' or 'vm.create'"),
    session: AsyncSession = Depends(db_session),
    actor: Actor = Depends(current_actor),
) -> list[OperationInfo]:
    """Return operations, newest first.

    Admins (callers without a tenant) see every operation; tenant callers only
    see the operations recorded for their own tenant.
    """
    query = select(Operation).order_by(Operation.started_at.desc()).limit(limit)
    if actor.tenant_id is not None:
        query = query.where(Operation.tenant_id == actor.tenant_id)
    if before is not None:
        query = query.where(Operation.started_at < before)
    if op_status is not None:
        query = query.where(Operation.status == op_status)
    if user is not None:
        query = query.where(Operation.user_label == user)
    if action is not None:
        query = query.where(Operation.action.startswith(action, autoescape=True))
    result = await session.execute(query)
    return [_to_info(op) for op in result.scalars().all()]
