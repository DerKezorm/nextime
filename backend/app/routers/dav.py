"""The calendar account (TIME-20): a CalDAV server under ``/dav/`` for Apple Calendar, DAVx5, Thunderbird.

Addresses::

    /.well-known/caldav                    -> /dav/
    /dav/                                  the root, names the principal
    /dav/principals/<account>/             the principal, names the calendar home
    /dav/calendars/<account>/              the calendar home: own calendars and those shared with the account
    /dav/calendars/<account>/<calendar>/   a calendar
    /dav/calendars/<account>/<calendar>/<object>.ics

Only a device password opens it (HTTP Basic). Methods: OPTIONS, PROPFIND (depth 0 and 1), PROPPATCH, MKCALENDAR
(and MKCOL with a calendar), GET, HEAD, PUT, DELETE, REPORT calendar-multiget, calendar-query and sync-collection
(RFC 4791, RFC 6578). Events are kept as a device sends them (``services/events.py`` does the bookkeeping), but for
what belongs to one person: a device gets the alarms of the person it belongs to (TIME-7, ``services/alarms.py``) and
the attachments of nextime's own calendars (TIME-9); what it sends of both is taken out before the event is stored.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import Any
from urllib.parse import quote, unquote, urlsplit

from fastapi import APIRouter, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from lxml import etree
from sqlalchemy import func, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import SessionLocal
from ..deps import client_ip
from ..models import (
    CALENDAR_COLORS,
    RIGHT_BUSY,
    Account,
    Calendar,
    CalendarSetting,
    Event,
    EventChange,
    ReminderChoice,
    Setting,
)
from ..security import brake
from ..services import alarms, calendars, devices, events, ical, live, reminders

logger = logging.getLogger("nextime.dav")

router = APIRouter(include_in_schema=False)

D, C, CS, A = "DAV:", "urn:ietf:params:xml:ns:caldav", "http://calendarserver.org/ns/", "http://apple.com/ns/ical/"
NSMAP = {"d": D, "c": C, "cs": CS, "a": A}
METHODS = ["OPTIONS", "PROPFIND", "PROPPATCH", "REPORT", "GET", "HEAD", "PUT", "DELETE", "MKCALENDAR", "MKCOL"]
ALLOW = ", ".join(METHODS)
#: Wrong passwords that pass before the brake waits (EC-3); a phone asking again with an old password is no attacker.
FREE_TRIES = 10
#: The light shade of each calendar color, as Apple Calendar wants it (``#RRGGBBAA``).
COLOR_HEX = {
    "slate": "#4A6491", "red": "#C62F47", "orange": "#C2410C", "yellow": "#A16207", "green": "#2F7D4F",
    "teal": "#0E7F74", "blue": "#2563A8", "violet": "#6D4BC2", "pink": "#BE3D78", "brown": "#8A5A3B",
    "gray": "#61616F",
}
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False,
                          remove_blank_text=True)
CALENDAR_DATA_TYPE = "text/calendar; charset=utf-8"
#: The answer to a calendar program that came without HTTPS (TIME-20 EC-21).
HTTPS_ONLY = "The calendar account of nextime answers over HTTPS only. Ask the operator to set HTTPS up.\n"
#: Sources whose calendars the calendar account does not hand on.
PASSED_BY = ("icloud", "google", "caldav")
#: The settings row with the database's mark for sync tokens (``epoch``).
SYNC_EPOCH = "sync_epoch"


def q(namespace: str, name: str) -> str:
    return f"{{{namespace}}}{name}"


# --- Answers --------------------------------------------------------------------------------------------------------


def xml_response(root: etree._Element, status: int = 207, headers: dict[str, str] | None = None) -> Response:
    body = etree.tostring(root, xml_declaration=True, encoding="utf-8")
    return Response(content=body, status_code=status, media_type="application/xml; charset=utf-8",
                    headers=headers or {})


def precondition(status: int, namespace: str, name: str) -> Response:
    root = etree.Element(q(D, "error"), nsmap=NSMAP)
    etree.SubElement(root, q(namespace, name))
    return xml_response(root, status)


def unauthorized() -> Response:
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="nextime", charset="UTF-8"'})


def element(tag: str, text: str | None = None, children: list[etree._Element] | None = None,
            **attributes: str) -> etree._Element:
    node = etree.Element(tag, **attributes)
    if text is not None:
        node.text = text
    for child in children or []:
        node.append(child)
    return node


def href(path: str) -> etree._Element:
    return element(q(D, "href"), path)


def response_for(path: str, found: dict[str, Any], missing: list[str] | None = None) -> etree._Element:
    node = element(q(D, "response"), children=[href(path)])
    if found:
        stat = etree.SubElement(node, q(D, "propstat"))
        prop = etree.SubElement(stat, q(D, "prop"))
        for tag, value in found.items():
            item = etree.SubElement(prop, tag)
            if isinstance(value, list):
                for child in value:
                    item.append(child)
            elif isinstance(value, etree._Element):
                item.append(value)
            elif value is not None:
                item.text = str(value)
        etree.SubElement(stat, q(D, "status")).text = "HTTP/1.1 200 OK"
    if missing:
        stat = etree.SubElement(node, q(D, "propstat"))
        prop = etree.SubElement(stat, q(D, "prop"))
        for tag in missing:
            etree.SubElement(prop, tag)
        etree.SubElement(stat, q(D, "status")).text = "HTTP/1.1 404 Not Found"
    return node


def gone_response(path: str) -> etree._Element:
    node = element(q(D, "response"), children=[href(path)])
    etree.SubElement(node, q(D, "status")).text = "HTTP/1.1 404 Not Found"
    return node


def multistatus(responses: list[etree._Element], sync_token: str | None = None) -> Response:
    root = etree.Element(q(D, "multistatus"), nsmap=NSMAP)
    for node in responses:
        root.append(node)
    if sync_token is not None:
        etree.SubElement(root, q(D, "sync-token")).text = sync_token
    return xml_response(root)


# --- Paths ----------------------------------------------------------------------------------------------------------


def principal_path(name: str) -> str:
    return f"/dav/principals/{quote(name, safe='')}/"


def home_path(name: str) -> str:
    return f"/dav/calendars/{quote(name, safe='')}/"


def calendar_path(account: str, calendar: Calendar) -> str:
    return f"{home_path(account)}{quote(calendar.dav_name, safe='')}/"


def object_path(account: str, calendar: Calendar, name: str) -> str:
    return f"{calendar_path(account, calendar)}{quote(name, safe='')}"


def segments(path: str) -> list[str]:
    return [unquote(part) for part in path.split("/") if part]


# --- Who asks ---------------------------------------------------------------------------------------------------------


@dataclass
class Caller:
    account: Account
    device_id: int
    #: The address devices reach nextime under (for the addresses of attachments, TIME-9 AC-4).
    base: str = ""
    #: Set up from the nextime app on the same iPhone or iPad (TIME-29, TIMEIOS-4 AC-7): no alarms go there, the app
    #: reminds by itself, and an event coming back without them changes nobody's reminders.
    quiet: bool = False

    @property
    def name(self) -> str:
        """The account's fixed name in every CalDAV address (``accounts.dav_name``), never its username: a renamed
        account keeps every address its calendar programs know."""
        return devices.dav_name_of(self.account)

    @property
    def german(self) -> bool:
        return (self.account.language or self.account.browser_language or "").lower().startswith("de")


def _authenticate(request: Request, db: Session) -> Caller | Response:
    header = request.headers.get("authorization", "")
    key = "dav:" + client_ip(request)
    if not header:
        return unauthorized()
    wait = brake.wait_seconds(key, free=FREE_TRIES)
    if wait:
        return Response(status_code=429, headers={"Retry-After": str(wait)})
    found = devices.authenticate(db, header, request.headers.get("user-agent", ""))
    if found is None:
        brake.failed(key)
        logger.info("Calendar account: sign-in refused")
        return unauthorized()
    brake.succeeded(key)
    account, device = found
    db.expunge(account)
    return Caller(account, device.id, quiet=device.app_device_id is not None)


# --- Properties -----------------------------------------------------------------------------------------------------


def privileges(writable: bool, home: bool = False) -> list[etree._Element]:
    names = [(D, "read"), (D, "read-current-user-privilege-set"), (C, "read-free-busy")]
    if writable:
        names += [(D, "write"), (D, "write-properties"), (D, "write-content"), (D, "bind"), (D, "unbind")]
    elif home:
        names += [(D, "bind")]
    return [element(q(D, "privilege"), children=[element(q(namespace, name))]) for namespace, name in names]


def report_set(names: list[tuple[str, str]]) -> list[etree._Element]:
    return [element(q(D, "supported-report"), children=[element(q(D, "report"), children=[element(q(ns, name))])])
            for ns, name in names]


def last_change(db: Session, calendar_id: int) -> EventChange | None:
    return db.scalar(select(EventChange).where(EventChange.calendar_id == calendar_id)
                     .order_by(EventChange.id.desc()).limit(1))


def _check(number: int, moment: datetime) -> str:
    return hashlib.sha256(f"{number}:{moment.isoformat()}".encode()).hexdigest()[:10]


def epoch(db: Session) -> str:
    """This database's mark, part of every sync token and collection tag: a restored backup gets a new one
    (``services/backups.py``), so no device keeps a token or tag from before and skips what changed (EC-20)."""
    row = db.get(Setting, SYNC_EPOCH)
    if row is not None and row.value:
        return str(row.value)
    db.execute(insert(Setting).values(key=SYNC_EPOCH, value=secrets.token_hex(4))
               .on_conflict_do_nothing(index_elements=["key"]))
    db.commit()
    row = db.get(Setting, SYNC_EPOCH)
    return str(row.value) if row is not None else "0"


def _state(db: Session, calendar: Calendar) -> tuple[int, str]:
    change = last_change(db, calendar.id)
    if change is None:
        return 0, _check(0, calendar.created_at)
    return change.id, _check(change.id, change.created_at)


def sync_token(db: Session, calendar: Calendar) -> str:
    """The calendar's state as a token: the database's mark, the number of its last change and a check made from when
    that change happened. An old token after a restored backup is refused instead of skipping changes (EC-20)."""
    number, check = _state(db, calendar)
    return f"urn:x-nextime:sync:{epoch(db)}:{calendar.id}:{number}:{check}"


def read_token(db: Session, calendar: Calendar, token: str) -> int | None:
    """The change number a token stands for, or ``None`` when it is not one of this calendar's in this database."""
    parts = token.strip().split(":")
    if len(parts) != 7 or parts[:3] != ["urn", "x-nextime", "sync"]:
        return None
    if parts[3] != epoch(db) or parts[4] != str(calendar.id):
        return None
    try:
        number = int(parts[5])
    except ValueError:
        return None
    if number == 0:
        return 0 if parts[6] == _check(0, calendar.created_at) else None
    change = db.get(EventChange, number)
    if change is None or change.calendar_id != calendar.id or parts[6] != _check(change.id, change.created_at):
        return None
    return number


