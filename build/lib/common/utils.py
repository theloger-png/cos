"""Common utility functions for COS."""

from __future__ import annotations

import hashlib
import os
import secrets
from pathlib import Path


def generate_api_key() -> str:
    """Generate a cryptographically secure 64-character hex API key."""
    return secrets.token_hex(32)


def hash_api_key(key: str) -> str:
    """Return a SHA-256 hex digest of the given API key."""
    return hashlib.sha256(key.encode()).hexdigest()


def verify_api_key(key: str, key_hash: str) -> bool:
    """Return True if the key matches the stored hash."""
    return secrets.compare_digest(hash_api_key(key), key_hash)


def load_or_create_secret(path: str, generator=secrets.token_hex, gen_args=(32,)) -> str:
    """Load a secret from *path*, or create and persist a new one if absent."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists():
        return p.read_text().strip()
    value = generator(*gen_args)
    p.write_text(value)
    os.chmod(path, 0o600)
    return value
