"""WebSocket client for communicating with COS agents."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import websockets
from websockets.exceptions import WebSocketException

from common.models import AgentCommand, AgentCommandResult

logger = logging.getLogger(__name__)

_WS_PORT = 8091
_TIMEOUT_SECONDS = 30


def _parse_progress(raw: str | bytes) -> tuple[int, int | None] | None:
    """Return (done, total) if *raw* is an interim progress frame, else None."""
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    frame = data.get("progress") if isinstance(data, dict) else None
    if not isinstance(frame, dict):
        return None
    total = frame.get("total")
    return int(frame.get("done", 0)), int(total) if total else None


class AgentClient:
    """Send commands to a remote COS agent over WebSocket."""

    async def send_command(
        self,
        node_ip: str,
        command: str,
        payload: dict,
        timeout_seconds: int = _TIMEOUT_SECONDS,
        on_progress: Callable[[int, int | None], Awaitable[None]] | None = None,
    ) -> AgentCommandResult:
        """Send *command* with *payload* to the agent at *node_ip*.

        *timeout_seconds* defaults to the standard 30s for ordinary fast
        commands; pass a larger value for commands that can legitimately run
        much longer (e.g. template_image_fetch, which can take up to ~30
        minutes for a large image over a slow link).

        *on_progress* is awaited with (bytes_done, bytes_total_or_None) for each
        interim progress frame a long-running command sends before its final
        result (currently only template_image_fetch).

        Returns AgentCommandResult(success=False) on timeout or connection error.
        """
        uri = f"ws://{node_ip}:{_WS_PORT}/ws"
        cmd = AgentCommand(command=command, payload=payload)
        try:
            async with asyncio.timeout(timeout_seconds):
                async with websockets.connect(uri) as ws:
                    await ws.send(cmd.model_dump_json())
                    while True:
                        raw = await ws.recv()
                        progress = _parse_progress(raw)
                        if progress is None:
                            return AgentCommandResult.model_validate_json(raw)
                        if on_progress is not None:
                            await on_progress(*progress)
        except TimeoutError:
            logger.error("Agent command '%s' timed out for node %s", command, node_ip)
            return AgentCommandResult(success=False, output="", error="timeout")
        except (WebSocketException, OSError) as exc:
            logger.error("Agent connection failed for node %s: %s", node_ip, exc)
            return AgentCommandResult(success=False, output="", error=str(exc))
        except Exception as exc:
            logger.error("Unexpected error sending agent command '%s' to %s: %s", command, node_ip, exc)
            return AgentCommandResult(success=False, output="", error=str(exc))

    def console_uri(self, node_ip: str, libvirt_uuid: str) -> str:
        """Return the agent's raw console WebSocket URI for a domain.

        Unlike send_command, the console is a long-lived binary relay, not a
        single request/response, so it is opened directly by the caller
        (controller/api/routers/console.py) rather than through this class.
        """
        return f"ws://{node_ip}:{_WS_PORT}/console?uuid={libvirt_uuid}"
