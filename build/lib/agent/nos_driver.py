"""Thin async wrapper around NOS REST API for local agent networking tasks."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class NOSDriver:
    """Configure local networking via the NOS REST API."""

    def __init__(self, base_url: str, api_key: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key, "Content-Type": "application/json"}

    async def configure_vlan(self, vlan_id: int) -> bool:
        """Create or activate a VLAN on the local switch port."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/vlans",
                    headers=self._headers,
                    json={"vlan_id": vlan_id},
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("configure_vlan %d failed: %s", vlan_id, exc)
            return False

    async def remove_vlan(self, vlan_id: int) -> bool:
        """Remove a VLAN from the local switch port."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.delete(
                    f"{self._base_url}/api/v1/vlans/{vlan_id}",
                    headers=self._headers,
                )
                resp.raise_for_status()
                return True
        except Exception as exc:
            logger.error("remove_vlan %d failed: %s", vlan_id, exc)
            return False
