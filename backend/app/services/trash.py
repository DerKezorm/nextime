"""The trash (TIME-23): what an account's calendars lost, for the deadline the operator set (30 days from the start).

**What is in it.** Deleting stays soft, as it was since TIME-4 and TIME-5: a calendar or an event keeps its row with
``deleted_at``. A deleted calendar leaves its events as they were, so that they come back with it (AC-7); an event
deleted on its own is an entry of its own. Each row keeps the deadline that held when it was deleted (``trash_until``,
EC-13) and the name of who deleted it as it was then (``deleted_by_name``, EC-12).

**Whose it is.** The trash of the calendar's owner (EC-10): what somebody with "edit" deleted in a shared calendar
stands there, not with them. Deleting through the calendar account counts for the device's person (EC-2, EC-11).
What a source (iCloud, Google, a CalDAV server) deleted itself never comes here (``sync.remove``, AC-5).

**Back.** Exactly once (EC-6): the row is claimed with ``UPDATE … WHERE deleted_at IS NOT NULL``, so a second click,
a second window or the deadline running out in the same moment find nothing. A calendar is one row in one
transaction (EC-7); its events, shares, collections and guest link hang at it still. An event whose calendar is gone
by now asks for a target (EC-1, EC-9). An event of iCloud, Google or a CalDAV server goes to its source anew, unless
its deletion was still waiting to go there: then only the deletion is dropped (nothing was gone at the source).

**For good.** Once its deadline passed (the cleanup round, ``expire``) or with "Empty trash" (``empty``): the rows
first, the files of their attachments once the transaction is through. Nothing goes to a source then.

A calendar whose source went (disconnected, no longer chosen, gone there) while events of it lie in the trash stays as
an ``orphan``: shown nowhere, not an entry itself, gone with its last event (EC-9).
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from ..errors import error
from ..models import (
    Account,
    Attachment,
    Calendar,
    CalendarShare,
    CollectionCalendar,
    Event,
    GuestLink,
    RemoteChange,
    Source,
    TrashedPart,
    utcnow,
)
from . import calendars, live, settings_service

logger = logging.getLogger("nextime.trash")

#: The last days before the deadline, in which the time left shows orange (AC-6).
WARN_DAYS = 7
#: Entries per page of the list (EC-4).
PAGE = 100
PAGE_MAX = 500
#: Rows deleted for good per transaction.
BATCH = 500
#: How long restoring waits for a round of the event's source to end (a round takes at most 45 s).
SOURCE_WAIT = 50.0


def clock() -> datetime:
    """Now, by the server's clock (EC-14); the tests set it."""
    return utcnow()


def until(db: Session, deleted_at: datetime) -> datetime:
    """When something deleted at ``deleted_at`` goes for good: the deadline that holds now (EC-13)."""
    return deleted_at + timedelta(days=settings_service.trash_days(db))


def _via() -> str:
    from .events import via

    return via.get()


def name_of(db: Session, account_id: int | None) -> str:
    if account_id is None:
        return ""
    person = db.get(Account, account_id)
    return person.shown_name if person is not None else ""


def _deadline(row: Event | Calendar | TrashedPart, days: int) -> datetime:
    """A row deleted before the deadline was kept counts from its deletion with today's deadline."""
    if row.trash_until is not None:
        return row.trash_until
    assert row.deleted_at is not None
    return row.deleted_at + timedelta(days=days)


def _expired_clause(model: type[Event | Calendar | TrashedPart], now: datetime, days: int) -> Any:
    return or_(model.trash_until <= now,
               and_(model.trash_until.is_(None), model.deleted_at <= now - timedelta(days=days)))


def _alive_clause(model: type[Event | Calendar | TrashedPart], now: datetime, days: int) -> Any:
    return or_(model.trash_until > now,
               and_(model.trash_until.is_(None), model.deleted_at > now - timedelta(days=days)))


# --- The list (AC-1, AC-6, EC-3, EC-4) ---------------------------------------------------------------------------


def _owned_events(account_id: int) -> Any:
    return select(Event.id).join(Calendar, Calendar.id == Event.calendar_id).where(
        Calendar.owner_id == account_id, Event.deleted_at.is_not(None))


