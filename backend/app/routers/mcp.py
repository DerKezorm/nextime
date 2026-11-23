"""MCP over HTTP (``POST /api/mcp``, TIME-26 AC-3), as nexnotes has it. Keys are made and revoked in
``routers/keys.py``; a key works here when it was made "for MCP" and the operator has MCP switched on.

The protocol is JSON-RPC 2.0 as MCP's "Streamable HTTP" transport has it, without streams: every request gets its
answer as ``application/json``, a notification gets 202. No session is kept; each request names its key in
``Authorization: Bearer``. The walls are those of ``/api/v1/`` (``api_v1.authenticate``): no ``Origin`` (EC-14), the
operator's switch ``mcp_enabled`` (AC-5), wrong keys braked per sender, a valid key "for MCP" of an account that may
sign in, the key's rate (EC-2). The tools are in ``services/mcp.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from .. import __version__
from ..services import mcp
from ..services.apiv1 import Caller
from ..services.mcp import ToolError
from .api_v1 import authenticate

logger = logging.getLogger("nextime.mcp")

router = APIRouter(tags=["mcp"])

#: Protocol versions this server speaks, newest first.
PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
#: The largest request body: an event's notes at their limit, escaped as JSON, fit many times.
MAX_BODY = 1024 * 1024


def _error(message_id: Any, code: int, text: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def _tool_result(value: Any, failed: bool = False) -> dict[str, Any]:
    value = jsonable_encoder(value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}], "isError": failed}
    if not failed and isinstance(value, dict):
        result["structuredContent"] = value
    return result


async def call_tool(caller: Caller, name: Any, args: Any) -> dict[str, Any]:
    tool = mcp.BY_NAME.get(name) if isinstance(name, str) else None
    if tool is None or not caller.may(tool.level):
        # A tool above the key's level is as unknown as one that does not exist.
        return _tool_result(f"No tool called {str(name)[:100]!r}.", failed=True)
    if not isinstance(args, dict):
        return _tool_result("'arguments' must be an object.", failed=True)
    problem = mcp.check_arguments(tool.schema, args)
    if problem:
        return _tool_result(problem, failed=True)
    logger.info("MCP tool called tool=%s key_id=%s", tool.name, caller.key_id)
    try:
        return _tool_result(await tool.run(caller, dict(args)))
    except ToolError as exc:
        return _tool_result(str(exc), failed=True)
    except HTTPException as exc:
        return _tool_result(mcp._problem(exc), failed=True)


async def handle(message: Any, caller: Caller) -> dict[str, Any] | None:
    """One JSON-RPC message: its answer, or None for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "Invalid request.")
    method = message["method"]
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if "id" not in message:
        return None
    message_id = message["id"]
    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return {"jsonrpc": "2.0", "id": message_id, "result": {
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "nextime", "version": __version__},
            "instructions": mcp.INSTRUCTIONS,
        }}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": message_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message_id,
                "result": {"tools": [mcp.listed(tool) for tool in mcp.tools_for(caller)]}}
    if method == "tools/call":
        return {"jsonrpc": "2.0", "id": message_id,
                "result": await call_tool(caller, params.get("name"), params.get("arguments", {}))}
    return _error(message_id, -32601, "Method not found.")


def _refuse(status: int, code: str, text: str) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": text}}, status_code=status)


@router.post("/api/mcp", summary="MCP (JSON-RPC over HTTP) with a key made for MCP in Authorization: Bearer")
async def endpoint(request: Request) -> Response:
    asked = request.headers.get("mcp-protocol-version")
    if asked is not None and asked not in PROTOCOL_VERSIONS and not request.headers.get("origin"):
        return _refuse(400, "protocol_version", "This MCP protocol version is not spoken here.")
    caller = await asyncio.to_thread(authenticate, request, where="mcp")
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_BODY:
            return _refuse(413, "too_large", "The request is too large.")
    try:
        message = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError):
        # RecursionError: JSON nested deeper than Python's stack, sent to break the server.
        return JSONResponse(_error(None, -32700, "Parse error."), status_code=400)
    answer = await handle(message, caller)
    if answer is None:
        return Response(status_code=202)
    return JSONResponse(answer)


@router.get("/api/mcp", include_in_schema=False)
def no_stream() -> Response:
    # No server-sent stream: every answer comes with its request.
    return Response(status_code=405, headers={"Allow": "POST"})
