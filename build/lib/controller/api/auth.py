"""API key authentication middleware for COS controller."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from common.utils import generate_api_key, hash_api_key
from controller.db.models import APIKey, Tenant
from controller.db.session import AsyncSessionLocal
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=True)

ADMIN_KEY_PATH = "/opt/cos/admin_api_key"


async def ensure_admin_key(session: AsyncSession) -> None:
    """Create or recover the master admin API key.

    First start (file absent): generate key, write to file, insert hash into DB.
    Subsequent starts (file present): read key from file; insert into DB only if
    the hash is missing (handles the case where the DB was wiped/reset).
    """
    p = Path(ADMIN_KEY_PATH)

    if p.exists():
        raw_key = p.read_text().strip()
        key_hash = hash_api_key(raw_key)
        result = await session.execute(select(APIKey).where(APIKey.key_hash == key_hash))
        if result.scalar_one_or_none():
            return  # already present in DB — nothing to do
        # File exists but DB was reset: re-insert the existing key (do not regenerate)
        logger.warning("Admin key file found but not in DB; re-inserting into api_keys")
    else:
        raw_key = generate_api_key()
        key_hash = hash_api_key(raw_key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw_key)
        os.chmod(ADMIN_KEY_PATH, 0o640)
        logger.info("Admin API key written to %s", ADMIN_KEY_PATH)

    admin_key = APIKey(
        key_hash=key_hash,
        description="admin",
        tenant_id=None,
    )
    session.add(admin_key)
    await session.commit()
    logger.info("Admin API key inserted into api_keys table")


async def get_api_key(
    raw_key: str = Security(_api_key_header),
) -> tuple[APIKey, Tenant | None]:
    """Validate X-API-Key header and return (APIKey, Tenant|None).

    Hashes the incoming key with SHA-256 and looks up the hash in api_keys.
    Raises HTTP 401 if no matching key is found.
    """
    key_hash = hash_api_key(raw_key)
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(APIKey).where(APIKey.key_hash == key_hash)
        )
        api_key_obj: APIKey | None = result.scalar_one_or_none()

        if not api_key_obj:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key",
            )

        api_key_obj.last_used = datetime.now(timezone.utc)
        await session.commit()

        tenant: Tenant | None = None
        if api_key_obj.tenant_id:
            result = await session.execute(
                select(Tenant).where(Tenant.id == api_key_obj.tenant_id)
            )
            tenant = result.scalar_one_or_none()

    return api_key_obj, tenant


def require_admin(
    auth: tuple[APIKey, Tenant | None],
) -> None:
    """Raise 403 if the caller is not the master admin (no tenant attached)."""
    _, tenant = auth
    if tenant is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
