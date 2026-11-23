"""Reminders before an event (TIME-7), as a notification in the browser or the installed web app.

**Whose reminder** (AC-9): a reminder belongs to the person who set it (``reminder_choices``). An event the person
chose nothing for follows the person's default for its calendar (``calendar_settings.reminder_default``): from the
start the owner's choice when making an own calendar (``calendars.reminder``), "as in iCloud/Google" for the one who
connected such a calendar (the alarms of the source, AC-5), and none for everybody else (AC-3).

**When** (EC-3, EC-10, EC-11): a ticker looks every ``TICK_SECONDS`` at the occurrences of each account that has a
browser to remind (``events.occurrence_list``: series rolled out with their exceptions, moved and deleted ones as they
are, declined invitations and hidden calendars not). A reminder is due from its moment until ``CATCH_UP`` later and
never once the event began (a whole day: once its day ended); what the server missed while it was off comes late
within that, else not at all. Whole days of a lead time count on the wall clock in the event's zone, a whole day's
reminder at its time in the member's zone. ``reminder_log`` keeps what went out, so nothing comes twice. The plan of an
account is kept for ``REPLAN`` and made anew whenever events, choices, defaults or shares change (``touch``).

**Where** (AC-6, AC-7, EC-1): to every browser of the member that allowed notifications and keeps "Remind me on this
device" on, one row of ``notify_outbox`` each (kind ``reminder``), sent by ``notify.send_round``. While "Not twice" is
on, a browser on an Apple device (``ios``, ``mac``) stays quiet when the account has the calendar account set up on
such a device: Apple Calendar reminds there itself (``services/alarms.py`` hands it the person's alarms).

**What** (AC-1, EC-7, EC-12, EC-16): the words are made when the message goes, in the member's language, zone and
clock, from the event as it is then (``events.detail``): "Zahnarzt Ben · in 15 Minuten", below the times and the place.
Plain text, cut short. "Busy only" names neither title nor place. Before every try the reminder is asked again: the
account, its right, the device, the time of the occurrence, and the deadline; a failed try comes again every minute
until then (EC-14).

**Again in 5 minutes** (AC-4, EC-17): the notification carries a mark only the server can make, for this browser, this
event and occurrence; the service worker sends it back, and the reminder comes again on this browser only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from sqlalchemy import delete, false, func, insert, literal, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..errors import error
from ..models import (
    WRITABLE_SOURCES,
    Account,
    CalendarSetting,
    Device,
    Event,
    EventChange,
    NotifyOutbox,
    ReminderChoice,
    ReminderLog,
    UsedMark,
    UtcDateTime,
    WebPushSubscription,
    utcnow,
)
from ..security import account_out, server_key
from . import alarms, calendars, display, webpush

logger = logging.getLogger("nextime.reminders")

#: A reminder the server missed comes at most this late (EC-3).
CATCH_UP = timedelta(minutes=15)
#: How long a reminder "at the start" (0 minutes) may still come after the start.
AT_THE_START = timedelta(minutes=1)
#: A calendar account device counts for "Not twice" while it synced within this long (review of TIME-7, finding 3).
DEVICE_RECENT = timedelta(days=14)
#: How far ahead a plan reaches, and how long it is kept before it is made anew.
AHEAD = timedelta(minutes=10)
REPLAN = timedelta(minutes=5)
TICK_SECONDS = 20
SNOOZE = timedelta(minutes=5)
#: A reminder that could not be delivered is tried again this often, until its deadline (EC-14).
RETRY = timedelta(minutes=1)
#: How long "Again in 5 min" works after the notification came.
SNOOZE_VALID = timedelta(days=1)
MAX_PER_EVENT = 10
#: The fixed choices (AC-2): minutes before a timed event; for a whole day "the day before at 18:00", "on the day at
#: 09:00".
PRESETS = (5, 15, 30, 60, 1440)
ALL_DAY_PRESETS = (360, -540)
#: The choices of a calendar's default (AC-3, AC-8); ``source``: as the source has it (AC-5).
NONE = "none"
SOURCE = "source"
DEFAULTS = (5, 15, 30, 60, 1440)
TITLE_CHARS = 60
TEXT_CHARS = 300
LOG_KEEP = timedelta(days=2)
APPLE = ("iphone", "ipad", "mac")
NOT_DOUBLE = "reminders_not_double"


def _now() -> datetime:
    return utcnow()


#: The clock of the reminders; the tests put their own here.
clock: Callable[[], datetime] = _now

_revision_lock = threading.Lock()
#: What changed since a plan was made (review of TIME-7, finding 4): per calendar a running number and the events
#: changed with it (``None``: the whole calendar, a share or a default); per account its own number (choices, its
#: zone, "Not twice"). A plan is made anew only for the accounts that see what changed, and for a changed event only
#: that event.
_calendar_rev: dict[int, int] = {}
_calendar_log: dict[int, list[tuple[int, str | None]]] = {}
_account_rev: dict[int, int] = {}
LOG_LENGTH = 500


def touch(*, calendar_id: int | None = None, href: str | None = None, account_id: int | None = None) -> None:
    """Something plans depend on changed: an event of a calendar (``href``), a whole calendar, or what one account
    chose. Without anything: everything (a restored backup, a test)."""
    with _revision_lock:
        if calendar_id is not None:
            number = _calendar_rev.get(calendar_id, 0) + 1
            _calendar_rev[calendar_id] = number
            log = _calendar_log.setdefault(calendar_id, [])
            log.append((number, href))
            del log[:-LOG_LENGTH]
        if account_id is not None:
            _account_rev[account_id] = _account_rev.get(account_id, 0) + 1
        if calendar_id is None and account_id is None:
            _plans.clear()


def touch_on_commit(db: Session, *, calendar_id: int, href: str | None = None) -> None:
    """``touch`` now and once more when the change is committed: a plan made in between from what was not yet
    committed does not stay."""
    from sqlalchemy import event

    touch(calendar_id=calendar_id, href=href)
    event.listen(db, "after_commit", lambda _session: touch(calendar_id=calendar_id, href=href), once=True)


def _changed_since(calendar_id: int, since: int) -> set[str] | None:
    """The events of a calendar changed since ``since``; ``None`` when more than single events changed."""
    with _revision_lock:
        number = _calendar_rev.get(calendar_id, 0)
        if number == since:
            return set()
        log = _calendar_log.get(calendar_id, [])
        if not log or log[0][0] > since + 1:
            return None
        hrefs: set[str] = set()
        for entry, href in log:
            if entry <= since:
                continue
            if href is None:
                return None
            hrefs.add(href)
        return hrefs


# --- Choices and defaults ---------------------------------------------------------------------------------------


def clean(values: list[Any], all_day: bool) -> list[int]:
    """Lead times as the page sends them: whole numbers within the range, each once (EC-8)."""
    if len(values) > MAX_PER_EVENT:
        raise error("too_many_reminders", f"At most {MAX_PER_EVENT} reminders.", 422, field="reminders",
                    max=MAX_PER_EVENT)
    found: set[int] = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or not alarms.valid(value, all_day):
            raise error("invalid_reminder", "This reminder is not valid.", 422, field="reminders")
        found.add(value)
    return sorted(found, reverse=True)


def for_all_day(minutes: int) -> int:
    """A default for timed events on a whole day: under a day "on the day at 09:00", else "the day before at 18:00"."""
    return 360 if minutes >= 1440 else -540


def default_choice(access: calendars.Access, stored: str) -> str:
    """What the default of a calendar is for the account: its own choice, else as from the start (AC-3, AC-5, AC-9)."""
    if stored:
        return stored
    if access.right == calendars.OWNER:
        if access.kind in WRITABLE_SOURCES:
            return SOURCE
        if not access.kind and access.calendar.reminder:
            return str(access.calendar.reminder)
    return NONE


def options(access: calendars.Access) -> list[str]:
    """The choices of the default: "as in iCloud/Google" only for the one who connected the calendar."""
    first = [SOURCE] if access.right == calendars.OWNER and access.kind in WRITABLE_SOURCES else []
    return [*first, NONE, *[str(value) for value in DEFAULTS]]


def default_leads(choice: str, all_day: bool, etag: str = "", text: str = "") -> list[int]:
    if choice == NONE:
        return []
    if choice == SOURCE:
        return alarms.source_leads(etag, text)
    minutes = int(choice)
    return [for_all_day(minutes) if all_day else minutes]


def _stored_defaults(db: Session, account_id: int) -> dict[int, str]:
    return dict(db.execute(select(CalendarSetting.calendar_id, CalendarSetting.reminder_default).where(
        CalendarSetting.account_id == account_id)).all())


def _choices(db: Session, account_id: int, event_ids: list[int]) -> dict[int, ReminderChoice]:
    found: dict[int, ReminderChoice] = {}
    for start in range(0, len(event_ids), 500):
        part = event_ids[start:start + 500]
        for row in db.scalars(select(ReminderChoice).where(ReminderChoice.account_id == account_id,
                                                           ReminderChoice.event_id.in_(part))):
            found[row.event_id] = row
    return found


@dataclass
class Leads:
    minutes: list[int]
    #: The person chose for this event itself; else it follows the default.
    own: bool
    #: What the default of its calendar gives.
    default: list[int]
    #: The VALARMs a device sent, to hand back to it.
    raw: str = ""
    #: Single occurrences with alarms of their own a device set: by the occurrence's name, their lead times.
    occurrences: dict[str, list[int]] = field(default_factory=dict)


def leads_for(db: Session, account_id: int, rows: list[Event], access_of: dict[int, calendars.Access],
              stored: dict[int, str] | None = None) -> dict[int, Leads]:
    """The lead times of many events for one account, in one go."""
    stored = _stored_defaults(db, account_id) if stored is None else stored
    chosen = _choices(db, account_id, [row.id for row in rows])
    found: dict[int, Leads] = {}
    for row in rows:
        access = access_of.get(row.calendar_id)
        if access is None:
            continue
        choice = default_choice(access, stored.get(row.calendar_id, ""))
        if choice == SOURCE and row.alarm_leads is not None:
            default = sorted({int(value) for value in row.alarm_leads}, reverse=True)
        else:
            default = default_leads(choice, row.all_day, row.etag, row.ical if choice == SOURCE else "")
        mine = chosen.get(row.id)
        if mine is not None:
            occurrences = {str(key): [int(value) for value in (entry or {}).get("minutes", [])]
                           for key, entry in (mine.occurrences or {}).items() if isinstance(entry, dict)}
            if mine.minutes is None:
                # The series follows the default again; single occurrences set on a device keep theirs.
                found[row.id] = Leads(default, False, default, mine.alarms or "", occurrences)
            else:
                found[row.id] = Leads(sorted({int(value) for value in mine.minutes}, reverse=True), True, default,
                                      mine.alarms or "", occurrences)
        else:
            found[row.id] = Leads(default, False, default)
    return found


def of_event(db: Session, account: Account, row: Event, access: calendars.Access) -> dict[str, Any]:
    """What the side panel shows of the member's reminders at an event (AC-2, AC-3)."""
    leads = leads_for(db, account.id, [row], {row.calendar_id: access})[row.id]
    return {"minutes": leads.minutes, "own": leads.own, "default": leads.default,
            "presets": list(ALL_DAY_PRESETS if row.all_day else PRESETS),
            # Occurrences of the series with reminders of their own, set on a device (review of TIME-7, finding 5).
            "occurrences": sorted(leads.occurrences)}


