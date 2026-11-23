"""The search of the signed-in account (TIME-22, ``services/search.py``).

| Method | Path | What |
|---|---|---|
| GET | ``/api/search?q=&calendars=&now=&zone=&offset=&limit=`` | hits by title, place and notes, coming then past |
| GET | ``/api/search/upcoming?calendars=&now=&zone=&offset=&limit=`` | every event of the calendars shown (column) |
| GET | ``/api/search/calendar/{id}?now=&zone=&offset=&limit=`` | every event of one calendar (also a hidden one) |

``calendars=1,4``: only those (the collection chosen on the device). ``now`` and ``zone``: the device's clock and
zone, which decide what counts as coming (TIME-11 EC-11); without them the server's.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, FastAPI, Query
from sqlalchemy import select

from ..db import SessionLocal
from ..deps import Account, DbSession
from ..errors import error
from ..models import Event, utcnow
from ..services import ical, search

logger = logging.getLogger("nextime.search")

#: Rows filled in per transaction at the start.
FILL_BATCH = 500


def fill_search_text() -> int:
    """Once per start: what the search looks in, for events written before it was kept (TIME-22). Block by block,
    each in its own transaction: a start that stops halfway goes on where it stopped next time."""
    filled = 0
    with SessionLocal() as db:
        ids = list(db.scalars(select(Event.id).where(Event.search_text.is_(None))))
    for start in range(0, len(ids), FILL_BATCH):
        with SessionLocal() as db:
            for row in db.scalars(select(Event).where(Event.id.in_(ids[start:start + FILL_BATCH]))):
                row.search_text = ical.search_text(row.ical)
                filled += 1
            db.commit()
    if filled:
        logger.info("Search texts filled in count=%s", filled)
    return filled


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        await asyncio.to_thread(fill_search_text)
    except Exception:
        logger.exception("A step at the start was left out step=fill_search_text")
    yield


router = APIRouter(prefix="/api/search", tags=["search"], lifespan=lifespan)


@router.get("", summary="Search events by title, place and notes")
def find(account: Account, db: DbSession, q: str = Query(default="", max_length=2000),
         calendars: str | None = Query(default=None, max_length=4000),
         now: str | None = Query(default=None, max_length=40), zone: str = Query(default="", max_length=64),
         offset: int = Query(default=0, ge=0, le=10_000_000),
         limit: int = Query(default=search.PAGE, ge=1, le=search.PAGE_MAX)) -> dict[str, Any]:
    only = _calendar_ids(calendars)
    moment = _moment_and_zone(now, zone)
    return search.search(db, account, q, only=only, now=moment, zone=zone, offset=offset, limit=limit)


def _calendar_ids(calendars: str | None) -> set[int] | None:
    if calendars is None:
        return None
    try:
        return {int(part) for part in calendars.split(",") if part.strip()}
    except ValueError:
        raise error("invalid_input", "The input is not valid.", 422, fields=["calendars"]) from None


def _moment_and_zone(now: str | None, zone: str) -> datetime:
    moment = utcnow()
    if now:
        try:
            moment = datetime.fromisoformat(now)
        except ValueError:
            raise error("invalid_input", "The input is not valid.", 422, fields=["now"]) from None
        if moment.tzinfo is None:
            raise error("invalid_input", "The input is not valid.", 422, fields=["now"])
    if zone:
        try:
            ical.zone(zone)
        except ValueError:
            raise error("invalid_time_zone", "This time zone is not known.", 422, field="zone") from None
    return moment


@router.get("/upcoming", summary="Every event of the calendars shown, coming then past, a series once")
def upcoming(account: Account, db: DbSession, calendars: str | None = Query(default=None, max_length=4000),
             now: str | None = Query(default=None, max_length=40), zone: str = Query(default="", max_length=64),
             offset: int = Query(default=0, ge=0, le=10_000_000),
             limit: int = Query(default=50, ge=1, le=search.PAGE_MAX),
             background: bool = Query(default=True),
             from_day: date | None = Query(default=None, alias="from")) -> dict[str, Any]:
    """The column beside Month and Week for "Alles" or a collection (TIME-12, TIME-13, asked for while testing,
    2026-11-23): ``calendars=1,4`` the collection's, left out every calendar shown; hidden ones never, a calendar the
    account does not see not at all, "busy only" without title and place, the same event in two calendars once, nothing
    from the trash. Today's "Nächste Termine" (TIME-11) asks with ``background=false`` and ``from=<tomorrow>``: only
    what is still coming from that day on, without holidays and the like."""
    moment = _moment_and_zone(now, zone)
    return search.search(db, account, "", only=_calendar_ids(calendars), now=moment, zone=zone, offset=offset,
                         limit=limit, everything=True, background=background, from_day=from_day)


@router.get("/calendar/{calendar_id}", summary="Every event of one calendar, coming then past, a series once")
def calendar_events(calendar_id: int, account: Account, db: DbSession,
                    now: str | None = Query(default=None, max_length=40), zone: str = Query(default="", max_length=64),
                    offset: int = Query(default=0, ge=0, le=10_000_000),
                    limit: int = Query(default=50, ge=1, le=search.PAGE_MAX),
                    background: bool = Query(default=True),
                    from_day: date | None = Query(default=None, alias="from")) -> dict[str, Any]:
    """The column beside the month while one calendar is shown alone (TIME-13, decided while testing, 2026-11-23): by
    the rights of the views ("busy only" without title and place, a calendar the account does not see is not there), a
    series once with its next occurrence and its rule, nothing from the trash."""
    from ..services import calendars

    if calendars.access(db, account.id, calendar_id) is None:
        raise error("not_found", "Not found.", 404)
    moment = _moment_and_zone(now, zone)
    return search.search(db, account, "", only={calendar_id}, now=moment, zone=zone, offset=offset, limit=limit,
                         include_hidden=True, everything=True, background=background, from_day=from_day)
