"""Pytest configuration for controller unit tests."""

import os
import sys
from unittest.mock import AsyncMock, MagicMock

# Prevent Settings() from reading /opt/cos/secret_key at import time
os.environ.setdefault("COS_SECRET_KEY", "test-secret-key-placeholder")

# Stub asyncpg and controller.db.session so unit tests don't need a live DB
sys.modules.setdefault("asyncpg", MagicMock())

_session_stub = MagicMock()
_session_stub.AsyncSessionLocal = MagicMock()
sys.modules.setdefault("controller.db.session", _session_stub)
