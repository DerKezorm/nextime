"""The tasks of nextasks and the deadlines of nexpaper on the calendar account (asked for while testing, 2026-11-28):
two calendars only to be read, "Meine Aufgaben" and "Meine Fristen", for the account whose connection they come
from. nextime keeps none of it: each request of a device builds them from what the views get (``nextasks.tasks_in``,
``nexpaper.deadlines_in``, one answer of the other app per account and minute). A calendar hidden in nextime (the eye
of "Meine Aufgaben" or "Meine Fristen", an area of nextasks) is not on the device either, as with any calendar
(TIME-4 AC-3).

Every entry keeps the id it has in the other app in its UID, so a device knows it again. A task is one event on its
due day (whole day, or 15 minutes at its time as a floating time: the clock of the device), a repeating one a series
with nextasks' rule; done tasks and the previews of the views do not come along. A deadline is a whole-day event on
the day to act by. While the other app does not answer, the device keeps what it had last: an app away for a while
empties no calendar."""

from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta

import icalendar
from sqlalchemy.orm import Session

from ..models import Account
from . import ical, nexpaper, nextasks

TASKS = "nextasks-tasks"
DEADLINES = "nexpaper-deadlines"
#: The names of these calendars on the calendar account; a device can make no calendar of its own under them.
NAMES = (TASKS, DEADLINES)
#: Their colors, from the eleven of the design system.
COLORS = {TASKS: "teal", DEADLINES: "orange"}
#: A task with a time: this long on the device.
TASK_MINUTES = 15
#: A fixed DTSTAMP: the text of an entry changes only when the entry does, and so does its ETag.
STAMP = datetime(2026, 1, 1, tzinfo=UTC)

_TEXTS = {
    True: {TASKS: "Meine Aufgaben", DEADLINES: "Meine Fristen", "inbox": "liegt noch im Eingang",
           "from": "von {}", "for": "für {}",
           "what": {"pay": "Zahlen", "cancel": "Kündigen bis", "ends": "Vertrag endet", "warranty": "Garantie bis",
                    "follow_up": "Wiedervorlage"}},
    False: {TASKS: "My tasks", DEADLINES: "My deadlines", "inbox": "still in the inbox",
            "from": "from {}", "for": "for {}",
            "what": {"pay": "Pay", "cancel": "Cancel by", "ends": "Contract ends", "warranty": "Warranty until",
                     "follow_up": "Follow-up"}},
}

_last_lock = threading.Lock()
#: What a device got last per account and calendar: shown again while the other app does not answer.
_last: dict[tuple[int, str], dict[str, str]] = {}


@dataclass
class Partner:
    """One of the two calendars as the calendar account shows it."""

    dav_name: str
    name: str
    color: str
    #: The objects by their name on the calendar account (``<uid>.ics``) and their iCalendar text.
    objects: dict[str, str] = field(default_factory=dict)

    def etag(self, name: str) -> str:
        return ical.etag_of(self.objects[name])

    @property
    def mark(self) -> str:
        """The state of the whole calendar, for its collection tag and sync token: its entries, name and color."""
        digest = hashlib.sha256(f"{self.name}\n{self.color}".encode())
        for name in sorted(self.objects):
            digest.update(f"\n{name}\n{self.etag(name)}".encode())
        return digest.hexdigest()[:16]


def forget() -> None:
    """For tests: nothing kept from before."""
    with _last_lock:
        _last.clear()


def _kept(account_id: int, dav_name: str, objects: dict[str, str] | None) -> dict[str, str]:
    """``objects`` fresh, kept for the next time; ``None`` (the other app away) gives what was kept."""
    with _last_lock:
        if objects is None:
            return dict(_last.get((account_id, dav_name), {}))
        _last[(account_id, dav_name)] = dict(objects)
        return objects


def _object(uid: str, summary: str, start: date | datetime, end: date | datetime, *, description: str = "",
            url: str = "", rule: str = "") -> str:
    calendar = ical.new_calendar()
    event = icalendar.Event()
    event.add("uid", uid)
    event.add("dtstamp", STAMP)
    event.add("summary", summary)
    event.add("dtstart", start)
    event.add("dtend", end)
    event.add("transp", "TRANSPARENT")
    if rule:
        event.add("rrule", icalendar.vRecur.from_ical(rule))
    if description:
        event.add("description", description)
    if url:
        event.add("url", url)
    calendar.add_component(event)
    return ical.serialize(calendar)