def ctag(db: Session, calendar: Calendar) -> str:
    """The collection tag Apple Calendar compares: changes, name and color, rights, and the database's mark."""
    number, check = _state(db, calendar)
    return f"{epoch(db)}-{number}-{check}-{calendar.props_rev}"


def calendar_props(db: Session, caller: Caller, access: calendars.Access, order: int) -> dict[str, Any]:
    calendar = access.calendar
    owner = db.get(Account, calendar.owner_id)
    return {
        q(D, "resourcetype"): [element(q(D, "collection")), element(q(C, "calendar"))],
        q(D, "displayname"): calendar.name,
        q(A, "calendar-color"): hex_of(calendar.color) + "FF",
        q(A, "calendar-order"): str(order),
        q(CS, "getctag"): ctag(db, calendar),
        q(D, "sync-token"): sync_token(db, calendar),
        q(C, "supported-calendar-component-set"): [element(q(C, "comp"), name="VEVENT")],
        q(C, "supported-calendar-data"): [element(q(C, "calendar-data"), **{"content-type": "text/calendar",
                                                                            "version": "2.0"})],
        q(C, "max-resource-size"): str(ical.MAX_OBJECT_BYTES),
        q(D, "current-user-privilege-set"): privileges(access.writable),
        q(D, "owner"): href(principal_path(devices.dav_name_of(owner) if owner else caller.name)),
        q(D, "current-user-principal"): href(principal_path(caller.name)),
        q(D, "supported-report-set"): report_set([(C, "calendar-multiget"), (C, "calendar-query"),
                                                  (D, "sync-collection")]),
    }


