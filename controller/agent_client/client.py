"""WebSocket client for communicating with COS agents."""

from __future__ import annotations

import asyncio
import json
import logging

import websockets
from websockets.exceptions import WebSocketException

from common.models import AgentCommand, AgentCommandResult

logger = logging.getLogger(__name__)

_WS_PORT = 8091
_TIMEOUT_SECONDS = 30


class AgentClient:
    """Send commands to a remote COS agent over WebSocket."""

    async def send_command(
        self,
        node_ip: str,
        command: str,
        payload: dict,
    ) -> AgentCommandResult:
        """Send *command* with *payload* to the agent at *node_ip*.

        Returns AgentCommandResult(success=False) on timeout or connection error.
        """
        uri = f"ws://{node_ip}:{_WS_PORT}/ws"
        cmd = AgentCommand(command=command, payload=payload)
        try:
            async with asyncio.timeout(_TIMEOUT_SECONDS):
                async with websockets.connect(uri) as ws:
                    await ws.send(cmd.model_dump_json())
                    raw = await ws.recv()
                    return AgentCommandResult.model_validate_json(raw)
        except TimeoutError:
            logger.error("Agent command '%s' timed out for node %s", command, node_ip)
            return AgentCommandResult(success=False, output="", error="timeout")
        except (WebSocketException, OSError) as exc:
            logger.error("Agent connection failed for node %s: %s", node_ip, exc)
            return AgentCommandResult(success=False, output="", error=str(exc))
        except Exception as exc:
            logger.error("Unexpected error sending agent command '%s' to %s: %s", command, node_ip, exc)
            return AgentCommandResult(success=False, output="", error=str(exc))