def _rule(raw: str, timed: bool) -> str:
    """nextasks' rule for a series on the device: its UNTIL of the same kind as the start (RFC 5545)."""
    until = re.search(r"UNTIL=([0-9TZ]+)", raw)
    if until is None:
        return raw
    value = until.group(1)
    if timed and "T" not in value:
        value = f"{value[:8]}T235959"
    elif not timed:
        value = value[:8]
    return raw[:until.start(1)] + value + raw[until.end(1):]


def _task_objects(db: Session, account: Account, german: bool) -> dict[str, str] | None:
    today = nextasks.clock().date()
    found = nextasks.tasks_in(db, account, None, today + timedelta(days=nextasks.HORIZON_DAYS), previews=False)
    if found.get("problem"):
        return None
    hidden = {area["id"] for area in found.get("areas", []) if area.get("hidden")}
    texts = _TEXTS[german]
    objects: dict[str, str] = {}
    for task in found["tasks"]:
        if task.get("done") or task.get("preview") or (task.get("row") and task["row"] in hidden):
            continue
        day = date.fromisoformat(task["day"])
        if task.get("time"):
            start: date | datetime = datetime.combine(day, time.fromisoformat(task["time"]))
            end: date | datetime = start + timedelta(minutes=TASK_MINUTES)
        else:
            start, end = day, day + timedelta(days=1)
        place = " › ".join(part for part in (task.get("area_name"), task.get("list_name"), task.get("section"))
                           if part)
        people = ", ".join(task.get("assignees") or [])
        lines = [place, texts["for"].format(people) if people else "", task.get("notes") or "", task.get("link") or ""]
        uid = f"nextime-nextasks-{task['id']}"
        objects[f"{uid}.ics"] = _object(uid, task["title"] or "…", start, end,
                                        description="\n".join(line for line in lines if line),
                                        url=task.get("link") or "",
                                        rule=_rule(task["repeat"], bool(task.get("time"))) if task.get("repeat")
                                        else "")
    return objects


def _amount(cents: int, currency: str, german: bool) -> str:
    whole = f"{abs(cents) / 100:,.2f}"
    if german:
        whole = whole.replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{'-' if cents < 0 else ''}{whole} {currency}".strip()


def _deadline_objects(db: Session, account: Account, german: bool) -> dict[str, str] | None:
    found = nexpaper.deadlines_in(db, account, None, date.max)
    if found.get("problem"):
        return None
    texts = _TEXTS[german]
    objects: dict[str, str] = {}
    for item in found["deadlines"]:
        day = date.fromisoformat(item["day"])
        summary = f"{texts['what'][item['what']]}: {item['title'] or '…'}"
        lines = [texts["from"].format(item["sender"]) if item.get("sender") else "",
                 _amount(item["amount"], item.get("currency", ""), german) if item.get("amount") is not None else "",
                 item.get("note") or "", texts["inbox"] if item.get("status") == "inbox" else "",
                 item.get("link") or ""]
        uid = f"nextime-nexpaper-{item['id']}"
        objects[f"{uid}.ics"] = _object(uid, summary, day, day + timedelta(days=1),
                                        description="\n".join(line for line in lines if line),
                                        url=item.get("link") or "")
    return objects


def speaks_german(account: Account) -> bool:
    return (account.language or account.browser_language or "").lower().startswith("de")


def shown(db: Session, account: Account, german: bool) -> list[Partner]:
    """The calendars of the account's connections that are shown in nextime, without their entries (nothing asked of
    the other apps): "Meine Aufgaben" while nextasks is connected, "Meine Fristen" while nexpaper is."""
    texts = _TEXTS[german]
    out: list[Partner] = []
    tasks_row = nextasks.connection_of(db, account.id)
    if tasks_row is not None and not tasks_row.hidden:
        out.append(Partner(TASKS, texts[TASKS], COLORS[TASKS]))
    paper_row = nexpaper.connection_of(db, account.id)
    if paper_row is not None and not paper_row.hidden:
        out.append(Partner(DEADLINES, texts[DEADLINES], COLORS[DEADLINES]))
    return out


def listing(db: Session, account: Account, german: bool) -> list[Partner]:
    """The calendars of ``shown``, each with its entries."""
    build = {TASKS: _task_objects, DEADLINES: _deadline_objects}
    for partner in (out := shown(db, account, german)):
        partner.objects = _kept(account.id, partner.dav_name, build[partner.dav_name](db, account, german))
    return out


def find(db: Session, account: Account, german: bool, dav_name: str) -> Partner | None:
    if dav_name not in NAMES:
        return None
    return next((one for one in listing(db, account, german) if one.dav_name == dav_name), None)
