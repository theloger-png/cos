"""AgentClient.send_command: interim progress frames before the final result."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from controller.agent_client.client import AgentClient, _parse_progress


def _ws(frames: list[str]) -> MagicMock:
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.recv = AsyncMock(side_effect=frames)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=ws)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestParseProgress:
    def test_progress_frame(self):
        assert _parse_progress(json.dumps({"progress": {"done": 5, "total": 10}})) == (5, 10)

    def test_unknown_total_is_none(self):
        assert _parse_progress(json.dumps({"progress": {"done": 5, "total": None}})) == (5, None)

    def test_final_result_is_not_progress(self):
        assert _parse_progress(json.dumps({"success": True, "output": "x", "error": None})) is None

    def test_garbage_is_not_progress(self):
        assert _parse_progress("not json") is None


class TestSendCommandProgress:
    @pytest.mark.asyncio
    async def test_progress_frames_are_forwarded_then_result_returned(self):
        frames = [
            json.dumps({"progress": {"done": 1, "total": 4}}),
            json.dumps({"progress": {"done": 3, "total": 4}}),
            json.dumps({"success": True, "output": "/p", "error": None}),
        ]
        seen = []

        async def on_progress(done, total):
            seen.append((done, total))

        with patch("controller.agent_client.client.websockets.connect", return_value=_ws(frames)):
            result = await AgentClient().send_command("10.0.0.1", "template_image_fetch", {}, on_progress=on_progress)

        assert seen == [(1, 4), (3, 4)]
        assert result.success is True and result.output == "/p"

    @pytest.mark.asyncio
    async def test_progress_frames_are_skipped_without_callback(self):
        frames = [
            json.dumps({"progress": {"done": 1, "total": 4}}),
            json.dumps({"success": False, "output": "", "error": "boom"}),
        ]
        with patch("controller.agent_client.client.websockets.connect", return_value=_ws(frames)):
            result = await AgentClient().send_command("10.0.0.1", "x", {})
        assert result.success is False and result.error == "boom"

    @pytest.mark.asyncio
    async def test_plain_single_result_still_works(self):
        frames = [json.dumps({"success": True, "output": "ok", "error": None})]
        with patch("controller.agent_client.client.websockets.connect", return_value=_ws(frames)):
            result = await AgentClient().send_command("10.0.0.1", "vm_start", {})
        assert result.output == "ok"
