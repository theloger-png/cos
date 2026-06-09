"""Unit tests for the NOS REST API client (httpx mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from controller.nos_client.client import NOSClient


@pytest.fixture
def client() -> NOSClient:
    return NOSClient(base_url="http://nos.local:8080", api_key="test-key")


def _mock_response(status_code: int = 200, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


class TestNOSClientGetInterfaces:
    @pytest.mark.asyncio
    async def test_returns_interfaces(self, client):
        mock_resp = _mock_response(json_data={"eth0": {"speed": 1000}})
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.get = AsyncMock(return_value=mock_resp)
            result = await client.get_interfaces()
        assert result == {"eth0": {"speed": 1000}}

    @pytest.mark.asyncio
    async def test_returns_empty_on_connection_error(self, client):
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.get = AsyncMock(side_effect=OSError("connection refused"))
            result = await client.get_interfaces()
        assert result == {}


class TestNOSClientConfigureVlan:
    @pytest.mark.asyncio
    async def test_success(self, client):
        mock_resp = _mock_response(status_code=200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            result = await client.configure_vlan(100, "prod")
        assert result is True

    @pytest.mark.asyncio
    async def test_failure_returns_false(self, client):
        mock_resp = _mock_response(status_code=500)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            result = await client.configure_vlan(100, "prod")
        assert result is False

    @pytest.mark.asyncio
    async def test_connection_error_returns_false(self, client):
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=OSError("timeout"))
            result = await client.configure_vlan(200, "dev")
        assert result is False


class TestNOSClientDeleteVlan:
    @pytest.mark.asyncio
    async def test_success(self, client):
        mock_resp = _mock_response(status_code=204)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.delete = AsyncMock(return_value=mock_resp)
            result = await client.delete_vlan(100)
        assert result is True

    @pytest.mark.asyncio
    async def test_failure_returns_false(self, client):
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.delete = AsyncMock(side_effect=OSError("refused"))
            result = await client.delete_vlan(100)
        assert result is False


class TestNOSClientCommit:
    @pytest.mark.asyncio
    async def test_success(self, client):
        mock_resp = _mock_response(status_code=200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            result = await client.commit()
        assert result is True

    @pytest.mark.asyncio
    async def test_failure_returns_false(self, client):
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=Exception("boom"))
            result = await client.commit()
        assert result is False


class TestNOSClientConfigureInterface:
    @pytest.mark.asyncio
    async def test_success(self, client):
        mock_resp = _mock_response(status_code=200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=mock_resp)
            result = await client.configure_interface("eth0", {"vlan": 100})
        assert result is True
