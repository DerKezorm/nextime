"""Notifications when others change something in a shared calendar (TIME-21), bundled like nexnotes (NOTES-14).

**The switch** (AC-1) is per account and calendar (``calendar_settings.notify``), off from the start, and only for a
calendar more than one person sees: an own one the account shares with members, or one shared with it.

**What is a change** (AC-2, AC-7): every event made, changed or deleted, wherever it was done: in the interface, on a
phone through the calendar account, later through the programming interface (``events.record_change`` with the
person who did it). A change that came from the source of an iCloud, Google or CalDAV calendar names nobody: it counts
as the owner's (the owner hears nothing of it) and the others hear the calendar and its owner (EC-3). A person never
hears of their own changes, whichever device made them.

**Bundling** (AC-3): the first change of an event keeps where it began (``before_start``), every change widens its
row in ``notify_changes`` and keeps how the event looks now (EC-6). Once nobody changed anything in the calendar for
``QUIET`` (two minutes), the ticker sends one notification per member, naming everybody else who changed something
(EC-5) and cutting the list short with a count. The rows are in the database: a restart in the quiet time still sends
the bundle, once (EC-11).

**Who hears it**, decided when the bundle goes: a member whose switch is on, whose account is neither blocked nor
deleted (EC-12), and who still sees the calendar (EC-1). "Busy only" (EC-2, TIME-19 AC-11): the notification names no
title, only "busy time" and the day, and events marked "free" tell nothing. Before every try to send, the right is
asked again: a message for somebody who lost the calendar meanwhile is dropped.

**Where it goes**: every browser of the member that allowed notifications (Web Push, ``services/webpush.py``; the
same permission TIME-7 reminders use), one row of ``notify_outbox`` each, tried again after a failure for a while
(EC-10). A browser its push service calls gone is dropped and shows "Allow notifications" again. Times and words are
the member's own: its zone, 12 or 24 hours, its language (EC-13).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta, tzinfo
from typing import Any

from sqlalchemy import delete, or_, select, update
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..errors import error
from ..models import (
    RIGHT_BUSY,
    Account,
    AppDevice,
    AuthSession,
    Calendar,
    CalendarSetting,
    CalendarShare,
    Event,
    NotifyChange,
    NotifyOutbox,
    WebPushSubscription,
    utcnow,
)
from ..security import account_out, server_key
from . import apppush, display, webpush

logger = logging.getLogger("nextime.notify")

#: Who made a change that came from the calendar's source (TIME-21 EC-3).
FROM_SOURCE = 0
#: A bundle goes once nobody changed anything in the calendar for this long (AC-1, AC-3) ...
QUIET = timedelta(minutes=2)
#: ... or at the latest after this long of changes without such a pause.
LONGEST = timedelta(minutes=30)
#: How often the ticker looks.
TICK_SECONDS = 15
#: Minutes to wait after the 1st, 2nd, ... failure; after the last one the message is given up (EC-10).
RETRY_MINUTES = (1, 5, 15, 60)
#: Sent and given-up messages stay this long (without their text), then go.
KEEP = timedelta(days=7)
BATCH = 100
#: Changes a notification names one by one; more are counted (EC-5).
MAX_ITEMS = 3
#: A title in a notification is cut to this many characters (EC-7); the whole text to ``TEXT_CHARS``.
TITLE_CHARS = 40
TEXT_CHARS = 300
#: How often the log says again that notifications cannot arrive (EC-14).
WARN_EVERY = 3600.0


def _now() -> datetime:
    return utcnow()


#: The clock of the notifications; the tests put their own here.
clock: Callable[[], datetime] = _now


# --- Recording a change -------------------------------------------------------------------------------------------


def _someone_listens(db: Session, calendar_id: int, actor: int) -> bool:
    return db.scalar(select(CalendarSetting.account_id).where(
        CalendarSetting.calendar_id == calendar_id, CalendarSetting.notify.is_(True),
        CalendarSetting.account_id != actor).limit(1)) is not None


def note(db: Session, calendar_id: int, row: Event, *, deleted: bool, by: int | None) -> None:
    """``row`` in ``calendar_id`` was made, changed or deleted by ``by`` (``FROM_SOURCE``: by the source). Nothing is
    kept when nobody else asked for notifications of the calendar."""
    if by is None:
        return
    owner = db.scalar(select(Calendar.owner_id).where(Calendar.id == calendar_id))
    if owner is None:
        return
    if not _someone_listens(db, calendar_id, owner if by == FROM_SOURCE else by):
        return
    if row.id is None:
        db.flush()
    now = clock()
    arrived = bool(getattr(row, "nx_arrived", False)) and not deleted
    kind = "deleted" if deleted else ("created" if (not row.prev_etag or arrived) else "changed")
    found = db.scalar(select(NotifyChange).where(NotifyChange.calendar_id == calendar_id,
                                                 NotifyChange.event_id == row.id))
    if found is None:
        before = getattr(row, "nx_before_start", None) or row.starts_at
        found = NotifyChange(calendar_id=calendar_id, event_id=row.id, kind=kind, actors=[by], first_at=now,
                             last_at=now, before_start=before if kind == "changed" else None,
                             starts_at=row.starts_at)
        db.add(found)
    else:
        if found.kind == "created" and kind == "deleted":
            # Made and deleted again within the quiet time: nothing to tell.
            db.delete(found)
            return
        if found.kind == "created":
            kind = "created"
        elif found.kind == "deleted" and kind != "deleted":
            kind = "changed"
        if kind == "changed" and found.before_start is None:
            found.before_start = found.starts_at
        found.kind = kind
        actors = list(found.actors or [])
        if by not in actors:
            actors.append(by)
        found.actors = actors
        found.last_at = now
    found.title = row.title or ""
    found.starts_at = row.starts_at
    found.all_day = bool(row.all_day)
    found.recurring = bool(row.recurring)
    found.busy = bool(row.busy)


def share_ended(db: Session, calendar_id: int, account_id: int) -> None:
    """``shares.end``: the account no longer sees the calendar (EC-1). Its switch goes back to off, and whatever waits
    for it about this calendar is dropped."""
    db.execute(update(CalendarSetting).where(CalendarSetting.account_id == account_id,
                                             CalendarSetting.calendar_id == calendar_id).values(notify=False))
    db.execute(update(NotifyOutbox).where(NotifyOutbox.account_id == account_id,
                                          NotifyOutbox.calendar_id == calendar_id, NotifyOutbox.sent_at.is_(None),
                                          NotifyOutbox.failed_at.is_(None))
               .values(failed_at=clock(), last_error="no_right", payload={}))


# --- The words (EC-7, EC-13) -------------------------------------------------------------------------------------

_DE_DAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
_EN_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_EN_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

TEXTS: dict[str, dict[str, str]] = {
    "de": {
        "created": "„{title}“ {when} eingetragen", "changed": "„{title}“ {when} geändert",
        "moved": "„{title}“ auf {to} verschoben", "deleted": "„{title}“ {when} gelöscht",
        "busy_created": "neuer belegter Zeitraum {when}", "busy_changed": "belegter Zeitraum {when} geändert",
        "busy_moved": "belegter Zeitraum auf {to} verschoben", "busy_deleted": "belegter Zeitraum {when} entfernt",
        "on": "am {day}", "on_at": "am {day} um {time}", "to_day": "{day}", "to_day_at": "{day} um {time}",
        "one": "{names} hat {items}.", "many": "{names} haben {items}.", "nobody": "{items}.",
        "more": "{count} weitere Änderungen", "more_one": "eine weitere Änderung",
        "and": "und", "own": "{name}", "shared": "{name} (von {owner})", "busy": "{name} (von {owner}, nur belegt)",
        "untitled": "Ohne Titel",
    },
    "en": {
        "created": "added “{title}” {when}", "changed": "changed “{title}” {when}",
        "moved": "moved “{title}” to {to}", "deleted": "deleted “{title}” {when}",
        "busy_created": "new busy time {when}", "busy_changed": "busy time {when} changed",
        "busy_moved": "busy time moved to {to}", "busy_deleted": "busy time {when} removed",
        "on": "on {day}", "on_at": "on {day} at {time}", "to_day": "{day}", "to_day_at": "{day} at {time}",
        "one": "{names} {items}.", "many": "{names} {items}.", "nobody": "{items}.",
        "more": "{count} more changes", "more_one": "one more change",
        "and": "and", "own": "{name}", "shared": "{name} (from {owner})", "busy": "{name} (from {owner}, busy only)",
        "untitled": "Untitled",
    },
}

_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]+")


def _plain(text: str, limit: int) -> str:
    """Plain text on one line, cut to ``limit`` characters (EC-7). A notification shows text, never markup."""
    flat = " ".join(_CONTROL.sub(" ", text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1].rstrip() + "…"


class Words:
    """How one member reads days and times."""

    def __init__(self, account: Account) -> None:
        from .events import language_of

        self.language = language_of(account)
        self.zone: tzinfo = display.zone_of(account)
        self.twelve = display.twelve_hours(account, self.language)
        self.texts = TEXTS[self.language]

    def local(self, value: datetime) -> datetime:
        return value.astimezone(self.zone)

    def day(self, value: date) -> str:
        if self.language == "de":
            return f"{_DE_DAYS[value.weekday()]}, {value.day}.{value.month}."
        return f"{_EN_DAYS[value.weekday()]}, {_EN_MONTHS[value.month - 1]} {value.day}"

    def time(self, value: datetime) -> str:
        if self.twelve:
            hour = value.hour % 12 or 12
            return f"{hour}:{value.minute:02d} {'AM' if value.hour < 12 else 'PM'}"
        return f"{value.hour}:{value.minute:02d}"

    def date_of(self, starts_at: datetime, all_day: bool) -> date:
        # A whole day is stored as its date at midnight UTC: it is that date in every zone.
        return starts_at.date() if all_day else self.local(starts_at).date()

    def when(self, starts_at: datetime, all_day: bool, *, day_only: bool = False) -> str:
        day = self.day(self.date_of(starts_at, all_day))
        if all_day or day_only:
            return self.texts["on"].format(day=day)
        return self.texts["on_at"].format(day=day, time=self.time(self.local(starts_at)))

    def to(self, before: datetime | None, starts_at: datetime, all_day: bool, *, day_only: bool = False) -> str:
        """"to 11:30" on the same day, else with the day."""
        local = self.local(starts_at)
        if all_day or day_only:
            return self.texts["to_day"].format(day=self.day(self.date_of(starts_at, all_day)))
        if before is not None and self.local(before).date() == local.date():
            return self.time(local)
        return self.texts["to_day_at"].format(day=self.day(local.date()), time=self.time(local))

    def join(self, parts: list[str]) -> str:
        if len(parts) <= 1:
            return "".join(parts)
        return f"{', '.join(parts[:-1])} {self.texts['and']} {parts[-1]}"


def _moved(change: NotifyChange) -> bool:
    return (change.kind == "changed" and not change.recurring and change.before_start is not None
            and change.before_start != change.starts_at)


def _item(words: Words, change: NotifyChange, busy: bool) -> str:
    kind = "moved" if _moved(change) else change.kind
    texts = words.texts
    if busy:
        return texts[f"busy_{kind}"].format(
            when=words.when(change.starts_at, change.all_day, day_only=True),
            to=words.to(change.before_start, change.starts_at, change.all_day, day_only=True))
    title = _plain(change.title, TITLE_CHARS) or texts["untitled"]
    return texts[kind].format(title=title, when=words.when(change.starts_at, change.all_day),
                              to=words.to(change.before_start, change.starts_at, change.all_day))


def _capital(text: str) -> str:
    return text[:1].upper() + text[1:]


def _sentence(words: Words, changes: list[NotifyChange], names: list[str], busy: bool) -> str:
    texts = words.texts
    items = [_item(words, change, busy) for change in changes]
    if len(items) > MAX_ITEMS:
        rest = len(items) - (MAX_ITEMS - 1)
        items = [*items[: MAX_ITEMS - 1], texts["more_one"] if rest == 1 else texts["more"].format(count=rest)]
    listed = words.join(items)
    if names and not busy:
        sentence = texts["one" if len(names) == 1 else "many"].format(names=words.join(names), items=listed)
    else:
        sentence = _capital(texts["nobody"].format(items=listed))
    if sentence.endswith(".."):
        # A German date ends with its own full stop ("am Do, 3.12.").
        sentence = sentence[:-1]
    return sentence


def compose(words: Words, heading: str, changes: list[NotifyChange], names: list[str], busy: bool,
            quiet: list[NotifyChange] | None = None) -> dict[str, str]:
    """Heading and text of one notification: the calendar, then who did what (AC-2, AC-3, EC-2, EC-5). ``quiet`` are
    changes that came from the calendar's source: a sentence of their own, without a person (EC-3; a person is named
    only for what that person did, review of TIME-21, finding 5)."""
    parts = [_sentence(words, changes, names, busy)] if changes else []
    if quiet:
        parts.append(_sentence(words, quiet, [], busy))
    return {"title": _plain(heading, 120), "text": _plain(" ".join(parts), TEXT_CHARS)}


def heading_of(words: Words, calendar: Calendar, owner_name: str, right: str, receiver_id: int) -> str:
    texts = words.texts
    if calendar.owner_id == receiver_id:
        return texts["own"].format(name=calendar.name)
    return texts["busy" if right == RIGHT_BUSY else "shared"].format(name=calendar.name, owner=owner_name)


# --- Bundling ------------------------------------------------------------------------------------------------------


def _counts_for(change: NotifyChange, receiver_id: int, owner_id: int) -> bool:
    """A change tells ``receiver_id`` something when somebody else made it; one from the source counts as the
    owner's."""
    return any((owner_id if actor == FROM_SOURCE else actor) != receiver_id for actor in change.actors or [])


