"""Keys in the interface (TIME-26): the member's list under Settings, My account, Connections, making one (shown once,
AC-1), revoking one (AC-4); and the operator's overview of how many there are, without contents (AC-5).

| Method | Path | What |
|---|---|---|
| GET | ``/api/keys`` | the account's keys, the operator's switches, the address programs use |
| POST | ``/api/keys`` | a new key; the key itself is in this answer only |
| DELETE | ``/api/keys/{id}`` | revoke at once, or remove an expired one |
| GET | ``/api/admin/keys`` | per account the number of keys, of them for MCP |
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Path, Request, Response
from pydantic import BaseModel, Field

from ..db import SessionLocal
from ..deps import Account, DbSession, OperatorAccount, require_account, require_operator
from ..services import apikeys, calendars
from ..services.schluessel_store import CONFIG, KeyStore
from ..vendor.nexschluessel import api as schluessel_api
from . import devices

router = APIRouter(tags=["keys"])

KeyId = Annotated[int, Path(ge=1, le=2**62)]


class KeyIn(BaseModel):
    name: str = Field(max_length=1000)
    level: str = Field(pattern="^(read|write)$")
    #: "für MCP", off from the start.
    mcp: bool = False
    #: The chosen calendars; left out or null for all (later ones too).
    calendar_ids: list[int] | None = Field(default=None, max_length=apikeys.MAX_CALENDARS)
    #: 30, 90 or 365 days, or null for never; 90 when left out (AC-11).
    days: int | None = apikeys.DEFAULT_DAYS
    #: What the dialog sends with "Anlegen": a second click gives the first key (EC-16).
    client_ref: str | None = Field(default=None, max_length=64)


@router.get("/api/keys", summary="The account's keys for programs and AI tools")
def listing(account: Account, db: DbSession, request: Request) -> dict[str, Any]:
    now = apikeys.clock()
    names = {item.calendar.id: item.calendar.name for item in calendars.accesses(db, account.id)}
    return {
        "api_allowed": apikeys.api_on(db),
        "mcp_allowed": apikeys.mcp_on(db),
        "address": devices.base_url(request, db),
        "max_keys": apikeys.MAX_KEYS,
        "expiry_days": list(apikeys.EXPIRY_DAYS),
        "default_days": apikeys.DEFAULT_DAYS,
        "keys": [apikeys.key_out(db, row, now, names) for row in apikeys.keys_of(db, account.id)],
    }


@router.post("/api/keys", status_code=201, summary="A new key; the key itself is shown this once")
def make(body: KeyIn, account: Account, db: DbSession, response: Response) -> dict[str, Any]:
    row, token = apikeys.make(db, account, name=body.name, level=body.level, mcp=body.mcp,
                              calendar_ids=body.calendar_ids, days=body.days, client_ref=body.client_ref)
    if token is None:
        response.status_code = 200
    return {"made": apikeys.key_out(db, row), "token": token}


@router.delete("/api/keys/{key_id}", status_code=204, summary="Revoke a key at once, or remove an expired one")
def revoke(key_id: KeyId, account: Account, db: DbSession) -> Response:
    apikeys.revoke(db, account, key_id)
    return Response(status_code=204)


@router.get("/api/admin/keys", summary="How many keys each account holds; never a key, a name or a calendar")
def overview(_operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    rows = apikeys.overview(db)
    return {"accounts": rows, "keys": sum(row["keys"] for row in rows), "mcp": sum(row["mcp"] for row in rows),
            "api_allowed": apikeys.api_on(db), "mcp_allowed": apikeys.mcp_on(db)}


def _key_store(db: DbSession) -> KeyStore:
    return KeyStore(db)


def _address(request: Request) -> str:
    with SessionLocal() as db:
        return devices.base_url(request, db)


#: The ways of the shared key module (schluessel 1.0.0) for the cards "API-Schlüssel", "KI-Werkzeuge (MCP)",
#: "Schnittstellen" and "API-Schlüssel aller Konten": list, make, change, revoke one's own; lock and unlock any, the
#: switches (operator). Only a browser session manages keys (``require_account``), never a key or a device.
connections_router = schluessel_api.router(config=CONFIG, store=_key_store, account=require_account,
                                           operator=require_operator, address=_address)
