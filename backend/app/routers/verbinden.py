"""Connecting nextime with the other nex apps (the shared module verbinden, bauplaene/verbinden 0.1.1): the fixed
places of every app (``/.well-known/nex-app``, ``/api/connect/…``; the pages ``/connect/app`` and ``/connect/return``).
The store is ``services/verbinden_store.py``. The older ways (``PUT /api/nextasks``, ``PUT /api/nexpaper``) stay."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request

from ..db import SessionLocal
from ..deps import require_account, require_operator
from ..services.verbinden_store import CONFIG, Store, connected, old_key
from ..vendor.nexverbinden import api as verbinden_api
from . import devices


def _store() -> Iterator[Store]:
    with SessionLocal() as db:
        yield Store(db)


def _address(request: Request) -> str:
    with SessionLocal() as db:
        return devices.base_url(request, db)


router = verbinden_api.router(
    config=CONFIG,
    store=_store,
    account=require_account,
    operator=require_operator,
    address=_address,
    old_key=old_key,
    on_connected=connected,
)
