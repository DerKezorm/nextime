"""Keeping the calendars of iCloud, Google, other CalDAV servers, subscriptions and holidays in step (TIME-15 to
TIME-18).

A round per source when it is due: every 60 seconds for iCloud, Google and CalDAV (changes there show within two
minutes, TIME-15 AC-4), once an hour for a subscription (TIME-17 AC-2), once a day for the holidays. A change made in
nextime is noted in ``remote_changes`` (``note_change``, called for every stored change) and pushed at once by the
next round, which the change wakes (AC-3). A source not answering keeps its changes waiting, across restarts, and
gets them as soon as it answers again (EC-2, EC-7); a source that refuses one for good gets its own state back, and
the member a notice with the lost change (EC-6).

The rounds run in a thread of their own; the views read the copy in the database and never wait for a source
(EC-17). Tests drive a round directly (``run_due``, ``run_source``) with their own clock (``clock``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any

import icalendar
from sqlalchemy import delete, event, func, select
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    ORIGIN_IMIP,
    SOURCE_CALDAV,
    SOURCE_GOOGLE,
    SOURCE_HOLIDAYS,
    SOURCE_ICLOUD,
    SOURCE_SUBSCRIPTION,
    STATE_EXPIRED,
    STATE_OK,
    STATE_PAUSED,
    STATE_UNREACHABLE,
    WRITABLE_SOURCES,
    Calendar,
    Event,
    EventChange,
    EventConflict,
    RemoteChange,
    RemoteNotice,
    Source,
    utcnow,
)
from . import ical, internet, live, vault
from .remote_dav import AUTH, CONFLICT, DOWN, GONE, Remote, RemoteError

logger = logging.getLogger("nextime.sync")

#: Seconds between rounds of each kind.
INTERVALS = {SOURCE_ICLOUD: 60, SOURCE_CALDAV: 60, SOURCE_GOOGLE: 60, SOURCE_SUBSCRIPTION: 3600,
             SOURCE_HOLIDAYS: 86400}
#: The longest wait after rounds that found a source not answering (it asks to slow down, or is away).
LONGEST_WAIT = 900
#: An access that expired is tried again at most this often (TIME-15 EC-1).
EXPIRED_RETRY = 3600
#: The most one round of one source may take altogether (TIME-15 EC-17, AC-4).
ROUND_SECONDS = 45
#: Sources synced at the same time: one that hangs does not hold up the others.
PARALLEL = 4
#: How often the loop looks for due rounds.
TICK_SECONDS = 5
#: Events written per transaction on a first fetch: the views stay usable meanwhile (TIME-15 EC-12).
COMMIT_EVERY = 500
#: The sources that live on the internet pause without it (TIME-1 EC-2, TIME-17 EC-15); a CalDAV server may be at
#: home.
NEEDS_INTERNET = (SOURCE_ICLOUD, SOURCE_GOOGLE, SOURCE_SUBSCRIPTION)

#: The time, replaceable in tests.
clock: Callable[[], datetime] = utcnow
_locks: dict[int, threading.Lock] = {}
_locks_lock = threading.Lock()
_wake = threading.Event()
_NUDGE = "sync_nudge"


def place(source: Source) -> str:
    return f"source:{source.id}"


def access_of(source: Source) -> dict[str, Any] | None:
    return vault.open_(source.secret_enc, place(source))


def _lock(source_id: int) -> threading.Lock:
    with _locks_lock:
        return _locks.setdefault(source_id, threading.Lock())


# --- Noting changes made in nextime --------------------------------------------------------------------------------


def note_change(db: Session, calendar_id: int, href: str, deleted: bool, row: Event | None) -> None:
    """A change to an event of a calendar from iCloud, Google or another CalDAV server waits to go there."""
    source_id = db.scalar(select(Calendar.source_id).where(Calendar.id == calendar_id))
    if source_id is None:
        return
    kind = db.scalar(select(Source.kind).where(Source.id == source_id))
    if kind not in WRITABLE_SOURCES:
        return
    remote_etag = ""
    if row is not None and row.remote_of == calendar_id:
        remote_etag = row.remote_etag
    db.add(RemoteChange(calendar_id=calendar_id, href=href, op="delete" if deleted else "put",
                        remote_etag=remote_etag, created_at=clock()))
    if row is not None and not deleted:
        row.waiting = True
    db.info[_NUDGE] = True


def note_reply(db: Session, row: Event, answer: str, occurrence: str) -> None:
    """An answer to an invitation from Google, sent through Google's API (TIME-18 AC-2)."""
    db.add(RemoteChange(calendar_id=row.calendar_id, href=row.href, op="reply", answer=answer,
                        occurrence=occurrence, remote_etag=row.remote_etag, created_at=clock()))
    row.waiting = True
    db.info[_NUDGE] = True


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    if session.info.pop(_NUDGE, None):
        nudge()


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop(_NUDGE, None)