def principal_props(caller: Caller) -> dict[str, Any]:
    addresses = [href(f"mailto:{caller.account.email}")] if caller.account.email else []
    return {
        q(D, "resourcetype"): [element(q(D, "collection")), element(q(D, "principal"))],
        q(D, "displayname"): caller.account.shown_name,
        q(D, "current-user-principal"): href(principal_path(caller.name)),
        q(D, "principal-URL"): href(principal_path(caller.name)),
        q(C, "calendar-home-set"): href(home_path(caller.name)),
        q(C, "calendar-user-address-set"): addresses,
        q(D, "supported-report-set"): [],
    }


def home_props(caller: Caller) -> dict[str, Any]:
    return {
        q(D, "resourcetype"): [element(q(D, "collection"))],
        q(D, "displayname"): caller.account.shown_name,
        q(D, "current-user-principal"): href(principal_path(caller.name)),
        q(D, "owner"): href(principal_path(caller.name)),
        q(C, "calendar-home-set"): href(home_path(caller.name)),
        q(D, "current-user-privilege-set"): privileges(False, home=True),
        q(D, "supported-report-set"): [],
    }


def object_props(row: Event, text: str | None = None, etag: str | None = None) -> dict[str, Any]:
    return {
        q(D, "getetag"): etag or row.etag,
        q(D, "getcontenttype"): CALENDAR_DATA_TYPE + "; component=VEVENT",
        q(D, "resourcetype"): None,
        q(D, "getcontentlength"): str(len((text or row.ical).encode("utf-8"))) if text is not None else str(row.size),
        q(D, "getlastmodified"): format_datetime(row.updated_at.astimezone(UTC), usegmt=True),
    }


def select_props(available: dict[str, Any], wanted: list[str] | None, names_only: bool = False) -> tuple[dict, list]:
    """What PROPFIND asked for: everything (allprop), the names (propname), or a list, with the missing ones."""
    if names_only:
        return {tag: None for tag in available}, []
    if wanted is None:
        return {tag: value for tag, value in available.items() if tag != q(C, "calendar-data")}, []
    found = {tag: available[tag] for tag in wanted if tag in available}
    return found, [tag for tag in wanted if tag not in available]


def wanted_props(body: bytes) -> tuple[list[str] | None, bool]:
    """The properties a PROPFIND or REPORT names; ``None`` for allprop; True for propname."""
    if not body.strip():
        return None, False
    root = etree.fromstring(body, _PARSER)
    if root.find(q(D, "propname")) is not None:
        return None, True
    prop = root.find(q(D, "prop"))
    if prop is None:
        return None, False
    return [child.tag for child in prop if isinstance(child.tag, str)], False


# --- "Busy only" (EC-5) -------------------------------------------------------------------------------------------


_BUSY_KEEP = {"UID", "DTSTART", "DTEND", "DURATION", "RRULE", "RDATE", "EXDATE", "RECURRENCE-ID", "DTSTAMP",
              "SEQUENCE", "TRANSP", "LAST-MODIFIED", "CREATED"}


