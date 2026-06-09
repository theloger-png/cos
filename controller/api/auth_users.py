"""JWT and password utilities for user authentication."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from controller.config import settings
from jose import jwt
from passlib.context import CryptContext

_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 8


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password*."""
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if *plain* matches *hashed*."""
    return _pwd_context.verify(plain, hashed)


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """Return a signed JWT containing *data* with an expiry claim."""
    payload = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta if expires_delta is not None else timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    )
    payload["exp"] = expire
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    """Decode and return the JWT payload; raises JWTError on failure."""
    return jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
