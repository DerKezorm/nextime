"""Invitations that arrive in iCloud, Google or another CalDAV account, and the member's answer (TIME-18).

An event is an invitation for the member when it stands in a calendar of a source the member connected, names the
member among its attendees (one of the member's own addresses there) and somebody else as organizer. The answer goes
back the way the source wants it: for iCloud and CalDAV servers that schedule themselves, the member's part of the
event changes and the server sends the answer (RFC 6638); for Google through Google's API (``services/google.py``).
A server that does neither, and a subscription, show the attendees without buttons (EC-2).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import icalendar
from sqlalchemy.orm import Session

from ..errors import error
from ..models import SOURCE_CALDAV, SOURCE_GOOGLE, WRITABLE_SOURCES, Account, Calendar, Event, Source
from . import calendars, ical

#: The member's state in an invitation, by the PARTSTAT of its part.
STATES = {"NEEDS-ACTION": "open", "ACCEPTED": "accepted", "TENTATIVE": "tentative", "DECLINED": "declined",
          "DELEGATED": "open"}
PARTSTAT = {"accepted": "ACCEPTED", "tentative": "TENTATIVE", "declined": "DECLINED"}
ANSWERS = tuple(PARTSTAT)
#: The first attendees an invitation shows; the rest unfold with "+n more" (EC-8).
FIRST_PEOPLE = 8


def address(value: Any) -> str:
    text = str(value or "").strip()
    return text[7:].lower() if text.lower().startswith("mailto:") else text.lower()


def own_addresses(source: Source | None) -> set[str]:
    if source is None:
        return set()
    found = {address(item) for item in (source.addresses or []) if item}
    if source.kind == SOURCE_GOOGLE and source.username:
        found.add(source.username.strip().lower())
    return {item for item in found if item}


def _attendees(component: icalendar.cal.Component) -> list[Any]:
    value = component.get("ATTENDEE")
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _name(value: Any) -> str:
    params = getattr(value, "params", {}) or {}
    name = str(params.get("CN", "") or "").strip().strip('"')
    return name or address(value)


def people(component: icalendar.cal.Component, mine: set[str]) -> list[dict[str, Any]]:
    """The organizer first, then the attendees, each with name, address and state; the member as ``self``."""
    organizer = component.get("ORGANIZER")
    by = address(organizer) if organizer is not None else ""
    out: list[dict[str, Any]] = []
    if organizer is not None:
        out.append({"name": _name(organizer), "address": by, "state": "accepted", "organizer": True,
                    "self": by in mine})
    for item in _attendees(component):
        who = address(item)
        if who and who == by:
            out[0]["state"] = STATES.get(str(item.params.get("PARTSTAT", "NEEDS-ACTION")).upper(), "open")
            continue
        out.append({"name": _name(item), "address": who,
                    "state": STATES.get(str(item.params.get("PARTSTAT", "NEEDS-ACTION")).upper(), "open"),
                    "organizer": False, "self": who in mine})
    return out


def _self_part(component: icalendar.cal.Component, mine: set[str]) -> Any | None:
    organizer = component.get("ORGANIZER")
    if organizer is None or address(organizer) in mine:
        return None
    for item in _attendees(component):
        if address(item) in mine:
            return item
    return None


def state_of(component: icalendar.cal.Component, mine: set[str]) -> dict[str, Any] | None:
    """The member's invitation in this event (or occurrence): its state and who invites; ``None`` for none."""
    if not mine:
        return None
    part = _self_part(component, mine)
    if part is None:
        return None
    organizer = component.get("ORGANIZER")
    cancelled = str(component.get("STATUS", "")).upper() == "CANCELLED"
    return {"state": STATES.get(str(part.params.get("PARTSTAT", "NEEDS-ACTION")).upper(), "open"),
            "by": _name(organizer), "cancelled": cancelled}


def context(db: Session, item: calendars.Access) -> tuple[set[str], Source | None]:
    """The member's own addresses for a calendar it sees: only the one who connected the source answers (EC-10)."""
    if item.right != calendars.OWNER or item.kind not in WRITABLE_SOURCES or item.calendar.source_id is None:
        return set(), None
    source = db.get(Source, item.calendar.source_id)
    return own_addresses(source), source


def can_answer(source: Source | None, calendar: Calendar) -> tuple[bool, str]:
    """Whether answers go out from here, and why not (EC-2)."""
    if source is None:
        return False, "read_only_source"
    if source.kind == SOURCE_CALDAV and not source.schedules:
        return False, "no_replies"
    if calendar.locked:
        return False, "locked"
    if calendar.read_only:
        # Only to be read at the source: an answer would be refused there (TIME-18 EC-2).
        return False, "read_only_source"
    return True, ""