def busy_view(text: str, german: bool) -> str | None:
    """The event as a block "Busy": times and rule only, no title, place, notes or attachments; free ones not at
    all (EC-5)."""
    import icalendar

    parsed = ical.parse(text)
    master = ical.master_of(parsed)
    if str(master.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
        return None
    view = ical.new_calendar()
    for component in parsed.subcomponents:
        if component.name == "VTIMEZONE":
            view.add_component(component)
    free_ones = []
    for component in ical.events_of(parsed):
        if component is not master and str(component.get("TRANSP", "OPAQUE")).upper() == "TRANSPARENT":
            free_ones.append(component.decoded("RECURRENCE-ID"))
            continue
        block = icalendar.Event()
        for name, value in component.property_items(recursive=False):
            if name in _BUSY_KEEP:
                block.add(name, value, encode=False)
        block.add("SUMMARY", "Belegt" if german else "Busy")
        block.add("CLASS", "CONFIDENTIAL")
        view.add_component(block)
    for original in free_ones:
        ical.master_of(view).add("EXDATE", original)
    return ical.serialize(view)


# --- The work, in a thread (database and parsing) ------------------------------------------------------------------


def _calendar_listing(db: Session, caller: Caller) -> list[tuple[calendars.Access, int]]:
    """The calendars a device gets, with their place: hidden ones not, the hiding holds on every device of the
    account (TIME-4 AC-3); shown again, they come back."""
    calendars.ensure_seeded(db, caller.account)
    rows = calendars.ordered(db, caller.account.id)
    db.commit()
    # Calendars from iCloud, Google and CalDAV servers are on the owner's phone already from there, not twice (TIME-15
    # Out of Scope); shared with somebody else they come along to that one's devices (TIME-19 AC-10). Subscriptions
    # and holidays come along, only to be read (AC-15).
    return [(item, setting.position) for item, setting in rows
            if not setting.hidden and not (item.right == calendars.OWNER and item.kind in PASSED_BY)]


def _find_calendar(db: Session, caller: Caller, dav_name: str) -> tuple[calendars.Access, int] | None:
    for item, order in _calendar_listing(db, caller):
        if item.calendar.dav_name == dav_name:
            return item, order
    return None


def _live_object(db: Session, calendar: Calendar, name: str) -> Event | None:
    return db.scalar(select(Event).where(Event.calendar_id == calendar.id, Event.href == name,
                                         Event.deleted_at.is_(None)))


class Personal:
    """What the events of one calendar carry for the account a device belongs to: its own reminders as alarms (its
    choice for the event, else its default for the calendar, TIME-7 AC-7, AC-9) and, but for "busy only", nextime's
    attachments (TIME-9 AC-4). Read once per calendar and request."""

    def __init__(self, db: Session, caller: Caller, access: calendars.Access) -> None:
        account_id = caller.account.id
        calendar_id = access.calendar.id
        self.busy = access.right == RIGHT_BUSY
        self.quiet = getattr(caller, "quiet", False)
        self.choices = {row.event_id: row for row in db.scalars(
            select(ReminderChoice).join(Event, Event.id == ReminderChoice.event_id)
            .where(ReminderChoice.account_id == account_id, Event.calendar_id == calendar_id))}
        stored = db.scalar(select(CalendarSetting.reminder_default).where(
            CalendarSetting.account_id == account_id, CalendarSetting.calendar_id == calendar_id)) or ""
        self.choice = reminders.default_choice(access, stored)
        self.files: dict[int, str] = {} if self.busy else _attachment_lines(db, access, caller.base)

    def lines(self, event_id: int, all_day: bool, etag: str = "", text: str = "") -> tuple[str, str, str]:
        """The alarms as a device sent them, or as nextime makes them, and the attachments' lines."""
        chosen = self.choices.get(event_id)
        files = self.files.get(event_id, "")
        if self.quiet:
            return "", "", files
        if chosen is None or chosen.minutes is None:
            # The default; single occurrences a device gave alarms of their own keep them (review of TIME-7, finding 5).
            made = alarms.generated(reminders.default_leads(self.choice, all_day, etag, text), all_day, default=True)
            return (chosen.alarms or "") if chosen is not None else "", made, files
        raw = chosen.alarms or ""
        if alarms.covers_series(raw):
            return raw, "", files
        return raw, alarms.generated([int(value) for value in chosen.minutes], all_day, default=False), files

    @staticmethod
    def etag(etag: str, lines: tuple[str, str, str]) -> str:
        """The event's own ETag with the mark of what this account's device gets besides (``"<event>-<mark>"``), so
        that an ``If-Match`` of the device still names the version of the event it read (review of TIME-7/9,
        finding 1)."""
        joined = "\x00".join(lines) if any(lines) else ""
        base = etag.strip('"')
        return f'"{base}-{alarms.signature(joined)}"' if joined else etag


def same_version(if_match: str, etag: str) -> bool:
    """``If-Match`` names the stored version of the event: its own ETag, or one a device got with its alarms and
    attachments in (``Personal.etag``)."""
    if if_match == "*" or if_match == etag:
        return True
    return if_match.strip().strip('"').split("-", 1)[0] == etag.strip('"')


def _attachment_lines(db: Session, access: calendars.Access, base: str) -> dict[int, str]:
    from ..services import attachments

    return attachments.dav_lines(db, access, base)


def _shown(row: Event, access: calendars.Access, caller: Caller, personal: Personal) -> tuple[str, str] | None:
    """The text and ETag a device gets for an event; ``None`` when the account does not see it ("busy only", free).
    Alarms of others never come along, the account's own do."""
    if access.right == RIGHT_BUSY:
        base = busy_view(row.ical, caller.german)
        if base is None:
            return None
    else:
        base = alarms.strip(row.ical)
    extra = personal.lines(row.id, row.all_day, row.etag, row.ical)
    text = alarms.place(base, *extra)
    if access.right == RIGHT_BUSY:
        return text, ical.etag_of(text)
    return text, personal.etag(row.etag, extra)


def _propfind(db: Session, caller: Caller, parts: list[str], depth: str, body: bytes) -> Response:
    wanted, names_only = wanted_props(body)
    if not parts:
        root = {q(D, "resourcetype"): [element(q(D, "collection"))],
                q(D, "current-user-principal"): href(principal_path(caller.name)),
                q(D, "principal-URL"): href(principal_path(caller.name)),
                q(C, "calendar-home-set"): href(home_path(caller.name))}
        return multistatus([response_for("/dav/", *select_props(root, wanted, names_only))])
    if parts[0] == "principals":
        if len(parts) == 1:
            root = {q(D, "resourcetype"): [element(q(D, "collection"))],
                    q(D, "current-user-principal"): href(principal_path(caller.name))}
            return multistatus([response_for("/dav/principals/", *select_props(root, wanted, names_only))])
        if len(parts) == 2 and parts[1] == caller.name:
            return multistatus([response_for(principal_path(caller.name),
                                             *select_props(principal_props(caller), wanted, names_only))])
        return Response(status_code=404)
    if parts[0] != "calendars" or len(parts) < 2 or parts[1] != caller.name:
        return Response(status_code=404)
    if len(parts) == 2:
        out = [response_for(home_path(caller.name), *select_props(home_props(caller), wanted, names_only))]
        if depth != "0":
            for item, order in _calendar_listing(db, caller):
                out.append(response_for(calendar_path(caller.name, item.calendar),
                                        *select_props(calendar_props(db, caller, item, order), wanted, names_only)))
        return multistatus(out)
    found = _find_calendar(db, caller, parts[2])
    if found is None:
        return Response(status_code=404)
    access, order = found
    calendar = access.calendar
    if len(parts) == 3:
        out = [response_for(calendar_path(caller.name, calendar),
                            *select_props(calendar_props(db, caller, access, order), wanted, names_only))]
        if depth != "0":
            out += _object_responses(db, caller, access, wanted, names_only)
        return multistatus(out)
    if len(parts) == 4:
        row = _live_object(db, calendar, parts[3])
        shown = _shown(row, access, caller, Personal(db, caller, access)) if row is not None else None
        if row is None or shown is None:
            return Response(status_code=404)
        available = object_props(row, shown[0], shown[1])
        available[q(C, "calendar-data")] = shown[0]
        return multistatus([response_for(object_path(caller.name, calendar, row.href),
                                         *select_props(available, wanted, names_only))])
    return Response(status_code=404)


def _object_responses(db: Session, caller: Caller, access: calendars.Access, wanted: list[str] | None,
                      names_only: bool) -> list[etree._Element]:
    calendar = access.calendar
    needs_text = access.right == RIGHT_BUSY or (wanted is not None and q(C, "calendar-data") in wanted)
    out = []
    personal = Personal(db, caller, access)
    if not needs_text:
        # 20,000 events (EC-12): only the columns a listing needs.
        rows = db.execute(select(Event.id, Event.all_day, Event.href, Event.etag, Event.size, Event.updated_at).where(
            Event.calendar_id == calendar.id, Event.deleted_at.is_(None)).order_by(Event.id)).all()
        for event_id, all_day, name, etag, size, updated in rows:
            extra = personal.lines(event_id, all_day)
            available = {
                q(D, "getetag"): personal.etag(etag, extra),
                q(D, "getcontenttype"): CALENDAR_DATA_TYPE + "; component=VEVENT",
                q(D, "resourcetype"): None,
                q(D, "getcontentlength"): str(size + len("".join(extra).encode("utf-8"))),
                q(D, "getlastmodified"): format_datetime(updated.astimezone(UTC), usegmt=True),
            }
            out.append(response_for(object_path(caller.name, calendar, name),
                                    *select_props(available, wanted, names_only)))
        return out
    for row in db.scalars(select(Event).where(Event.calendar_id == calendar.id, Event.deleted_at.is_(None))
                          .order_by(Event.id)):
        shown = _shown(row, access, caller, personal)
        if shown is None:
            continue
        available = object_props(row, shown[0], shown[1])
        available[q(C, "calendar-data")] = shown[0]
        out.append(response_for(object_path(caller.name, calendar, row.href),
                                *select_props(available, wanted, names_only)))
    return out


def _report(db: Session, caller: Caller, parts: list[str], body: bytes) -> Response:
    try:
        root = etree.fromstring(body, _PARSER)
    except etree.XMLSyntaxError:
        return Response(status_code=400)
    kind = etree.QName(root).localname
    if len(parts) < 3 or parts[0] != "calendars" or parts[1] != caller.name:
        return precondition(403, D, "supported-report")
    found = _find_calendar(db, caller, parts[2])
    if found is None:
        return Response(status_code=404)
    access, _order = found
    prop = root.find(q(D, "prop"))
    wanted = [child.tag for child in prop if isinstance(child.tag, str)] if prop is not None else [q(D, "getetag")]
    if kind == "calendar-multiget":
        return _multiget(db, caller, access, root, wanted)
    if kind == "calendar-query":
        return _query(db, caller, access, root, wanted)
    if kind == "sync-collection":
        return _sync(db, caller, access, root, wanted)
    return precondition(403, D, "supported-report")


def _entry(caller: Caller, access: calendars.Access, row: Event, wanted: list[str],
           personal: Personal) -> etree._Element | None:
    shown = _shown(row, access, caller, personal)
    if shown is None:
        return None
    available = object_props(row, shown[0], shown[1])
    available[q(C, "calendar-data")] = shown[0]
    found, missing = select_props(available, wanted)
    return response_for(object_path(caller.name, access.calendar, row.href), found, missing)


def _multiget(db: Session, caller: Caller, access: calendars.Access, root: etree._Element,
              wanted: list[str]) -> Response:
    out = []
    personal = Personal(db, caller, access)
    for node in root.findall(q(D, "href")):
        target = (node.text or "").strip()
        path = unquote(urlsplit(target).path)
        name = path.rstrip("/").rsplit("/", 1)[-1]
        row = _live_object(db, access.calendar, name)
        entry = _entry(caller, access, row, wanted, personal) if row is not None else None
        out.append(entry if entry is not None else gone_response(target))
    return multistatus(out)


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def _query(db: Session, caller: Caller, access: calendars.Access, root: etree._Element,
           wanted: list[str]) -> Response:
    """calendar-query: events (VEVENT) in a time range, series rolled out to decide; a text match on UID or
    SUMMARY. Other components (VTODO) find nothing."""
    filter_node = root.find(q(C, "filter"))
    outer = filter_node.find(q(C, "comp-filter")) if filter_node is not None else None
    inner = outer.find(q(C, "comp-filter")) if outer is not None else None
    if inner is not None and inner.get("name", "").upper() != "VEVENT":
        return multistatus([])
    range_node = inner.find(q(C, "time-range")) if inner is not None else None
    begin = _time(range_node.get("start")) if range_node is not None else None
    finish = _time(range_node.get("end")) if range_node is not None else None
    matches: list[tuple[str, str, bool]] = []
    for prop_filter in (inner.findall(q(C, "prop-filter")) if inner is not None else []):
        match = prop_filter.find(q(C, "text-match"))
        if match is not None and match.text:
            matches.append((prop_filter.get("name", "").upper(), match.text,
                            match.get("negate-condition", "no") == "yes"))
    statement = select(Event).where(Event.calendar_id == access.calendar.id, Event.deleted_at.is_(None))
    if finish is not None:
        statement = statement.where(Event.starts_at < finish)
    if begin is not None:
        statement = statement.where((Event.ends_at.is_(None)) | (Event.ends_at > begin) | (Event.ends_at == begin))
    out = []
    personal = Personal(db, caller, access)
    for row in db.scalars(statement.order_by(Event.id)):
        if matches and not _text_matches(row, matches):
            continue
        if (begin or finish) and row.recurring:
            low = begin or datetime(1900, 1, 1, tzinfo=UTC)
            high = finish or datetime(2200, 1, 1, tzinfo=UTC)
            if not ical.occurrences(ical.parse(row.ical), low, high):
                continue
        entry = _entry(caller, access, row, wanted, personal)
        if entry is not None:
            out.append(entry)
    return multistatus(out)


def _text_matches(row: Event, matches: list[tuple[str, str, bool]]) -> bool:
    for name, text, negate in matches:
        value = row.uid if name == "UID" else row.title if name == "SUMMARY" else None
        if value is None:
            continue
        hit = text.casefold() in value.casefold()
        if hit == negate:
            return False
    return True


def _sync(db: Session, caller: Caller, access: calendars.Access, root: etree._Element,
          wanted: list[str]) -> Response:
    """sync-collection (RFC 6578): what changed since the token; without one, everything. A token nextime does not
    know (a backup brought back, another calendar) is refused, and the device starts over (EC-13, EC-20)."""
    calendar = access.calendar
    token_node = root.find(q(D, "sync-token"))
    token = (token_node.text or "").strip() if token_node is not None else ""
    since = 0
    if token:
        known = read_token(db, calendar, token)
        if known is None:
            return precondition(403, D, "valid-sync-token")
        since = known
    out: list[etree._Element] = []
    personal = Personal(db, caller, access)
    if since == 0:
        for row in db.scalars(select(Event).where(Event.calendar_id == calendar.id, Event.deleted_at.is_(None))
                              .order_by(Event.id)):
            entry = _entry(caller, access, row, wanted, personal)
            if entry is not None:
                out.append(entry)
        return multistatus(out, sync_token(db, calendar))
    names = db.execute(select(EventChange.href, func.max(EventChange.id)).where(
        EventChange.calendar_id == calendar.id, EventChange.id > since).group_by(EventChange.href)).all()
    for name, _latest in sorted(names, key=lambda pair: pair[1]):
        row = _live_object(db, calendar, name)
        entry = _entry(caller, access, row, wanted, personal) if row is not None else None
        out.append(entry if entry is not None else gone_response(object_path(caller.name, calendar, name)))
    return multistatus(out, sync_token(db, calendar))


def _get(db: Session, caller: Caller, parts: list[str], head: bool) -> Response:
    if len(parts) == 2 and parts[0] == "attachments":
        return _attachment(db, caller, parts[1], head)
    if len(parts) != 4 or parts[0] != "calendars" or parts[1] != caller.name:
        return Response(status_code=404 if len(parts) != 3 else 405)
    found = _find_calendar(db, caller, parts[2])
    if found is None:
        return Response(status_code=404)
    access, _order = found
    row = _live_object(db, access.calendar, parts[3])
    shown = _shown(row, access, caller, Personal(db, caller, access)) if row is not None else None
    if shown is None:
        return Response(status_code=404)
    text, etag = shown
    body = b"" if head else text.encode("utf-8")
    return Response(content=body, media_type=CALENDAR_DATA_TYPE,
                    headers={"ETag": etag, "Last-Modified": format_datetime(row.updated_at.astimezone(UTC),
                                                                            usegmt=True)})


def _attachment(db: Session, caller: Caller, attachment_id: str, head: bool) -> Response:
    """A file of nextime's attachments, for a device whose account sees its event (TIME-9 AC-4)."""
    from ..services import attachments
    from .attachments import headers_for

    row = attachments.dav_file(db, caller.account, attachment_id)
    path = attachments.path_of(row.id) if row is not None else None
    if row is None or path is None or not path.is_file():
        return Response(status_code=404)
    media_type = row.mime if row.inline else "application/octet-stream"
    if head:
        return Response(media_type=media_type,
                        headers=headers_for(row.name, shown=False) | {"Content-Length": str(path.stat().st_size)})
    # Sent from the file, piece by piece: never the whole file in memory (review of TIME-9, finding 2).
    return FileResponse(path, media_type=media_type, headers=headers_for(row.name, shown=False))


def _put(db: Session, caller: Caller, parts: list[str], body: bytes, headers: dict[str, str]) -> Response:
    """A device stores an event. Kept as sent; refused (nothing changes) when it is not one event with a start,
    is too large, or the calendar may only be read (EC-1, EC-11). An older If-Match than the stored version still
    wins as the later change, and the one it overwrote is told so (TIME-5 EC-2, EC-14).

    Its alarms are the account's own reminders (TIME-7 AC-9): taken out of the event and kept for the account; a
    calendar from iCloud, Google or a CalDAV server keeps the alarms of its source instead, so that nothing of the
    phone goes there unasked. nextime's attachments (TIME-9) come off as well. A stored object that is not what the
    device sent answers without an ETag, so the device reads it again."""
    if len(parts) != 4 or parts[0] != "calendars" or parts[1] != caller.name:
        return Response(status_code=405)
    found = _find_calendar(db, caller, parts[2])
    if found is None:
        return Response(status_code=409)
    access, _order = found
    if not access.writable:
        return precondition(403, D, "need-privileges")
    if len(body) > ical.MAX_OBJECT_BYTES:
        return precondition(403, C, "max-resource-size")
    try:
        text = body.decode("utf-8")
        parsed = ical.parse(text)
        uid = ical.check(parsed)
    except UnicodeDecodeError:
        return precondition(403, C, "supported-calendar-data")
    except ical.IcalError as exc:
        return precondition(403, C, exc.code)
    name = parts[3]
    row = _live_object(db, access.calendar, name)
    if_match = headers.get("if-match", "").strip()
    if_none_match = headers.get("if-none-match", "").strip()
    if row is None and if_match:
        return Response(status_code=412)
    if row is not None and if_none_match == "*":
        return Response(status_code=412)
    clash = db.scalar(select(Event.id).where(Event.calendar_id == access.calendar.id, Event.uid == uid,
                                             Event.deleted_at.is_(None), Event.href != name))
    if clash is not None:
        return precondition(403, C, "no-uid-conflict")
    previous = ""
    if row is not None:
        if row.uid != uid:
            return precondition(403, C, "no-uid-conflict")
        previous = row.ical
    sent = text
    taken = alarms.take(parsed, text)
    text = _drop_attachment_lines(alarms.strip(text))
    if access.kind:
        text = alarms.restore(text, previous)
    if row is None:
        row = Event(calendar_id=access.calendar.id, uid=uid, href=name, ical="", etag="",
                    created_by=caller.account.id, starts_at=datetime.now(UTC))
        db.add(row)
        status = 201
    else:
        if if_match and not same_version(if_match, row.etag):
            events._keep_conflict(db, row, row.ical, caller.account)
        status = 204
    events.write(db, row, text, parsed, caller.account.id)
    events.record_change(db, row.calendar_id, row.href, row=row, by=caller.account.id)
    db.flush()
    if not caller.quiet:
        _keep_alarms(db, caller.account, access, row, taken, existed=status == 204)
    db.commit()
    shown = _shown(row, access, caller, Personal(db, caller, access))
    # The device's own text comes back as it was sent: its ETag. Otherwise none, and the device reads it again.
    return Response(status_code=status, headers={"ETag": shown[1]} if shown and shown[0] == sent else {})


def _keep_alarms(db: Session, account: Account, access: calendars.Access, row: Event, taken: alarms.FromDevice,
                 existed: bool) -> None:
    """The alarms a device sent are the account's reminders for the event (TIME-7 AC-9). Sent back as the default
    gave them: it stays with the default. None at all on a changed event that reminded: the person removed them."""
    if taken.any:
        if not taken.only_defaults:
            reminders.choose(db, account, row, taken.minutes, taken.raw, taken.occurrences)
        return
    if existed and reminders.leads_for(db, account.id, [row], {row.calendar_id: access})[row.id].minutes:
        reminders.choose(db, account, row, [])


def _drop_attachment_lines(text: str) -> str:
    from ..services import attachments

    return attachments.drop_dav_lines(text)


def _delete(db: Session, caller: Caller, parts: list[str]) -> Response:
    if len(parts) < 3 or parts[0] != "calendars" or parts[1] != caller.name:
        return Response(status_code=405)
    found = _find_calendar(db, caller, parts[2])
    if found is None:
        return Response(status_code=404)
    access, _order = found
    if len(parts) == 3:
        # A calendar: one's own goes into the trash (AC-14); a shared one stays, the device gets it back.
        if access.right != calendars.OWNER:
            return precondition(403, D, "need-privileges")
        calendars.delete(db, caller.account, access.calendar.id)
        return Response(status_code=204)
    if not access.writable:
        return precondition(403, D, "need-privileges")
    row = _live_object(db, access.calendar, parts[3])
    if row is None:
        return Response(status_code=404)
    events.trash(db, row, caller.account.id)
    db.commit()
    return Response(status_code=204)


def hex_of(color: str) -> str:
    """The color as Apple's calendar takes it: one of the eleven by its value, an own color as it is (TIME-4)."""
    if color in COLOR_HEX:
        return COLOR_HEX[color]
    return color.upper() if calendars.CUSTOM_COLOR.fullmatch(color or "") else COLOR_HEX["slate"]


def _device_color(value: str) -> str:
    """A color a device sets (``#RRGGBB`` or ``#RRGGBBAA``): one of the eleven when it is exactly that one, else the
    own color as it is (decided while testing, 2026-11-23: no longer the nearest of the eleven)."""
    text = value.strip().lstrip("#")[:6]
    if not calendars.CUSTOM_COLOR.fullmatch("#" + text):
        return "slate"
    for name, shade in COLOR_HEX.items():
        if shade.lstrip("#").lower() == text.lower():
            return name
    return "#" + text.lower()


def _nearest_color(value: str) -> str:
    """A color from a device (``#RRGGBB`` or ``#RRGGBBAA``) as the nearest of the eleven (TIME-4)."""
    text = value.strip().lstrip("#")
    try:
        red, green, blue = int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)
    except (ValueError, IndexError):
        return "slate"

    def distance(key: str) -> int:
        shade = COLOR_HEX[key].lstrip("#")
        return sum((int(shade[i:i + 2], 16) - part) ** 2 for i, part in zip((0, 2, 4), (red, green, blue),
                                                                            strict=True))

    return min(CALENDAR_COLORS, key=distance)


