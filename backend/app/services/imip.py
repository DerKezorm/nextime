"""Invitations a program enters from a mail (iMIP, RFC 6047) through ``POST /api/v1/events/ical`` (TIME-26, for
nexmail).

The program read the invitation in a mail, the member answered there, and the program sends that answer to the
organizer itself. nextime only keeps the event, and sends nothing:

- The object stays as the organizer sent it: UID, ORGANIZER, ATTENDEE, SEQUENCE, the exceptions of a series. Only the
  member's own attendee line takes the answer (PARTSTAT, RSVP gone), METHOD and alarms of the sender go, and the
  organizer gets ``SCHEDULE-AGENT=CLIENT``. Into iCloud or another CalDAV server it goes with ``Schedule-Reply: F``
  (``sync._send``), into Google through ``events.import``; nextime's own calendars send nothing anyway.
- The row is marked ``origin = imip``: such a copy is answered by mail (``invitations.by_mail``), takes a higher
  SEQUENCE from a later mail, goes into the trash on a CANCEL, and gives way once a source of the same person brings
  the same UID (``sync._copies_give_way``). An event from a source is never changed here: the source decides.
- The same UID in the key's calendars is the same event: a second delivery gives the first (200), and two requests at
  the same moment are one after the other (``held``), so there is never a second copy.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import icalendar
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import error
from ..models import ORIGIN_IMIP, Account, Event
from . import calendars, events, ical, invitations

#: The longest text a program may send: a mail's invitation is far smaller.
MAX_CHARS = 1_000_000
#: What a mail may ask for. PUBLISH (an event to take without answering) is taken like a REQUEST.
TAKEN = ("REQUEST", "PUBLISH")
CANCEL = "CANCEL"

CREATED = "created"
EXISTING = "existing"
UPDATED = "updated"
CANCELLED = "cancelled"


class _Held:
    """One lock per key (an account and a UID, or an account and an event), gone once nobody holds or waits."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._keys: dict[tuple[Any, ...], list[Any]] = {}

    @contextmanager
    def __call__(self, *key: Any) -> Iterator[None]:
        with self._lock:
            entry = self._keys.setdefault(key, [threading.Lock(), 0])
            entry[1] += 1
        entry[0].acquire()
        try:
            yield
        finally:
            entry[0].release()
            with self._lock:
                entry[1] -= 1
                if not entry[1]:
                    self._keys.pop(key, None)


#: Two requests about the same invitation of the same account wait for each other (no second copy, no second answer).
held = _Held()


@dataclass
class Invitation:
    """A mail's invitation, read and checked, before any transaction."""

    method: str
    uid: str
    sequence: int
    parsed: icalendar.Calendar


def read(text: str) -> Invitation:
    """The VCALENDAR of a mail, read like any object a device stores (``ical.parse``, ``ical.check``): one event with
    its exceptions, one UID, within the size of one object (TIME-20 EC-11)."""
    if len(text) > MAX_CHARS or len(text.encode("utf-8")) > ical.MAX_OBJECT_BYTES:
        raise error("ical_too_large", "The calendar data is too large.", 413)
    try:
        parsed = ical.parse(text)
    except ical.IcalError:
        raise error("invalid_ical", "The calendar data is not valid iCalendar.", 422) from None
    method = str(parsed.get("METHOD", "") or "REQUEST").strip().upper()
    if method not in (*TAKEN, CANCEL):
        raise error("ical_method_unsupported", "Only REQUEST, PUBLISH and CANCEL are taken.", 422, method=method)
    parsed.pop("METHOD", None)
    try:
        uid = ical.check(parsed)
        ical.derive(parsed)
    except ical.IcalError as exc:
        raise error("invalid_ical", "The calendar data is not valid iCalendar.", 422, why=exc.code) from None
    except Exception:  # noqa: BLE001 - whatever a mail brings, it is refused as data, never a server error
        raise error("invalid_ical", "The calendar data is not valid iCalendar.", 422) from None
    for component in ical.events_of(parsed):
        # The sender's alarms are not the member's: the member's own reminders apply (TIME-7).
        component.subcomponents = [part for part in component.subcomponents if part.name != "VALARM"]
    return Invitation(method=method, uid=uid, sequence=_sequence(ical.master_of(parsed)), parsed=parsed)


def _sequence(component: icalendar.cal.Component) -> int:
    try:
        return max(0, int(str(component.get("SEQUENCE", 0) or 0)))
    except ValueError:
        return 0


def own_part(parsed: icalendar.Calendar, mine: set[str], wanted: str = "") -> str:
    """The member's address among the attendees (``wanted`` first); refused when the member organizes it or is not
    invited."""
    master = ical.master_of(parsed)
    organizer = master.get("ORGANIZER")
    if organizer is None or invitations.address(organizer) in mine:
        raise error("not_an_invitation", "This is no invitation to you.", 422)
    found = {invitations.address(item) for component in ical.events_of(parsed)
             for item in invitations._attendees(component)}
    wanted = invitations.address(wanted)
    if wanted and wanted in found:
        return wanted
    for item in invitations._attendees(master):
        if invitations.address(item) in mine:
            return invitations.address(item)
    for component in ical.events_of(parsed):
        for item in invitations._attendees(component):
            if invitations.address(item) in mine:
                return invitations.address(item)
    raise error("attendee_unknown", "None of your addresses is among the attendees.", 422)


