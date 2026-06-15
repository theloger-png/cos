"""Unit tests for agent/nos_api_client.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from agent.nos_api_client import NOSApiClient, load_nos_api_client


@pytest.fixture
def client() -> NOSApiClient:
    return NOSApiClient(base_url="http://127.0.0.1:8080", api_key="test-key")


class TestGetConfig:
    def test_returns_parsed_json(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"interfaces": {"vnet0": {}}}
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.get.return_value = mock_resp
            result = client.get_config()

        assert result == {"interfaces": {"vnet0": {}}}

    def test_returns_none_on_http_error(self, client):
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.get.side_effect = Exception("conn refused")
            result = client.get_config()

        assert result is None

    def test_sends_auth_header(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {}
        mock_resp.raise_for_status = MagicMock()
        captured_kwargs: list[dict] = []

        def _get(url, **kwargs):
            captured_kwargs.append(kwargs)
            return mock_resp

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.get.side_effect = _get
            client.get_config()

        assert captured_kwargs
        assert captured_kwargs[0]["headers"]["X-API-Key"] == "test-key"


class TestPostConfig:
    def test_returns_true_on_success(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.return_value = mock_resp
            result = client.post_config(["set vlans vlan101 vlan-id 101"])

        assert result is True

    def test_treats_404_as_success(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.return_value = mock_resp
            result = client.post_config(["delete interfaces vnet0"])

        assert result is True

    def test_returns_false_on_http_error(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=mock_resp
        )

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.return_value = mock_resp
            result = client.post_config(["bad command"])

        assert result is False

    def test_returns_false_on_network_error(self, client):
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.side_effect = Exception("timeout")
            result = client.post_config(["set something"])

        assert result is False

    def test_sends_commands_in_body(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        captured_json: list[dict] = []

        def _post(url, **kwargs):
            captured_json.append(kwargs.get("json", {}))
            return mock_resp

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.side_effect = _post
            client.post_config(["set interfaces vnet1 unit 0 family ethernet-switching vlan members vlan101"])

        assert captured_json
        assert captured_json[0]["commands"] == [
            "set interfaces vnet1 unit 0 family ethernet-switching vlan members vlan101"
        ]


class TestCommit:
    def test_returns_true_on_success(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.return_value = mock_resp
            result = client.commit()

        assert result is True

    def test_returns_false_on_error(self, client):
        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.side_effect = Exception("refused")
            result = client.commit()

        assert result is False

    def test_calls_commit_endpoint(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        captured_urls: list[str] = []

        def _post(url, **kwargs):
            captured_urls.append(url)
            return mock_resp

        with patch("httpx.Client") as MockClient:
            MockClient.return_value.__enter__.return_value.post.side_effect = _post
            client.commit()

        assert captured_urls
        assert captured_urls[0].endswith("/api/v1/commit")


class TestLoadNosApiClient:
    def test_returns_client_when_key_file_exists(self, tmp_path):
        key_file = tmp_path / "api_key"
        key_file.write_text("my-secret-key")
        result = load_nos_api_client(base_url="http://127.0.0.1:8080", api_key_file=str(key_file))
        assert isinstance(result, NOSApiClient)

    def test_returns_none_when_key_file_missing(self):
        result = load_nos_api_client(
            base_url="http://127.0.0.1:8080",
            api_key_file="/nonexistent/path/api_key",
        )
        assert result is None

    def test_strips_whitespace_from_key(self, tmp_path):
        key_file = tmp_path / "api_key"
        key_file.write_text("  secret-key\n")
        client = load_nos_api_client(base_url="http://127.0.0.1:8080", api_key_file=str(key_file))
        assert client is not None
        assert client._headers["X-API-Key"] == "secret-key"