def detail(db: Session, item: calendars.Access, component: icalendar.cal.Component) -> dict[str, Any]:
    """What the side panel shows of attendees and the invitation (AC-3, AC-4, EC-8)."""
    mine, source = context(db, item)
    everybody = people(component, mine)
    found = state_of(component, mine)
    result: dict[str, Any] = {"attendees": everybody, "attendees_count": len(everybody), "invitation": None}
    if found is not None:
        allowed, why = can_answer(source, item.calendar)
        result["invitation"] = {**found, "source": source.kind if source else "", "can_answer": allowed,
                                "why_not": why}
    elif everybody and source is None and item.kind and item.kind not in WRITABLE_SOURCES:
        # A subscription (also Google's secret address) brings attendees but takes no answers (EC-2).
        result["no_answers"] = "read_only_source"
    return result


def answer(db: Session, account: Account, event_id: int, reply: str, *, scope: str, occurrence: str | None,
           etag: str | None) -> Event:
    """The member's answer (AC-2, AC-5): its part of the event changes here at once; the source gets it with the
    next round, and while it does not answer the event shows "waiting" (EC-3). The same answer twice sends one
    (EC-5); an invitation that changed or was cancelled meanwhile shows its new state instead (EC-1, EC-4)."""
    from . import events, sync

    if reply not in ANSWERS:
        raise error("invalid_answer", "Answer accepted, tentative or declined.", 422)
    row, item = events.find(db, account, event_id, for_writing=False)
    mine, source = context(db, item)
    allowed, why = can_answer(source, item.calendar)
    if source is None or not mine:
        raise error("not_an_invitation", "This event is no invitation to you.", 409)
    if not allowed:
        raise error("answers_not_possible", "Answers cannot go out from here.", 409, why=why)
    if etag and etag != row.etag:
        raise error("invitation_changed", "The invitation changed meanwhile.", 409)
    parsed = ical.parse(row.ical)
    master = ical.master_of(parsed)
    series = master.get("RRULE") is not None or master.get("RDATE") is not None
    if series and scope == "this" and occurrence:
        original = events._occurrence_start(master, occurrence)
        if not events._occurs(parsed, original):
            raise error("occurrence_unknown", "This occurrence is not part of the series.", 422)
        target = events._override(parsed, original) or events._new_override(parsed, master, original)
        targets = [target]
        key = ical.recurrence_key(original)
    else:
        targets = ical.events_of(parsed)
        key = ""
    parts = [(component, _self_part(component, mine)) for component in targets]
    if any(part is None for _component, part in parts) or any(
            str(component.get("STATUS", "")).upper() == "CANCELLED" for component, _part in parts):
        raise error("invitation_changed", "The invitation changed meanwhile.", 409)
    wanted = PARTSTAT[reply]
    if all(str(part.params.get("PARTSTAT", "")).upper() == wanted for _component, part in parts):
        return row
    for component, part in parts:
        part.params["PARTSTAT"] = wanted
        part.params.pop("RSVP", None)
        component.pop("DTSTAMP", None)
        component.add("DTSTAMP", sync.clock().replace(microsecond=0))
    text = ical.serialize(parsed)
    events.write(db, row, text, parsed, account.id)
    if source.kind == SOURCE_GOOGLE:
        events.record_change(db, row.calendar_id, row.href, remote=False)
        sync.note_reply(db, row, reply, key)
    else:
        events.record_change(db, row.calendar_id, row.href, row=row)
    db.commit()
    return row


#: How far ahead the head of "Today" looks for open invitations.
AHEAD_DAYS = 180


def open_ones(db: Session, account: Account) -> list[dict[str, Any]]:
    """The open invitations from now on, for "1 invitation" in the head of "Today" (AC-1, AC-6); one in the past no
    longer counts (EC-9)."""
    from . import events, sync

    now = sync.clock()
    # Only calendars the member connected itself can hold its invitations: the rest is not rolled out at all.
    candidates = {item.calendar.id for item in calendars.accesses(db, account.id)
                  if item.right == calendars.OWNER and item.kind in WRITABLE_SOURCES}
    if not candidates:
        return []
    found = events.occurrence_list(db, account, now, now + timedelta(days=AHEAD_DAYS), only=candidates)
    return [{"id": row["id"], "occurrence": row["occurrence"], "calendar_id": row["calendar_id"],
             "title": row["title"], "start": row["start"], "end": row["end"], "all_day": row["all_day"],
             "by": row["invitation"]["by"]}
            for row in found if row.get("invitation") and row["invitation"]["state"] == "open"
            and (row["end"] >= now.date().isoformat() if row["all_day"] else row["end"] > _iso(now))]


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat().replace("+00:00", "Z")
