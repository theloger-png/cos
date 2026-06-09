"""COS controller entry point."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import uvicorn
from controller.api.app import create_app
from controller.api.auth import ensure_admin_key
from controller.config import settings
from controller.db.base import Base
from controller.db.models import Node
from controller.db.session import AsyncSessionLocal, engine
from fastapi import FastAPI
from sqlalchemy import select

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        await ensure_admin_key(session)

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
