"""Thin async wrapper around NOS REST API for local agent networking tasks."""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)


class NOSDriver:
    """Configure local networking via the NOS REST API."""

    def __init__(self, base_url: str, api_key: str, unavailable_reason: str | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        self._unavailable_reason = unavailable_reason

    async def _run_commands(self, commands: list[str]) -> bool:
        """POST a list of CLI commands to /api/v1/config."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/config",
                    headers=self._headers,
                    json={"commands": commands},
                )
                resp.raise_for_status()
                return True
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None  # type: ignore[return-value]  # caller interprets as "not found"
            logger.error("NOS config command failed (%s): %s", commands, exc)
            return False
        except Exception as exc:
            logger.error("NOS config command failed (%s): %s", commands, exc)
            return False

    async def _commit(self) -> bool:
        """POST to /api/v1/commit to apply staged changes."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/commit",
                    headers=self._headers,
                    json={},
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("NOS commit failed: %s", exc)
            return False

    async def configure_vlan(self, vlan_id: int) -> bool:
        """Create or activate a VLAN on the local switch port."""
        if self._unavailable_reason:
            logger.error("configure_vlan skipped: %s", self._unavailable_reason)
            return False
        name = f"vlan{vlan_id}"
        ok = await self._run_commands([f"set vlans {name} vlan-id {vlan_id}"])
        if ok is False:
            return False
        # ok is True or None (not-found treated as idempotent)
        committed = await self._commit()
        if not committed:
            logger.error("configure_vlan %d: commit failed", vlan_id)
            return False
        return True

    async def remove_vlan(self, vlan_id: int) -> bool:
        """Remove a VLAN from the local switch port."""
        if self._unavailable_reason:
            logger.error("remove_vlan skipped: %s", self._unavailable_reason)
            return False
        name = f"vlan{vlan_id}"
        result = await self._run_commands([f"delete vlans {name}"])
        if result is False:
            # Hard failure (non-404 error)
            return False
        # result is True (deleted) or None (404 = already absent) — both are OK
        committed = await self._commit()
        if not committed:
            logger.error("remove_vlan %d: commit failed", vlan_id)
            return False
        return True


def load_nos_driver(base_url: str, api_key: str, api_key_file: str) -> NOSDriver:
    """Construct a NOSDriver, loading the API key from *api_key_file* if not provided.

    Never raises: if the key file is missing or unreadable, returns a driver that
    logs an error and returns False on every call (lazy failure instead of crash).
    """
    unavailable_reason: str | None = None
    if not api_key:
        try:
            api_key = Path(api_key_file).read_text().strip()
        except FileNotFoundError:
            logger.warning("NOS API key file not found: %s", api_key_file)
        except OSError as exc:
            unavailable_reason = f"cannot read NOS API key file {api_key_file}: {exc}"
            logger.warning(unavailable_reason)
    return NOSDriver(base_url=base_url, api_key=api_key, unavailable_reason=unavailable_reason)