def _devices_learn(db: Session, calendar_id: int, hrefs: list[str] | None = None) -> None:
    """The calendar account's devices fetch the events again (their alarms changed for somebody)."""
    if hrefs is None:
        db.execute(insert(EventChange).from_select(
            ["calendar_id", "href", "deleted", "created_at"],
            select(literal(calendar_id), Event.href, false(), literal(utcnow(), UtcDateTime()))
            .where(Event.calendar_id == calendar_id, Event.deleted_at.is_(None))))
    else:
        for href in hrefs:
            db.add(EventChange(calendar_id=calendar_id, href=href, deleted=False))


def choose(db: Session, account: Account, row: Event, minutes: list[int] | None, raw: str = "",
           occurrences: dict[str, Any] | None = None) -> None:
    """The member's own reminders for an event (AC-2), or ``None`` to follow the default again; ``occurrences``: single
    occurrences with alarms of their own a device set, which stay with their blocks in ``raw``. The caller commits."""
    if row.id is None:
        db.flush()
    found = db.get(ReminderChoice, (row.id, account.id))
    if minutes is None and not occurrences:
        if found is not None:
            db.delete(found)
    elif found is None:
        db.add(ReminderChoice(event_id=row.id, account_id=account.id, minutes=minutes, alarms=raw,
                              occurrences=occurrences or None, updated_at=utcnow()))
    else:
        found.minutes, found.alarms, found.updated_at = minutes, raw, utcnow()
        found.occurrences = occurrences or None
    _devices_learn(db, row.calendar_id, [row.href])
    touch(account_id=account.id)