def _names(db: Session, changes: list[NotifyChange], receiver_id: int) -> list[str]:
    seen: list[int] = []
    for change in changes:
        for actor in change.actors or []:
            if actor not in (FROM_SOURCE, receiver_id) and actor not in seen:
                seen.append(actor)
    names = []
    for actor in seen:
        person = db.get(Account, actor)
        if person is not None:
            names.append(person.shown_name)
    return names


def _url(words: Words, changes: list[NotifyChange], busy: bool) -> str:
    """Tapping it opens the event, or the day (AC-4)."""
    first = min(changes, key=lambda change: change.starts_at)
    day = words.date_of(first.starts_at, first.all_day).isoformat()
    if len(changes) == 1 and first.kind != "deleted" and not busy:
        return f"/day?day={day}&event={first.event_id}"
    return f"/day?day={day}"


def message(db: Session, receiver: Account, calendar: Calendar, right: str,
            changes: list[NotifyChange]) -> dict[str, Any] | None:
    """The notification for one member, or ``None`` when nothing in the bundle is for it."""
    busy = right == RIGHT_BUSY
    mine = [change for change in changes if _counts_for(change, receiver.id, calendar.owner_id)
            and (change.busy or not busy)]
    if not mine:
        return None
    mine.sort(key=lambda change: (change.first_at, change.id or 0))
    words = Words(receiver)
    owner = db.get(Account, calendar.owner_id)
    heading = heading_of(words, calendar, owner.shown_name if owner else "", right, receiver.id)
    by_people = [change for change in mine if any(actor not in (FROM_SOURCE, receiver.id)
                                                  for actor in change.actors or [])]
    from_source = [change for change in mine if change not in by_people]
    if busy:
        by_people, from_source = [], mine
    names = _names(db, by_people, receiver.id)
    shown = compose(words, heading, by_people, names, busy, from_source)
    url = _url(words, mine, busy)
    return {**shown, "url": url, "calendar_id": calendar.id, "app": _app_fields(calendar, mine, names, busy, url)}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def _app_fields(calendar: Calendar, changes: list[NotifyChange], names: list[str], busy: bool,
                url: str) -> dict[str, Any]:
    """What the iPhone app gets besides the ready words (TIME-29, contract 0.3): enough to say it in the device's
    language itself (G7 of TIMEIOS) and to open the event or the day. Nothing a "busy only" share hides."""
    query = dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&")) if "?" in url else {}
    items = [{
        "kind": "moved" if _moved(change) else change.kind,
        "event_id": None if busy or change.kind == "deleted" else change.event_id,
        "title": "" if busy else _plain(change.title, TITLE_CHARS),
        "starts_at": _iso(change.starts_at), "all_day": bool(change.all_day),
        "before_start": _iso(change.before_start) if _moved(change) else None,
        "recurring": bool(change.recurring),
    } for change in changes[:MAX_ITEMS]]
    return {"calendar": _plain(calendar.name, TITLE_CHARS), "who": [] if busy else names, "busy": busy,
            "count": len(changes), "items": items, "day": query.get("day"),
            "event_id": int(query["event"]) if query.get("event") else None}