def _entries(db: Session, account_id: int, now: datetime, days: int) -> list[tuple[datetime, str, int]]:
    """Every entry of the account's trash still within its deadline: (deleted at, kind, id), newest first."""
    found: list[tuple[datetime, str, int]] = []
    for calendar_id, deleted_at in db.execute(select(Calendar.id, Calendar.deleted_at).where(
            Calendar.owner_id == account_id, Calendar.deleted_at.is_not(None), Calendar.orphan.is_(False),
            _alive_clause(Calendar, now, days))):
        found.append((deleted_at, "calendar", calendar_id))
    for event_id, deleted_at in db.execute(
            select(Event.id, Event.deleted_at).join(Calendar, Calendar.id == Event.calendar_id).where(
                Calendar.owner_id == account_id, Event.deleted_at.is_not(None), _alive_clause(Event, now, days))):
        found.append((deleted_at, "event", event_id))
    for part_id, deleted_at in db.execute(
            select(TrashedPart.id, TrashedPart.deleted_at).join(Event, Event.id == TrashedPart.event_id)
            .join(Calendar, Calendar.id == Event.calendar_id)
            .where(Calendar.owner_id == account_id, _alive_clause(TrashedPart, now, days))):
        found.append((deleted_at, "part", part_id))
    found.sort(key=lambda entry: (entry[0], entry[1] == "calendar", entry[2]), reverse=True)
    return found


def _who(db: Session, account: Account, by: int | None, stored: str, people: dict[int, Account | None]
         ) -> dict[str, Any] | None:
    """Who deleted it: oneself, or the person by the name it has, or had when it was deleted, if it is gone (EC-12)."""
    if by is None and not stored:
        return None
    if by is not None and by == account.id:
        return {"self": True, "name": account.shown_name}
    if by is not None and by not in people:
        people[by] = db.get(Account, by)
    person = people.get(by) if by is not None else None
    if person is not None and person.deleted_at is None:
        return {"self": False, "name": person.shown_name}
    return {"self": False, "name": stored}


def _times(deleted_at: datetime, deadline: datetime, now: datetime) -> dict[str, Any]:
    left = max(0, math.ceil((deadline - now).total_seconds() / 86400))
    return {"deleted_at": deleted_at.isoformat(), "expires_at": deadline.isoformat(), "days_left": left,
            "warn": left <= WARN_DAYS}