def nudge() -> None:
    """Something waits to go out: the loop looks at once instead of at its next tick."""
    _wake.set()


def pending(db: Session, source_id: int) -> int:
    """How many changes of the source still wait (TIME-15 EC-8)."""
    calendars = select(Calendar.id).where(Calendar.source_id == source_id)
    return int(db.scalar(select(func.count(func.distinct(RemoteChange.href))).where(
        RemoteChange.calendar_id.in_(calendars))) or 0)


# --- States --------------------------------------------------------------------------------------------------------


def set_state(db: Session, source: Source, state: str, problem: str = "") -> None:
    now = clock()
    if source.state != state:
        source.state = state
        source.state_since = now
        logger.info("Source state changed source=%s kind=%s state=%s problem=%s", source.id, source.kind, state,
                    problem or "-")
    source.problem = problem
    locked = state in (STATE_EXPIRED, STATE_PAUSED)
    for calendar in db.scalars(select(Calendar).where(Calendar.source_id == source.id)):
        if calendar.locked != locked:
            calendar.locked = locked
            live.touch_calendar(db, calendar.id)
    live.touch_accounts(db, {source.account_id})


def _worked(db: Session, source: Source) -> None:
    set_state(db, source, STATE_OK)
    source.synced_at = clock()
    source.failures = 0
    source.due_at = clock() + timedelta(seconds=INTERVALS[source.kind])


def _failed(db: Session, source: Source, problem: str) -> None:
    source.failures += 1
    wait = min(INTERVALS[source.kind] * (2 ** min(source.failures - 1, 4)), max(LONGEST_WAIT, INTERVALS[source.kind]))
    source.due_at = clock() + timedelta(seconds=wait)
    set_state(db, source, STATE_UNREACHABLE, problem)


def _expired(db: Session, source: Source, problem: str = "credentials") -> None:
    # The old password again every minute could get the account locked at Apple: once an hour, and at once after
    # "Reconnect" (TIME-15 EC-1).
    source.due_at = clock() + timedelta(seconds=max(INTERVALS[source.kind], EXPIRED_RETRY))
    set_state(db, source, STATE_EXPIRED, problem)


# --- Rounds ---------------------------------------------------------------------------------------------------------


def run_due(now: datetime | None = None) -> list[int]:
    """Every source whose round is due, one after the other. The ids that ran."""
    moment = now or clock()
    with SessionLocal() as db:
        due = list(db.scalars(select(Source.id).where((Source.due_at.is_(None)) | (Source.due_at <= moment))
                              .order_by(Source.id)))
        waiting = set(db.scalars(select(Calendar.source_id).join(RemoteChange, RemoteChange.calendar_id == Calendar.id)
                                 .where(Calendar.source_id.is_not(None))))
    # A change waiting to go out does not wait for the round (AC-3), unless the source is resting after failures.
    wanted = sorted(set(due) | waiting)
    if len(wanted) <= 1:
        return [source_id for source_id in wanted if run_source(source_id, only_push=source_id not in due)]
    with ThreadPoolExecutor(max_workers=PARALLEL, thread_name_prefix="nextime-sync") as pool:
        results = list(pool.map(lambda source_id: run_source(source_id, only_push=source_id not in due), wanted))
    return [source_id for source_id, done in zip(wanted, results, strict=True) if done]