def set_for_event(db: Session, account: Account, event_id: int, minutes: list[Any] | None) -> None:
    """``PUT /api/events/{id}/reminders``: anybody who sees the event, with any right (AC-9, EC-12). It changes the
    series; single occurrences with alarms of their own a device set keep them (review of TIME-7, finding 5)."""
    from . import events

    row, _found = events.find(db, account, event_id, for_writing=False)
    known = db.get(ReminderChoice, (row.id, account.id))
    kept = dict(known.occurrences or {}) if known is not None else {}
    raw = alarms.keep_occurrences(known.alarms or "", kept) if known is not None else ""
    choose(db, account, row, None if minutes is None else clean(minutes, row.all_day), raw, kept)
    db.commit()


def set_default(db: Session, account: Account, calendar_id: int, value: str) -> None:
    """The default of a calendar for the account (AC-3, AC-8). For an own calendar it is the one chosen when making it
    ("Reminder for new events")."""
    found = calendars.access(db, account.id, calendar_id)
    if found is None:
        raise error("not_found", "Not found.", 404)
    if value not in options(found):
        raise error("invalid_reminder", "This reminder is not valid.", 422, field="reminder")
    setting = calendars.setting(db, account.id, calendar_id)
    if found.right == calendars.OWNER and not found.kind:
        found.calendar.reminder = 0 if value == NONE else int(value)
        setting.reminder_default = ""
    else:
        setting.reminder_default = value
    _devices_learn(db, calendar_id)
    touch(account_id=account.id)
    db.commit()


def owner_default_changed(db: Session, calendar_id: int) -> None:
    """``calendars.change`` changed "Reminder for new events": the devices fetch the alarms again."""
    _devices_learn(db, calendar_id)
    touch(calendar_id=calendar_id)


_adopted = False


def _receivers(db: Session, row: Event) -> list[int]:
    """Who gets the alarms an event of an own calendar carried before this version: everybody who saw them on a device
    (the owner and members of the calendar with a device at the calendar account; "busy only" never had them); when
    nobody has a device, the one who stored the event last."""
    from ..models import Calendar, CalendarShare

    calendar = db.get(Calendar, row.calendar_id)
    if calendar is None:
        return []
    people = [calendar.owner_id, *db.scalars(select(CalendarShare.account_id).where(
        CalendarShare.calendar_id == calendar.id, CalendarShare.right != "busy"))]
    alive = set(db.scalars(select(Account.id).where(Account.id.in_(people), Account.deleted_at.is_(None))))
    with_device = set(db.scalars(select(Device.account_id).where(Device.account_id.in_(alive))))
    found = [person for person in dict.fromkeys(people) if person in with_device]
    if found:
        return found
    writer = row.updated_by or row.created_by
    return [writer] if writer is not None and writer in alive else []