def listing(db: Session, account: Account, *, offset: int = 0, limit: int = PAGE) -> dict[str, Any]:
    now = clock()
    days = settings_service.trash_days(db)
    entries = _entries(db, account.id, now, days)
    page = entries[max(0, offset):max(0, offset) + max(1, min(limit, PAGE_MAX))]
    calendar_ids = [entry_id for _at, kind, entry_id in page if kind == "calendar"]
    event_ids = [entry_id for _at, kind, entry_id in page if kind == "event"]
    calendars_by_id = {row.id: row for row in db.scalars(select(Calendar).where(Calendar.id.in_(calendar_ids)))} \
        if calendar_ids else {}
    counts = dict(db.execute(select(Event.calendar_id, func.count()).where(
        Event.calendar_id.in_(calendar_ids), Event.deleted_at.is_(None)).group_by(Event.calendar_id)).all()) \
        if calendar_ids else {}
    part_ids = [entry_id for _at, kind, entry_id in page if kind == "part"]
    parts_by_id = {row.id: row for row in db.scalars(select(TrashedPart).where(TrashedPart.id.in_(part_ids)))} \
        if part_ids else {}
    event_ids += [part.event_id for part in parts_by_id.values()]
    events_by_id = {row.id: row for row in db.scalars(select(Event).where(Event.id.in_(event_ids)))} \
        if event_ids else {}
    their_calendars = {row.id: row for row in db.scalars(select(Calendar).where(
        Calendar.id.in_({row.calendar_id for row in events_by_id.values()})))} if events_by_id else {}
    kinds = dict(db.execute(select(Source.id, Source.kind).where(Source.id.in_(
        {row.source_id for row in their_calendars.values() if row.source_id is not None}))).all())
    people: dict[int, Account | None] = {}
    items: list[dict[str, Any]] = []
    for _at, kind, entry_id in page:
        if kind == "calendar":
            calendar = calendars_by_id[entry_id]
            assert calendar.deleted_at is not None
            items.append({
                "kind": "calendar", "id": calendar.id, "title": calendar.name, "color": calendar.color,
                "events": int(counts.get(calendar.id, 0)),
                "deleted_by": _who(db, account, calendar.deleted_by, calendar.deleted_by_name, people),
                **_times(calendar.deleted_at, _deadline(calendar, days), now),
            })
            continue
        if kind == "part":
            part = parts_by_id[entry_id]
            series = events_by_id[part.event_id]
            home = their_calendars[series.calendar_id]
            items.append({
                "kind": "part", "id": part.id, "part": part.kind, "title": part.title, "color": home.color,
                "calendar": {"id": home.id, "name": home.name, "source": kinds.get(home.source_id, "")},
                "calendar_gone": home.deleted_at is not None,
                # Its series is in the trash itself, or its calendar is gone: the series comes back first.
                "series_gone": series.deleted_at is not None or home.deleted_at is not None,
                "all_day": part.all_day,
                "start": part.starts_at.date().isoformat() if part.all_day else part.starts_at.isoformat(),
                "time_zone": part.time_zone,
                "recurring": part.kind == "following",
                "exceptions": part.exceptions,
                "deleted_by": _who(db, account, part.deleted_by, part.deleted_by_name, people),
                "deleted_via": part.deleted_via or "",
                **_times(part.deleted_at, _deadline(part, days), now),
            })
            continue
        row = events_by_id[entry_id]
        home = their_calendars[row.calendar_id]
        assert row.deleted_at is not None
        # Its calendar is gone by now (deleted itself, or its source went): restoring asks for a target (EC-1, EC-9).
        gone = home.deleted_at is not None
        exceptions = row.recurring and ("EXDATE" in row.ical or "RECURRENCE-ID" in row.ical)
        items.append({
            "kind": "event", "id": row.id, "title": row.title, "color": home.color,
            "calendar": {"id": home.id, "name": home.name, "source": kinds.get(home.source_id, "")},
            "calendar_gone": gone,
            "all_day": row.all_day,
            "start": row.starts_at.date().isoformat() if row.all_day else row.starts_at.isoformat(),
            "time_zone": row.time_zone,
            "recurring": row.recurring,
            "exceptions": exceptions,
            "deleted_by": _who(db, account, row.deleted_by, row.deleted_by_name, people),
            "deleted_via": row.deleted_via or "",
            **_times(row.deleted_at, _deadline(row, days), now),
        })
    return {"now": now.isoformat(), "trash_days": days, "warn_days": WARN_DAYS, "total": len(entries),
            "offset": offset, "items": items}


# --- Back (AC-2, AC-4, AC-5, AC-7, EC-1, EC-6 to EC-10, EC-16) -----------------------------------------------------


def _not_in_trash() -> Exception:
    return error("not_in_trash", "This is no longer in the trash.", 404)


def restore_calendar(db: Session, account: Account, calendar_id: int) -> Calendar:
    """The calendar is back with its events, its shares, its places in collections and, unless it ran out, its guest
    link (AC-7). One row, one transaction: half restored never happens (EC-7)."""
    from . import reminders, shares

    row = db.get(Calendar, calendar_id)
    now = clock()
    days = settings_service.trash_days(db)
    if row is None or row.owner_id != account.id or row.deleted_at is None or row.orphan:
        raise _not_in_trash()
    if _deadline(row, days) <= now:
        raise _not_in_trash()
    claimed = db.execute(update(Calendar).where(
        Calendar.id == calendar_id, Calendar.deleted_at.is_not(None), Calendar.orphan.is_(False),
    ).values(deleted_at=None, deleted_by=None, trash_until=None, deleted_by_name="", updated_at=now))
    if claimed.rowcount != 1:
        db.rollback()
        raise _not_in_trash()
    db.expire(row)
    # Every event counts as changed for the calendar account: a device that kept its sync token for the calendar
    # gets them all again (AC-2); nothing goes to a source (own calendars only come into the trash).
    shares._everything_changed(db, calendar_id)
    shares.calendar_restored(db, calendar_id)
    sharers = set(db.scalars(select(CalendarShare.account_id).where(CalendarShare.calendar_id == calendar_id)))
    live.touch_accounts(db, {account.id} | sharers)
    reminders.touch(calendar_id=calendar_id)
    db.commit()
    logger.info("Calendar restored from the trash calendar=%s", calendar_id)
    return row