def _device_name(value: str) -> str:
    name = " ".join((value or "").split())[: calendars.NAME_MAX].strip()
    return name or "Calendar"


def _mkcalendar(db: Session, caller: Caller, parts: list[str], body: bytes) -> Response:
    """A device makes a calendar: a new own calendar in nextime (AC-14)."""
    if len(parts) != 3 or parts[0] != "calendars" or parts[1] != caller.name:
        return Response(status_code=403)
    dav_name = parts[2]
    if not dav_name or len(dav_name) > 128 or db.scalar(select(Calendar.id).where(Calendar.dav_name == dav_name)):
        return precondition(405, D, "resource-must-be-null")
    name, color = "", "slate"
    if body.strip():
        try:
            root = etree.fromstring(body, _PARSER)
        except etree.XMLSyntaxError:
            return Response(status_code=400)
        for prop in root.iter(q(D, "prop")):
            for child in prop:
                if child.tag == q(D, "displayname"):
                    name = child.text or ""
                elif child.tag == q(A, "calendar-color"):
                    color = _device_color(child.text or "")
            resource = prop.find(q(D, "resourcetype"))
            if resource is not None and resource.find(q(C, "calendar")) is None:
                return precondition(403, D, "valid-resourcetype")
            components = prop.find(q(C, "supported-calendar-component-set"))
            if components is not None and "VEVENT" not in {
                    (comp.get("name") or "").upper() for comp in components.findall(q(C, "comp"))}:
                # A list of tasks (Apple Reminders) is no calendar of events: nextime keeps events only.
                return precondition(403, C, "supported-calendar-component")
    calendars.create(db, caller.account, name=_device_name(name), color=color, background=False, reminder=0,
                     make_default=False, client_ref=None, dav_name=dav_name)
    return Response(status_code=201)