def run_source(source_id: int, *, force: bool = False, only_push: bool = False) -> bool:
    """One round of one source. False when a round of it is already running (a second "Fetch again" makes no second
    fetch, TIME-17 EC-9)."""
    lock = _lock(source_id)
    if not lock.acquire(blocking=False):
        return False
    try:
        with SessionLocal() as db:
            source = db.get(Source, source_id)
            if source is None:
                return False
            if only_push and (source.state != STATE_OK or source.failures):
                return False
            if source.kind in NEEDS_INTERNET and not internet.reachable():
                # Paused, not failed: the last known events stay, nothing turns orange (TIME-17 EC-15).
                source.due_at = clock() + timedelta(seconds=min(INTERVALS[source.kind], 300))
                db.commit()
                return True
            try:
                _round(db, source, force=force, only_push=only_push)
            except Exception:
                db.rollback()
                logger.exception("Sync round failed source=%s kind=%s", source_id, source.kind)
                source = db.get(Source, source_id)
                if source is not None:
                    _failed(db, source, "error")
            db.commit()
        return True
    finally:
        lock.release()


def _round(db: Session, source: Source, *, force: bool, only_push: bool) -> None:
    from . import google, holidays, subscriptions

    if source.kind == SOURCE_HOLIDAYS:
        holidays.round_(db, source)
        _worked(db, source)
        return
    if source.kind == SOURCE_SUBSCRIPTION:
        subscriptions.round_(db, source, force=force)
        return
    if source.kind == SOURCE_GOOGLE and not google.enabled(db):
        # The operator switched Google off: the account rests with its last known events (TIME-16 EC-5).
        source.due_at = clock() + timedelta(seconds=INTERVALS[source.kind])
        set_state(db, source, STATE_PAUSED, "google_off")
        return
    secret = access_of(source)
    if secret is None:
        # A backup restored next to another key, or nothing stored: "Reconnect" (TIME-15 EC-22).
        _expired(db, source, "key")
        return
    try:
        remote = google.remote_for(db, source, secret) if source.kind == SOURCE_GOOGLE else Remote(
            source.server, username=source.username, password=str(secret.get("password", "")))
    except RemoteError as exc:
        _handle(db, source, exc)
        return
    for attempt in (0, 1):
        with remote:
            remote.limit(ROUND_SECONDS)
            try:
                push(db, source, remote)
                if not only_push:
                    pull(db, source, remote)
                    _worked(db, source)
                elif source.state != STATE_OK:
                    _worked(db, source)
                return
            except RemoteError as exc:
                if exc.kind == AUTH and source.kind == SOURCE_GOOGLE and attempt == 0:
                    # Google's token may be withdrawn before its time: a fresh one from the stored grant first,
                    # "Access expired" only when that fails too (TIME-16 AC-8, EC-1).
                    google.drop_token(source.id)
                    try:
                        remote = google.remote_for(db, source, secret)
                    except RemoteError as again:
                        _handle(db, source, again)
                        return
                    continue
                _handle(db, source, exc)
                return


def _handle(db: Session, source: Source, exc: RemoteError) -> None:
    if exc.kind == AUTH:
        _expired(db, source)
    else:
        _failed(db, source, exc.kind if exc.status == 0 else f"{exc.kind}_{exc.status}")


# --- Pulling --------------------------------------------------------------------------------------------------------


def pull(db: Session, source: Source, remote: Remote) -> None:
    """The calendar list first (renamed, read only, deleted, new ones offered), then what changed in each chosen
    calendar."""
    from . import sources

    home = str((source.options or {}).get("home") or "")
    if home:
        found = remote.calendars(home)
        sources.apply_listing(db, source, found)
    calendars = list(db.scalars(select(Calendar).where(Calendar.source_id == source.id).order_by(Calendar.id)))
    for calendar in calendars:
        remote_tag = next((item.get("tag", "") for item in (source.remote or []) if item.get("url") ==
                           calendar.remote_url), "")
        if remote_tag and remote_tag == calendar.ctag and calendar.sync_token:
            continue
        pull_calendar(db, source, remote, calendar)
        calendar.ctag = remote_tag
        db.commit()
    source.loading = False


def pull_calendar(db: Session, source: Source, remote: Remote, calendar: Calendar) -> None:
    rows = db.execute(select(Event.href, Event.remote_etag).where(Event.calendar_id == calendar.id,
                                                                  Event.deleted_at.is_(None))).all()
    known = {href: etag for href, etag in rows}
    waiting = set(db.scalars(select(RemoteChange.href).where(RemoteChange.calendar_id == calendar.id)))
    try:
        found = remote.changes(calendar.remote_url, calendar.sync_token, known)
    except RemoteError as exc:
        if exc.kind == GONE:
            return
        raise
    unreadable = set((source.options or {}).get("unreadable", {}).get(str(calendar.id), []))
    written = 0
    for href, etag, text in found.changed:
        if href in waiting:
            continue
        if text is None or not store(db, calendar, href, etag, text):
            unreadable.add(href)
            continue
        unreadable.discard(href)
        written += 1
        if written % COMMIT_EVERY == 0:
            db.commit()
    for href in found.deleted:
        unreadable.discard(href)
        if href not in waiting:
            remove(db, calendar, href)
    calendar.sync_token = found.sync_token
    options = dict(source.options or {})
    by_calendar = dict(options.get("unreadable", {}))
    by_calendar[str(calendar.id)] = sorted(unreadable)
    options["unreadable"] = by_calendar
    source.options = options


