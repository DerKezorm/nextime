"""Calendars of the signed-in account (TIME-4).

| Method | Path | What |
|---|---|---|
| GET | ``/api/calendars`` | the list by group, the default for new events, the count against 20 |
| POST | ``/api/calendars`` | a new own calendar (201; the same ``client_ref`` again: 200 with the first one) |
| PATCH | ``/api/calendars/{id}`` | name, color, icon, background, reminder (owner); hidden, default, holiday (own) |
| PUT | ``/api/calendars/order`` | the order within one group |
| DELETE | ``/api/calendars/{id}`` | into the trash with its events |
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field

from ..deps import Account, DbSession
from ..models import Account as AccountRow
from ..models import CalendarSetting, Source
from ..services import calendars, settings_service, symbols

router = APIRouter(prefix="/api/calendars", tags=["calendars"])


class CalendarIn(BaseModel):
    name: str = Field(max_length=400)
    color: str = Field(default="slate", max_length=16)
    #: A symbol of the list nexnotes' folders have; "" for none (the color dot).
    icon: str = Field(default="", max_length=64)
    background: bool = False
    default: bool = False
    reminder: int = 0
    client_ref: str | None = Field(default=None, max_length=64)
    #: What new events of it start with, for this account (TIME-4); left out: as from the start.
    defaults: dict[str, Any] | None = None
    #: "Highlight its days" for this account; left out: as from the start (off for an own calendar).
    holiday: bool | None = None


class CalendarChange(BaseModel):
    name: str | None = Field(default=None, max_length=400)
    color: str | None = Field(default=None, max_length=16)
    #: "" takes the symbol away.
    icon: str | None = Field(default=None, max_length=64)
    background: bool | None = None
    reminder: int | None = None
    #: The account's own defaults for new events (TIME-4); ``{}`` as from the start.
    defaults: dict[str, Any] | None = None
    hidden: bool | None = None
    default: bool | None = None
    #: "Highlight its days", the account's own with any right; ``null`` goes back to as from the start (on for the
    #: built-in public holidays, off for everything else).
    holiday: bool | None = None


class OrderIn(BaseModel):
    ids: list[int] = Field(max_length=500)


def _view(db: DbSession, item: calendars.Access, row: CalendarSetting, default_id: int | None) -> dict[str, Any]:
    calendar = item.calendar
    owner = None
    if item.right != calendars.OWNER:
        person = db.get(AccountRow, calendar.owner_id)
        owner = person.shown_name if person else ""
    return {
        "id": calendar.id,
        "name": calendar.name,
        "color": calendar.color,
        "icon": symbols.shown(calendar.icon),
        "background": calendar.background,
        # Its days stand out in the views ("Tage hervorheben", the account's own): as from the start only the public
        # holidays (TIME-17), not the school holidays.
        "holiday": calendars.highlighted(calendar, row),
        "reminder": calendar.reminder,
        # The account's own defaults for new events of it (TIME-4), a source's calendar too.
        "defaults": row.new_defaults or {},
        "group": item.group,
        "source": "nextime",
        "right": item.right,
        "writable": item.writable,
        "hidden": row.hidden,
        "position": row.position,
        "default": calendar.id == default_id,
        "owner": owner,
        "shared": item.right == calendars.OWNER and _shared(db, calendar.id),
        "events": calendars.count_events(db, calendar.id),
        "read_only": calendar.read_only,
        "origin": _source(db, item),
    }


def _source(db: DbSession, item: calendars.Access) -> dict[str, Any] | None:
    """Where a calendar comes from, for the one who connected it: the group of each CalDAV account, "waiting", and
    "state of 15:52" while the source does not answer (TIME-15 AC-9, EC-5)."""
    calendar = item.calendar
    if calendar.source_id is None or item.right != calendars.OWNER:
        return None
    row = db.get(Source, calendar.source_id)
    if row is None:
        return None
    # A subscription made by "Mit nexfolk verbinden" names its partner: the sidebar shows it under "Aus nexfolk".
    return {"id": row.id, "kind": row.kind, "label": row.label, "state": row.state,
            "synced_at": row.synced_at.isoformat() if row.synced_at else None,
            "connection": (row.options or {}).get("connection") or None}


def _shared(db: DbSession, calendar_id: int) -> bool:
    from ..services.events import calendars_shared

    return calendars_shared(db, calendar_id)


def listing(db: DbSession, account: AccountRow) -> dict[str, Any]:
    calendars.ensure_seeded(db, account)
    chosen = calendars.default_of(db, account.id)
    default_id = chosen.calendar.id if chosen.calendar else None
    rows = calendars.ordered(db, account.id)
    db.commit()
    return {
        "calendars": [_view(db, item, row, default_id) for item, row in rows],
        "default_id": default_id,
        "default_moved": chosen.moved or None,
        "default_was": chosen.was or None,
        "count": len(rows),
        "smooth_up_to": calendars.SMOOTH_UP_TO,
        "reminders": list(calendars.REMINDERS),
        # The operator's switches the side panel of an event needs (TIME-8 AC-6, TIME-9 AC-1).
        "places": bool(settings_service.get(db, "address_suggestions")),
        "attachment_max_mb": settings_service.upload_max_mb(db),
    }


@router.get("", summary="The account's calendars by group, and the default for new events")
def list_calendars(account: Account, db: DbSession) -> dict[str, Any]:
    return listing(db, account)


@router.post("", status_code=201, summary="A new own calendar")
def create_calendar(payload: CalendarIn, response: Response, account: Account, db: DbSession) -> dict[str, Any]:
    # Checked before anything is made: a refused default makes no calendar.
    calendars.clean_defaults(payload.defaults)
    row, new = calendars.create(db, account, name=payload.name, color=payload.color, background=payload.background,
                                reminder=payload.reminder, make_default=payload.default, client_ref=payload.client_ref,
                                icon=payload.icon)
    own: dict[str, Any] = {}
    if payload.defaults:
        own["defaults"] = payload.defaults
    if payload.holiday is not None:
        own["holiday"] = payload.holiday
    if new and own:
        calendars.change(db, account, row.id, own)
    if not new:
        response.status_code = 200
    result = listing(db, account)
    result["created"] = row.id
    return result


@router.put("/order", summary="The order of the calendars within one group")
def order_calendars(payload: OrderIn, account: Account, db: DbSession) -> dict[str, Any]:
    calendars.reorder(db, account, payload.ids)
    return listing(db, account)


@router.patch("/{calendar_id}", summary="Change a calendar")
def change_calendar(calendar_id: int, payload: CalendarChange, account: Account, db: DbSession) -> dict[str, Any]:
    values = payload.model_dump(exclude_none=True)
    if "holiday" in payload.model_fields_set:
        # ``null`` here is a choice: back to as from the start.
        values["holiday"] = payload.holiday
    calendars.change(db, account, calendar_id, values)
    return listing(db, account)


@router.delete("/{calendar_id}", summary="Put a calendar with its events into the trash")
def delete_calendar(calendar_id: int, account: Account, db: DbSession) -> dict[str, Any]:
    events = calendars.delete(db, account, calendar_id)
    result = listing(db, account)
    result["deleted_events"] = events
    return result
