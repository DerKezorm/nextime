"""What the installed web app keeps for reading without a connection (TIME-28 AC-3): the next weeks of the calendars
the account shows, as the views draw them.

The occurrences are exactly those of ``events.occurrence_list`` (the views' own list, its quick way through the stored
columns included), so "busy only" stays a block without title, place and notes, free events of such a calendar do not
come, hidden calendars and the trash neither. Each occurrence also brings its notes, read for all events of the window
with one query (never one query per event): the side panel shows them without a connection, and the search without a
connection looks in them (EC-11). A calendar shared "busy only" never gives notes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import error
from ..models import Account, Event, utcnow
from . import events, ical

#: How far one snapshot reaches at most: the four weeks ahead of the spec, the week behind (the week shown today starts
#: before today) and a few days of slack for the zone of the device.
MAX_DAYS = 40
#: Event ids per query (SQLite takes at most 32,766 parameters; far below that, and the same on every version).
CHUNK = 900


def _notes_of(text: str) -> tuple[str, dict[str, str]]:
    """The notes of a stored object: the series' (or the single event's), and those of changed occurrences by their
    name in the API (``ical.recurrence_key``)."""
    parsed = ical.parse(text)
    master = ""
    changed: dict[str, str] = {}
    for component in parsed.walk("VEVENT"):
        notes = str(component.get("DESCRIPTION", ""))
        if component.get("RECURRENCE-ID") is not None:
            changed[ical.recurrence_key(component.decoded("RECURRENCE-ID"))] = notes
        else:
            master = notes
    return master, changed


def snapshot(db: Session, account: Account, start: datetime, end: datetime) -> dict[str, Any]:
    """The occurrences in [start, end) with their notes, and when the server took them."""
    if end <= start or end - start > timedelta(days=MAX_DAYS):
        raise error("invalid_range", f"Ask for at most {MAX_DAYS} days at once.", 422, max=MAX_DAYS)
    taken = utcnow()
    rows = events.occurrence_list(db, account, start, end)
    wanted = sorted({row["id"] for row in rows if not row["busy_only"]})
    notes: dict[int, tuple[str, dict[str, str]]] = {}
    for first in range(0, len(wanted), CHUNK):
        part = wanted[first:first + CHUNK]
        for event_id, text in db.execute(select(Event.id, Event.ical).where(Event.id.in_(part))):
            try:
                notes[event_id] = _notes_of(text)
            except Exception:  # noqa: BLE001 - one object that does not read leaves its notes out, not the snapshot
                notes[event_id] = ("", {})
    for row in rows:
        if row["busy_only"]:
            row["notes"] = ""
            continue
        master, changed = notes.get(row["id"], ("", {}))
        row["notes"] = changed.get(row["occurrence"] or "", master)
    return {
        "taken_at": taken.isoformat().replace("+00:00", "Z"),
        "start": start.isoformat().replace("+00:00", "Z"),
        "end": end.isoformat().replace("+00:00", "Z"),
        "occurrences": rows,
    }