def _bundle(db: Session, calendar_id: int) -> int:
    """The bundle of one calendar: who hears what. Returns how many messages were queued."""
    from . import calendars

    connection = db.connection()
    raw = connection.connection.driver_connection
    if raw is not None and not raw.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
    changes = list(db.scalars(select(NotifyChange).where(NotifyChange.calendar_id == calendar_id)))
    db.execute(delete(NotifyChange).where(NotifyChange.calendar_id == calendar_id))
    calendar = db.get(Calendar, calendar_id)
    if calendar is None or calendar.deleted_at is not None or not changes:
        return 0
    listening = set(db.scalars(select(CalendarSetting.account_id).where(
        CalendarSetting.calendar_id == calendar_id, CalendarSetting.notify.is_(True))))
    queued = 0
    for account_id in sorted(listening):
        receiver = db.get(Account, account_id)
        if receiver is None or account_out(receiver):
            continue
        found = calendars.access(db, account_id, calendar_id)
        if found is None or not shared(db, found):
            continue
        payload = message(db, receiver, calendar, found.right, changes)
        if payload is None:
            continue
        queued += _enqueue(db, receiver, calendar_id, payload)
    return queued


def _enqueue(db: Session, account: Account, calendar_id: int, payload: dict[str, Any]) -> int:
    now = clock()
    count = 0
    for row in webpush.subscriptions(db, account.id):
        db.add(NotifyOutbox(account_id=account.id, target_id=row.id, calendar_id=calendar_id, payload=payload,
                            created_at=now, next_try_at=now))
        count += 1
    if apppush.relay_url():
        # The iPhone app (TIME-29 AC-3): its devices that show notifications with text. Never reminders (G6).
        for device_id in db.scalars(select(AppDevice.id).where(AppDevice.account_id == account.id,
                                                               AppDevice.push_ticket != "",
                                                               AppDevice.push_alerts.is_(True))):
            db.add(NotifyOutbox(account_id=account.id, kind=APP, target_id=device_id, calendar_id=calendar_id,
                                payload=payload, created_at=now, next_try_at=now))
            count += 1
    return count