def store(db: Session, calendar: Calendar, href: str, etag: str, text: str) -> bool:
    """An object from the source into the calendar, kept as it came (nothing it brings gets lost, TIME-15 EC-19).
    False when nextime cannot read it (EC-18)."""
    from . import events

    if len(text.encode("utf-8")) > ical.MAX_OBJECT_BYTES:
        return False
    try:
        parsed = ical.parse(text)
        uid = ical.check(parsed)
        ical.derive(parsed)
    except (ical.IcalError, Exception):  # noqa: BLE001 - whatever a source sends, it must not stop the round
        return False
    row = db.scalar(select(Event).where(Event.calendar_id == calendar.id, Event.deleted_at.is_(None),
                                        (Event.href == href) | (Event.uid == uid)))
    if row is not None and row.ical == text and row.href == href:
        row.remote_etag, row.remote_of, row.waiting = etag, calendar.id, False
        return True
    if row is None:
        row = Event(calendar_id=calendar.id, uid=uid, href=href, ical="", etag="", created_by=None,
                    starts_at=clock())
        db.add(row)
    elif row.href != href:
        events.record_change(db, calendar.id, row.href, deleted=True, remote=False)
        row.href = href
    row.uid = uid
    events.write(db, row, text, parsed, None)
    row.remote_etag, row.remote_of, row.waiting = etag, calendar.id, False
    events.record_change(db, calendar.id, href, remote=False)
    _copies_give_way(db, calendar, row)
    from . import notify

    # Changed at the source: the others it is shared with hear of it, naming the calendar, not a person (TIME-21 EC-3).
    notify.note(db, calendar.id, row, deleted=False, by=notify.FROM_SOURCE)
    return True


def _copies_give_way(db: Session, calendar: Calendar, row: Event) -> None:
    """An invitation a program entered from a mail (``imip``) goes into the trash once iCloud, Google or a CalDAV
    server of the same person brings the same UID: the source's event wins, the event stands there once (TIME-26,
    for nexmail). The copy can be brought back from the trash, which names why it went. The member's answer in the
    copy moves over to the source's event without anything being sent: the program sent it by mail already."""
    from . import events

    copies = list(db.scalars(select(Event).join(Calendar, Calendar.id == Event.calendar_id).where(
        Event.uid == row.uid, Event.origin == ORIGIN_IMIP, Event.deleted_at.is_(None),
        Event.calendar_id != calendar.id, Calendar.owner_id == calendar.owner_id)))
    if not copies or calendar.source_id is None:
        return
    source = db.get(Source, calendar.source_id)
    if source is None or source.kind not in WRITABLE_SOURCES:
        return
    answer = _answer_of(db, calendar.owner_id, copies[-1])
    for copy in copies:
        events.trash(db, copy, None)
        copy.deleted_reason = "source_has_it"
    logger.info("Copies from a mail gave way to their source calendar=%s count=%s", calendar.id, len(copies))
    if answer:
        _carry_answer(db, source, calendar, row, answer)


def _answer_of(db: Session, owner_id: int, copy: Event) -> str:
    """The member's answer in a copy from a mail (``accepted``, ``tentative``, ``declined``), or ""."""
    from . import invitations

    mine = invitations.mail_addresses(copy, invitations.account_addresses(db, owner_id))
    found = invitations.state_of(ical.master_of(ical.parse(copy.ical)), mine)
    return found["state"] if found and found["state"] in invitations.PARTSTAT else ""


