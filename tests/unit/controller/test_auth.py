"""Unit tests for controller API key authentication."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from common.utils import generate_api_key, hash_api_key
from controller.api.auth import ensure_admin_key, get_api_key
from controller.db.models import APIKey


def _make_api_key_obj(key_hash: str, tenant_id=None) -> APIKey:
    obj = APIKey()
    obj.id = uuid.uuid4()
    obj.key_hash = key_hash
    obj.description = "admin"
    obj.tenant_id = tenant_id
    obj.created_at = datetime.now(timezone.utc)
    obj.last_used = None
    return obj


def _mock_session(scalar_return=None) -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_return
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


class TestEnsureAdminKey:
    @pytest.mark.asyncio
    async def test_first_start_generates_and_inserts_key(self, tmp_path):
        """No file → generate key, write file, insert into DB with description='admin'."""
        key_file = tmp_path / "admin_api_key"
        session = _mock_session(scalar_return=None)

        with patch("controller.api.auth.ADMIN_KEY_PATH", str(key_file)):
            await ensure_admin_key(session)

        assert key_file.exists()
        raw_key = key_file.read_text().strip()
        assert len(raw_key) == 64  # secrets.token_hex(32)

        session.add.assert_called_once()
        session.commit.assert_called_once()
        inserted: APIKey = session.add.call_args[0][0]
        assert inserted.key_hash == hash_api_key(raw_key)
        assert inserted.tenant_id is None
        assert inserted.description == "admin"

    @pytest.mark.asyncio
    async def test_subsequent_start_key_in_db_does_nothing(self, tmp_path):
        """File exists and key is in DB → return without touching DB."""
        raw_key = generate_api_key()
        key_file = tmp_path / "admin_api_key"
        key_file.write_text(raw_key)

        existing = _make_api_key_obj(hash_api_key(raw_key))
        session = _mock_session(scalar_return=existing)

        with patch("controller.api.auth.ADMIN_KEY_PATH", str(key_file)):
            await ensure_admin_key(session)

        session.add.assert_not_called()
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_db_reset_reinserts_existing_file_key(self, tmp_path):
        """File exists but key absent from DB (DB reset) → insert file's key, not a new one."""
        raw_key = generate_api_key()
        key_file = tmp_path / "admin_api_key"
        key_file.write_text(raw_key)

        session = _mock_session(scalar_return=None)

        with patch("controller.api.auth.ADMIN_KEY_PATH", str(key_file)):
            await ensure_admin_key(session)

        session.add.assert_called_once()
        session.commit.assert_called_once()
        inserted: APIKey = session.add.call_args[0][0]
        assert inserted.key_hash == hash_api_key(raw_key)
        assert inserted.tenant_id is None
        assert inserted.description == "admin"
        # The file must not be overwritten with a new key
        assert key_file.read_text().strip() == raw_key


class TestGetApiKey:
    def _make_mock_session_ctx(self, scalar_return=None) -> AsyncMock:
        result = MagicMock()
        result.scalar_one_or_none.return_value = scalar_return
        session = AsyncMock()
        session.execute = AsyncMock(return_value=result)
        session.commit = AsyncMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        return session

    @pytest.mark.asyncio
    async def test_valid_key_returns_api_key_and_no_tenant(self):
        """Correct key returns (APIKey, None) for an admin key (no tenant)."""
        raw_key = generate_api_key()
        key_obj = _make_api_key_obj(hash_api_key(raw_key))

        mock_session = self._make_mock_session_ctx(scalar_return=key_obj)

        with patch("controller.api.auth.AsyncSessionLocal", return_value=mock_session):
            returned_key, returned_tenant = await get_api_key(raw_key)

        assert returned_key is key_obj
        assert returned_tenant is None

    @pytest.mark.asyncio
    async def test_invalid_key_raises_401(self):
        """Unknown key raises HTTPException with status 401."""
        mock_session = self._make_mock_session_ctx(scalar_return=None)

        with patch("controller.api.auth.AsyncSessionLocal", return_value=mock_session):
            with pytest.raises(HTTPException) as exc_info:
                await get_api_key("totally-wrong-key")

        assert exc_info.value.status_code == 401
        assert "Invalid API key" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_valid_key_updates_last_used(self):
        """Successful auth commits a last_used timestamp update."""
        raw_key = generate_api_key()
        key_obj = _make_api_key_obj(hash_api_key(raw_key))

        mock_session = self._make_mock_session_ctx(scalar_return=key_obj)

        with patch("controller.api.auth.AsyncSessionLocal", return_value=mock_session):
            await get_api_key(raw_key)

        mock_session.commit.assert_called_once()
        assert key_obj.last_used is not None
