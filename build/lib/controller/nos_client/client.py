"""Async HTTP client for the NOS (Network Operating System) REST API."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class NOSClient:
    """Thin async wrapper around the NOS REST API."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get(self, path: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self._base_url}{path}", headers=self._headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.error("NOS GET %s failed: %s", path, exc)
            return None

    async def _post(self, path: str, body: dict) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._base_url}{path}", headers=self._headers, json=body
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("NOS POST %s failed: %s", path, exc)
            return False

    async def _delete(self, path: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.delete(f"{self._base_url}{path}", headers=self._headers)
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("NOS DELETE %s failed: %s", path, exc)
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_interfaces(self) -> dict:
        """Return all network interfaces reported by NOS."""
        result = await self._get("/api/v1/interfaces")
        return result or {}

    async def configure_vlan(self, vlan_id: int, name: str) -> bool:
        """Create or update a VLAN on the NOS switch fabric."""
        return await self._post("/api/v1/vlans", {"vlan_id": vlan_id, "name": name})

    async def delete_vlan(self, vlan_id: int) -> bool:
        """Remove a VLAN from the NOS switch fabric."""
        return await self._delete(f"/api/v1/vlans/{vlan_id}")

    async def configure_interface(self, name: str, config: dict) -> bool:
        """Apply a configuration dict to a named interface."""
        return await self._post(f"/api/v1/interfaces/{name}", config)

    async def commit(self) -> bool:
        """Commit pending configuration changes on NOS."""
        return await self._post("/api/v1/commit", {})