def _carry_answer(db: Session, source: Source, calendar: Calendar, row: Event, answer: str) -> None:
    """The member's part of the source's event takes ``answer`` and goes back quietly: to iCloud and CalDAV with
    ``SCHEDULE-AGENT=CLIENT`` and ``Schedule-Reply: F`` (``silent``), to Google through its API with
    ``sendUpdates=none`` (``quiet``). Nothing is written when the source has that answer already."""
    from . import events, invitations

    mine = invitations.own_addresses(source)
    parsed = ical.parse(row.ical)
    wanted = invitations.PARTSTAT[answer]
    changed = False
    for component in ical.events_of(parsed):
        part = invitations._self_part(component, mine)
        if part is not None and str(part.params.get("PARTSTAT", "")).upper() != wanted:
            part.params["PARTSTAT"] = wanted
            part.params.pop("RSVP", None)
            changed = True
    if not changed:
        return
    events.write(db, row, ical.serialize(parsed), parsed, None)
    events.record_change(db, calendar.id, row.href, remote=False)
    if source.kind == SOURCE_GOOGLE:
        db.add(RemoteChange(calendar_id=calendar.id, href=row.href, op="quiet", answer=answer, occurrence="",
                            remote_etag=row.remote_etag, created_at=clock()))
    else:
        db.add(RemoteChange(calendar_id=calendar.id, href=row.href, op="silent", remote_etag=row.remote_etag,
                            created_at=clock()))
    row.waiting = True
    db.info[_NUDGE] = True


#: Sent with every write of an invitation entered from a mail: the server sends no answer of its own (RFC 6638).
NO_REPLY = {"Schedule-Reply": "F"}


def _outgoing(row: Event) -> str:
    """The text that goes to the source. An invitation entered from a mail keeps ``SCHEDULE-AGENT=CLIENT`` at its
    organizer whatever the source made of it meanwhile: the program that took it from the mail answers, the server
    must not (RFC 6638)."""
    if row.origin != ORIGIN_IMIP:
        return row.ical
    return client_scheduled(row.ical)


def client_scheduled(text: str) -> str:
    """``SCHEDULE-AGENT=CLIENT`` on the ORGANIZER of every part; the text as it was when nothing was missing."""
    parsed = ical.parse(text)
    missing = False
    for component in ical.events_of(parsed):
        organizer = component.get("ORGANIZER")
        if organizer is not None and str(organizer.params.get("SCHEDULE-AGENT", "")).upper() != "CLIENT":
            organizer.params["SCHEDULE-AGENT"] = "CLIENT"
            missing = True
    return ical.serialize(parsed) if missing else text


def _send(db: Session, source: Source, remote: Remote, calendar: Calendar, row: Event, url: str, etag: str,
          quiet: bool = False) -> str:
    """The event to its source; the new ETag. An invitation from a mail goes to Google through ``events.import``
    (keeps its UID, sends nothing), to iCloud and CalDAV with ``Schedule-Reply: F``; so does a ``quiet`` change (an
    answer carried over from such a copy), once."""
    from . import google

    if row.origin == ORIGIN_IMIP and source.kind == SOURCE_GOOGLE:
        google.import_event(db, source, calendar, row)
        return ""
    if quiet:
        return remote.put(url, client_scheduled(row.ical), etag, NO_REPLY)
    if row.origin == ORIGIN_IMIP:
        return remote.put(url, _outgoing(row), etag, NO_REPLY)
    return remote.put(url, row.ical, etag)


def remove(db: Session, calendar: Calendar, href: str) -> None:
    """Gone at the source: gone here, not into the trash (TIME-15 EC-20, TIME-23 AC-5)."""
    row = db.scalar(select(Event).where(Event.calendar_id == calendar.id, Event.href == href,
                                        Event.deleted_at.is_(None)))
    if row is None:
        return
    from . import notify

    notify.note(db, calendar.id, row, deleted=True, by=notify.FROM_SOURCE)
    db.delete(row)
    db.add(EventChange(calendar_id=calendar.id, href=href, deleted=True))
    live.touch_calendar(db, calendar.id)
    from . import reminders

    reminders.touch_on_commit(db, calendar_id=calendar.id, href=href)


def replace_all(db: Session, calendar: Calendar, objects: dict[str, str]) -> None:
    """The whole content of a subscription or the holidays at once, in one transaction: nothing shows half, and a
    fetch that broke off changes nothing (TIME-17 EC-8). ``objects`` maps hrefs to iCalendar texts."""
    current = {row.href: row for row in db.scalars(select(Event).where(Event.calendar_id == calendar.id,
                                                                       Event.deleted_at.is_(None)))}
    for href, text in objects.items():
        row = current.get(href)
        if row is not None and row.ical == text:
            continue
        store(db, calendar, href, ical.etag_of(text), text)
    for href in set(current) - set(objects):
        remove(db, calendar, href)