def adopt_stored_alarms() -> int:
    """Once per start: alarms a device stored in an event of an own calendar before reminders were each person's own
    (before this version) become the reminders of everybody who saw them on a device, and leave the stored object
    (review of TIME-7, finding 6). One event at a time in its own transaction: a start that breaks off leaves the rest
    for the next, and nobody gets a reminder twice. An event that cannot be read is logged and left; the start goes on.
    Returns how many events it took them from."""
    global _adopted
    if _adopted:
        return 0
    _adopted = True
    from ..db import SessionLocal
    from ..models import Calendar
    from . import ical

    moved = 0
    with SessionLocal() as db:
        ids = list(db.scalars(select(Event.id).join(Calendar, Calendar.id == Event.calendar_id).where(
            Calendar.source_id.is_(None), Event.deleted_at.is_(None), Event.ical.contains("BEGIN:VALARM"))))
    for event_id in ids:
        try:
            with SessionLocal() as db:
                row = db.get(Event, event_id)
                if row is None or not alarms.present(row.ical):
                    continue
                parsed = ical.parse(row.ical)
                taken = alarms.take(parsed, row.ical)
                if not taken.only_defaults:
                    for person in _receivers(db, row):
                        if db.get(ReminderChoice, (row.id, person)) is None:
                            db.add(ReminderChoice(event_id=row.id, account_id=person, minutes=taken.minutes,
                                                  alarms=taken.raw, occurrences=taken.occurrences or None,
                                                  updated_at=utcnow()))
                row.ical = alarms.strip(row.ical)
                row.etag = ical.etag_of(row.ical)
                row.size = len(row.ical.encode("utf-8"))
                db.add(EventChange(calendar_id=row.calendar_id, href=row.href, deleted=False))
                db.commit()
                moved += 1
        except Exception:
            logger.exception("The alarms of an event could not be taken over event_id=%s", event_id)
    if moved:
        logger.info("Alarms of events taken over as reminders count=%s", moved)
        touch()
    return moved


def fill_alarm_leads() -> int:
    """Once per start: the lead times of events of sources written before they were kept as a column (review of
    TIME-7, finding 4)."""
    from ..db import SessionLocal
    from ..models import Calendar
    from . import ical

    filled = 0
    with SessionLocal() as db:
        ids = list(db.scalars(select(Event.id).join(Calendar, Calendar.id == Event.calendar_id).where(
            Calendar.source_id.is_not(None), Event.alarm_leads.is_(None), Event.ical.contains("BEGIN:VALARM"))))
    for start in range(0, len(ids), 500):
        with SessionLocal() as db:
            for row in db.scalars(select(Event).where(Event.id.in_(ids[start:start + 500]))):
                try:
                    leads = alarms.leads(ical.parse(row.ical)) if alarms.present(row.ical) else []
                except Exception:  # noqa: BLE001 - an event a source broke reminds of nothing
                    leads = []
                row.alarm_leads, row.alarm_lead_max = leads, (max(leads) if leads else None)
                filled += 1
            db.commit()
    return filled


# --- Not twice with Apple Calendar (AC-7) --------------------------------------------------------------------------


def not_double(account: Account) -> bool:
    stored = account.preferences if isinstance(account.preferences, dict) else {}
    return bool(stored.get(NOT_DOUBLE, True))


def set_not_double(db: Session, account: Account, on: bool) -> None:
    row = db.get(Account, account.id) or account
    preferences = dict(row.preferences) if isinstance(row.preferences, dict) else {}
    preferences[NOT_DOUBLE] = bool(on)
    row.preferences = preferences
    account.preferences = preferences
    db.commit()


def kind_of(platform: str) -> str:
    """``ios`` of before is an iPhone."""
    return "iphone" if platform == "ios" else platform


def apple_devices(db: Session, account_id: int, now: datetime | None = None) -> set[str]:
    """The kinds of Apple devices (iPhone, iPad, Mac) on which the account set up the calendar account and synced in
    the last ``DEVICE_RECENT`` (review of TIME-7, finding 3)."""
    since = (now or clock()) - DEVICE_RECENT
    return {kind_of(platform) for platform in db.scalars(select(Device.platform).where(
        Device.account_id == account_id, Device.platform.in_((*APPLE, "ios")), Device.last_used_at >= since))}


def through_account(db: Session, account: Account) -> set[int]:
    """The calendars the account's devices get over the calendar account: what Apple Calendar can remind of there
    (not hidden ones, not the owner's own iCloud, Google and CalDAV calendars, which come from their source)."""
    from ..routers.devices import PASSED_BY

    return {item.calendar.id for item, setting in calendars.ordered(db, account.id)
            if not setting.hidden and not (item.right == calendars.OWNER and item.kind in PASSED_BY)}


def silenced(account: Account, target: WebPushSubscription, apple: set[str], calendar_passes: bool = True) -> bool:
    """This browser stays quiet for an event: it runs on an Apple device of a kind on which the calendar account is
    set up, and the event's calendar goes there, so Apple Calendar reminds of it itself."""
    return (not_double(account) and kind_of(target.platform) in APPLE and kind_of(target.platform) in apple
            and calendar_passes)


def reminding(db: Session, account: Account) -> list[WebPushSubscription]:
    """The account's browsers that remind at all (switched on); which of them stays quiet is asked per event."""
    return [row for row in webpush.subscriptions(db, account.id) if row.reminders]


def set_device(db: Session, account_id: int, session_id: int, on: bool) -> None:
    """"Remind me on this device" (AC-6), for the browser of this session."""
    row = webpush.of_session(db, session_id)
    if row is None or row.account_id != account_id:
        raise error("not_subscribed", "This browser does not take notifications yet.", 409)
    row.reminders = on
    db.commit()


# --- When ------------------------------------------------------------------------------------------------------------


def _zone(name: str, fallback: tzinfo) -> tzinfo:
    if not name:
        return fallback
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return fallback


def _at(naive: datetime, zone: tzinfo) -> datetime:
    """The wall time in the zone as an instant; a time in the gap of the spring change moves on."""
    return naive.replace(tzinfo=zone).astimezone(UTC)