#: ``notify_outbox.kind`` of a notification for the iPhone app.
APP = "app"


def _send_app(db: Session, account: Account, row: NotifyOutbox) -> None:
    """One notification of changes to one app device, through the relay. ``LookupError`` when it must not go."""
    from . import appdevices

    device = db.get(AppDevice, row.target_id)
    if device is None or device.account_id != row.account_id or not device.push_ticket or not device.push_alerts:
        raise LookupError("no_target")
    payload = dict(row.payload or {})
    app = dict(payload.get("app") or {})
    message = {"type": "changes", "title": _plain(str(payload.get("title") or ""), apppush.TITLE_MAX),
               "text": _plain(str(payload.get("text") or ""), apppush.TEXT_MAX), "account": account.id,
               "server": appdevices.server_id(), "calendar_id": row.calendar_id, **app}
    try:
        apppush.alert(device, message, f"nt-{opaque(row.calendar_id)}")
    except apppush.Gone:
        device.push_ticket, device.push_key, device.push_alerts = "", "", False
        raise


def flush_due(now: datetime | None = None) -> int:
    """Every bundle whose calendar was quiet for ``QUIET`` (or changed for longer than ``LONGEST``)."""
    now = now or clock()
    with SessionLocal() as db:
        rows = db.execute(select(NotifyChange.calendar_id, NotifyChange.first_at, NotifyChange.last_at)).all()
    first: dict[int, datetime] = {}
    last: dict[int, datetime] = {}
    for row in rows:
        first[row.calendar_id] = min(first.get(row.calendar_id, row.first_at), row.first_at)
        last[row.calendar_id] = max(last.get(row.calendar_id, row.last_at), row.last_at)
    due = [calendar_id for calendar_id in last
           if last[calendar_id] <= now - QUIET or first[calendar_id] <= now - LONGEST]
    queued = 0
    for calendar_id in due:
        try:
            with SessionLocal() as db:
                queued += _bundle(db, calendar_id)
                db.commit()
        except Exception:
            logger.exception("Bundling the notifications of a calendar failed calendar=%s", calendar_id)
    if queued:
        logger.info("Notifications queued count=%s", queued)
    return queued


