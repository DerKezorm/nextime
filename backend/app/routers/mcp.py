"""MCP over HTTP (``POST /api/mcp``, TIME-26 AC-3) from the shared module mcp (``vendor/nexmcp``, bauplaene/mcp 1.1.0).

The module brings the endpoint with its walls in a fixed order (an ``Origin`` 403 ``origin_refused`` (EC-14), the
operator's switch ``mcp_enabled`` 403 ``mcp_off`` as before (AC-5), wrong keys braked per sender, no valid key 401
``token_invalid`` as before, now with ``WWW-Authenticate`` naming the metadata, 240 requests a minute per key (EC-2), an
unknown protocol version, a body over 1 MB), OAuth for programs (``/api/oauth/…``, ``/.well-known/…``; the consent
page ``/oauth/authorize``), rights per tool, requests that wait for a yes and the operator's settings under
``/api/connections/mcp``. Keys are made under ``/api/connections`` (module schluessel) and by the older way
``/api/keys``; the tools are in ``services/mcp.py``, the store in ``services/mcp_store.py``.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request

from ..db import SessionLocal
from ..deps import client_ip, require_account, require_operator
from ..services import mcp
from ..services.mcp_store import Store
from ..vendor.nexmcp import api as mcp_api
from . import devices


def _store() -> Iterator[Store]:
    with SessionLocal() as db:
        yield Store(db)


def _address(request: Request) -> str:
    with SessionLocal() as db:
        return devices.base_url(request, db)


router = mcp_api.router(
    config=mcp.CONFIG,
    store=_store,
    account=require_account,
    operator=require_operator,
    address=_address,
    sender=client_ip,
    prefix="/api/connections/mcp",
)