def prepared(parsed: icalendar.Calendar, me: str, answer: str) -> str:
    """The object as it is kept: the member's part answered, the organizer told the client schedules."""
    wanted = invitations.PARTSTAT[answer]
    for component in ical.events_of(parsed):
        organizer = component.get("ORGANIZER")
        if organizer is not None:
            organizer.params["SCHEDULE-AGENT"] = "CLIENT"
        for item in invitations._attendees(component):
            if invitations.address(item) == me:
                item.params["PARTSTAT"] = wanted
                item.params.pop("RSVP", None)
    ical.add_timezones(parsed)
    return ical.serialize(parsed)


def _answered(parsed: icalendar.Calendar, me: str, answer: str) -> bool:
    """The member's part takes ``answer`` where it differs; whether anything changed."""
    wanted = invitations.PARTSTAT[answer]
    changed = False
    for component in ical.events_of(parsed):
        for item in invitations._attendees(component):
            if invitations.address(item) == me and str(item.params.get("PARTSTAT", "")).upper() != wanted:
                item.params["PARTSTAT"] = wanted
                item.params.pop("RSVP", None)
                changed = True
    return changed


def _href(uid: str) -> str:
    from .transfer import _href as href_of

    return href_of(uid)


@dataclass
class Outcome:
    status: str
    row: Event | None = None
    trash: list[dict[str, Any]] | None = None


def take(db: Session, account: Account, reach: dict[int, calendars.Access], invitation: Invitation, *,
         calendar_id: int | None, answer: str | None, attendee: str = "") -> Outcome:
    """The invitation into the member's calendars (call it under ``held(account.id, uid)``). ``calendar_id`` is where
    a new one goes (already checked against the key); ``reach`` the key's calendars."""
    known = list(db.scalars(select(Event).where(Event.uid == invitation.uid, Event.deleted_at.is_(None),
                                                Event.calendar_id.in_(list(reach) or [0])).order_by(Event.id)))
    copies = [row for row in known if row.origin == ORIGIN_IMIP]
    others = [row for row in known if row.origin != ORIGIN_IMIP]
    if invitation.method == CANCEL:
        if copies:
            entries = []
            for row in copies:
                events.find(db, account, row.id, for_writing=True)
                events.trash(db, row, account.id)
                row.deleted_reason = "cancelled"
                entries.append({"kind": "event", "id": row.id})
            db.commit()
            return Outcome(CANCELLED, None, entries)
        if others:
            # The source tells of the cancellation itself: its event stays as it is.
            return Outcome(EXISTING, others[0])
        raise error("not_found", "Not found.", 404)
    if answer not in invitations.PARTSTAT:
        raise error("invalid_answer", "Answer accepted, tentative or declined.", 422)
    base = invitations.account_addresses(db, account.id)
    if others:
        # From a source (or made in nextime): that one stands, nothing here changes it.
        return Outcome(EXISTING, others[0])
    if copies:
        row = copies[0]
        me = own_part(invitation.parsed, invitations.mail_addresses(row, base), attendee or row.origin_address)
        events.find(db, account, row.id, for_writing=True)
        if invitation.sequence > row.origin_sequence:
            text = prepared(invitation.parsed, me, answer)
            status = UPDATED
        else:
            # The same or an older version again: only the answer may differ.
            current = ical.parse(row.ical)
            if not _answered(current, me, answer):
                return Outcome(EXISTING, row)
            text = ical.serialize(current)
            status = EXISTING
        parsed = ical.parse(text)
        events.write(db, row, text, parsed, account.id)
        row.origin_address = me
        row.origin_sequence = max(row.origin_sequence, invitation.sequence)
        events.record_change(db, row.calendar_id, row.href, row=row)
        db.commit()
        return Outcome(status, row)
    if calendar_id is None:
        raise error("no_writable_calendar", "There is no calendar to write into.", 409)
    events._writable_calendar(db, account, calendar_id)
    me = own_part(invitation.parsed, base | ({invitations.address(attendee)} if attendee else set()), attendee)
    text = prepared(invitation.parsed, me, answer)
    parsed = ical.parse(text)
    row = Event(calendar_id=calendar_id, uid=invitation.uid, href=_href(invitation.uid), ical="", etag="",
                created_by=account.id, starts_at=ical.derive(parsed).starts_at, origin=ORIGIN_IMIP,
                origin_address=me, origin_sequence=invitation.sequence)
    db.add(row)
    events.write(db, row, text, parsed, account.id)
    events.record_change(db, row.calendar_id, row.href, row=row)
    try:
        db.commit()
    except IntegrityError:
        # The same UID in that calendar meanwhile, by another way (a device, a source round).
        db.rollback()
        found = db.scalar(select(Event).where(Event.calendar_id == calendar_id, Event.uid == invitation.uid,
                                              Event.deleted_at.is_(None)))
        if found is None:
            raise
        return Outcome(EXISTING, found)
    return Outcome(CREATED, row)