def restore_event(db: Session, account: Account, event_id: int, target_id: int | None = None) -> Event:
    """The event is back where it was, with its series' exceptions, attachments, reminders, place and notes (AC-2,
    AC-4, EC-16); for everybody who sees the calendar and on their devices (EC-10). Its calendar gone by now: into
    ``target_id`` (EC-1, EC-9)."""
    from . import sync

    row = db.get(Event, event_id)
    now = clock()
    days = settings_service.trash_days(db)
    if row is None or row.deleted_at is None:
        raise _not_in_trash()
    home = db.get(Calendar, row.calendar_id)
    if home is None or home.owner_id != account.id:
        raise _not_in_trash()
    if _deadline(row, days) <= now:
        raise _not_in_trash()
    found = calendars.access(db, account.id, home.id)
    needs_target = found is None or not found.writable
    destination = home.id
    if needs_target:
        if target_id is None:
            raise error("target_needed", "Its calendar is gone. Choose another one.", 409)
        target = calendars.access(db, account.id, target_id)
        if target is None:
            raise error("not_found", "Not found.", 404, field="calendar_id")
        if not target.writable:
            raise error("calendar_read_only", "This calendar may only be read.", 403, field="calendar_id")
        destination = target.calendar.id
    # Itself restored meanwhile (another window): no clash, the claim below finds nothing (EC-6).
    clash = db.scalar(select(Event.id).where(Event.calendar_id == destination, Event.deleted_at.is_(None),
                                             Event.id != row.id, (Event.uid == row.uid) | (Event.href == row.href)))
    if clash is not None:
        raise error("event_exists", "The calendar holds this event already.", 409)
    home_id, source_id = home.id, home.source_id
    if destination == home_id and source_id is not None:
        # Back into a calendar of iCloud, Google or a CalDAV server: not while a round of its source runs. Otherwise
        # the deletion could be on its way there while it is dropped here, and the event would be gone on both sides
        # (review of TIME-23, finding 1). The read so far ends first: SQLite lets no stale snapshot write.
        lock = sync._lock(source_id)
        db.rollback()
        if not lock.acquire(timeout=SOURCE_WAIT):
            raise error("source_busy", "The calendar is being synced right now. Try again in a moment.", 409)
        try:
            return _claim_event(db, account, event_id, home_id, destination, now)
        finally:
            lock.release()
    return _claim_event(db, account, event_id, home_id, destination, now)


def _claim_event(db: Session, account: Account, event_id: int, home_id: int, destination: int,
                 now: datetime) -> Event:
    from . import events, sync

    claimed = db.execute(update(Event).where(Event.id == event_id, Event.deleted_at.is_not(None)).values(
        deleted_at=None, deleted_by=None, trash_until=None, deleted_by_name="", deleted_via="", calendar_id=destination,
        updated_at=now))
    if claimed.rowcount != 1:
        db.rollback()
        raise _not_in_trash()
    row = db.get(Event, event_id)
    assert row is not None
    db.refresh(row)
    home = db.get(Calendar, home_id)
    assert home is not None
    if destination != home.id:
        from . import attachments

        # Elsewhere it is a new event: at a source a new object; files cannot go to a source (TIME-9).
        row.remote_etag, row.remote_of = "", 0
        attachments.event_moved(db, row, destination)
        events.record_change(db, destination, row.href, row=row, by=account.id)
        if home.orphan:
            db.flush()
            _orphans_away(db, account.id)
    elif home.source_id is None:
        events.record_change(db, home.id, row.href, row=row, by=account.id)
    else:
        waiting = list(db.scalars(select(RemoteChange).where(
            RemoteChange.calendar_id == home.id, RemoteChange.href == row.href, RemoteChange.op == "delete")))
        if waiting:
            # The deletion never reached the source (no round runs, the lock says so): dropping it is all.
            for change in waiting:
                db.delete(change)
            events.record_change(db, home.id, row.href, row=row, by=account.id, remote=False)
        else:
            # Gone at the source since: made there anew (AC-5), "waiting" until it answers (EC-8).
            row.remote_etag, row.remote_of = "", 0
            events.record_change(db, home.id, row.href, row=row, by=account.id)
    db.commit()
    sync.nudge()
    logger.info("Event restored from the trash event=%s", event_id)
    return row