def moment(start: str, end: str, all_day: bool, zone_name: str, minutes: int,
           member_zone: tzinfo) -> tuple[datetime, datetime]:
    """When a reminder ``minutes`` before an occurrence is due, and its deadline: the start (a whole day: its end), a
    reminder "at the start" a minute after it (review of TIME-7, finding 2)."""
    if all_day:
        first, last = date.fromisoformat(start), date.fromisoformat(end)
        days_before = -((-minutes) // 1440)
        clock_minutes = days_before * 1440 - minutes
        due = _at(datetime.combine(first - timedelta(days=days_before), time()) + timedelta(minutes=clock_minutes),
                  member_zone)
        return due, _at(datetime.combine(last + timedelta(days=1), time()), member_zone)
    begin = datetime.fromisoformat(start)
    zone = _zone(zone_name, member_zone)
    days, rest = divmod(minutes, 1440)
    wall = begin.astimezone(zone).replace(tzinfo=None) - timedelta(days=days)
    at = _at(wall, zone) - timedelta(minutes=rest)
    return at, max(begin, at + AT_THE_START)


@dataclass(frozen=True)
class Due:
    event_id: int
    calendar_id: int
    occurrence: str | None
    #: The occurrence's start as an instant or a day: with ``minutes`` what ``reminder_log`` keeps once.
    key: str
    minutes: int
    at: datetime
    deadline: datetime
    start: str
    end: str
    all_day: bool
    #: Every event it stands for (the same event in two calendars of one person comes once), by calendar and address.
    parts: tuple[tuple[int, str], ...] = ()


@dataclass
class Plan:
    key: tuple
    made: datetime
    revs: dict[int, int]
    longest: int | None
    due: list[Due]


_plans: dict[int, Plan] = {}
_plans_lock = threading.Lock()


def forget() -> None:
    with _plans_lock:
        _plans.clear()
    with _revision_lock:
        _calendar_rev.clear()
        _calendar_log.clear()
        _account_rev.clear()


def _longest(db: Session, account_id: int, visible: dict[int, calendars.Access], stored: dict[int, str],
             now: datetime) -> int | None:
    """The longest lead time the account uses anywhere; ``None`` when it uses none at all."""
    found: list[int] = []
    for minutes in db.scalars(select(ReminderChoice.minutes).where(ReminderChoice.account_id == account_id)):
        found += [int(value) for value in minutes or []]
    sources = []
    for calendar_id, access in visible.items():
        choice = default_choice(access, stored.get(calendar_id, ""))
        if choice == SOURCE:
            sources.append(calendar_id)
        elif choice != NONE:
            found.append(int(choice))
    if sources:
        # The longest alarm of the sources' events, kept as a column when they are written: one question, no text read.
        longest = db.scalar(select(func.max(Event.alarm_lead_max)).where(
            Event.calendar_id.in_(sources), Event.deleted_at.is_(None),
            Event.ends_at.is_(None) | (Event.ends_at > now - timedelta(days=1))))
        if longest is not None:
            found.append(int(longest))
    return max(found) if found else None


def _visible(db: Session, account: Account) -> dict[int, calendars.Access]:
    return {item.calendar.id: item for item, setting in calendars.ordered(db, account.id) if not setting.hidden}


def plan(db: Session, account: Account, now: datetime, *, only: dict[int, set[str]] | None = None,
         longest: int | None = None) -> tuple[list[Due], int | None]:
    """The reminders of one account due from ``CATCH_UP`` ago until ``AHEAD`` from now (``only``: of these events),
    and the longest lead time the account uses."""
    zone = display.zone_of(account)
    visible = _visible(db, account)
    stored = _stored_defaults(db, account.id)
    if longest is None:
        longest = _longest(db, account.id, visible, stored, now)
    if longest is None or not visible:
        return [], longest
    from . import events

    begin = now - CATCH_UP - timedelta(days=1)
    finish = now + AHEAD + timedelta(minutes=max(longest, 0)) + timedelta(days=2)
    entries = events.occurrence_list(db, account, begin, finish, events_of=only)
    ids = sorted({source["id"] for entry in entries for source in entry.get("sources") or [entry]})
    rows: list[Event] = []
    for start in range(0, len(ids), 500):
        rows += list(db.scalars(select(Event).where(Event.id.in_(ids[start:start + 500]))))
    href_of = {row.id: (row.calendar_id, row.href) for row in rows}
    leads = leads_for(db, account.id, rows, visible, stored)
    due: list[Due] = []
    for entry in entries:
        minutes: set[int] = set()
        sources = entry.get("sources") or [entry]
        for source in sources:
            found = leads.get(source["id"])
            if found is not None:
                # A single occurrence with alarms of its own set on a device keeps them (review of TIME-7, finding 5).
                own = found.occurrences.get(source.get("occurrence") or "")
                minutes |= set(own if own is not None else found.minutes)
        parts = tuple(href_of[source["id"]] for source in sources if source["id"] in href_of)
        for lead in sorted(minutes):
            at, deadline = moment(entry["start"], entry["end"], entry["all_day"], entry.get("time_zone") or "", lead,
                                  zone)
            if now - CATCH_UP <= at <= now + AHEAD and at < deadline:
                due.append(Due(entry["id"], entry["calendar_id"], entry.get("occurrence"), entry["start"], lead, at,
                               deadline, entry["start"], entry["end"], entry["all_day"], parts))
    return due, longest


def _plan_of(db: Session, account: Account, now: datetime) -> list[Due]:
    """The plan of an account: kept for ``REPLAN``; made anew when what the account chose or sees changed; for single
    changed events only those (review of TIME-7, finding 4)."""
    visible = _visible(db, account)
    with _revision_lock:
        revs = {calendar_id: _calendar_rev.get(calendar_id, 0) for calendar_id in visible}
        key = (_account_rev.get(account.id, 0), str(display.zone_of(account)), frozenset(visible))
    with _plans_lock:
        kept = _plans.get(account.id)
    if kept is not None and kept.key == key and kept.made <= now < kept.made + REPLAN:
        changed: dict[int, set[str]] = {}
        whole = False
        for calendar_id in revs:
            hrefs = _changed_since(calendar_id, kept.revs.get(calendar_id, 0))
            if hrefs is None:
                whole = True
                break
            if hrefs:
                changed[calendar_id] = hrefs
        if not whole and not changed:
            return kept.due
        if not whole:
            stored = _stored_defaults(db, account.id)
            longest = _longest(db, account.id, visible, stored, now)
            if longest is None or (kept.longest is not None and longest <= kept.longest):
                touched = {(calendar_id, href) for calendar_id, hrefs in changed.items() for href in hrefs}
                # Whatever stood together with a changed event (the same event in two calendars) is planned anew too.
                again = set(touched)
                for item in kept.due:
                    if touched & set(item.parts):
                        again |= set(item.parts)
                only: dict[int, set[str]] = {}
                for calendar_id, href in again:
                    only.setdefault(calendar_id, set()).add(href)
                fresh, _ = plan(db, account, now, only=only, longest=kept.longest)
                due = [item for item in kept.due if not (again & set(item.parts))] + fresh
                with _plans_lock:
                    _plans[account.id] = Plan(key, kept.made, revs, kept.longest, due)
                return due
    made, longest = plan(db, account, now)
    with _plans_lock:
        _plans[account.id] = Plan(key, now, revs, longest, made)
    return made


def _payload(due: Due, now: datetime) -> dict[str, Any]:
    return {"event_id": due.event_id, "occurrence": due.occurrence, "key": due.key, "start": due.start,
            "end": due.end, "all_day": due.all_day, "minutes": due.minutes, "at": due.at.isoformat(),
            "deadline": min(due.deadline, due.at + CATCH_UP).isoformat()}


def _account_round(db: Session, account_id: int, now: datetime) -> int:
    account = db.get(Account, account_id)
    if account is None or account_out(account):
        return 0
    targets = reminding(db, account)
    if not targets:
        return 0
    apple = apple_devices(db, account_id, now)
    passing = through_account(db, account) if apple else set()
    queued = 0
    for due in _plan_of(db, account, now):
        if not (now - CATCH_UP <= due.at <= now < due.deadline):
            continue
        made = db.execute(sqlite_insert(ReminderLog).values(
            account_id=account_id, event_id=due.event_id, occurrence=due.key, minutes=due.minutes, fire_at=due.at,
        ).on_conflict_do_nothing())
        if not made.rowcount:
            continue
        for target in targets:
            if silenced(account, target, apple, due.calendar_id in passing):
                continue
            db.add(NotifyOutbox(account_id=account_id, kind="reminder", target_id=target.id,
                                calendar_id=due.calendar_id, payload=_payload(due, now), created_at=now,
                                next_try_at=now))
            queued += 1
    return queued


def queue_due(now: datetime | None = None) -> int:
    """Every reminder that is due now goes into the outbox, once."""
    now = now or clock()
    with SessionLocal() as db:
        accounts = list(db.scalars(select(WebPushSubscription.account_id).where(
            WebPushSubscription.reminders.is_(True)).distinct()))
    queued = 0
    for account_id in accounts:
        try:
            with SessionLocal() as db:
                queued += _account_round(db, account_id, now)
                db.commit()
        except Exception:
            logger.exception("Planning the reminders of an account failed account_id=%s", account_id)
    if queued:
        logger.info("Reminders queued count=%s", queued)
    return queued


def tick(now: datetime | None = None) -> tuple[int, int]:
    """One round: the reminders due go into the outbox, then what waits there goes out."""
    from . import notify

    now = now or clock()
    return queue_due(now), notify.send_round(now)


def tidy(now: datetime | None = None) -> None:
    now = now or clock()
    with SessionLocal() as db:
        db.execute(delete(ReminderLog).where(ReminderLog.fire_at < now - LOG_KEEP))
        db.execute(delete(UsedMark).where(UsedMark.used_at < now - LOG_KEEP))
        db.commit()


async def run_forever(stop: Any) -> None:
    import asyncio

    rounds = 0
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick)
            if rounds % 180 == 0:
                await asyncio.to_thread(tidy)
        except Exception:
            logger.exception("The round of reminders failed")
        rounds += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
            return
        except TimeoutError:
            pass


