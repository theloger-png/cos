"""VM cloud-init credential generation utilities."""

from __future__ import annotations

import crypt  # deprecated in 3.12, removed in 3.13; sufficient for current Ubuntu 24.04 target
import secrets
import string

_CHARSET = string.ascii_letters + string.digits


def generate_password(length: int = 16) -> str:
    """Return a cryptographically random alphanumeric password of *length* characters."""
    return "".join(secrets.choice(_CHARSET) for _ in range(length))


def hash_password(password: str) -> str:
    """Return a SHA-512 crypt hash of *password* suitable for cloud-init chpasswd.

    The returned string has the form ``$6$<salt>$<hash>``.
    """
    return crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512))