def _proppatch(db: Session, caller: Caller, parts: list[str], body: bytes) -> Response:
    """Name, color and place of a calendar from a device: name and color for one's own calendar (the color the
    nearest of the eleven), the place in its group for any (TIME-4 AC-5, the account's own order). Other properties
    are answered without being kept, so that Apple Calendar shows no error. All or nothing (RFC 4918): one refused
    property leaves the others unchanged (424)."""
    if len(parts) != 3 or parts[0] != "calendars" or parts[1] != caller.name:
        return Response(status_code=403)
    found = _find_calendar(db, caller, parts[2])
    if found is None:
        return Response(status_code=404)
    access, _order = found
    try:
        root = etree.fromstring(body, _PARSER)
    except etree.XMLSyntaxError:
        return Response(status_code=400)
    calendar = access.calendar
    wanted = [child for prop in root.iter(q(D, "prop")) for child in prop if isinstance(child.tag, str)]
    own_only = (q(D, "displayname"), q(A, "calendar-color"))
    refused = [child.tag for child in wanted if child.tag in own_only and access.right != calendars.OWNER]
    node = element(q(D, "response"), children=[href(calendar_path(caller.name, calendar))])

    def stat(tags: list[str], status: str) -> None:
        if not tags:
            return
        holder = etree.SubElement(node, q(D, "propstat"))
        prop = etree.SubElement(holder, q(D, "prop"))
        for tag in tags:
            etree.SubElement(prop, tag)
        etree.SubElement(holder, q(D, "status")).text = status

    if refused:
        stat(refused, "HTTP/1.1 403 Forbidden")
        stat([child.tag for child in wanted if child.tag not in refused], "HTTP/1.1 424 Failed Dependency")
        return multistatus([node])
    changed = False
    for child in wanted:
        if child.tag == q(D, "displayname") and (child.text or "").strip():
            calendar.name = _device_name(child.text or "")
            changed = True
        elif child.tag == q(A, "calendar-color") and child.text:
            calendar.color = _device_color(child.text)
            changed = True
        elif child.tag == q(A, "calendar-order") and (child.text or "").strip().lstrip("-").isdigit():
            calendars.setting(db, caller.account.id, calendar.id).position = int((child.text or "0").strip())
            live.touch_accounts(db, {caller.account.id})
    if changed:
        calendar.props_rev += 1
        calendar.updated_at = datetime.now(UTC)
        live.touch_calendar(db, calendar.id)
    db.commit()
    stat([child.tag for child in wanted], "HTTP/1.1 200 OK")
    return multistatus([node])


