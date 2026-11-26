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
from ..errors import error
from ..models import ApiKey
from ..services import apikeys, calendars
from ..services.mcp_store import Store
from ..services.schluessel_store import API, CONFIG, MCP, KeyStore
from ..vendor.nexschluessel import api as schluessel_api
from ..vendor.nexschluessel import keys as schluessel_keys
from ..vendor.nexschluessel.errors import KeyRefused
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


#: The module's codes as these old ways answered them before (``invalid_input`` with the field).
FORMER = {"right_invalid": ("level",), "days_invalid": ("days",), "areas_invalid": ("calendar_ids",),
          "choose_areas": ("calendar_ids",)}


def _refused(refusal: KeyRefused) -> Exception:
    """A refusal of the module in the old ways' words: their codes (``keys_too_many``, ``key_name_missing``,
    ``invalid_input`` with ``fields``), their status."""
    if refusal.code in FORMER:
        return error("invalid_input", "The input is not valid.", 422, fields=list(FORMER[refusal.code]))
    return error(refusal.public_code, refusal.message, refusal.status, **refusal.details)


def _old(db: DbSession, key_id: int, names: dict[int, str] | None = None) -> dict[str, Any]:
    row = db.get(ApiKey, key_id)
    assert row is not None
    return apikeys.key_out(db, row, apikeys.clock(), names)


@router.get("/api/keys", summary="The account's keys for programs and AI tools")
def listing(account: Account, db: DbSession, request: Request) -> dict[str, Any]:
    """The old shape of TIME-26, from the shared key module (``keys.listing``)."""
    store = KeyStore(db)
    found = schluessel_keys.listing(store, CONFIG, account.id, now=apikeys.clock())
    names = {item.calendar.id: item.calendar.name for item in calendars.accesses(db, account.id)}
    return {
        "api_allowed": found["api"],
        "mcp_allowed": found["mcp"],
        "address": devices.base_url(request, db),
        "max_keys": apikeys.MAX_KEYS,
        "expiry_days": list(apikeys.EXPIRY_DAYS),
        "default_days": apikeys.DEFAULT_DAYS,
        # The API keys only, as before (MCP keys and programs have their card); by number, as before.
        "keys": [_old(db, number, names) for number in sorted(
            int(str(entry["id"])[len(API):]) for entry in found["keys"] if str(entry["id"]).startswith(API))],
    }


@router.post("/api/keys", status_code=201, summary="A new key; the key itself is shown this once")
def make(body: KeyIn, account: Account, db: DbSession, response: Response) -> dict[str, Any]:
    """Made by the shared key module (``keys.make``); the answer in the old shape (``made``, ``token``)."""
    try:
        made = schluessel_keys.make(
            KeyStore(db), CONFIG, account.id, kind="api", name=body.name, right=body.level,
            areas="all" if body.calendar_ids is None else body.calendar_ids, options={"mcp": body.mcp},
            days=body.days, client_ref=body.client_ref, now=apikeys.clock(),
            # The tick "also for AI tools" is retired on the page (MCP decisions 6, 11); this older way sets it as
            # before, so scripts notice nothing (decision 12).
            retired=True,
        )
    except KeyRefused as exc:
        db.rollback()
        raise _refused(exc) from exc
    if not made.new:
        response.status_code = 200
    return {"made": _old(db, int(str(made.key.id)[len(API):])), "token": made.secret if made.new else None}


@router.delete("/api/keys/{key_id}", status_code=204, summary="Revoke a key at once, or remove an expired one")
def revoke(key_id: KeyId, account: Account, db: DbSession) -> Response:
    try:
        schluessel_keys.revoke(KeyStore(db), CONFIG, account.id, f"{API}{key_id}")
    except KeyRefused as exc:
        raise _refused(exc) from exc
    return Response(status_code=204)


@router.get("/api/admin/keys", summary="How many keys each account holds; never a key, a name or a calendar")
def overview(_operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    """The old shape (per account the number of keys, of them for MCP), counted from the module's overview
    (``keys.overview``)."""
    store = KeyStore(db)
    counted: dict[int, dict[str, Any]] = {}
    for entry in schluessel_keys.overview(store, CONFIG, now=apikeys.clock()):
        # API keys as before; an MCP key made by hand counts as a key for MCP (programs have their own place).
        made_by_hand_mcp = str(entry["id"]).startswith(MCP) and not entry.get("program")
        if not str(entry["id"]).startswith(API) and not made_by_hand_mcp:
            continue
        holder = entry["account"]
        row = counted.setdefault(int(holder["id"]), {"account": {"id": int(holder["id"]),
                                                                 "name": holder.get("display_name") or holder["name"]},
                                                     "keys": 0, "mcp": 0})
        row["keys"] += 1
        row["mcp"] += 1 if made_by_hand_mcp or (entry.get("options") or {}).get("mcp") else 0
    rows = sorted(counted.values(), key=lambda row: row["account"]["name"].casefold())
    return {"accounts": rows, "keys": sum(row["keys"] for row in rows), "mcp": sum(row["mcp"] for row in rows),
            "api_allowed": store.switch("api"), "mcp_allowed": store.switch("mcp")}


def _key_store(db: DbSession) -> Store:
    """One store for both modules (schluessel and mcp), as the reference app has it."""
    return Store(db)


def _address(request: Request) -> str:
    with SessionLocal() as db:
        return devices.base_url(request, db)


#: The ways of the shared key module (schluessel 1.0.0) for the cards "API-Schlüssel", "KI-Werkzeuge (MCP)",
#: "Schnittstellen" and "API-Schlüssel aller Konten": list, make, change, revoke one's own; lock and unlock any, the
#: switches (operator). Only a browser session manages keys (``require_account``), never a key or a device.
connections_router = schluessel_api.router(config=CONFIG, store=_key_store, account=require_account,
                                           operator=require_operator, address=_address)
