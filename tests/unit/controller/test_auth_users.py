"""Unit tests for JWT and password utilities, and the auth endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from controller.api.auth_users import (
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


# ---------------------------------------------------------------------------
# Password hashing
# ---------------------------------------------------------------------------

class TestPasswordHashing:
    def test_hash_is_different_from_plain(self):
        plain = "s3cr3t!"
        assert hash_password(plain) != plain

    def test_verify_correct_password(self):
        plain = "correct-horse-battery-staple"
        hashed = hash_password(plain)
        assert verify_password(plain, hashed) is True

    def test_verify_wrong_password(self):
        hashed = hash_password("right-password")
        assert verify_password("wrong-password", hashed) is False

    def test_different_hashes_for_same_password(self):
        plain = "same"
        assert hash_password(plain) != hash_password(plain)  # bcrypt salts


# ---------------------------------------------------------------------------
# JWT token
# ---------------------------------------------------------------------------

class TestJWT:
    def test_create_and_decode_token(self):
        data = {"sub": str(uuid.uuid4()), "username": "admin", "role": "admin"}
        token = create_access_token(data)
        payload = decode_access_token(token)
        assert payload["sub"] == data["sub"]
        assert payload["username"] == "admin"
        assert payload["role"] == "admin"

    def test_token_has_expiry(self):
        token = create_access_token({"sub": "x"})
        payload = decode_access_token(token)
        assert "exp" in payload

    def test_custom_expiry(self):
        token = create_access_token({"sub": "x"}, expires_delta=timedelta(hours=1))
        payload = decode_access_token(token)
        from datetime import timezone
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        assert timedelta(minutes=55) < (exp - now) < timedelta(hours=1, minutes=5)

    def test_invalid_token_raises(self):
        from jose import JWTError
        with pytest.raises(JWTError):
            decode_access_token("not.a.valid.token")


# ---------------------------------------------------------------------------
# Login endpoint
# ---------------------------------------------------------------------------

def _make_user(username: str, password: str, role: str = "admin"):
    from controller.db.models import User
    u = User()
    u.id = uuid.uuid4()
    u.username = username
    u.hashed_password = hash_password(password)
    u.role = role
    u.email = None
    u.tenant_id = None
    u.active = True
    u.created_at = datetime.now(timezone.utc)
    return u


def _mock_session_ctx(scalar_return=None) -> AsyncMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = scalar_return
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


class TestLoginEndpoint:
    def _get_client(self, user_in_db):
        from fastapi import FastAPI
        from controller.api.routers.auth import router
        from controller.api.deps import db_session

        app = FastAPI()
        app.include_router(router)

        async def _fake_db():
            session = _mock_session_ctx(scalar_return=user_in_db)
            yield session

        app.dependency_overrides[db_session] = _fake_db
        return TestClient(app)

    def test_correct_credentials_return_token(self):
        user = _make_user("admin", "secret123")
        client = self._get_client(user)
        response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "secret123"})
        assert response.status_code == 200
        data = response.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"
        assert data["role"] == "admin"
        assert data["username"] == "admin"

    def test_wrong_password_returns_401(self):
        user = _make_user("admin", "right-password")
        client = self._get_client(user)
        response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"})
        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]

    def test_unknown_user_returns_401(self):
        client = self._get_client(user_in_db=None)
        response = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "anything"})
        assert response.status_code == 401
        assert "Invalid" in response.json()["detail"]
