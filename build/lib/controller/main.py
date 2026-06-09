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
            for node in result.scalars().all():
                if node.last_heartbeat and node.last_heartbeat < cutoff:
                    if node.status != "offline":
                        node.status = "offline"
                        logger.warning("Node %s marked offline (heartbeat timeout)", node.hostname)
            await session.commit()


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

    monitor_task = asyncio.create_task(_heartbeat_monitor())
    logger.info("COS controller started on %s:%d", settings.api_host, settings.api_port)
    yield
    monitor_task.cancel()
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