# --- The words (AC-1, EC-7, EC-12, EC-16) ---------------------------------------------------------------------

TEXTS: dict[str, dict[str, str]] = {
    "de": {
        "now": "jetzt", "in_minute": "in 1 Minute", "in_minutes": "in {n} Minuten", "in_hour": "in 1 Stunde",
        "in_hours": "in {n} Stunden", "in_hm": "in {h} Std. {m} Min.", "today": "heute", "tomorrow": "morgen",
        "today_at": "heute um {time}", "tomorrow_at": "morgen um {time}", "on": "am {day}",
        "on_at": "am {day} um {time}", "all_day": "ganztägig", "busy": "Belegt", "untitled": "Ohne Titel",
        "snooze": "In 5 Min erneut", "open": "Öffnen",
        "example_title": "Zahnarzt Ben", "example_place": "Praxis Dr. Weber, Lindenstraße 4",
    },
    "en": {
        "now": "now", "in_minute": "in 1 minute", "in_minutes": "in {n} minutes", "in_hour": "in 1 hour",
        "in_hours": "in {n} hours", "in_hm": "in {h} hr {m} min", "today": "today", "tomorrow": "tomorrow",
        "today_at": "today at {time}", "tomorrow_at": "tomorrow at {time}", "on": "on {day}",
        "on_at": "on {day} at {time}", "all_day": "all day", "busy": "Busy", "untitled": "Untitled",
        "snooze": "Again in 5 min", "open": "Open",
        "example_title": "Dentist Ben", "example_place": "Dr. Weber's practice, Lindenstraße 4",
    },
}


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _until(words: Any, texts: dict[str, str], start: str, all_day: bool, now: datetime) -> str:
    """"in 15 Minuten", "morgen um 16:30", "heute" (AC-1)."""
    today = now.astimezone(words.zone).date()
    if all_day:
        day = date.fromisoformat(start)
        if day == today:
            return texts["today"]
        if day == today + timedelta(days=1):
            return texts["tomorrow"]
        return texts["on"].format(day=words.day(day))
    begin = _instant(start)
    minutes = round((begin - now).total_seconds() / 60)
    if minutes < 1:
        return texts["now"]
    if minutes < 60:
        return texts["in_minute"] if minutes == 1 else texts["in_minutes"].format(n=minutes)
    if minutes < 360:
        hours, rest = divmod(minutes, 60)
        if rest == 0:
            return texts["in_hour"] if hours == 1 else texts["in_hours"].format(n=hours)
        return texts["in_hm"].format(h=hours, m=rest)
    local = words.local(begin)
    if local.date() == today:
        return texts["today_at"].format(time=words.time(local))
    if local.date() == today + timedelta(days=1):
        return texts["tomorrow_at"].format(time=words.time(local))
    return texts["on_at"].format(day=words.day(local.date()), time=words.time(local))