# --- Sending ----------------------------------------------------------------------------------------------------------


def opaque(calendar_id: int | None) -> str:
    """What the push service sees of a calendar (as ``Topic``, so a newer message replaces an older one): a keyed hash
    of its id, cut short."""
    digest = hmac.new(server_key(), f"notify-calendar:{calendar_id}".encode(), hashlib.sha256).hexdigest()
    return "c" + digest[:24]


def _still_for(db: Session, account: Account, row: NotifyOutbox) -> str:
    """Why a message must not go any more (empty: it may): the member no longer sees the calendar (EC-1) or switched
    notifications off for it. Asked before every try."""
    from . import calendars

    if row.calendar_id is None:
        return ""
    found = calendars.access(db, account.id, row.calendar_id)
    if found is None:
        return "no_right"
    setting = db.get(CalendarSetting, (account.id, row.calendar_id))
    if setting is None or not setting.notify:
        return "switched_off"
    return ""


#: One round of sending at a time: the ticker of the notifications and the one of the reminders (TIME-7) both send.
_sending = threading.Lock()


def send_round(now: datetime | None = None) -> int:
    """Sends what is due, notifications of changes and reminders (TIME-7) alike. Returns how many went out."""
    now = now or clock()
    with _sending:
        return _send_round(now)


def _send_round(now: datetime) -> int:
    from . import reminders

    sent = 0
    with SessionLocal() as db:
        due = list(db.scalars(select(NotifyOutbox).where(NotifyOutbox.sent_at.is_(None),
                                                         NotifyOutbox.failed_at.is_(None),
                                                         NotifyOutbox.next_try_at <= now)
                              .order_by(NotifyOutbox.id).limit(BATCH)))
        for row in due:
            account = db.get(Account, row.account_id)
            row.attempts += 1
            reminder = row.kind == "reminder"
            try:
                if account is None or account_out(account):
                    raise LookupError("account_out")
                if row.kind == APP:
                    refused = _still_for(db, account, row)
                    if refused:
                        raise LookupError(refused)
                    _send_app(db, account, row)
                else:
                    if reminder:
                        target = db.get(WebPushSubscription, row.target_id)
                        if target is None or target.account_id != row.account_id:
                            raise LookupError("no_target")
                        body = reminders.message_for(db, account, row, target, now)
                        topic = reminders.topic_of(row)
                    else:
                        refused = _still_for(db, account, row)
                        if refused:
                            raise LookupError(refused)
                        target = db.get(WebPushSubscription, row.target_id)
                        if target is None or target.account_id != row.account_id:
                            raise LookupError("no_target")
                        payload = dict(row.payload or {})
                        body = {"title": payload.get("title"), "body": payload.get("text"), "url": payload.get("url"),
                                "tag": f"calendar-{row.calendar_id}"}
                        topic = opaque(row.calendar_id)
                    webpush.deliver(db, target, json.dumps(body, ensure_ascii=False).encode("utf-8"), topic=topic)
                    target.last_ok_at = now
            except LookupError as reason:
                row.failed_at, row.last_error, row.payload = now, str(reason)[:40], {}
            except apppush.Gone:
                logger.info("The relay forgot an app device's ticket; removing it")
                row.failed_at, row.last_error, row.payload = now, "gone", {}
            except apppush.Failed as exc:
                row.last_error = exc.code
                if exc.final or row.attempts > len(RETRY_MINUTES):
                    row.failed_at, row.payload = now, {}
                    logger.warning("App notification given up account_id=%s tries=%s reason=%s", row.account_id,
                                   row.attempts, exc.code)
                else:
                    row.next_try_at = now + timedelta(minutes=RETRY_MINUTES[row.attempts - 1])
                    logger.warning("App notification not delivered, tried again later account_id=%s try=%s "
                                   "reason=%s", row.account_id, row.attempts, exc.code)
            except webpush.Gone:
                logger.info("A browser's push subscription is gone; removing it")
                db.execute(delete(WebPushSubscription).where(WebPushSubscription.id == row.target_id))
                row.failed_at, row.last_error, row.payload = now, "gone", {}
            except webpush.Refused as refused:
                row.failed_at, row.last_error, row.payload = now, refused.code, {}
                logger.warning("Notification refused by the push service, given up account_id=%s reason=%s",
                               row.account_id, refused.code)
            except webpush.Failed as exc:
                row.last_error = exc.code
                if reminder:
                    # A reminder is worth something only until its deadline: every minute until then (TIME-7 EC-14).
                    later = reminders.retry_at(row, now, exc.retry_after)
                    if later is None:
                        row.failed_at, row.payload = now, {}
                        logger.warning("Reminder given up account_id=%s tries=%s reason=%s", row.account_id,
                                       row.attempts, exc.code)
                    else:
                        row.next_try_at = later
                        logger.warning("Reminder not delivered, tried again later account_id=%s try=%s reason=%s",
                                       row.account_id, row.attempts, exc.code)
                elif row.attempts > len(RETRY_MINUTES):
                    row.failed_at, row.payload = now, {}
                    logger.warning("Notification given up account_id=%s tries=%s reason=%s", row.account_id,
                                   row.attempts, exc.code)
                else:
                    # The plan, or later when the push service asks for it (``Retry-After``).
                    wait = timedelta(minutes=RETRY_MINUTES[row.attempts - 1])
                    if exc.retry_after is not None:
                        wait = max(wait, timedelta(seconds=exc.retry_after))
                    row.next_try_at = now + wait
                    logger.warning("Notification not delivered, tried again later account_id=%s try=%s reason=%s",
                                   row.account_id, row.attempts, exc.code)
            else:
                row.sent_at, row.payload = now, {}
                sent += 1
            db.commit()
    if sent:
        logger.info("Notifications delivered count=%s", sent)
    return sent