# --- Parts of a series (TIME-5 AC-6; review of TIME-23, finding 2) -----------------------------------------------


def part_deleted(db: Session, row: Event, *, kind: str, occurrence: str, title: str, start: Any, removed: str,
                 before: str, exceptions: bool, by: int | None) -> TrashedPart:
    """``events.delete`` took one occurrence, or this and the following, out of a series: the part goes into the trash
    of the calendar's owner, with what is needed to put it back."""
    from . import ical

    deleted_at = clock()
    all_day = not isinstance(start, datetime)
    starts_at = ical.midnight(start) if all_day else ical._as_utc(start)
    zone = "" if all_day else ical.zone_name(start)
    part = TrashedPart(event_id=row.id, kind=kind, occurrence=occurrence, title=title, starts_at=starts_at,
                       all_day=all_day, time_zone=zone, removed=removed, before=before, etag_after=row.etag,
                       exceptions=exceptions, deleted_at=deleted_at, deleted_by=by, deleted_by_name=name_of(db, by),
                       trash_until=until(db, deleted_at), deleted_via=_via())
    db.add(part)
    return part


def restore_part(db: Session, account: Account, part_id: int) -> Event:
    """The part is back in its series: an occurrence without its EXDATE and with its changed version, the following
    ones with the series' old end and their exceptions. Has the series changed since, so that the following cannot go
    back cleanly, or does the series no longer have the occurrence, the part comes back as an event of its own."""
    from . import events, ical, sync

    part = db.get(TrashedPart, part_id)
    now = clock()
    days = settings_service.trash_days(db)
    if part is None:
        raise _not_in_trash()
    row = db.get(Event, part.event_id)
    home = db.get(Calendar, row.calendar_id) if row is not None else None
    if row is None or home is None or home.owner_id != account.id or _deadline(part, days) <= now:
        raise _not_in_trash()
    found = calendars.access(db, account.id, home.id)
    if row.deleted_at is not None or found is None:
        raise error("series_gone", "Its series is not there any more. Restore the series first.", 409)
    if not found.writable:
        raise error("calendar_read_only", "This calendar may only be read.", 403)
    kind, key, removed, etag_after = part.kind, part.occurrence, part.removed, part.etag_after
    before = db.scalar(select(TrashedPart.before).where(TrashedPart.id == part_id)) or ""
    claimed = db.execute(delete(TrashedPart).where(TrashedPart.id == part_id))
    if claimed.rowcount != 1:
        db.rollback()
        raise _not_in_trash()
    if kind == "following":
        if before and row.etag == etag_after:
            # Nothing changed since: the series as it was before the cut.
            parsed = ical.parse(before)
            ical.stamp(ical.master_of(parsed))
            events._store(db, row, parsed, account.id)
            result = row
        else:
            source = ical.parse(before)
            original = events._occurrence_start(ical.master_of(source), key)
            uid = str(uuid.uuid4())
            result = events.add_new(db, row.calendar_id, events.tail_of(source, original, uid), uid, account.id)
    else:
        parsed = ical.parse(row.ical)
        master = ical.master_of(parsed)
        original = events._occurrence_start(master, key)
        exdates = master.get("EXDATE")
        if exdates is not None:
            values = [item.dt for entry in (exdates if isinstance(exdates, list) else [exdates]) for item in entry.dts]
            master.pop("EXDATE")
            for value in values:
                if not ical.same_recurrence(value, original):
                    master.add("EXDATE", value)
        changed = ical.overrides_of(ical.parse(removed)) if removed else []
        if events._occurs(parsed, original):
            if events._override(parsed, original) is None:
                for component in changed:
                    parsed.add_component(component)
            ical.stamp(master)
            events._store(db, row, parsed, account.id)
            result = row
        else:
            # The series moved on meanwhile: the occurrence comes back as an event of its own.
            shown = changed[0] if changed else master
            begin = changed[0].decoded("DTSTART") if changed else original
            uid = str(uuid.uuid4())
            result = events.add_new(db, row.calendar_id, events.single_from(
                shown, begin, begin + ical.duration_of(shown), uid), uid, account.id)
    db.commit()
    sync.nudge()
    logger.info("Part of a series restored from the trash event=%s kind=%s", row.id, kind)
    return result