def _span(words: Any, texts: dict[str, str], start: str, end: str, all_day: bool) -> str:
    if all_day:
        return texts["all_day"]
    begin, finish = words.local(_instant(start)), words.local(_instant(end))
    if finish.date() != begin.date():
        return f"{words.time(begin)} – {words.day(finish.date())} {words.time(finish)}"
    if finish == begin:
        return words.time(begin)
    return f"{words.time(begin)}–{words.time(finish)}"


def compose(words: Any, title: str, location: str, start: str, end: str, all_day: bool, now: datetime,
            busy: bool) -> dict[str, str]:
    """Heading and text of a reminder: "Zahnarzt Ben · in 15 Minuten", "16:30–17:15 · Praxis …"."""
    from .notify import _plain

    texts = TEXTS[words.language]
    name = texts["busy"] if busy else (_plain(title, TITLE_CHARS) or texts["untitled"])
    heading = f"{name} · {_until(words, texts, start, all_day, now)}"
    text = _span(words, texts, start, end, all_day)
    place = "" if busy else _plain(location, TEXT_CHARS)
    if place:
        text = f"{text} · {place}"
    return {"title": _plain(heading, TITLE_CHARS + 40), "text": _plain(text, TEXT_CHARS)}


def preview(account: Account) -> dict[str, str]:
    """"This is how the reminder looks" (AC-8), in the member's language and clock, with a made-up event."""
    from .notify import Words

    words = Words(account)
    texts = TEXTS[words.language]
    start = datetime(2026, 11, 26, 16, 30, tzinfo=words.zone)
    shown = compose(words, texts["example_title"], texts["example_place"],
                    start.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                    (start + timedelta(minutes=45)).astimezone(UTC).isoformat().replace("+00:00", "Z"), False,
                    start - timedelta(minutes=15), False)
    return {**shown, "snooze": texts["snooze"], "open": texts["open"]}


# --- Sending (called by ``notify.send_round``) ------------------------------------------------------------------


def _same_time(shown: dict[str, Any], payload: dict[str, Any]) -> bool:
    """The occurrence still starts when the reminder was planned for (moved: the new plan reminds anew, EC-9)."""
    if bool(shown["all_day"]) != bool(payload["all_day"]):
        return False
    if shown["all_day"]:
        return shown["start"] == payload["start"]
    from . import ical

    start = ical.at_wall(datetime.fromisoformat(shown["start"]), shown["time_zone"]) if shown["time_zone"] else \
        datetime.fromisoformat(shown["start"]).replace(tzinfo=UTC)
    return start == _instant(payload["start"])


def topic_of(row: NotifyOutbox) -> str:
    """What the push service sees: a keyed hash of event and occurrence, so that one never replaces another (EC-6)."""
    payload = row.payload or {}
    digest = hmac.new(server_key(), f"reminder:{payload.get('event_id')}:{payload.get('key')}".encode(),
                      hashlib.sha256).hexdigest()
    return "r" + digest[:24]


def _sign(data: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(server_key(), b"snooze:" + data, hashlib.sha256).digest()[:18]) \
        .decode().rstrip("=")


def browser_key(db: Session, target: WebPushSubscription) -> str:
    """The random name of a sign-up, made at the first need."""
    if not target.key:
        import secrets

        target.key = secrets.token_urlsafe(18)
        db.flush()
    return target.key


