"""Collections of calendars (TIME-13): "Work", "Family", "All", switched with one click like scenes of light.

They belong to the account and are the same on every device (AC-10); only which one is chosen stays on the device.
"All" is not stored: it is always every calendar the account shows, so a new calendar lands there and nowhere else
(AC-5), and it can neither be deleted nor edited (EC-3).

A calendar in the trash keeps its place in its collections but is left out of every answer, so that it is back where
it was when it comes back (EC-16). A share that ends takes the calendar out of the collections of that account
(EC-1, ``services/shares.py``).
"""

from __future__ import annotations

import json
import re

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import error
from ..models import Account, Collection, CollectionCalendar, utcnow
from . import calendars, live

NAME_MAX = 60


def clean_name(raw: str) -> str:
    """Blanks at the edges go; umlauts and emoji stay (EC-6); empty and longer than 60 are refused (EC-15)."""
    name = (raw or "").strip()
    if not name:
        raise error("name_missing", "Give the collection a name.", 422, field="name")
    if len(name) > NAME_MAX:
        raise error("name_too_long", f"At most {NAME_MAX} characters.", 422, field="name", max=NAME_MAX)
    return name


def _taken(db: Session, account_id: int, name: str, but: int | None = None) -> bool:
    """Another own collection of that name, upper and lower case alike ("familie" is "Familie", EC-15)."""
    wanted = name.casefold()
    for row in db.scalars(select(Collection).where(Collection.account_id == account_id)):
        if row.id != but and row.name.casefold() == wanted:
            return True
    return False


def _seen(db: Session, account_id: int) -> set[int]:
    """The calendars the account sees now (own and shared with it), hidden ones included (EC-4)."""
    return {item.calendar.id for item in calendars.accesses(db, account_id)}


def _clean_calendars(db: Session, account_id: int, ids: list[int]) -> list[int]:
    seen = _seen(db, account_id)
    unknown = [value for value in ids if value not in seen]
    if unknown:
        raise error("calendar_unknown", "This calendar does not exist.", 422, field="calendar_ids")
    return list(dict.fromkeys(ids))


def view(db: Session, account_id: int) -> list[dict]:
    """The account's own collections by name, each with the calendars it sees now (a deleted or no longer shared
    calendar is not listed, EC-1)."""
    seen = _seen(db, account_id)
    rows = db.scalars(select(Collection).where(Collection.account_id == account_id)).all()
    members: dict[int, list[int]] = {}
    if rows:
        for collection_id, calendar_id in db.execute(
                select(CollectionCalendar.collection_id, CollectionCalendar.calendar_id)
                .where(CollectionCalendar.collection_id.in_([row.id for row in rows]))):
            if calendar_id in seen:
                members.setdefault(collection_id, []).append(calendar_id)
    ordered = sorted(rows, key=lambda row: (row.name.casefold(), row.id))
    return [{"id": row.id, "name": row.name, "calendar_ids": sorted(members.get(row.id, [])),
             "with_tasks": bool(row.with_tasks), "task_areas": areas_of(row),
             "updated_at": row.updated_at.isoformat()} for row in ordered]


def areas_of(row: Collection) -> list[str]:
    """The areas of nextasks in a collection; ``["*"]`` for every area (one saved before areas with its tasks)."""
    if row.task_areas is None:
        return ["*"] if row.with_tasks else []
    try:
        found = json.loads(row.task_areas)
    except ValueError:
        return []
    return [one for one in found if isinstance(one, str)] if isinstance(found, list) else []


def _areas(areas: list[str]) -> str:
    return json.dumps(sorted({one for one in areas if one == "*" or re.fullmatch(r"[A-Za-z0-9-]{1,100}", one)}))


def _find(db: Session, account: Account, collection_id: int) -> Collection:
    row = db.get(Collection, collection_id)
    if row is None or row.account_id != account.id:
        raise error("collection_gone", "This collection is gone.", 404)
    return row


def _set_calendars(db: Session, row: Collection, ids: list[int]) -> None:
    """The calendars of the collection as given; calendars in the trash keep their place (EC-16)."""
    from ..models import Calendar

    keep_trashed = select(CollectionCalendar.calendar_id).join(
        Calendar, Calendar.id == CollectionCalendar.calendar_id).where(
        CollectionCalendar.collection_id == row.id, Calendar.deleted_at.is_not(None))
    trashed = set(db.scalars(keep_trashed))
    db.execute(delete(CollectionCalendar).where(CollectionCalendar.collection_id == row.id,
                                                CollectionCalendar.calendar_id.not_in(trashed)))
    for calendar_id in ids:
        if calendar_id not in trashed:
            db.add(CollectionCalendar(collection_id=row.id, calendar_id=calendar_id))


def create(db: Session, account: Account, *, name: str, calendar_ids: list[int],
           client_ref: str | None, with_tasks: bool = False,
           task_areas: list[str] | None = None) -> tuple[Collection, bool]:
    """A new collection. The same ``client_ref`` again (a second click on "Save", EC-10) gives back the first one."""
    name = clean_name(name)
    if client_ref:
        known = db.scalar(select(Collection).where(Collection.account_id == account.id,
                                                   Collection.client_ref == client_ref))
        if known is not None:
            return known, False
    if _taken(db, account.id, name):
        raise error("collection_name_taken", "There is a collection of that name already.", 422, field="name")
    ids = _clean_calendars(db, account.id, calendar_ids)
    row = Collection(account_id=account.id, name=name, client_ref=client_ref, with_tasks=with_tasks)
    if task_areas is not None:
        row.task_areas = _areas(task_areas)
        row.with_tasks = bool(areas_of(row))
    db.add(row)
    try:
        db.flush()
        _set_calendars(db, row, ids)
        live.touch_accounts(db, {account.id})
        db.commit()
    except IntegrityError:
        db.rollback()
        known = db.scalar(select(Collection).where(Collection.account_id == account.id,
                                                   Collection.client_ref == client_ref))
        if known is None:
            raise
        return known, False
    return row, True


def change(db: Session, account: Account, collection_id: int, *, name: str | None,
           calendar_ids: list[int] | None, with_tasks: bool | None = None,
           task_areas: list[str] | None = None) -> Collection:
    """Name and calendars as saved; the later save wins (EC-5), the other devices hear of it."""
    row = _find(db, account, collection_id)
    if name is not None:
        name = clean_name(name)
        if _taken(db, account.id, name, but=row.id):
            raise error("collection_name_taken", "There is a collection of that name already.", 422, field="name")
        row.name = name
    if calendar_ids is not None:
        _set_calendars(db, row, _clean_calendars(db, account.id, calendar_ids))
    if with_tasks is not None:
        row.with_tasks = with_tasks
        row.task_areas = None
    if task_areas is not None:
        row.task_areas = _areas(task_areas)
        row.with_tasks = bool(areas_of(row))
    row.updated_at = utcnow()
    live.touch_accounts(db, {account.id})
    db.commit()
    return row


def remove(db: Session, account: Account, collection_id: int) -> None:
    """Gone for good; calendars and events stay as they are (AC-8)."""
    row = _find(db, account, collection_id)
    db.delete(row)
    live.touch_accounts(db, {account.id})
    db.commit()


def leave(db: Session, account_id: int, calendar_id: int) -> None:
    """The calendar is no longer the account's to see (a share ended): out of its collections (EC-1)."""
    own = select(Collection.id).where(Collection.account_id == account_id)
    db.execute(delete(CollectionCalendar).where(CollectionCalendar.calendar_id == calendar_id,
                                                CollectionCalendar.collection_id.in_(own)))