def _handle(method: str, path: str, request_headers: dict[str, str], body: bytes, caller: Caller) -> Response:
    parts = segments(path)[1:]  # without "dav"
    with SessionLocal() as db:
        depth = request_headers.get("depth", "0").strip().lower()
        try:
            if method == "PROPFIND":
                return _propfind(db, caller, parts, "1" if depth in ("1", "infinity") else "0", body)
            if method == "REPORT":
                return _report(db, caller, parts, body)
            if method in ("GET", "HEAD"):
                return _get(db, caller, parts, method == "HEAD")
            if method == "PUT":
                return _put(db, caller, parts, body, request_headers)
            if method == "DELETE":
                return _delete(db, caller, parts)
            if method in ("MKCALENDAR", "MKCOL"):
                return _mkcalendar(db, caller, parts, body)
            if method == "PROPPATCH":
                return _proppatch(db, caller, parts, body)
        except etree.XMLSyntaxError:
            return Response(status_code=400)
    return Response(status_code=405, headers={"Allow": ALLOW})


async def _body(request: Request) -> bytes | None:
    """The body up to the largest object nextime takes and some room for XML; ``None`` when larger."""
    limit = ical.MAX_OBJECT_BYTES + 64 * 1024
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > limit):
        return None
    received = bytearray()
    async for chunk in request.stream():
        received += chunk
        if len(received) > limit:
            return None
    return bytes(received)