# --- Pushing --------------------------------------------------------------------------------------------------------


def push(db: Session, source: Source, remote: Remote) -> None:
    """What waits goes out, oldest first; per event only its latest state."""
    from . import google

    calendars = {row.id: row for row in db.scalars(select(Calendar).where(Calendar.source_id == source.id))}
    if not calendars:
        return
    changes = list(db.scalars(select(RemoteChange).where(RemoteChange.calendar_id.in_(calendars))
                              .order_by(RemoteChange.id)))
    done: set[tuple[int, str, str]] = set()

    def kind_of(item: RemoteChange) -> str:
        # The object's latest state goes once; answers once per occurrence they are for (TIME-18 EC-6).
        return f"reply:{item.occurrence}" if item.op in ("reply", "quiet") else "object"

    for change in changes:
        kind = kind_of(change)
        key = (change.calendar_id, change.href, kind)
        if key in done:
            continue
        done.add(key)
        latest = [item for item in changes if (item.calendar_id, item.href, kind_of(item)) == key]
        calendar = calendars[change.calendar_id]
        url = calendar.remote_url + change.href
        row = db.scalar(select(Event).where(Event.calendar_id == calendar.id, Event.href == change.href,
                                            Event.deleted_at.is_(None)))
        try:
            if kind != "object":
                last = latest[-1]
                if row is not None:
                    google.reply(db, source, calendar, row, last.answer, last.occurrence,
                                 send=last.op != "quiet")
            elif row is not None:
                etag = row.remote_etag if row.remote_of == calendar.id else ""
                _put(db, source, remote, calendar, row, url, etag, latest[-1])
            else:
                deletes = [item for item in latest if item.op == "delete"]
                etag = deletes[-1].remote_etag if deletes else ""
                gone = db.scalar(select(Event).where(Event.calendar_id == calendar.id, Event.href == change.href,
                                                     Event.deleted_at.is_not(None)).order_by(Event.id.desc()))
                if gone is not None and gone.origin == ORIGIN_IMIP and source.kind == SOURCE_GOOGLE:
                    google.delete_imported(db, source, calendar, gone.uid)
                else:
                    _delete(db, source, remote, calendar, change.href, url, etag,
                            deletes[-1].created_at if deletes else latest[-1].created_at,
                            NO_REPLY if gone is not None and gone.origin == ORIGIN_IMIP else None)
        except RemoteError as exc:
            if exc.kind in (AUTH, DOWN):
                for item in latest:
                    item.attempts += 1
                db.commit()
                raise
            _refused(db, source, remote, calendar, change.href, url, row, latest)
        for item in latest:
            db.delete(item)
        if row is not None and not db.scalar(select(func.count()).select_from(RemoteChange).where(
                RemoteChange.calendar_id == calendar.id, RemoteChange.href == change.href,
                RemoteChange.id.not_in([item.id for item in latest]))):
            row.waiting = False
        live.touch_calendar(db, calendar.id)
        db.commit()


def _put(db: Session, source: Source, remote: Remote, calendar: Calendar, row: Event, url: str, etag: str,
         change: RemoteChange) -> None:
    try:
        new_etag = _send(db, source, remote, calendar, row, url, etag, change.op == "silent")
    except RemoteError as exc:
        if exc.kind != CONFLICT:
            raise
        _conflict(db, source, remote, calendar, row, url, change)
        return
    row.remote_etag, row.remote_of = new_etag, calendar.id


def _changed_at(text: str) -> datetime | None:
    try:
        master = ical.master_of(ical.parse(text))
        for name in ("LAST-MODIFIED", "DTSTAMP"):
            if master.get(name) is not None:
                value = master.decoded(name)
                if isinstance(value, datetime):
                    return ical._as_utc(value)
    except Exception:  # noqa: BLE001
        return None
    return None


