"""Unit tests for agent/nos_driver.py (httpx mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent.nos_driver import NOSDriver, load_nos_driver


@pytest.fixture
def driver() -> NOSDriver:
    return NOSDriver(base_url="http://127.0.0.1:8080", api_key="test-key")


def _mock_response(status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=resp
        )
    return resp


class TestConfigureVlan:
    @pytest.mark.asyncio
    async def test_sends_set_command_and_commits(self, driver):
        config_resp = _mock_response(200)
        commit_resp = _mock_response(200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=[config_resp, commit_resp])
            result = await driver.configure_vlan(100)

        assert result is True
        calls = instance.post.call_args_list
        assert len(calls) == 2
        config_call = calls[0]
        assert "/api/v1/config" in config_call.args[0]
        assert config_call.kwargs["json"] == {"commands": ["set vlans vlan100 vlan-id 100"]}
        commit_call = calls[1]
        assert "/api/v1/commit" in commit_call.args[0]

    @pytest.mark.asyncio
    async def test_uses_correct_vlan_name(self, driver):
        config_resp = _mock_response(200)
        commit_resp = _mock_response(200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=[config_resp, commit_resp])
            await driver.configure_vlan(42)

        cmd = instance.post.call_args_list[0].kwargs["json"]["commands"][0]
        assert cmd == "set vlans vlan42 vlan-id 42"

    @pytest.mark.asyncio
    async def test_idempotent_when_vlan_exists(self, driver):
        """configure_vlan succeeds even if NOS returns 200 (set is idempotent)."""
        config_resp = _mock_response(200)
        commit_resp = _mock_response(200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=[config_resp, commit_resp])
            result = await driver.configure_vlan(100)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_config_error(self, driver):
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=OSError("refused"))
            result = await driver.configure_vlan(100)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_commit_failure(self, driver):
        config_resp = _mock_response(200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=[config_resp, OSError("commit failed")])
            result = await driver.configure_vlan(100)
        assert result is False

    @pytest.mark.asyncio
    async def test_uses_api_key_header(self, driver):
        config_resp = _mock_response(200)
        commit_resp = _mock_response(200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=[config_resp, commit_resp])
            await driver.configure_vlan(10)

        headers = instance.post.call_args_list[0].kwargs["headers"]
        assert headers["X-API-Key"] == "test-key"


class TestRemoveVlan:
    @pytest.mark.asyncio
    async def test_sends_delete_command_and_commits(self, driver):
        config_resp = _mock_response(200)
        commit_resp = _mock_response(200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=[config_resp, commit_resp])
            result = await driver.remove_vlan(100)

        assert result is True
        cmd = instance.post.call_args_list[0].kwargs["json"]["commands"][0]
        assert cmd == "delete vlans vlan100"

    @pytest.mark.asyncio
    async def test_idempotent_when_vlan_absent(self, driver):
        """remove_vlan succeeds when NOS returns 404 (VLAN already gone)."""
        config_resp = _mock_response(404)
        commit_resp = _mock_response(200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=[config_resp, commit_resp])
            result = await driver.remove_vlan(100)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_config_5xx(self, driver):
        config_resp = _mock_response(500)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(return_value=config_resp)
            result = await driver.remove_vlan(100)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_commit_failure(self, driver):
        config_resp = _mock_response(200)
        with patch("httpx.AsyncClient") as mock_cls:
            instance = mock_cls.return_value.__aenter__.return_value
            instance.post = AsyncMock(side_effect=[config_resp, OSError("commit down")])
            result = await driver.remove_vlan(100)
        assert result is False


class TestLoadNosDriver:
    def test_uses_provided_api_key(self):
        drv = load_nos_driver("http://127.0.0.1:8080", "my-key", "/nonexistent")
        assert drv._headers["X-API-Key"] == "my-key"

    def test_reads_key_from_file_when_api_key_empty(self, tmp_path):
        key_file = tmp_path / "api_key"
        key_file.write_text("file-key\n")
        drv = load_nos_driver("http://127.0.0.1:8080", "", str(key_file))
        assert drv._headers["X-API-Key"] == "file-key"

    def test_empty_key_when_file_missing(self):
        drv = load_nos_driver("http://127.0.0.1:8080", "", "/nonexistent/api_key")
        assert drv._headers["X-API-Key"] == ""

    def test_does_not_raise_when_key_file_unreadable(self):
        """Agent must not crash-loop if /opt/nos/api_key is mode 640 root:nos and cos is not in nos group."""
        with patch("agent.nos_driver.Path") as mock_path_cls:
            mock_path_cls.return_value.read_text.side_effect = PermissionError("Permission denied")
            drv = load_nos_driver("http://127.0.0.1:8080", "", "/opt/nos/api_key")
        assert drv is not None
        assert drv._unavailable_reason is not None
        assert "api_key" in drv._unavailable_reason

    @pytest.mark.asyncio
    async def test_configure_vlan_returns_false_when_key_unreadable(self):
        with patch("agent.nos_driver.Path") as mock_path_cls:
            mock_path_cls.return_value.read_text.side_effect = PermissionError("Permission denied")
            drv = load_nos_driver("http://127.0.0.1:8080", "", "/opt/nos/api_key")
        result = await drv.configure_vlan(100)
        assert result is False

    @pytest.mark.asyncio
    async def test_remove_vlan_returns_false_when_key_unreadable(self):
        with patch("agent.nos_driver.Path") as mock_path_cls:
            mock_path_cls.return_value.read_text.side_effect = PermissionError("Permission denied")
            drv = load_nos_driver("http://127.0.0.1:8080", "", "/opt/nos/api_key")
        result = await drv.remove_vlan(100)
        assert result is False