# --- For good (AC-3, EC-6) ----------------------------------------------------------------------------------------


def _purge_events(db: Session, ids: list[int]) -> int:
    """Rows of events in the trash away now (with their attachments' rows), the files after the commit. A row
    restored meanwhile is no longer in the trash and stays."""
    from . import attachments

    if not ids:
        return 0
    files = db.execute(select(Attachment.id, Attachment.event_id).where(Attachment.event_id.in_(ids))).all()
    gone = set(db.scalars(delete(Event).where(Event.id.in_(ids), Event.deleted_at.is_not(None))
                          .returning(Event.id)))
    attachments.files_gone(db, [file_id for file_id, owner in files if owner in gone])
    return len(gone)


def _live_events_away(db: Session, calendar_id: int) -> None:
    from . import attachments

    files = list(db.scalars(select(Attachment.id).join(Event, Event.id == Attachment.event_id).where(
        Event.calendar_id == calendar_id, Event.deleted_at.is_(None))))
    db.execute(delete(Event).where(Event.calendar_id == calendar_id, Event.deleted_at.is_(None)))
    attachments.files_gone(db, files)


def _detach(db: Session, calendar: Calendar) -> None:
    """Only the events in the trash keep the calendar: as an orphan it is nobody's calendar any more."""
    _live_events_away(db, calendar.id)
    db.execute(delete(CalendarShare).where(CalendarShare.calendar_id == calendar.id))
    db.execute(delete(GuestLink).where(GuestLink.calendar_id == calendar.id))
    db.execute(delete(CollectionCalendar).where(CollectionCalendar.calendar_id == calendar.id))
    db.execute(delete(RemoteChange).where(RemoteChange.calendar_id == calendar.id))
    calendar.orphan = True
    calendar.source_id = None
    calendar.remote_url = ""
    calendar.sync_token = ""
    if calendar.deleted_at is None:
        calendar.deleted_at = clock()


def _retire_calendar(db: Session, calendar: Calendar, now: datetime, days: int) -> bool:
    """A calendar in the trash goes for good; events of it that were deleted on their own and still have time left
    stay (EC-13), with the calendar as an orphan. True when the calendar went."""
    later = db.scalar(select(func.count()).select_from(Event).where(
        Event.calendar_id == calendar.id, Event.deleted_at.is_not(None), _alive_clause(Event, now, days)))
    if later:
        _detach(db, calendar)
        return False
    files = list(db.scalars(select(Attachment.id).join(Event, Event.id == Attachment.event_id).where(
        Event.calendar_id == calendar.id)))
    gone = db.execute(delete(Calendar).where(Calendar.id == calendar.id, Calendar.deleted_at.is_not(None))
                      .returning(Calendar.id)).scalar()
    if gone is None:
        return False
    from . import attachments

    attachments.files_gone(db, files)
    return True


def keep_trashed(db: Session, calendar: Calendar) -> bool:
    """Its source goes (disconnected, no longer chosen, gone there): events of it in the trash stay restorable into
    another calendar until their deadline (EC-9). Called before the calendar's row goes; True when the row stays as an
    orphan (then it must not be deleted), False without such events: the row goes as before."""
    trashed = db.scalar(select(func.count()).select_from(Event).where(
        Event.calendar_id == calendar.id, Event.deleted_at.is_not(None)))
    if not trashed:
        return False
    _detach(db, calendar)
    db.flush()
    return True