def _conflict(db: Session, source: Source, remote: Remote, calendar: Calendar, row: Event, url: str,
              change: RemoteChange) -> None:
    """Changed there and here at the same time: the later save wins, the one it overwrote is told (TIME-15 EC-3,
    TIME-5 EC-2)."""
    from . import events

    fetched = remote.get(url)
    if fetched is None:
        raise RemoteError("refused", 404)
    text, etag = fetched
    theirs = _changed_at(text)
    mine_at = ical._as_utc(change.created_at)
    if theirs is not None and theirs > mine_at:
        mine = row.ical
        store(db, calendar, row.href, etag, text)
        db.add(EventConflict(event_id=row.id, account_id=source.account_id, by_id=None, mine_ical=mine,
                             theirs_etag=row.etag))
        return
    new_etag = _send(db, source, remote, calendar, row, url, etag, change.op == "silent")
    row.remote_etag, row.remote_of = new_etag, calendar.id
    events.record_change(db, calendar.id, row.href, remote=False)


def _delete(db: Session, source: Source, remote: Remote, calendar: Calendar, href: str, url: str,
            etag: str, deleted_at: datetime, extra: dict[str, str] | None = None) -> None:
    """The deletion goes out. Changed there meanwhile (412): as for a change, the later one wins (TIME-15 EC-3); a
    deletion that waited long loses against a newer version from the phone, which comes back with a notice."""
    try:
        remote.delete(url, etag, *([extra] if extra else []))
    except RemoteError as exc:
        if exc.kind != CONFLICT:
            raise
        fetched = remote.get(url)
        if fetched is None:
            return
        text, newer = fetched
        theirs = _changed_at(text)
        if theirs is not None and theirs > ical._as_utc(deleted_at):
            gone = db.scalar(select(Event).where(Event.calendar_id == calendar.id, Event.href == href,
                                                 Event.deleted_at.is_not(None)).order_by(Event.id.desc()))
            db.add(RemoteNotice(account_id=source.account_id, calendar_id=calendar.id,
                                title=gone.title if gone is not None else "", lost_ical="", source_kind=source.kind,
                                created_at=clock()))
            store(db, calendar, href, newer, text)
            live.touch_accounts(db, {source.account_id})
            return
        remote.delete(url, newer, *([extra] if extra else []))


def _refused(db: Session, source: Source, remote: Remote, calendar: Calendar, href: str, url: str,
             row: Event | None, latest: list[RemoteChange]) -> None:
    """The source refused the change for good (TIME-15 EC-6): its own state comes back, the member hears what got
    lost with "Create as a new event" and "Discard"."""
    deleted_row = None
    if row is None:
        deleted_row = db.scalar(select(Event).where(Event.calendar_id == calendar.id, Event.href == href,
                                                    Event.deleted_at.is_not(None)).order_by(Event.id.desc()))
    title = (row or deleted_row).title if (row or deleted_row) is not None else ""
    lost = row.ical if row is not None and any(item.op == "put" for item in latest) else ""
    db.add(RemoteNotice(account_id=source.account_id, calendar_id=calendar.id, title=title, lost_ical=lost,
                        source_kind=source.kind, created_at=clock()))
    logger.info("Source refused a change source=%s calendar=%s", source.id, calendar.id)
    try:
        fetched = remote.get(url)
    except RemoteError as exc:
        if exc.kind in (AUTH, DOWN):
            raise
        fetched = None
    if fetched is None:
        if row is not None:
            db.delete(row)
            db.add(EventChange(calendar_id=calendar.id, href=href, deleted=True))
            from . import reminders

            reminders.touch_on_commit(db, calendar_id=calendar.id, href=href)
        return
    text, etag = fetched
    if row is not None:
        row.ical = ""  # stored anew below, whatever it held
    store(db, calendar, href, etag, text)
    live.touch_accounts(db, {source.account_id})


# --- The loop -------------------------------------------------------------------------------------------------------


async def run_forever(stop: asyncio.Event) -> None:
    while not stop.is_set():
        _wake.clear()
        try:
            await asyncio.to_thread(run_due)
        except Exception:
            logger.exception("Sync failed")
        waited = 0.0
        while waited < TICK_SECONDS and not stop.is_set() and not _wake.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=0.5)
                return
            except TimeoutError:
                waited += 0.5


def http_date(value: str) -> datetime | None:
    try:
        return parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None


def drop_changes(db: Session, source_id: int) -> None:
    calendars = select(Calendar.id).where(Calendar.source_id == source_id)
    db.execute(delete(RemoteChange).where(RemoteChange.calendar_id.in_(calendars)))


__all__ = ["icalendar"]