def snooze_mark(target: WebPushSubscription, key: str, payload: dict[str, Any], now: datetime) -> str:
    """The mark "Again in 5 min" sends back: this browser (its number, account and random name), this occurrence,
    valid for a day, once."""
    data = json.dumps({"t": target.id, "u": target.account_id, "b": key, "e": payload["event_id"],
                       "o": payload.get("occurrence"),
                       "k": payload["key"], "s": payload["start"], "n": payload["end"], "a": payload["all_day"],
                       "m": payload["minutes"], "x": int((now + SNOOZE_VALID).timestamp())},
                      separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip("=") + "." + _sign(data)


def message_for(db: Session, account: Account, row: NotifyOutbox, target: WebPushSubscription,
                now: datetime) -> dict[str, Any]:
    """The notification as it goes now; ``LookupError`` with the reason why it must not go any more."""
    from . import events
    from .notify import Words

    payload = dict(row.payload or {})
    if now >= datetime.fromisoformat(payload["deadline"]):
        raise LookupError("late")
    if not target.reminders:
        raise LookupError("device_off")
    apple = apple_devices(db, account.id, now)
    if apple and silenced(account, target, apple, (row.calendar_id or 0) in through_account(db, account)):
        raise LookupError("apple_reminds")
    try:
        shown = events.detail(db, account, int(payload["event_id"]), payload.get("occurrence"))
    except HTTPException:
        raise LookupError("gone") from None
    if not _same_time(shown, payload):
        raise LookupError("moved")
    words = Words(account)
    texts = TEXTS[words.language]
    body = compose(words, shown["title"], shown["location"], payload["start"], payload["end"], payload["all_day"],
                   now, bool(shown["busy_only"]))
    day = payload["start"][:10] if payload["all_day"] else words.local(_instant(payload["start"])).date().isoformat()
    url = f"/day?day={day}&event={payload['event_id']}"
    if payload.get("occurrence"):
        url += f"&occurrence={payload['occurrence']}"
    return {"title": body["title"], "body": body["text"], "url": url, "kind": "reminder",
            "tag": f"reminder-{payload['event_id']}-{payload['key']}",
            "actions": [{"action": "snooze", "title": texts["snooze"]}, {"action": "open", "title": texts["open"]}],
            "snooze": snooze_mark(target, browser_key(db, target), payload, now)}


def retry_at(row: NotifyOutbox, now: datetime, wait: float | None) -> datetime | None:
    """When a reminder that could not be delivered is tried again; ``None`` once its deadline passed (EC-14)."""
    later = now + max(RETRY, timedelta(seconds=wait or 0))
    deadline = datetime.fromisoformat((row.payload or {}).get("deadline", now.isoformat()))
    return later if later < deadline else None


def snooze(mark: str, now: datetime | None = None) -> None:
    """"Again in 5 min" (AC-4): the reminder once more in five minutes, on this browser only (EC-17)."""
    now = now or clock()
    try:
        data_text, signed = mark.split(".", 1)
        data = base64.urlsafe_b64decode(data_text + "=" * (-len(data_text) % 4))
        if not hmac.compare_digest(_sign(data), signed):
            raise ValueError("mark")
        found = json.loads(data)
        if int(found["x"]) < now.timestamp():
            raise ValueError("old")
    except (ValueError, KeyError, TypeError):
        raise error("snooze_invalid", "This reminder cannot come again.", 404) from None
    with SessionLocal() as db:
        target = db.get(WebPushSubscription, int(found["t"]))
        if (target is None or target.account_id != found.get("u") or not target.key
                or not hmac.compare_digest(target.key, str(found.get("b", "")))):
            raise error("snooze_invalid", "This reminder cannot come again.", 404)
        # Once: the same mark again (a second tap, a copy) does nothing.
        digest = hashlib.sha256(mark.encode()).hexdigest()
        if not db.execute(sqlite_insert(UsedMark).values(digest=digest, used_at=now)
                          .on_conflict_do_nothing()).rowcount:
            db.commit()
            return
        at = now + SNOOZE
        payload = {"event_id": found["e"], "occurrence": found["o"], "key": found["k"], "start": found["s"],
                   "end": found["n"], "all_day": found["a"], "minutes": found["m"], "at": at.isoformat(),
                   "deadline": (at + CATCH_UP).isoformat(), "again": True}
        event = db.get(Event, int(found["e"]))
        db.add(NotifyOutbox(account_id=target.account_id, kind="reminder", target_id=target.id,
                            calendar_id=event.calendar_id if event is not None else None, payload=payload,
                            created_at=now, next_try_at=at))
        db.commit()


# --- The area "Reminders" under My account (AC-6, AC-7, AC-8, EC-2, EC-15) -------------------------------------


def overview(db: Session, account: Account, session_id: int | None, https: bool) -> dict[str, Any]:
    from . import notify

    stored = _stored_defaults(db, account.id)
    apple = apple_devices(db, account.id)
    own = webpush.of_session(db, session_id) if session_id is not None else None
    browsers = []
    for row in webpush.subscriptions(db, account.id):
        browser, system = webpush.describe(row.user_agent)
        browsers.append({"id": row.id, "browser": browser, "system": system, "installed": row.installed,
                         "this": own is not None and row.id == own.id, "reminders": row.reminders,
                         "quiet": silenced(account, row, apple)})
    rows = []
    for item, setting in calendars.ordered(db, account.id):
        owner = db.get(Account, item.calendar.owner_id) if item.right != calendars.OWNER else None
        rows.append({"id": item.calendar.id, "name": item.calendar.name, "color": item.calendar.color,
                     "owner": owner.shown_name if owner else None, "kind": item.kind or "own",
                     "hidden": setting.hidden, "choice": default_choice(item, stored.get(item.calendar.id, "")),
                     "options": options(item)})
    result = {
        "not_double": not_double(account),
        "apple": sorted(apple),
        "this_browser": {"subscribed": own is not None and own.account_id == account.id,
                         "reminders": bool(own.reminders) if own is not None else True,
                         "quiet": own is not None and silenced(account, own, apple)},
        "browsers": browsers,
        "calendars": rows,
        "delivery": notify.delivery(https),
        "preview": preview(account),
    }
    db.commit()
    return result