def tick(now: datetime | None = None) -> tuple[int, int]:
    """One round of the ticker: bundles that are due, then what waits to be sent."""
    return flush_due(now), send_round(now)


def tidy(now: datetime | None = None) -> None:
    now = now or clock()
    with SessionLocal() as db:
        db.execute(delete(NotifyOutbox).where(or_(NotifyOutbox.sent_at < now - KEEP,
                                                  NotifyOutbox.failed_at < now - KEEP)))
        db.commit()


async def run_forever(stop: asyncio.Event) -> None:
    rounds = 0
    while not stop.is_set():
        try:
            await asyncio.to_thread(tick)
            if rounds % 240 == 0:
                await asyncio.to_thread(tidy)
        except Exception:
            logger.exception("The round of notifications failed")
        rounds += 1
        try:
            await asyncio.wait_for(stop.wait(), timeout=TICK_SECONDS)
            return
        except TimeoutError:
            pass


# --- The settings of a calendar (AC-1, AC-5, AC-6, EC-8, EC-14) ------------------------------------------------------


def shared(db: Session, found: Any) -> bool:
    """More than one person sees the calendar: shared with the account, or an own one shared with a member."""
    from . import calendars

    if found.right != calendars.OWNER:
        return True
    return db.scalar(select(CalendarShare.account_id).join(Account, Account.id == CalendarShare.account_id)
                     .where(CalendarShare.calendar_id == found.calendar.id, Account.deleted_at.is_(None))
                     .limit(1)) is not None