def keep_trashed_of_source(db: Session, source_id: int) -> None:
    for calendar in db.scalars(select(Calendar).where(Calendar.source_id == source_id)):
        keep_trashed(db, calendar)


def empty(db: Session, account: Account, before: datetime | None) -> dict[str, int]:
    """"Empty trash": everything of the account's trash deleted until ``before`` (when the list was loaded), so that
    something deleted meanwhile in another window is not taken unseen (AC-3)."""
    now = clock()
    days = settings_service.trash_days(db)
    limit = min(before, now) if before is not None else now
    owned_parts = select(TrashedPart.id).join(Event, Event.id == TrashedPart.event_id).join(
        Calendar, Calendar.id == Event.calendar_id).where(Calendar.owner_id == account.id,
                                                          TrashedPart.deleted_at <= limit)
    parts_gone = db.execute(delete(TrashedPart).where(TrashedPart.id.in_(owned_parts))).rowcount or 0
    event_ids = list(db.scalars(_owned_events(account.id).where(Event.deleted_at <= limit)))
    removed = 0
    for start in range(0, len(event_ids), BATCH):
        removed += _purge_events(db, event_ids[start:start + BATCH])
    calendars_gone = 0
    for calendar in list(db.scalars(select(Calendar).where(
            Calendar.owner_id == account.id, Calendar.deleted_at.is_not(None), Calendar.orphan.is_(False),
            Calendar.deleted_at <= limit))):
        calendars_gone += int(_retire_calendar(db, calendar, now, days))
    _orphans_away(db, account.id)
    live.touch_accounts(db, {account.id})
    db.commit()
    logger.info("Trash emptied events=%s calendars=%s parts=%s", removed, calendars_gone, parts_gone)
    return {"events": removed + parts_gone, "calendars": calendars_gone}


def _orphans_away(db: Session, account_id: int | None = None) -> None:
    # A staging calendar of an import or a move (TIME-24) is no orphan of a source: its job removes it.
    statement = select(Calendar.id).where(Calendar.orphan.is_(True), Calendar.staging.is_(False),
                                          ~select(Event.id).where(Event.calendar_id == Calendar.id).exists())
    if account_id is not None:
        statement = statement.where(Calendar.owner_id == account_id)
    ids = list(db.scalars(statement))
    if ids:
        db.execute(delete(Calendar).where(Calendar.id.in_(ids)))


def expire(now: datetime | None = None) -> int:
    """The cleanup round: whatever's deadline passed goes for good (AC-3), by the server's clock (EC-14)."""
    from ..db import SessionLocal

    now = now or clock()
    removed = 0
    with SessionLocal() as db:
        days = settings_service.trash_days(db)
        removed += db.execute(delete(TrashedPart).where(_expired_clause(TrashedPart, now, days))).rowcount or 0
        db.commit()
        event_ids = list(db.scalars(select(Event.id).where(Event.deleted_at.is_not(None),
                                                           _expired_clause(Event, now, days))))
    for start in range(0, len(event_ids), BATCH):
        with SessionLocal() as db:
            removed += _purge_events(db, event_ids[start:start + BATCH])
            db.commit()
    with SessionLocal() as db:
        calendar_ids = list(db.scalars(select(Calendar.id).where(
            Calendar.deleted_at.is_not(None), Calendar.orphan.is_(False), _expired_clause(Calendar, now, days))))
    for calendar_id in calendar_ids:
        with SessionLocal() as db:
            calendar = db.get(Calendar, calendar_id)
            if calendar is not None and calendar.deleted_at is not None and not calendar.orphan:
                removed += int(_retire_calendar(db, calendar, now, days))
            db.commit()
    with SessionLocal() as db:
        _orphans_away(db)
        db.commit()
    if removed:
        logger.info("Trash deadline passed removed=%s", removed)
    return removed


def register() -> None:
    from . import cleanup

    if not any(name == "trash" for name, _job in cleanup._jobs):
        cleanup.register("trash", expire, every=3600)

