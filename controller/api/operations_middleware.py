"""ASGI middleware that records every state-changing API call as an Operation.

Written as a pure ASGI middleware (not BaseHTTPMiddleware) so the request body
can be observed as it streams to the application without being consumed, and
so the operation context set here is visible to the endpoint handler running
in the same task (see controller.operations.report_progress).

Only authenticated callers are recorded: an unauthenticated request is
rejected with 401 by the endpoint anyway, and logging it would let anyone
fill the table. Passwords and tokens are never stored - from request bodies
only the "name" / "hostname" (and, for login, "username") fields are read.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from common.utils import hash_api_key
from controller import operations
from controller.db import session as db_session_module
from controller.db.models import APIKey, User

logger = logging.getLogger(__name__)

_MAX_ERROR_CAPTURE = 8 * 1024


@dataclass(frozen=True)
class Actor:
    """The authenticated caller of a request."""

    label: str
    user_id: uuid.UUID | None
    tenant_id: uuid.UUID | None


def _header(scope: Scope, name: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key == name:
            return value.decode("latin-1")
    return None


async def resolve_actor(authorization: str | None, api_key: str | None) -> Actor | None:
    """Identify the caller from Authorization (JWT) or X-API-Key; None if unknown."""
    try:
        if authorization and authorization.lower().startswith("bearer "):
            from jose import JWTError

            from controller.api.auth_users import decode_access_token

            try:
                user_id = uuid.UUID(decode_access_token(authorization[7:].strip())["sub"])
            except (JWTError, KeyError, ValueError):
                return None
            async with db_session_module.AsyncSessionLocal() as session:
                user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            if user is None or not user.active:
                return None
            return Actor(label=user.username, user_id=user.id, tenant_id=user.tenant_id)

        if api_key:
            async with db_session_module.AsyncSessionLocal() as session:
                key = (
                    await session.execute(select(APIKey).where(APIKey.key_hash == hash_api_key(api_key)))
                ).scalar_one_or_none()
            if key is None:
                return None
            return Actor(label=f"api-key:{key.description or 'unnamed'}", user_id=None, tenant_id=key.tenant_id)
    except Exception:
        logger.exception("Could not resolve actor for operation log")
    return None


def _json_field(raw: bytes, *names: str) -> str | None:
    """Return the first non-empty string among *names* in a JSON object body."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    for name in names:
        value = data.get(name)
        if isinstance(value, str) and value:
            return value[:255]
    return None


def _error_detail(raw: bytes) -> str | None:
    """Extract FastAPI's "detail" message from an error response body."""
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return raw.decode("utf-8", "replace")[:500]
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, list):
        return "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
    return str(detail) if detail is not None else None


async def _buffer_request_body(receive: Receive) -> tuple[list[Message], bytes]:
    """Read the whole request body up front; return (messages to replay, body).

    API request bodies here are small JSON documents, so buffering them is
    cheap, and it lets the operation row carry the resource name from the
    very start (a "Create VM web-01" line instead of a bare "Create VM").
    """
    messages: list[Message] = []
    body = bytearray()
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break  # client disconnected
        body.extend(message.get("body", b""))
        if not message.get("more_body", False):
            break
    return messages, bytes(body)


class OperationLogMiddleware:
    """Record mutating HTTP requests in the operations table."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method: str = scope["method"]
        path: str = scope["path"]
        is_login = method == "POST" and path.rstrip("/") == operations.LOGIN_PATH
        if not is_login and operations.is_ignored(method, path):
            await self.app(scope, receive, send)
            return

        buffered, request_body = await _buffer_request_body(receive)
        replayed = iter(buffered)

        async def replaying_receive() -> Message:
            # Hand the application the buffered body first, then fall through
            # to the real receive (e.g. for the client-disconnect message).
            for message in replayed:
                return message
            return await receive()

        response_body = bytearray()
        http_status = 500

        async def capturing_send(message: Message) -> None:
            nonlocal http_status
            if message["type"] == "http.response.start":
                http_status = message["status"]
            elif message["type"] == "http.response.body" and len(response_body) < _MAX_ERROR_CAPTURE:
                response_body.extend(message.get("body", b""))
            await send(message)

        if is_login:
            try:
                await self.app(scope, replaying_receive, capturing_send)
            finally:
                await self._record_login(request_body, http_status, bytes(response_body))
            return

        actor = await resolve_actor(_header(scope, b"authorization"), _header(scope, b"x-api-key"))
        if actor is None:
            await self.app(scope, replaying_receive, send)
            return

        ctx = await operations.start_operation(
            user_label=actor.label,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            method=method,
            path=path,
            body_name=_json_field(request_body, "name", "hostname"),
        )
        if ctx is None:
            await self.app(scope, replaying_receive, send)
            return

        token = operations.bind_context(ctx)
        try:
            await self.app(scope, replaying_receive, capturing_send)
        except Exception as exc:
            await operations.finish_operation(ctx, http_status=500, error_detail=str(exc) or type(exc).__name__)
            raise
        finally:
            operations.unbind_context(token)
        await operations.finish_operation(
            ctx,
            http_status=http_status,
            error_detail=_error_detail(bytes(response_body)) if http_status >= 400 else None,
        )

    async def _record_login(self, request_body: bytes, http_status: int, response_body: bytes) -> None:
        username = _json_field(request_body, "username")
        if username is None:
            return
        success = http_status < 400
        await operations.record_completed(
            user_label=username,
            user_id=None,
            tenant_id=None,
            action="auth.login",
            description="Login" if success else "Failed login attempt",
            success=success,
            error=None if success else _error_detail(response_body),
            http_method="POST",
            path=operations.LOGIN_PATH,
        )
