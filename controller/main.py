"""COS controller entry point."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from controller.api.app import create_app
from controller.api.auth import ensure_admin_key
from controller import operations
from controller.api.auth_users import hash_password
from controller.config import settings
from controller.db.models import Node, User
from controller.db.session import AsyncSessionLocal, engine
from fastapi import FastAPI
from sqlalchemy import select

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ADMIN_PASSWORD_PATH = "/opt/cos/admin_password"


async def _heartbeat_monitor() -> None:
    """Mark nodes offline when their last heartbeat is too old."""
    timeout = timedelta(seconds=settings.agent_heartbeat_timeout_seconds)
    while True:
        await asyncio.sleep(30)
        cutoff = datetime.now(timezone.utc) - timeout
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Node))
            went_offline: list[Node] = []
            for node in result.scalars().all():
                if node.last_heartbeat and node.last_heartbeat < cutoff:
                    if node.status != "offline":
                        node.status = "offline"
                        went_offline.append(node)
                        logger.warning("Node %s marked offline (heartbeat timeout)", node.hostname)
            await session.commit()
        for node in went_offline:
            await operations.record_system_event(
                "node.offline",
                f"Node {node.hostname} marked offline (no heartbeat for {settings.agent_heartbeat_timeout_seconds}s)",
                success=False,
                target_type="node",
                target_id=str(node.id),
                target_name=node.hostname,
            )


async def _operations_maintenance() -> None:
    """Purge operation-log rows older than the retention period, once a day."""
    while True:
        try:
            purged = await operations.purge_old_operations()
            if purged:
                logger.info("Purged %d operation log entries older than %d days", purged, operations.RETENTION_DAYS)
        except Exception:
            logger.exception("Operation log purge failed")
        await asyncio.sleep(24 * 3600)


async def _ensure_admin_user(session) -> None:
    """Create a default admin user if no users exist."""
    result = await session.execute(select(User))
    if result.scalars().first() is not None:
        return

    p = Path(ADMIN_PASSWORD_PATH)
    if p.exists():
        password = p.read_text().strip()
    else:
        password = secrets.token_urlsafe(24)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(password)
        os.chmod(ADMIN_PASSWORD_PATH, 0o640)
        logger.info("Admin password written to %s", ADMIN_PASSWORD_PATH)

    admin = User(
        username="admin",
        email=None,
        hashed_password=hash_password(password),
        role="admin",
        tenant_id=None,
        active=True,
    )
    session.add(admin)
    await session.commit()
    logger.info("Default admin user created")


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncSessionLocal() as session:
        await ensure_admin_key(session)
        await _ensure_admin_user(session)

    try:
        stale = await operations.fail_stale_running_operations()
        if stale:
            logger.warning("Marked %d interrupted operation(s) as failed", stale)
    except Exception:
        logger.exception("Could not clean up stale operations")

    monitor_task = asyncio.create_task(_heartbeat_monitor())
    maintenance_task = asyncio.create_task(_operations_maintenance())
    logger.info("COS controller started on %s:%d", settings.api_host, settings.api_port)
    yield
    monitor_task.cancel()
    maintenance_task.cancel()
    await engine.dispose()


app = create_app()
app.router.lifespan_context = lifespan


if __name__ == "__main__":
    uvicorn.run(
        "controller.main:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )
