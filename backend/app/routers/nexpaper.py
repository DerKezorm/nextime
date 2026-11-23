"""The member's own nexpaper in the calendar: its open deadlines, to look at only. Everything here is for the
signed-in account alone, and the key never comes back.

| Method | Path | What |
|---|---|---|
| GET | ``/api/nexpaper`` | the connection: address, name, version, problem; whether the own network is allowed |
| PUT | ``/api/nexpaper`` | connect (or a new key): checked at once, kept only when it holds |
| PATCH | ``/api/nexpaper`` | "Meine Fristen" shown or hidden, like a calendar |
| DELETE | ``/api/nexpaper`` | disconnect; in nexpaper everything stays |
| GET | ``/api/deadlines?from=&to=`` | the open deadlines in those days (no ``from``: overdue ones too) |
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from pydantic import BaseModel, Field

from ..deps import Account, DbSession
from ..errors import error
from ..models import OPERATOR
from ..services import nexpaper

router = APIRouter(tags=["nexpaper"])

#: The farthest day a view asks for.
MAX_DAYS = 400


class ConnectIn(BaseModel):
    url: str = Field(max_length=nexpaper.MAX_URL + 100)
    token: str = Field(max_length=nexpaper.MAX_TOKEN + 100)


class ConnectionChange(BaseModel):
    hidden: bool


@router.get("/api/nexpaper", summary="The connection to nexpaper")
def connection(account: Account, db: DbSession) -> dict[str, Any]:
    return nexpaper.connection_out(db, nexpaper.connection_of(db, account.id))


@router.put("/api/nexpaper", summary="Connect nexpaper")
def connect(payload: ConnectIn, account: Account, db: DbSession) -> dict[str, Any]:
    try:
        row = nexpaper.connect(db, account.id, payload.url, payload.token)
    except nexpaper.NexpaperError as exc:
        if exc.local_only and account.role == OPERATOR:
            # The operator can change it and is told how; a member hears "cannot be reached" as for anything else.
            raise error("nexpaper_address_local", "This nexpaper is in the local network. Allow that under Settings, "
                        "Server, General: 'Connections into the local network'.", 422) from exc
        raise exc.http() from exc
    return nexpaper.connection_out(db, row)


@router.patch("/api/nexpaper", summary="Show or hide the deadlines")
def change(payload: ConnectionChange, account: Account, db: DbSession) -> dict[str, Any]:
    if nexpaper.connection_of(db, account.id) is None:
        raise error("nexpaper_not_connected", "No nexpaper is connected.", 409)
    nexpaper.set_hidden(db, account.id, payload.hidden)
    return nexpaper.connection_out(db, nexpaper.connection_of(db, account.id))


@router.delete("/api/nexpaper", status_code=204, summary="Disconnect nexpaper")
def disconnect(account: Account, db: DbSession) -> Response:
    nexpaper.disconnect(db, account.id)
    return Response(status_code=204)


@router.get("/api/deadlines", summary="The open deadlines of nexpaper")
def deadlines(account: Account, db: DbSession,
              to: Annotated[date, Query(description="The last day shown")],
              start: Annotated[date | None, Query(alias="from", description="The first day; none: overdue too")] = None,
              ) -> dict[str, Any]:
    today = nexpaper.clock().date()
    if abs((to - today).days) > MAX_DAYS:
        to = today + timedelta(days=MAX_DAYS if to > today else 0)
    if start is not None and (start > to or (to - start).days > MAX_DAYS):
        start = to - timedelta(days=MAX_DAYS) if start <= to else to
    return nexpaper.deadlines_in(db, account, start, to)
