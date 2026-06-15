"""Synchronous NOS REST API client for agent-side vnetX VLAN provisioning."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:8080"
_DEFAULT_API_KEY_FILE = "/opt/nos/api_key"


class NOSApiClient:
    """Thin synchronous wrapper for NOS REST API — used for hardware-edit vnetX operations."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    def get_config(self) -> dict | None:
        """GET /api/v1/config and return the parsed JSON, or None on error."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.get(f"{self._base_url}/api/v1/config", headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("NOS get_config failed: %s", exc)
            return None

    def post_config(self, commands: list[str]) -> bool:
        """POST CLI commands to /api/v1/config. Treats 404 as idempotent success."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{self._base_url}/api/v1/config",
                    headers=self._headers,
                    json={"commands": commands},
                )
                if resp.status_code == 404:
                    return True  # already absent — idempotent
                resp.raise_for_status()
                return True
        except httpx.HTTPStatusError as exc:
            logger.error("NOS post_config %s failed: %s", commands, exc)
            return False
        except Exception as exc:
            logger.error("NOS post_config %s failed: %s", commands, exc)
            return False

    def commit(self) -> bool:
        """POST to /api/v1/commit to apply staged changes."""
        try:
            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{self._base_url}/api/v1/commit",
                    headers=self._headers,
                    json={},
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("NOS commit failed: %s", exc)
            return False


def load_nos_api_client(
    base_url: str = _DEFAULT_BASE_URL,
    api_key_file: str = _DEFAULT_API_KEY_FILE,
) -> NOSApiClient | None:
    """Load NOS API key from *api_key_file* and return a NOSApiClient.

    Returns None (without raising) if the key file is missing or unreadable.
    """
    try:
        api_key = Path(api_key_file).read_text().strip()
        return NOSApiClient(base_url=base_url, api_key=api_key)
    except Exception as exc:
        logger.warning("Cannot load NOS API key from %s: %s", api_key_file, exc)
        return None
