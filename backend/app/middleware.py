"""Request id, timing and security headers for every request."""

from __future__ import annotations

import json
import logging
import re
import secrets
import sqlite3
import time
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from .services import logs

logger = logging.getLogger("nextime.api")


def _database_busy(exc: BaseException) -> bool:
    """SQLite's "database is locked" (or "busy"), however deep in the chain of causes."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, sqlite3.OperationalError) and ("locked" in str(seen) or "busy" in str(seen)):
            return True
        # SQLAlchemy keeps the driver's error as ``orig`` (and as the cause, when it raised it itself).
        seen = getattr(seen, "orig", None) or seen.__cause__ or seen.__context__
    return False


#: Paths whose calls explain nothing but fill the log.
QUIET_PATHS = ("/api/health", "/api/logs")
SLOW_MS = 3000
#: What takes long by nature: backups grow with the events and attachments, an upload with its file.
SLOW_EXPECTED = ("/api/backups", "/api/attachments", "/api/live")

#: The editor and the graph set inline styles, hence 'unsafe-inline' for styles only. Scripts stay strict.
#: ``blob:`` for images: a pasted picture is shown before it is uploaded.
CSP = (
    b"default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; font-src 'self' data:; "
    b"connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'"
)
SECURITY_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"content-security-policy", CSP),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"same-origin"),
    (b"x-frame-options", b"DENY"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=()"),
    # Nothing of nextime belongs in a search engine: not the app, not a shared calendar.
    (b"x-robots-tag", b"noindex, nofollow"),
)


#: The schemes a trusted proxy may hand on, per kind of connection. A WebSocket may arrive as http/https (nginx's
#: ``$scheme``) or as ws/wss (Traefik); either becomes ws/wss.
_SCHEMES = {
    "http": {"http": "http", "https": "https"},
    "websocket": {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"},
}


def forwarded_proto(headers: Any, kind: str = "http") -> str:
    """The scheme ``X-Forwarded-Proto`` names for this kind of connection (``http``/``https``, for a WebSocket
    ``ws``/``wss``), else an empty text. Of a list the RIGHTMOST value counts: it is the one the proxy nearest to the
    app appended, while the first can be sent by the visitor himself. With several header lines the last one counts.
    Case does not matter. Read only for a proxy in ``NEXTIME_TRUSTED_PROXIES`` (``ForwardedProtoMiddleware``)."""
    said: str | None = None
    for name, value in headers:
        if name.lower() == b"x-forwarded-proto":
            said = value.decode("latin-1")
    if said is None:
        return ""
    return _SCHEMES.get(kind, {}).get(said.rsplit(",", 1)[-1].strip().lower(), "")


class ForwardedProtoMiddleware:
    """The scheme a TLS-ending proxy says it received the request with (``X-Forwarded-Proto``), believed only when the
    connection comes from a proxy named in ``NEXTIME_TRUSTED_PROXIES``: the list the sign-in brake already trusts
    for ``X-Forwarded-For``. Nothing else of the proxy's headers is taken (not the host, not the port). Without that
    setting, or from any other sender, the scheme stays what the connection is (uvicorn starts with
    ``--no-proxy-headers``). It is what makes ``request.base_url`` and the card of the authentik button see
    ``https://`` behind a proxy, so that it stops warning about an address the sign-in does not use.

    Pure ASGI, so streaming responses and WebSockets are not buffered."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] in ("http", "websocket"):
            scheme = self._scheme(scope)
            if scheme:
                scope = {**scope, "scheme": scheme}
        await self.app(scope, receive, send)

    @staticmethod
    def _scheme(scope: dict) -> str:
        from .deps import is_trusted_proxy

        peer = scope.get("client")
        if not peer or not is_trusted_proxy(str(peer[0])):
            return ""
        return forwarded_proto(scope.get("headers", []), scope["type"])