def devices_of(db: Session, account: Account, session_id: int | None) -> dict[str, Any]:
    """Where the account's notifications arrive (AC-5): its browsers and installed web apps, and whether this browser
    is one of them."""
    browsers = []
    this_browser = False
    for row in webpush.subscriptions(db, account.id):
        browser, system = webpush.describe(row.user_agent)
        mine = session_id is not None and row.session_id == session_id
        this_browser = this_browser or mine
        browsers.append({"id": row.id, "browser": browser, "system": system, "installed": row.installed,
                         "this": mine})
    return {"browsers": browsers, "this_browser": this_browser}


_EXAMPLES = {
    "de": {"name": "Anna", "first": "Elternsprechtag", "second": "Schwimmen", "work": "Arbeit"},
    "en": {"name": "Anna", "first": "Parents' evening", "second": "Swimming", "work": "Work"},
}


def preview(account: Account, calendar: Calendar) -> dict[str, dict[str, str]]:
    """"This is how it looks" (AC-6): a bundle with two changes, and one of a calendar shared "busy only", in the
    member's language and clock, with made-up changes."""
    words = Words(account)
    example = _EXAMPLES[words.language]
    zone = words.zone
    first_start = datetime(2026, 12, 1, 17, 0, tzinfo=zone)
    second_before = datetime(2026, 12, 2, 10, 0, tzinfo=zone)
    second_start = datetime(2026, 12, 2, 11, 30, tzinfo=zone)
    made = NotifyChange(event_id=1, kind="created", title=example["first"], starts_at=first_start, all_day=False,
                        recurring=False, busy=True, actors=[-1])
    moved = NotifyChange(event_id=2, kind="changed", title=example["second"], starts_at=second_start,
                         before_start=second_before, all_day=False, recurring=False, busy=True, actors=[-1])
    bundle = compose(words, calendar.name, [made, moved], [example["name"]], False)
    block = NotifyChange(event_id=3, kind="created", title="", starts_at=datetime(2026, 12, 3, 9, 30, tzinfo=zone),
                         all_day=False, recurring=False, busy=True, actors=[FROM_SOURCE])
    busy_heading = words.texts["busy"].format(name=example["work"], owner=example["name"])
    return {"bundle": bundle, "busy": compose(words, busy_heading, [block], [], True)}