def _authenticated(request: Request) -> Caller | Response:
    from ..services import settings_service

    with SessionLocal() as db:
        found = _authenticate(request, db)
        if isinstance(found, Caller):
            found.base = settings_service.public_url(db) or str(request.base_url).rstrip("/")
        return found


@router.api_route("/.well-known/caldav", methods=METHODS)
async def well_known(request: Request) -> Response:
    if request.url.scheme != "https" and not get_settings().dav_allow_http:
        return Response(content=HTTPS_ONLY, status_code=403, media_type="text/plain; charset=utf-8")
    return Response(status_code=301, headers={"Location": "/dav/"})


@router.api_route("/dav", methods=METHODS)
@router.api_route("/dav/{path:path}", methods=METHODS)
async def dav(request: Request, path: str = "") -> Response:
    if request.url.scheme != "https" and not get_settings().dav_allow_http:
        # A device password must never cross the network in the clear: no sign-in is even looked at.
        return Response(content=HTTPS_ONLY, status_code=403, media_type="text/plain; charset=utf-8")
    if request.method == "OPTIONS":
        return Response(headers={"DAV": "1, 3, calendar-access", "Allow": ALLOW})
    caller = await run_in_threadpool(_authenticated, request)
    if isinstance(caller, Response):
        return caller
    body = await _body(request)
    if body is None:
        return precondition(413, C, "max-resource-size") if request.method == "PUT" else Response(status_code=413)
    headers = {name.lower(): value for name, value in request.headers.items()}
    return await run_in_threadpool(_handle, request.method, "/dav/" + path, headers, body, caller)