class RequestContextMiddleware:
    """Pure ASGI, so streaming responses and WebSockets are not buffered."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        request_id = secrets.token_hex(3)
        scope.setdefault("state", {})["request_id"] = request_id
        token = logs.bind_request(request_id)
        start = time.perf_counter()
        status = 0
        path = scope.get("path", "?")

        async def send_wrapper(message: dict) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", request_id.encode("ascii")))
                # A response that brings its own policy keeps it (a plugin's frame, M7); every other gets the app's.
                present = {name.lower() for name, _value in headers}
                headers.extend(header for header in SECURITY_HEADERS if header[0] not in present)
            await send(message)

        method = scope.get("method", "WS")
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            duration = (time.perf_counter() - start) * 1000
            logger.exception("Unhandled error on %s %s after %dms", method, path, duration)
            raise
        else:
            if scope["type"] != "http":
                return
            duration = (time.perf_counter() - start) * 1000
            if status >= 500:
                logger.error("%s %s -> %s in %dms", method, path, status, duration)
            elif duration >= SLOW_MS and not path.startswith(QUIET_PATHS + SLOW_EXPECTED):
                logger.warning("Slow request: %s %s -> %s in %dms", method, path, status, duration)
            elif not path.startswith(QUIET_PATHS):
                logger.debug("%s %s -> %s in %dms", method, path, status, duration)
        finally:
            logs.unbind_request(token)


#: Methods that change something. They must name their browser tab (see ``GuardMiddleware``).
CHANGING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CLIENT_HEADER = b"x-nextime-client"
CLIENT_PATTERN = re.compile(rb"^[A-Za-z0-9_-]{8,64}$")
#: The API for programs (TIME-26): a key in ``Authorization``, no tab.
API_PREFIX = "/api/v1/"
#: The iPhone app's interface (TIME-29): it never takes a cookie, only the device's token (or a pairing's secret).
APP_PREFIX = "/api/app/"
#: Routes programs call without a tab: MCP (TIME-26), with its key in ``Authorization``.
PROGRAM_PATHS: frozenset[str] = frozenset({"/api/mcp"})
#: Largest body an ordinary request may carry.
MAX_BODY = 16 * 1024 * 1024
#: Where a larger body is expected, with its own limit checked while streaming. An upload's limit is the operator's
#: setting, checked by the route while the file arrives; this is only the ceiling above every setting.
#: Features with uploads add their paths here.
LARGE_BODIES: dict[str, int] = {
    "/api/backups/upload": 64 * 1024**3,
    # The same upload on the way of the shared module sicherung (Settings, Storage and backups).
    "/api/server/storage/backups/upload": 64 * 1024**3,
    # Attachments (TIME-9): the operator's limit is at most 10,000 MB; the route checks the one set.
    "/api/attachments": 10_000 * 1024**2,
    # The same from the iPhone app (TIME-29, interface 0.5).
    "/api/app/1/attachments": 10_000 * 1024**2,
}
#: The same for paths with an id in them; this is the ceiling, the route checks the operator's limit.
LARGE_BODY_PATTERNS: tuple[tuple[re.Pattern[str], int], ...] = ()


def body_limit(path: str) -> int:
    """The largest body a request to ``path`` may carry."""
    if path in LARGE_BODIES:
        return LARGE_BODIES[path]
    for pattern, limit in LARGE_BODY_PATTERNS:
        if pattern.match(path):
            return limit
    return MAX_BODY


class BodyTooLarge(Exception):
    pass


def _refuse(status: int, code: str, text: str, **values: Any) -> tuple[dict, dict]:
    body = json.dumps({"detail": {"code": code, "message": text, **values}}).encode()
    start = {
        "type": "http.response.start",
        "status": status,
        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
    }
    return start, {"type": "http.response.body", "body": body}


class GuardMiddleware:
    """Two walls in front of every route, including routes still to come.

    * **A change must name its tab** in ``X-Nextime-Client``. A page on another site can make a browser
      send a form, but not a request with a header of its own (that needs CORS, which nextime does not allow): so
      the header is the wall against cross-site requests too.
    * **A body has a size limit** before anything reads it: the declared length is checked first, and a body sent
      without one is counted while it arrives.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Callable, send: Callable) -> None:
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        # Programs and paired devices bring a token in ``Authorization`` instead: no other site can make a browser send
        # that header either.
        bearer = headers.get(b"authorization", b"")[:7].lower() == b"bearer "
        exempt = (scope["path"].startswith((API_PREFIX, APP_PREFIX)) or bearer
                  or scope["path"] in PROGRAM_PATHS)
        if scope.get("method") in CHANGING and not exempt and not CLIENT_PATTERN.match(headers.get(CLIENT_HEADER, b"")):
            for message in _refuse(400, "client_required", "Changes need the header X-Nextime-Client."):
                await send(message)
            return
        limit = body_limit(scope["path"])
        # The page says the limit in its sentence ("larger than allowed ({{max_mb}} MB)"), as the upload routes do.
        size = {"max_mb": max(1, limit // (1024 * 1024))}
        declared = headers.get(b"content-length")
        if declared is not None and (not declared.isdigit() or int(declared) > limit):
            for message in _refuse(413, "too_large", "The request is too large.", **size):
                await send(message)
            return
        received = 0
        overflow = False

        async def counting_receive() -> dict:
            nonlocal received, overflow
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    overflow = True
                    raise BodyTooLarge
            return message

        started = False

        async def tracking_send(message: dict) -> None:
            nonlocal started
            # The framework turns the aborted read into an answer of its own (400); the caller gets the true one.
            if overflow:
                if message["type"] == "http.response.start" and not started:
                    started = True
                    for refusal in _refuse(413, "too_large", "The request is too large.", **size):
                        await send(refusal)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except BodyTooLarge:
            if not started:
                for message in _refuse(413, "too_large", "The request is too large.", **size):
                    await send(message)


async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    from .services.storage import disk_full

    request_id = getattr(request.state, "request_id", None) or "-"
    if disk_full(exc):
        # Nothing was written (the transaction rolled back); what was there stays as it was.
        logger.error("The data folder is full: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=507,
            content={"detail": {"code": "storage_full",
                                "message": "The data folder is full. Nothing was saved; free some space.",
                                "request_id": request_id}},
            headers={"X-Request-Id": request_id},
        )
    if _database_busy(exc):
        # The database stayed busy past the wait (SQLite's busy timeout): a moment later it works. The browser gets
        # something to say and to retry instead of "went wrong".
        return JSONResponse(
            status_code=503,
            content={"detail": {"code": "busy", "message": "nextime is busy. Try again in a moment.",
                                "request_id": request_id}},
            headers={"X-Request-Id": request_id, "Retry-After": "2"},
        )
    return JSONResponse(
        status_code=500,
        content={
            "detail": {
                "code": "internal_error",
                "message": f"Something went wrong on the server. Request id: {request_id}",
                "request_id": request_id,
            }
        },
        headers={"X-Request-Id": request_id},
    )