_warned: dict[str, float] = {}


def _warn(reason: str, text: str) -> None:
    now = time.monotonic()
    if now - _warned.get(reason, -WARN_EVERY * 2) >= WARN_EVERY:
        _warned[reason] = now
        logger.warning(text)


def delivery(https: bool) -> dict[str, bool]:
    """Whether notifications can arrive at all (EC-14): browsers take push only from a page over HTTPS, and the
    server must reach the push services in the internet. The log says why not, now and then."""
    from . import internet

    online = internet.reachable()
    if not https:
        _warn("https", "Notifications cannot arrive: nextime is not reached over HTTPS, and browsers allow push "
                       "only there")
    if not online:
        _warn("internet", "Notifications cannot arrive: the server does not reach the internet and with it the "
                          "push services")
    return {"https": https, "internet": online}


def calendar_settings(db: Session, account: Account, calendar_id: int, session_id: int | None,
                      https: bool) -> dict[str, Any]:
    from . import calendars

    found = calendars.access(db, account.id, calendar_id)
    if found is None:
        raise error("not_found", "Not found.", 404)
    setting = calendars.setting(db, account.id, calendar_id)
    is_shared = shared(db, found)
    owner = db.get(Account, found.calendar.owner_id)
    result = {
        "calendar": {"id": found.calendar.id, "name": found.calendar.name, "color": found.calendar.color,
                     "right": found.right, "owner": owner.shown_name if owner and found.right != calendars.OWNER
                     else None, "kind": found.kind or "own"},
        "shared": is_shared,
        "on": bool(setting.notify) and is_shared,
        "devices": devices_of(db, account, session_id),
        "delivery": delivery(https),
        "preview": preview(account, found.calendar),
    }
    db.commit()
    return result


def set_switch(db: Session, account: Account, calendar_id: int, on: bool) -> None:
    from . import calendars

    found = calendars.access(db, account.id, calendar_id)
    if found is None:
        raise error("not_found", "Not found.", 404)
    if on and not shared(db, found):
        raise error("calendar_not_shared", "Notifications are for calendars shared with others.", 409)
    calendars.setting(db, account.id, calendar_id).notify = on
    db.commit()


# --- Accounts going ------------------------------------------------------------------------------------------------


def accounts_out() -> None:
    """``security.SESSIONS_ENDED`` (a sign-out, a block, a delete, a new password): the browsers of a blocked or
    deleted account stop at once, and what waits for it goes (EC-12). A sign-out took its browser along already."""
    with SessionLocal() as db:
        out = select(Account.id).where(or_(Account.blocked_at.is_not(None), Account.deleted_at.is_not(None)))
        db.execute(delete(WebPushSubscription).where(WebPushSubscription.account_id.in_(out)))
        db.execute(update(NotifyOutbox).where(NotifyOutbox.account_id.in_(out), NotifyOutbox.sent_at.is_(None),
                                              NotifyOutbox.failed_at.is_(None))
                   .values(failed_at=clock(), last_error="account_out", payload={}))
        db.execute(delete(WebPushSubscription).where(WebPushSubscription.session_id.not_in(select(AuthSession.id))))
        db.commit()


def register() -> None:
    """Once per start: the hook for ended sessions, and the Web Push key made now if there is none yet."""
    from ..security import SESSIONS_ENDED

    if accounts_out not in SESSIONS_ENDED:
        SESSIONS_ENDED.append(accounts_out)
    with SessionLocal() as db:
        webpush.server_key(db)
