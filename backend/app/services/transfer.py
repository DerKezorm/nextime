"""Export, import and moving house (TIME-24).

**Export.** Every calendar the member sees as one ICS file, each event as the member would get it through the calendar
account: the stored text (fields of other programs byte for byte), the member's own reminders as VALARM, for "busy
only" times and rule alone (free events of such a calendar not at all). Streamed: the events come from the database
in blocks and go out piece by piece. The ZIP is streamed too, written straight into the answer: one file per calendar
and, beside it, a folder with the files attached to its events (nextime's own calendars only, never "busy only").

**Import.** An ICS file into an own calendar, in two steps. The file is streamed into a work file (at most
``IMPORT_MAX_BYTES``, at most ``FILE_EVENTS_MAX`` events) and read line by line, never parsed as a whole, as UTF-8 or,
when it is no UTF-8, as Windows-1252 (and the preview says so): the preview counts its events (a series with its
exceptions is one; a second event under the same UID becomes an event of its own with a UID made from its content),
the days they span, entries of other kinds and embedded files over the limit. "Übernehmen" is a job in the background:
the events are prepared outside any lock and go, a batch at a time in short transactions, into a staging calendar
nobody sees; one short last transaction moves them into the calendar chosen and marks the import done. A job that
fails or is cut off leaves nothing anybody sees: its staging calendar goes (at once, or at the next start), and the
import can start again. Duplicates are skipped: the same UID in the calendar, or the same title at the same time
(TIME-14), also twice within the file. Titles and notes longer than TIME-5 allows are cut, an event nested deeper than
``DEPTH_MAX`` or larger than ``OBJECT_MAX_CHARS`` is named in the result and left out.

**Moving house.** A calendar from iCloud or Google (also Google's secret address) becomes an own calendar with its name,
colour and events, made from nextime's own copy of it: nothing goes to the source; only attachments are read from the
source's server with its access. The old calendar is hidden for the member, its connection and shares stay. Accepted
invitations move as ordinary events, other ones do not (for Google's secret address the member's own address comes from
the address itself or from a connected Google account). A job like the import, on the staging calendar, which becomes
the new calendar at the end; the same ``client_ref`` (for the same calendar) gives the same move, moving the same
calendar again goes into the calendar of the first move and skips what is there.
"""

from __future__ import annotations

import base64
import codecs
import hashlib
import logging
import re
import secrets
import threading
import uuid
import zipfile
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import unquote, urlsplit

import icalendar
from sqlalchemy import delete, false, func, insert, literal, select, update
from sqlalchemy.orm import Session

from ..config import get_settings
from ..errors import error
from ..models import (
    RIGHT_BUSY,
    SOURCE_GOOGLE,
    SOURCE_ICLOUD,
    SOURCE_SUBSCRIPTION,
    STATE_EXPIRED,
    STATE_OK,
    Account,
    Attachment,
    Calendar,
    CalendarMove,
    CalendarSetting,
    CalendarShare,
    Event,
    EventChange,
    ImportFile,
    ReminderChoice,
    Source,
    UtcDateTime,
    utcnow,
)
from . import alarms, attachments, calendars, events, ical, invitations, live, reminders, settings_service, symbols

logger = logging.getLogger("nextime.transfer")

#: The largest ICS file nextime loads, and the most events in one file.
IMPORT_MAX_BYTES = 50 * 1024 * 1024
FILE_EVENTS_MAX = 50_000
#: Up to here an account stays quick (the briefing's "20,000 events per account"); past it nextime warns first (EC-2).
EVENT_LIMIT = 20_000
#: Events read from the database at a time while exporting, and written at a time by a job.
BLOCK = 500
BATCH = 500
#: How long a file waits between preview and "übernehmen"; how many may wait per account.
IMPORT_KEEP_HOURS = 24
OPEN_IMPORTS = 5
#: One event of a file: at most this many characters (without embedded files) and nested parts.
OBJECT_MAX_CHARS = 1_000_000
DEPTH_MAX = 10

_BLOCKS = re.compile(r"^BEGIN:(VTIMEZONE|VEVENT)\r?\n.*?^END:\1[ \t]*\r?$\n?", re.MULTILINE | re.DOTALL | re.IGNORECASE)
_TZID = re.compile(r"^TZID:(.*?)\r?$", re.MULTILINE | re.IGNORECASE)
#: An embedded file (``ATTACH;VALUE=BINARY`` or base64) with its folded continuation lines.
_INLINE = re.compile(r"^ATTACH;[^\r\n:]*(?:VALUE=BINARY|ENCODING=BASE64)[^\r\n]*\r?\n(?:[ \t][^\r\n]*\r?\n)*",
                     re.MULTILINE | re.IGNORECASE)
#: Characters no file name may carry on the systems people unpack on.
_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))}


# --- Names ------------------------------------------------------------------------------------------------------------


def file_name(name: str, taken: set[str], suffix: str = ".ics", fallback: str = "calendar") -> str:
    """A readable file name (umlauts stay) no other entry of ``taken`` has, case alike (EC-8)."""
    clean = _UNSAFE.sub("-", name).strip(" .-")[:100].strip(" .") or fallback
    if clean.split(".")[0].upper() in _RESERVED:
        clean = f"_{clean}"
    stem, number = clean, 1
    candidate = f"{stem}{suffix}"
    while candidate.casefold() in taken:
        number += 1
        candidate = f"{stem} ({number}){suffix}"
    taken.add(candidate.casefold())
    return candidate


def _suffix(name: str) -> tuple[str, str]:
    """A file's name without its ending, and its ending cleaned like a name (EC-8): ``("Plan", ".pdf")``."""
    stem, dot, ending = name.rpartition(".")
    if not dot or not stem:
        return name, ""
    clean = _UNSAFE.sub("", ending).strip(" .")[:16]
    return stem, f".{clean}" if clean else ""


def disposition(name: str) -> str:
    """``Content-Disposition`` for a download: a plain ASCII name for old programs, the real one per RFC 5987."""
    from urllib.parse import quote

    plain = name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "_")
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(name, safe='')}"


# --- Export -----------------------------------------------------------------------------------------------------------


def _german(account: Account) -> bool:
    return events.language_of(account) == "de"


def _visible(db: Session, account: Account, calendar_id: int) -> calendars.Access:
    found = calendars.access(db, account.id, calendar_id)
    if found is None or found.calendar.deleted_at is not None:
        raise error("not_found", "Not found.", 404)
    return found


def _personal(db: Session, account: Account, found: calendars.Access) -> Any:
    from ..routers.dav import Personal

    personal = Personal(db, SimpleNamespace(account=account, base=""), found)
    # The calendar account names nextime's attachments by address; a file of its own has none of them.
    personal.files = {}
    return personal


def _shown_text(row: Event, found: calendars.Access, personal: Any, german: bool) -> str | None:
    """The event as the member gets it: "busy only" as a block, else the stored text with the member's alarms."""
    from ..routers.dav import busy_view

    if found.right == RIGHT_BUSY:
        return busy_view(row.ical, german)
    base = alarms.strip(row.ical)
    return alarms.place(base, *personal.lines(row.id, row.all_day, row.etag, row.ical))


def _blocks_of(text: str) -> Iterator[tuple[str, str]]:
    """The VTIMEZONE and VEVENT blocks of a stored object, as written (CRLF, folded)."""
    for match in _BLOCKS.finditer(text):
        block = match.group(0)
        if not block.endswith("\n"):
            block += "\r\n"
        yield match.group(1).upper(), block


def calendar_lines(db: Session, account: Account, found: calendars.Access) -> Iterator[str]:
    """The ICS file of a calendar, piece by piece, without embedded files (the file carries no attachments, AC-7)."""
    german = _german(account)
    personal = _personal(db, account, found)
    yield ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//nextime//export//EN\r\nCALSCALE:GREGORIAN\r\n"
           f"X-WR-CALNAME:{_escape(found.calendar.name)}\r\n")
    icon = symbols.shown(found.calendar.icon)
    if icon:
        # The calendar's symbol (TIME-4, as nexnotes' folders): nextime's own line, other programs leave it.
        yield f"X-NEXTIME-ICON:{icon}\r\n"
    zones: set[str] = set()
    last = 0
    while True:
        rows = list(db.scalars(select(Event).where(Event.calendar_id == found.calendar.id, Event.deleted_at.is_(None),
                                                   Event.id > last).order_by(Event.id).limit(BLOCK)))
        if not rows:
            break
        for row in rows:
            last = row.id
            text = _shown_text(row, found, personal, german)
            if text is None:
                continue
            text = _INLINE.sub("", text)
            for kind, block in _blocks_of(text):
                if kind == "VTIMEZONE":
                    tzid = _TZID.search(block)
                    key = tzid.group(1).strip() if tzid else block
                    if key in zones:
                        continue
                    zones.add(key)
                yield block
        db.expunge_all()
    yield "END:VCALENDAR\r\n"


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _session() -> Session:
    from ..db import SessionLocal

    return SessionLocal()


def export_calendar(db: Session, account: Account, calendar_id: int) -> tuple[str, Iterator[bytes]]:
    """The file name and the bytes of one calendar's ICS file (AC-1). The bytes come from a session of their own, read
    while the answer goes out."""
    found = _visible(db, account, calendar_id)
    name = file_name(found.calendar.name, set())
    account_id = account.id

    def chunks() -> Iterator[bytes]:
        with _session() as reading:
            person = reading.get(Account, account_id)
            again = calendars.access(reading, account_id, calendar_id) if person is not None else None
            if person is None or again is None:
                return
            for piece in calendar_lines(reading, person, again):
                yield piece.encode("utf-8")

    return name, chunks()


def _listed(db: Session, account: Account) -> list[calendars.Access]:
    return [item for item, _setting in calendars.ordered(db, account.id) if item.calendar.deleted_at is None]


def zip_count(db: Session, account: Account) -> int:
    calendars.ensure_seeded(db, account)
    return len(_listed(db, account))


class _Sink:
    """Where the ZIP is written: the pieces wait here until the answer takes them (the ZIP needs no seeking)."""

    def __init__(self) -> None:
        self.pieces: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.pieces.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        return None

    def take(self) -> bytes:
        out = b"".join(self.pieces)
        self.pieces.clear()
        return out


def zip_entries(db: Session, account: Account) -> list[tuple[str, int, list[tuple[str, str]]]]:
    """What the ZIP holds: per calendar its file name, its id and its attachments (id, path in the ZIP). A folder never
    has the name of another entry (EC-8), endings of attachments are cleaned like names."""
    calendars.ensure_seeded(db, account)
    taken: set[str] = set()
    listed = _listed(db, account)
    names = [file_name(found.calendar.name, taken) for found in listed]
    out: list[tuple[str, int, list[tuple[str, str]]]] = []
    for found, name in zip(listed, names, strict=True):
        files: list[tuple[str, str]] = []
        if found.right != RIGHT_BUSY and found.calendar.source_id is None:
            rows = list(db.execute(select(Attachment.id, Attachment.name).join(
                Event, Event.id == Attachment.event_id).where(
                Event.calendar_id == found.calendar.id, Event.deleted_at.is_(None)).order_by(Attachment.created_at)))
            if rows:
                folder = file_name(name[:-4], taken, "", "calendar")
                inside: set[str] = set()
                for attachment_id, attachment_name in rows:
                    stem, ending = _suffix(attachment_name)
                    files.append((attachment_id, f"{folder}/{file_name(stem, inside, ending, 'file')}"))
        out.append((name, found.calendar.id, files))
    return out


def export_zip(account_id: int) -> Iterator[bytes]:
    """Every calendar of the account as a ZIP, written straight into the answer (AC-1, AC-7): one ICS file per calendar,
    shared ones, subscriptions and background calendars too, and beside each a folder with its attachments."""
    sink = _Sink()
    with _session() as db:
        account = db.get(Account, account_id)
        if account is None:
            return
        entries = zip_entries(db, account)
        with zipfile.ZipFile(sink, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for name, calendar_id, files in entries:
                found = calendars.access(db, account_id, calendar_id)
                if found is None:
                    continue
                with archive.open(name, "w", force_zip64=True) as entry:
                    for piece in calendar_lines(db, account, found):
                        entry.write(piece.encode("utf-8"))
                        if sum(map(len, sink.pieces)) >= 256 * 1024:
                            yield sink.take()
                yield sink.take()
                for attachment_id, path_in_zip in files:
                    path = attachments.path_of(attachment_id)
                    if not path.is_file():
                        continue
                    with archive.open(path_in_zip, "w", force_zip64=True) as entry, open(path, "rb") as source:
                        while chunk := source.read(256 * 1024):
                            entry.write(chunk)
                            yield sink.take()
                    yield sink.take()
        yield sink.take()


def _work_dir() -> Path:
    folder = get_settings().data_dir / "imports"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --- Reading a file line by line --------------------------------------------------------------------------------------


class NotCalendar(Exception):
    pass


class Broken(Exception):
    pass


def encoding_of(path: Path) -> str:
    """``utf-8`` when the whole file reads as UTF-8, else ``cp1252`` (older Windows and Outlook exports, EC-4)."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    try:
        with open(path, "rb") as handle:
            while chunk := handle.read(1024 * 1024):
                decoder.decode(chunk)
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8"


def _lines(path: Path, encoding: str = "utf-8") -> Iterator[str]:
    """The logical lines of a file (folded lines joined), without line ends."""
    pending: str | None = None
    with open(path, "rb") as handle:
        for raw in handle:
            line = raw.decode(encoding, errors="replace").rstrip("\r\n")
            if pending is None:
                pending = line.lstrip("\ufeff")
                continue
            if line[:1] in (" ", "\t"):
                pending += line[1:]
            else:
                yield pending
                pending = line
    if pending is not None:
        yield pending


def components(path: Path, encoding: str = "utf-8") -> Iterator[tuple[str, list[str]]]:
    """Every component right inside a VCALENDAR, as its name and its lines. ``NotCalendar`` for a file that does not
    start as one (a web page, an empty file), ``Broken`` for one whose parts do not close."""
    stack: list[str] = []
    current: list[str] = []
    seen = False
    for line in _lines(path, encoding):
        if not line.strip():
            continue
        name, colon, value = line.partition(":")
        key = name.split(";", 1)[0].strip().upper()
        if not seen:
            if key != "BEGIN" or value.strip().upper() != "VCALENDAR":
                raise NotCalendar
            seen = True
        if not colon:
            if len(stack) >= 2:
                current.append(line)
                continue
            raise Broken
        if key == "BEGIN":
            what = value.strip().upper()
            if not stack and what != "VCALENDAR":
                raise Broken
            stack.append(what)
            if len(stack) == 2:
                current = [line]
            elif len(stack) > 2:
                current.append(line)
        elif key == "END":
            what = value.strip().upper()
            if not stack or stack[-1] != what:
                raise Broken
            if len(stack) >= 2:
                current.append(line)
            if len(stack) == 2:
                yield what, current
                current = []
            stack.pop()
        elif len(stack) >= 2:
            current.append(line)
    if not seen:
        raise NotCalendar
    if stack:
        raise Broken


def _value(lines: list[str], wanted: str) -> tuple[str, str] | None:
    """The parameters and value of the first property ``wanted`` of a component's own lines (not of an alarm in it)."""
    depth = 0
    for line in lines[1:-1]:
        name, _colon, value = line.partition(":")
        key = name.split(";", 1)[0].strip().upper()
        if key == "BEGIN":
            depth += 1
        elif key == "END":
            depth -= 1
        elif depth == 0 and key == wanted:
            return name, value.strip()
    return None


#: Lines that change with every export of the same event: no part of a UID made from the content.
_FLEETING = ("DTSTAMP", "LAST-MODIFIED", "CREATED", "SEQUENCE")


def _made_uid(lines: list[str]) -> str:
    """A UID from an event's content: the same event in the same file gives the same one (EC-11)."""
    stable = "\n".join(line for line in lines if not line.upper().startswith(_FLEETING)
                       and not line.upper().startswith("UID"))
    return "import-" + hashlib.sha256(stable.encode("utf-8")).hexdigest()[:32]


def _uid_of(lines: list[str]) -> str:
    """The UID of an event; one made from its content when it has none."""
    found = _value(lines, "UID")
    if found and found[1]:
        return found[1][:255]
    return _made_uid(lines)


def _inline_sizes(lines: list[str]) -> list[int]:
    """The sizes of the embedded files of a component (base64, so three quarters of its characters)."""
    sizes = []
    for line in lines:
        head, _colon, value = line.partition(":")
        upper = head.upper()
        if upper.startswith("ATTACH;") and ("VALUE=BINARY" in upper or "ENCODING=BASE64" in upper):
            sizes.append(len(value.strip()) * 3 // 4)
    return sizes


def _shape(lines: list[str]) -> tuple[int, int]:
    """How deep a component nests and how many characters it has without its embedded files."""
    depth = deepest = size = 0
    for line in lines:
        head = line.partition(":")[0].upper()
        key = head.split(";", 1)[0].strip()
        if key == "BEGIN":
            depth += 1
            deepest = max(deepest, depth)
        elif key == "END":
            depth -= 1
        if not (head.startswith("ATTACH;") and ("VALUE=BINARY" in head or "ENCODING=BASE64" in head)):
            size += len(line)
    return deepest, size


def _day(raw: str) -> str | None:
    digits = raw.strip()[:8]
    if len(digits) == 8 and digits.isdigit():
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:]}"
    return None


@dataclass
class Group:
    """One event of a file: its UID, its parts (the series and its exceptions), what is wrong with it."""

    uid: str
    parts: list[list[str]]
    #: The same title at the same time as an event before it in the file (TIME-14): skipped like one in the calendar.
    twin: bool = False
    #: Too deep or too large: named in the result and left out.
    problem: str = ""


@dataclass
class FileContent:
    zones: dict[str, list[str]]
    groups: list[Group]
    skipped: int
    too_large: int
    first: str | None
    last: str | None


def _text_mark(lines: list[str]) -> tuple[str, str, str] | None:
    """Title and time as written: two events with the same ones in one file are the same event."""
    summary = _value(lines, "SUMMARY")
    start = _value(lines, "DTSTART")
    if summary is None or start is None:
        return None
    end = _value(lines, "DTEND") or _value(lines, "DURATION")
    return (ical._unescape(summary[1]).strip().casefold(), f"{start[0].upper()}:{start[1]}",
            f"{end[0].upper()}:{end[1]}" if end else "")


def read_content(path: Path, encoding: str, max_bytes: int) -> FileContent:
    """The events of a file, grouped by UID; a second event under the same UID becomes one of its own, with a UID made
    from its content (a file of Outlook or a phone app may carry two, review of TIME-24, finding 1)."""
    zones: dict[str, list[str]] = {}
    by_uid: dict[str, Group] = {}
    order: list[Group] = []
    marks: set[tuple[str, str, str]] = set()
    skipped = too_large = 0
    first: str | None = None
    last: str | None = None
    for kind, lines in components(path, encoding):
        if kind == "VTIMEZONE":
            tzid = _value(lines, "TZID")
            if tzid:
                zones.setdefault(tzid[1], lines)
            continue
        if kind != "VEVENT":
            skipped += 1
            continue
        start = _value(lines, "DTSTART")
        if start is None:
            raise Broken
        day = _day(start[1])
        if day:
            first = day if first is None or day < first else first
            last = day if last is None or day > last else last
        too_large += sum(1 for size in _inline_sizes(lines) if size > max_bytes)
        uid = _uid_of(lines)
        exception = _value(lines, "RECURRENCE-ID") is not None
        known = by_uid.get(uid)
        if known is not None and not exception and any(_value(part, "RECURRENCE-ID") is None for part in known.parts):
            # A second main part under the same UID: an event of its own.
            uid = _made_uid(lines)
            lines = [line if not line.upper().startswith("UID") else f"UID:{uid}" for line in lines]
            known = by_uid.get(uid)
        if known is None:
            known = Group(uid=uid, parts=[])
            by_uid[uid] = known
            order.append(known)
        known.parts.append(lines)
        depth, size = _shape(lines)
        if depth > DEPTH_MAX + 1:
            known.problem = "event_too_deep"
        elif size > OBJECT_MAX_CHARS:
            known.problem = "event_too_large"
        if not exception:
            mark = _text_mark(lines)
            if mark is not None:
                if mark in marks:
                    known.twin = True
                marks.add(mark)
        if len(order) > FILE_EVENTS_MAX:
            raise TooMany
    return FileContent(zones=zones, groups=order, skipped=skipped, too_large=too_large, first=first, last=last)


class TooMany(Exception):
    pass


def _object_text(uid: str, parts: list[list[str]], zones: dict[str, list[str]]) -> str:
    """One object of the file: its events (the series and its exceptions) with the time zones they name."""
    named = {match for lines in parts for line in lines for match in re.findall(r"TZID=\"?([^;:\"]+)", line)}
    out = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//nextime//import//EN"]
    for tzid in sorted(named):
        if tzid in zones:
            out.extend(zones[tzid])
    for lines in parts:
        if _value(lines, "UID") is None:
            lines = [lines[0], f"UID:{uid}", *lines[1:]]
        out.extend(lines)
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


# --- Preparing events -------------------------------------------------------------------------------------------------


@dataclass
class Item:
    """One event to make: its row (not added yet), the member's reminders from its alarms, the files it brings."""

    uid: str
    title: str
    row: Event
    taken: alarms.FromDevice | None = None
    choice: ReminderChoice | None = None
    files: list[attachments.Brought] = field(default_factory=list)


@dataclass
class Outcome:
    made: int = 0
    existing: int = 0
    shortened: int = 0
    failed: list[dict[str, str]] = field(default_factory=list)


def _href(uid: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9@._-]{1,200}", uid):
        return f"{uid}.ics"
    return hashlib.sha256(uid.encode("utf-8")).hexdigest()[:40] + ".ics"


def _same(title: str, starts_at: datetime | None, ends_at: datetime | None) -> tuple[str, str, str]:
    """When two events are the same though their UIDs differ (TIME-14 AC-3): the same title, upper and lower case and
    blanks at the edges alike, at the same start and end."""

    def moment(value: datetime | None) -> str:
        return value.astimezone(UTC).isoformat() if value is not None else ""

    return title.strip().casefold(), moment(starts_at), moment(ends_at)


def _live(db: Session, calendar_id: int) -> tuple[set[str], set[tuple[str, str, str]]]:
    """The UIDs and the title-and-time marks of the events in a calendar."""
    uids: set[str] = set()
    marks: set[tuple[str, str, str]] = set()
    found = db.execute(select(Event.uid, Event.title, Event.starts_at, Event.ends_at).where(
        Event.calendar_id == calendar_id, Event.deleted_at.is_(None)))
    for uid, title, starts_at, ends_at in found:
        uids.add(uid)
        marks.add(_same(title or "", starts_at, ends_at))
    return uids, marks


def _take_inline(parsed: icalendar.Calendar, title: str, max_bytes: int, outcome: Outcome,
                 files: list[attachments.Brought], fetch: Any = None) -> None:
    """Embedded files (and, with ``fetch``, files on the source's server) become nextime's attachments of the event;
    one over the limit or not to be had is named with its reason (EC-7), the event comes all the same."""
    for component in ical.events_of(parsed):
        values = component.get("ATTACH")
        if values is None:
            continue
        kept = []
        for value in values if isinstance(values, list) else [values]:
            params = getattr(value, "params", {}) or {}
            name = str(params.get("FILENAME") or params.get("X-FILENAME") or params.get("X-APPLE-FILENAME") or "")
            embedded = str(params.get("VALUE", "")).upper() == "BINARY" or \
                str(params.get("ENCODING", "")).upper() == "BASE64"
            if embedded:
                try:
                    data = base64.b64decode(value.to_ical())
                except Exception:  # noqa: BLE001 - a broken embedded file is one that cannot be had
                    outcome.failed.append({"title": title, "reason": "attachment_unreadable"})
                    continue
                if len(data) > max_bytes:
                    outcome.failed.append({"title": title, "reason": "attachment_too_large"})
                    continue
                work = attachments.work_file()
                work.write_bytes(data)
                files.append(attachments.bring(work, name or "file"))
                continue
            address = str(value)
            size = str(params.get("SIZE", ""))
            if fetch is not None and size.isdigit() and int(size) > max_bytes:
                # Over the limit by its own word: not fetched; its link stays, the result names it (EC-7).
                outcome.failed.append({"title": title, "reason": "attachment_too_large"})
            elif fetch is not None:
                got = fetch(address, name, title, outcome)
                if got is not None:
                    files.append(got)
                    continue
            kept.append(value)
        component.pop("ATTACH")
        for value in kept:
            component.add("ATTACH", value)


def _shorten(parsed: icalendar.Calendar) -> bool:
    """Titles and notes within the limits of TIME-5 (review of TIME-24, finding 9); whether something was cut."""
    cut = False
    for component in ical.events_of(parsed):
        for name, most in (("SUMMARY", events.TITLE_MAX), ("DESCRIPTION", events.NOTES_MAX)):
            value = component.get(name)
            if value is not None and len(str(value)) > most:
                component.pop(name)
                component.add(name, str(value)[:most])
                cut = True
    return cut


def _item(text: str, uid: str, by: int, max_bytes: int, outcome: Outcome, fetch: Any = None,
          limits: bool = False) -> Item:
    """An event made from an object's text, prepared outside any transaction."""
    parsed = ical.parse(text)
    if limits and _shorten(parsed):
        outcome.shortened += 1
    master = ical.master_of(parsed)
    title = str(master.get("SUMMARY", ""))
    files: list[attachments.Brought] = []
    _take_inline(parsed, title, max_bytes, outcome, files, fetch)
    ical.add_timezones(parsed)
    ical.derive(parsed)
    written = ical.serialize(parsed)
    taken = alarms.take(parsed, written)
    written = alarms.strip(written)
    row = Event(uid=uid, href=_href(uid), ical="", etag="", created_by=by, starts_at=utcnow())
    events.write(None, row, written, parsed, by)  # type: ignore[arg-type]
    return Item(uid=uid, title=title, row=row, taken=taken if taken.any and not taken.only_defaults else None,
                files=files)


def _drop_files(items: Iterable[Item]) -> None:
    for item in items:
        for brought in item.files:
            brought.work.unlink(missing_ok=True)


def _account_events(db: Session, account_id: int) -> int:
    own = select(Calendar.id).where(Calendar.owner_id == account_id, Calendar.deleted_at.is_(None))
    return int(db.scalar(select(func.count()).select_from(Event).where(
        Event.calendar_id.in_(own), Event.deleted_at.is_(None))) or 0)


# --- Jobs: staging, batches, the last step ----------------------------------------------------------------------------

#: The jobs running in this process, by key (``import:<id>``, ``move:<id>``).
_jobs: dict[str, threading.Thread] = {}
_jobs_lock = threading.Lock()


def _start(key: str, work: Callable[[], None]) -> None:
    with _jobs_lock:
        known = _jobs.get(key)
        if known is not None and known.is_alive():
            return
        thread = threading.Thread(target=work, name=f"nextime-{key}", daemon=True)
        _jobs[key] = thread
        thread.start()


def running(key: str) -> bool:
    with _jobs_lock:
        known = _jobs.get(key)
        return known is not None and known.is_alive()


def wait_jobs(seconds: float = 120.0) -> None:
    """For tests and shutting down: waits until every job of this process ended."""
    with _jobs_lock:
        threads = list(_jobs.values())
    for thread in threads:
        thread.join(seconds)


def _new_staging(owner_id: int, name: str, color: str, icon: str = "") -> int:
    """A calendar nobody sees (deleted, an orphan, kept from the orphans' cleanup) for the events of a job."""
    with _session() as db:
        row = Calendar(owner_id=owner_id, dav_name=uuid.uuid4().hex, name=name[: calendars.NAME_MAX] or "Import",
                       color=color, icon=icon, deleted_at=utcnow(), orphan=True, staging=True)
        db.add(row)
        db.commit()
        return row.id


def drop_staging(staging_id: int | None) -> None:
    """A job's staging calendar goes with its events and their files: nothing half stays (EC-9)."""
    if staging_id is None:
        return
    with _session() as db:
        calendar = db.get(Calendar, staging_id)
        if calendar is None or not calendar.staging:
            return
        event_ids = select(Event.id).where(Event.calendar_id == staging_id)
        files = list(db.scalars(select(Attachment.id).where(Attachment.event_id.in_(event_ids))))
        db.execute(delete(Attachment).where(Attachment.event_id.in_(event_ids)))
        db.execute(delete(ReminderChoice).where(ReminderChoice.event_id.in_(event_ids)))
        db.execute(delete(Event).where(Event.calendar_id == staging_id))
        db.execute(delete(Calendar).where(Calendar.id == staging_id))
        db.commit()
    attachments.remove_placed(files)


def _write_batch(staging_id: int, account_id: int, items: list[Item]) -> None:
    """One batch of events into the staging calendar, in a short transaction of its own."""
    placed: list[str] = []
    with _session() as db:
        try:
            for item in items:
                item.row.calendar_id = staging_id
                db.add(item.row)
            db.flush()
            for item in items:
                if item.choice is not None:
                    db.add(ReminderChoice(event_id=item.row.id, account_id=account_id, minutes=item.choice.minutes,
                                          alarms=item.choice.alarms, occurrences=item.choice.occurrences,
                                          updated_at=utcnow()))
                elif item.taken is not None:
                    db.add(ReminderChoice(event_id=item.row.id, account_id=account_id, minutes=item.taken.minutes,
                                          alarms=item.taken.raw, occurrences=item.taken.occurrences or None,
                                          updated_at=utcnow()))
                for brought in item.files:
                    placed.append(attachments.place_brought(db, item.row, account_id, brought))
            db.commit()
        except BaseException:
            db.rollback()
            attachments.remove_placed(placed)
            raise
    _drop_files(items)


def _settle_into(db: Session, staging_id: int, target_id: int, outcome: Outcome) -> list[str]:
    """In the last transaction: what the target calendar got meanwhile wins (the same UID, or title and time), the rest
    of the staging calendar moves into it, and devices and pages hear of it. Returns files to remove after commit."""
    uids, marks = _live(db, target_id)
    hrefs = set(db.scalars(select(Event.href).where(Event.calendar_id == target_id, Event.deleted_at.is_(None))))
    twins: list[int] = []
    for event_id, uid, title, starts_at, ends_at, href in db.execute(select(
            Event.id, Event.uid, Event.title, Event.starts_at, Event.ends_at, Event.href).where(
            Event.calendar_id == staging_id)):
        if uid in uids or _same(title or "", starts_at, ends_at) in marks:
            twins.append(event_id)
            continue
        if href in hrefs:
            db.execute(update(Event).where(Event.id == event_id).values(href=f"{secrets.token_hex(16)}.ics"))
    files: list[str] = []
    for start in range(0, len(twins), 900):
        part = twins[start:start + 900]
        files += list(db.scalars(select(Attachment.id).where(Attachment.event_id.in_(part))))
        db.execute(delete(Attachment).where(Attachment.event_id.in_(part)))
        db.execute(delete(ReminderChoice).where(ReminderChoice.event_id.in_(part)))
        db.execute(delete(Event).where(Event.id.in_(part)))
    outcome.existing += len(twins)
    outcome.made -= len(twins)
    db.execute(insert(EventChange).from_select(
        ["calendar_id", "href", "deleted", "created_at"],
        select(literal(target_id), Event.href, false(), literal(utcnow(), UtcDateTime()))
        .where(Event.calendar_id == staging_id)))
    db.execute(update(Event).where(Event.calendar_id == staging_id).values(calendar_id=target_id))
    live.touch_calendar(db, target_id)
    reminders.touch_on_commit(db, calendar_id=target_id)
    return files


def recover() -> int:
    """At the start: jobs that were running when the server stopped failed; their staging calendars go (EC-9)."""
    gone = 0
    with _session() as db:
        stuck_imports = list(db.execute(select(ImportFile.id, ImportFile.staging_id).where(
            ImportFile.state == "running")))
        stuck_moves = list(db.execute(select(CalendarMove.id, CalendarMove.staging_id).where(
            CalendarMove.state == "running")))
    for import_id, staging_id in stuck_imports:
        if running(f"import:{import_id}"):
            continue
        drop_staging(staging_id)
        with _session() as db:
            db.execute(update(ImportFile).where(ImportFile.id == import_id, ImportFile.state == "running").values(
                state="failed", problem="interrupted", staging_id=None))
            db.commit()
        gone += 1
    for move_id, staging_id in stuck_moves:
        if running(f"move:{move_id}"):
            continue
        drop_staging(staging_id)
        with _session() as db:
            db.execute(update(CalendarMove).where(CalendarMove.id == move_id, CalendarMove.state == "running").values(
                state="failed", problem="interrupted", staging_id=None))
            db.commit()
        gone += 1
    # Staging calendars no job knows any more (a start that stopped before writing its job's row).
    with _session() as db:
        known = {row for row in db.scalars(select(ImportFile.staging_id).where(ImportFile.staging_id.is_not(None)))}
        known |= {row for row in db.scalars(select(CalendarMove.staging_id).where(
            CalendarMove.staging_id.is_not(None)))}
        loose = [row for row in db.scalars(select(Calendar.id).where(Calendar.staging.is_(True))) if row not in known]
    for staging_id in loose:
        drop_staging(staging_id)
    if gone:
        logger.info("Jobs cut off by a stop were rolled back count=%s", gone)
    return gone


def _problem_of(exc: BaseException) -> str:
    from fastapi import HTTPException

    from .storage import disk_full

    if disk_full(exc):
        return "storage_full"
    if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
        return str(exc.detail.get("code") or "failed")[:64]
    if isinstance(exc, (Broken, NotCalendar, ical.IcalError)):
        return "import_broken"
    return "failed"


def _status(row: ImportFile | CalendarMove) -> dict[str, Any]:
    return {"state": row.state, "done": row.done, "total": row.total, "problem": row.problem or None,
            "result": row.result}


# --- Import -----------------------------------------------------------------------------------------------------------


def _import_path(import_id: str) -> Path:
    return _work_dir() / f"{import_id}.ics"


def _max_attachment(db: Session) -> int:
    return settings_service.upload_max_mb(db) * attachments.MB


async def receive(chunks: Any) -> str:
    """A file streamed into a work file, at most ``IMPORT_MAX_BYTES`` (``import_too_large``); its id."""
    import asyncio

    import_id = secrets.token_hex(16)
    path = _import_path(import_id)
    sink = await asyncio.to_thread(open, path, "wb")
    size = 0
    waiting: list[bytes] = []
    try:
        async for chunk in chunks:
            size += len(chunk)
            if size > IMPORT_MAX_BYTES:
                raise error("import_too_large", "The file is larger than allowed.", 413,
                            max_mb=IMPORT_MAX_BYTES // (1024 * 1024))
            waiting.append(chunk)
            if sum(map(len, waiting)) >= 1024 * 1024:
                await asyncio.to_thread(sink.write, b"".join(waiting))
                waiting.clear()
        if waiting:
            await asyncio.to_thread(sink.write, b"".join(waiting))
        await asyncio.to_thread(sink.close)
    except BaseException:
        sink.close()
        path.unlink(missing_ok=True)
        raise
    return import_id


def _forget_import(import_id: str) -> None:
    try:
        _import_path(import_id).unlink(missing_ok=True)
    except OSError:
        # Still open somewhere (Windows): the cleanup round takes it later.
        logger.info("An import file stays until the next cleanup import=%s", import_id)


def take(db: Session, account: Account, import_id: str, name: str) -> dict[str, Any]:
    """The preview of a file received (AC-2). A file that is no calendar, is broken or holds more than
    ``FILE_EVENTS_MAX`` events is refused and nothing of it stays (EC-1, EC-2)."""
    path = _import_path(import_id)
    try:
        if path.stat().st_size == 0:
            raise error("import_not_calendar", "This file is no calendar.", 422)
        encoding = encoding_of(path)
        try:
            content = read_content(path, encoding, _max_attachment(db))
        except NotCalendar:
            raise error("import_not_calendar", "This file is no calendar.", 422) from None
        except Broken:
            raise error("import_broken", "The calendar file is broken.", 422) from None
        except TooMany:
            raise error("import_too_many", f"At most {FILE_EVENTS_MAX} events per file.", 422,
                        max=FILE_EVENTS_MAX) from None
        if not content.groups:
            raise error("import_no_events", "There are no events in the file.", 422, skipped=content.skipped)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    clean = (name or "").strip().replace("\\", "/").rsplit("/", 1)[-1][:255] or "calendar.ics"
    summary = {"events": len(content.groups), "first": content.first, "last": content.last,
               "skipped": content.skipped, "too_large": content.too_large, "encoding": encoding}
    db.add(ImportFile(id=import_id, account_id=account.id, name=clean, summary=summary, state="ready",
                      total=len(content.groups)))
    db.commit()
    _limit_open(db, account.id, import_id)
    logger.info("Import file taken account=%s import=%s events=%s", account.id, import_id, summary["events"])
    return _preview(db, account, db.get(ImportFile, import_id), None)


def _limit_open(db: Session, account_id: int, keep: str) -> None:
    """At most ``OPEN_IMPORTS`` files wait per account; the oldest that do not run go (TIME-24 review, finding 10)."""
    rows = list(db.scalars(select(ImportFile).where(ImportFile.account_id == account_id, ImportFile.id != keep,
                                                    ImportFile.state != "running")
                           .order_by(ImportFile.created_at.desc())))
    extra = rows[OPEN_IMPORTS - 1:]
    for row in extra:
        _forget_import(row.id)
        db.delete(row)
    if extra:
        db.commit()


def _find_import(db: Session, account: Account, import_id: str) -> ImportFile:
    row = db.get(ImportFile, import_id) if re.fullmatch(r"[0-9a-f]{32}", import_id or "") else None
    if row is None or row.account_id != account.id:
        raise error("not_found", "Not found.", 404)
    return row


def _own_target(db: Session, account: Account, calendar_id: int) -> calendars.Access:
    """Only own nextime calendars take a file (out of scope: iCloud, Google, CalDAV, shared)."""
    found = calendars.access(db, account.id, calendar_id)
    if found is None or found.calendar.deleted_at is not None:
        raise error("not_found", "Not found.", 404)
    if found.right != calendars.OWNER or found.calendar.source_id is not None:
        raise error("import_target_not_own", "Files go into your own calendars only.", 403)
    return found


def _existing(db: Session, calendar_id: int, content: FileContent) -> int:
    """How many events of the file will be skipped, by the rule the import skips them with: the same UID in the
    calendar, the same title at the same start and end (TIME-14 AC-3), or the same title and time as one before it in
    the file. Only events whose title the calendar knows are read with icalendar."""
    uids, marks = _live(db, calendar_id)
    titles = {mark[0] for mark in marks}
    there = 0
    for group in content.groups:
        if group.uid in uids or group.twin:
            there += 1
            continue
        named = any((value := _value(part, "SUMMARY")) and ical._unescape(value[1]).strip().casefold() in titles
                    for part in group.parts)
        if not named:
            continue
        try:
            parsed = ical.parse(_object_text(group.uid, group.parts, content.zones))
            ical.add_timezones(parsed)
            derived = ical.derive(parsed)
        except Exception:  # noqa: BLE001, S112 - one that does not read is no duplicate; applying names it
            continue
        if _same(derived.title, derived.starts_at, derived.ends_at) in marks:
            there += 1
    return there


def _preview(db: Session, account: Account, row: ImportFile, calendar_id: int | None) -> dict[str, Any]:
    summary = dict(row.summary or {})
    existing = 0
    path = _import_path(row.id)
    if calendar_id is not None and row.state in ("ready", "failed") and path.is_file():
        _own_target(db, account, calendar_id)
        try:
            content = read_content(path, summary.get("encoding", "utf-8"), _max_attachment(db))
            existing = _existing(db, calendar_id, content)
        except (NotCalendar, Broken, TooMany):
            existing = 0
    count = _account_events(db, account.id)
    coming = summary.get("events", 0) - existing
    return {"id": row.id, "name": row.name, **summary, "existing": existing, "new": coming,
            "account_events": count, "limit": EVENT_LIMIT, "over_limit": count + coming > EVENT_LIMIT,
            **_status(row)}


def preview(db: Session, account: Account, import_id: str, calendar_id: int | None) -> dict[str, Any]:
    return _preview(db, account, _find_import(db, account, import_id), calendar_id)


_claiming = threading.Lock()


def apply_import(db: Session, account: Account, import_id: str, calendar_id: int) -> dict[str, Any]:
    """Starts making the file's events in the calendar, in the background (EC-2, EC-9); a second click finds it running
    or done and gets its state (EC-10). The page asks for the state until it is ``done`` or ``failed``."""
    row = _find_import(db, account, import_id)
    if row.state in ("running", "done"):
        return _status(row)
    _own_target(db, account, calendar_id)
    if not _import_path(import_id).is_file():
        raise error("not_found", "Not found.", 404)
    with _claiming:
        claimed = db.execute(update(ImportFile).where(ImportFile.id == import_id,
                                                      ImportFile.state.in_(("ready", "failed")))
                             .values(state="running", calendar_id=calendar_id, done=0, problem="", result=None)
                             ).rowcount
        db.commit()
        if claimed:
            account_id = account.id
            _start(f"import:{import_id}", lambda: _import_job(import_id, account_id, calendar_id))
    db.expire_all()
    return _status(_find_import(db, account, import_id))


def _import_job(import_id: str, account_id: int, calendar_id: int) -> None:
    staging_id: int | None = None
    try:
        with _session() as db:
            row = db.get(ImportFile, import_id)
            account = db.get(Account, account_id)
            if row is None or account is None:
                return
            target = _own_target(db, account, calendar_id)
            encoding = (row.summary or {}).get("encoding", "utf-8")
            max_bytes = _max_attachment(db)
            name, color = target.calendar.name, target.calendar.color
            live_uids, marks = _live(db, calendar_id)
        content = read_content(_import_path(import_id), encoding, max_bytes)
        staging_id = _new_staging(account_id, name, color)
        with _session() as db:
            db.execute(update(ImportFile).where(ImportFile.id == import_id).values(
                staging_id=staging_id, total=len(content.groups)))
            db.commit()
        outcome = Outcome()
        batch: list[Item] = []
        for done, group in enumerate(content.groups, start=1):
            if group.uid in live_uids or group.twin:
                outcome.existing += 1
            elif group.problem:
                title = next((ical._unescape(value[1]) for part in group.parts
                              if (value := _value(part, "SUMMARY"))), "")
                outcome.failed.append({"title": title[: events.TITLE_MAX], "reason": group.problem})
            else:
                try:
                    item = _item(_object_text(group.uid, group.parts, content.zones), group.uid, account_id,
                                 max_bytes, outcome, limits=True)
                except Exception as exc:
                    raise Broken from exc
                mark = _same(item.row.title or "", item.row.starts_at, item.row.ends_at)
                if mark in marks:
                    outcome.existing += 1
                    _drop_files([item])
                else:
                    marks.add(mark)
                    batch.append(item)
            if len(batch) >= BATCH or done == len(content.groups):
                if batch:
                    _write_batch(staging_id, account_id, batch)
                    outcome.made += len(batch)
                    batch = []
                with _session() as db:
                    db.execute(update(ImportFile).where(ImportFile.id == import_id).values(done=done))
                    db.commit()
        _finish_import(import_id, account_id, calendar_id, staging_id, outcome)
        _forget_import(import_id)
        logger.info("Import done account=%s import=%s made=%s existing=%s", account_id, import_id, outcome.made,
                    outcome.existing)
    except BaseException as exc:  # noqa: BLE001 - whatever stops the job, its staging calendar goes and it says so
        problem = _problem_of(exc)
        logger.info("An import failed import=%s problem=%s", import_id, problem)
        drop_staging(staging_id)
        with _session() as db:
            db.execute(update(ImportFile).where(ImportFile.id == import_id).values(
                state="failed", problem=problem, staging_id=None))
            db.commit()


def _finish_import(import_id: str, account_id: int, calendar_id: int, staging_id: int, outcome: Outcome) -> None:
    """The one short last step: into the calendar, done, with the result."""
    with _session() as db:
        try:
            account = db.get(Account, account_id)
            if account is None:
                raise error("not_found", "Not found.", 404)
            # The calendar chosen is asked again here: deleted or no longer one's own meanwhile, nothing comes.
            target = _own_target(db, account, calendar_id)
            files = _settle_into(db, staging_id, calendar_id, outcome)
            db.execute(delete(Calendar).where(Calendar.id == staging_id))
            result = {"made": outcome.made, "existing": outcome.existing, "shortened": outcome.shortened,
                      "failed": outcome.failed, "calendar": {"id": calendar_id, "name": target.calendar.name}}
            db.execute(update(ImportFile).where(ImportFile.id == import_id).values(
                state="done", result=result, staging_id=None))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    attachments.remove_placed(files)


def sweep(now: datetime | None = None) -> int:
    """Imports older than a day go with their files; jobs cut off go back (``recover``)."""
    moment = now or utcnow()
    recover()
    with _session() as db:
        old = list(db.scalars(select(ImportFile.id).where(
            ImportFile.created_at < moment - timedelta(hours=IMPORT_KEEP_HOURS), ImportFile.state != "running")))
        if old:
            db.execute(delete(ImportFile).where(ImportFile.id.in_(old)))
            db.commit()
    for import_id in old:
        _forget_import(import_id)
    return len(old)


# --- Moving house -----------------------------------------------------------------------------------------------------


def _google_address(source: Source) -> bool:
    return source.kind == SOURCE_SUBSCRIPTION and bool((source.options or {}).get("google"))


def _movable(db: Session, account: Account, calendar_id: int) -> tuple[calendars.Access, Source]:
    """A calendar from iCloud or Google (also Google's secret address) the member connected itself."""
    found = calendars.access(db, account.id, calendar_id)
    if found is None or found.calendar.deleted_at is not None:
        raise error("not_found", "Not found.", 404)
    source = db.get(Source, found.calendar.source_id) if found.calendar.source_id is not None else None
    if source is None or found.right != calendars.OWNER or source.account_id != account.id:
        raise error("move_not_possible", "Only calendars from iCloud or Google you connected move.", 403)
    if source.kind not in (SOURCE_ICLOUD, SOURCE_GOOGLE) and not _google_address(source):
        raise error("move_not_possible", "Only calendars from iCloud or Google you connected move.", 403)
    return found, source


def movable(db: Session, account: Account) -> list[dict[str, Any]]:
    """The calendars the member may move: from iCloud or Google it connected itself (Google's secret address too)."""
    calendars.ensure_seeded(db, account)
    found = []
    for item, _setting in calendars.ordered(db, account.id):
        calendar = item.calendar
        if item.right != calendars.OWNER or calendar.source_id is None or calendar.deleted_at is not None:
            continue
        source = db.get(Source, calendar.source_id)
        if source is None or source.account_id != account.id:
            continue
        if source.kind in (SOURCE_ICLOUD, SOURCE_GOOGLE) or _google_address(source):
            found.append({"id": calendar.id, "name": calendar.name, "color": calendar.color, "kind": _kind(source)})
    return found


def _kind(source: Source) -> str:
    return "icloud" if source.kind == SOURCE_ICLOUD else "google"


def own_addresses(db: Session, account: Account, found: calendars.Access, source: Source) -> set[str]:
    """The member's own addresses for a calendar it moves: those of the connection; for Google's secret address the
    calendar's own address, which the address names (``…/ical/<address>/private-…``; not a group calendar), and the
    addresses of the member's connected Google accounts (review of TIME-24, finding 2)."""
    mine = set(invitations.context(db, found)[0])
    if not _google_address(source):
        return mine
    from . import sync

    access = sync.access_of(source) or {}
    path = urlsplit(str(access.get("url") or "")).path
    parts = path.split("/")
    if "ical" in parts and parts.index("ical") + 1 < len(parts):
        named = unquote(parts[parts.index("ical") + 1]).strip().lower()
        if "@" in named and not named.endswith("calendar.google.com"):
            mine.add(named)
    for other in db.scalars(select(Source).where(Source.account_id == account.id, Source.kind == SOURCE_GOOGLE)):
        mine |= invitations.own_addresses(other)
    return mine


def _earlier_target(db: Session, account_id: int, calendar_id: int) -> Calendar | None:
    move = db.scalar(select(CalendarMove).where(CalendarMove.account_id == account_id,
                                                CalendarMove.source_calendar_id == calendar_id,
                                                CalendarMove.state == "done",
                                                CalendarMove.target_id.is_not(None))
                     .order_by(CalendarMove.id.desc()).limit(1))
    if move is None:
        return None
    target = db.get(Calendar, move.target_id)
    return target if target is not None and target.deleted_at is None and target.owner_id == account_id else None


def _shared(db: Session, calendar_id: int) -> bool:
    return db.scalar(select(func.count()).select_from(CalendarShare).where(
        CalendarShare.calendar_id == calendar_id)) != 0


def _invitation_state(mine: set[str], parsed: icalendar.Calendar) -> str | None:
    state = invitations.state_of(ical.master_of(parsed), mine)
    return state["state"] if state else None


def move_preview(db: Session, account: Account, calendar_id: int) -> dict[str, Any]:
    """What a move would do: events, invitations that stay, the calendar of an earlier move, shares, the limit."""
    found, source = _movable(db, account, calendar_id)
    mine = own_addresses(db, account, found, source)
    moving = staying = 0
    for row in db.scalars(select(Event).where(Event.calendar_id == calendar_id, Event.deleted_at.is_(None))):
        state = None
        if mine and "ATTENDEE" in row.ical.upper():
            try:
                state = _invitation_state(mine, ical.parse(row.ical))
            except ical.IcalError:
                state = None
        if state is not None and state != "accepted":
            staying += 1
        else:
            moving += 1
    earlier = _earlier_target(db, account.id, calendar_id)
    count = _account_events(db, account.id)
    return {"calendar": {"id": calendar_id, "name": found.calendar.name, "color": found.calendar.color},
            "kind": _kind(source), "events": moving, "invitations": staying,
            "moved_to": {"id": earlier.id, "name": earlier.name} if earlier else None,
            "shared": _shared(db, calendar_id), "state": source.state, "account_events": count, "limit": EVENT_LIMIT,
            "over_limit": count + moving > EVENT_LIMIT}


def move_status(db: Session, account: Account, client_ref: str) -> dict[str, Any]:
    row = db.scalar(select(CalendarMove).where(CalendarMove.account_id == account.id,
                                               CalendarMove.client_ref == (client_ref or "")[:64]))
    if row is None:
        raise error("not_found", "Not found.", 404)
    return {**_status(row), "calendar_id": row.source_calendar_id}


def move(db: Session, account: Account, calendar_id: int, client_ref: str) -> dict[str, Any]:
    """Starts moving the calendar into an own one, in the background (AC-3, AC-4, AC-8, EC-9 to EC-13). Nothing goes
    to the source. The page asks for the state with the same ``client_ref``."""
    ref = (client_ref or "").strip()
    if not ref or len(ref) > 64:
        raise error("invalid_input", "The input is not valid.", 422, fields=["client_ref"])
    with _claiming:
        known = db.scalar(select(CalendarMove).where(CalendarMove.account_id == account.id,
                                                     CalendarMove.client_ref == ref))
        if known is not None and known.source_calendar_id != calendar_id:
            # A reference names one move of one calendar (review of TIME-24, finding 5).
            raise error("move_ref_taken", "This reference belongs to the move of another calendar.", 409)
        if known is not None and known.state in ("running", "done"):
            return {**_status(known), "calendar_id": calendar_id}
        _found, source = _movable(db, account, calendar_id)
        if source.state == STATE_EXPIRED:
            raise error("move_source_expired", "The access to the source has expired. Connect it again first.", 409)
        if source.state != STATE_OK:
            raise error("move_source_unreachable", "The source does not answer right now. Try again later.", 409)
        busy = db.scalar(select(CalendarMove).where(CalendarMove.account_id == account.id,
                                                    CalendarMove.source_calendar_id == calendar_id,
                                                    CalendarMove.state == "running"))
        if busy is not None and busy.client_ref != ref:
            # The same calendar moving already (a second tab): that move's state, nothing twice (EC-10).
            return {**_status(busy), "calendar_id": calendar_id}
        if known is None:
            known = CalendarMove(account_id=account.id, source_calendar_id=calendar_id, client_ref=ref,
                                 state="running")
            db.add(known)
        else:
            known.state, known.problem, known.done, known.result = "running", "", 0, None
        db.commit()
        move_id, account_id = known.id, account.id
        _start(f"move:{move_id}", lambda: _move_job(move_id, account_id, calendar_id))
    db.expire_all()
    row = db.get(CalendarMove, move_id)
    return {**_status(row), "calendar_id": calendar_id}


def _move_job(move_id: int, account_id: int, calendar_id: int) -> None:
    staging_id: int | None = None
    try:
        with _session() as db:
            account = db.get(Account, account_id)
            if account is None:
                return
            found, source = _movable(db, account, calendar_id)
            mine = own_addresses(db, account, found, source)
            max_bytes = _max_attachment(db)
            name, color, kind, source_id = found.calendar.name, found.calendar.color, source.kind, source.id
            icon = found.calendar.icon or ""
            earlier = _earlier_target(db, account_id, calendar_id)
            target_id = earlier.id if earlier is not None else None
            live_uids, marks = _live(db, target_id) if target_id is not None else (set(), set())
            rows = list(db.execute(select(Event.id, Event.uid, Event.ical).where(
                Event.calendar_id == calendar_id, Event.deleted_at.is_(None)).order_by(Event.id)))
            choices = {choice.event_id: (choice.minutes, choice.alarms, choice.occurrences)
                       for choice in db.scalars(select(ReminderChoice).where(
                           ReminderChoice.account_id == account_id,
                           ReminderChoice.event_id.in_(select(Event.id).where(Event.calendar_id == calendar_id))))}
        staging_id = _new_staging(account_id, name, color, icon)
        with _session() as db:
            db.execute(update(CalendarMove).where(CalendarMove.id == move_id).values(
                staging_id=staging_id, total=len(rows)))
            db.commit()

        def fetch(address: str, file_name_: str, title: str, out: Outcome) -> attachments.Brought | None:
            """A file on the source's server, read with the source's access (only reading; EC-7 names what fails)."""
            from . import remote_files

            if not address.lower().startswith(("https://", "http://")):
                return None
            with _session() as reading:
                source_calendar = reading.get(Calendar, calendar_id)
                known_source = reading.get(Source, source_id)
                if source_calendar is None or known_source is None or not remote_files.allowed(known_source, address):
                    return None
                work = attachments.work_file()
                try:
                    remote_files.fetch(reading, source_calendar, address, max_bytes, work)
                except Exception:  # noqa: BLE001 - a file that cannot be had keeps its link; the event comes still
                    too_large = work.exists() and work.stat().st_size >= max_bytes
                    work.unlink(missing_ok=True)
                    out.failed.append({"title": title,
                                       "reason": "attachment_too_large" if too_large else "attachment_unavailable"})
                    return None
            guessed = file_name_ or address.rstrip("/").rsplit("/", 1)[-1].split("?")[0] or "file"
            return attachments.bring(work, guessed)

        outcome = Outcome()
        invitations_left = 0
        batch: list[Item] = []
        for done, (event_id, uid, text) in enumerate(rows, start=1):
            if uid in live_uids:
                outcome.existing += 1
            else:
                parsed = ical.parse(text)
                state = _invitation_state(mine, parsed) if mine and "ATTENDEE" in text.upper() else None
                if state is not None and state != "accepted":
                    invitations_left += 1
                else:
                    if state == "accepted":
                        # An accepted invitation becomes an ordinary event of one's own (AC-8).
                        for component in ical.events_of(parsed):
                            for prop in ("ATTENDEE", "ORGANIZER", "X-NEXTIME-SUMMARY-DE", "X-NEXTIME-SUMMARY-EN"):
                                component.pop(prop, None)
                        parsed.pop("METHOD", None)
                        text = ical.serialize(parsed)
                    item = _item(text, uid, account_id, max_bytes, outcome,
                                 fetch if kind == SOURCE_ICLOUD else None)
                    chosen = choices.get(event_id)
                    if chosen is not None:
                        item.choice = ReminderChoice(minutes=chosen[0], alarms=chosen[1], occurrences=chosen[2])
                    mark = _same(item.row.title or "", item.row.starts_at, item.row.ends_at)
                    if mark in marks:
                        outcome.existing += 1
                        _drop_files([item])
                    else:
                        marks.add(mark)
                        batch.append(item)
            if len(batch) >= BATCH or done == len(rows):
                if batch:
                    _write_batch(staging_id, account_id, batch)
                    outcome.made += len(batch)
                    batch = []
                with _session() as db:
                    db.execute(update(CalendarMove).where(CalendarMove.id == move_id).values(done=done))
                    db.commit()
        _finish_move(move_id, account_id, calendar_id, staging_id, target_id, outcome, invitations_left,
                     "icloud" if kind == SOURCE_ICLOUD else "google", name)
        logger.info("Calendar moved account=%s calendar=%s made=%s existing=%s", account_id, calendar_id, outcome.made,
                    outcome.existing)
    except BaseException as exc:  # noqa: BLE001 - whatever stops the job, its staging calendar goes and it says so
        problem = _problem_of(exc)
        logger.info("A move failed move=%s problem=%s", move_id, problem)
        drop_staging(staging_id)
        with _session() as db:
            db.execute(update(CalendarMove).where(CalendarMove.id == move_id).values(
                state="failed", problem=problem, staging_id=None))
            db.commit()


def _finish_move(move_id: int, account_id: int, calendar_id: int, staging_id: int, target_id: int | None,
                 outcome: Outcome, invitations_left: int, kind: str, name: str) -> None:
    """The one short last step: the staging calendar becomes the new calendar (or moves into the one of an earlier
    move), the old one is hidden for the member, the move is done with its result."""
    files: list[str] = []
    with _session() as db:
        try:
            source = db.get(Calendar, calendar_id)
            if source is None or source.deleted_at is not None:
                raise error("not_found", "Not found.", 404)
            again = target_id is not None
            if target_id is not None:
                target = db.get(Calendar, target_id)
                if target is None or target.deleted_at is not None:
                    raise error("not_found", "Not found.", 404)
                files = _settle_into(db, staging_id, target_id, outcome)
                db.execute(delete(Calendar).where(Calendar.id == staging_id))
            else:
                target = db.get(Calendar, staging_id)
                assert target is not None
                target.deleted_at, target.orphan, target.staging = None, False, False
                db.add(CalendarSetting(account_id=account_id, calendar_id=staging_id, hidden=False,
                                       position=calendars._next_position(db, account_id)))
                db.execute(insert(EventChange).from_select(
                    ["calendar_id", "href", "deleted", "created_at"],
                    select(literal(staging_id), Event.href, false(), literal(utcnow(), UtcDateTime()))
                    .where(Event.calendar_id == staging_id)))
                reminders.touch_on_commit(db, calendar_id=staging_id)
            # The old calendar is hidden for the member only; its connection and its shares stay (AC-3, AC-6, EC-12).
            calendars.setting(db, account_id, calendar_id).hidden = True
            live.touch_accounts(db, {account_id})
            result = {"made": outcome.made, "existing": outcome.existing, "failed": outcome.failed,
                      "invitations": invitations_left, "again": again, "kind": kind,
                      "shared": _shared(db, calendar_id), "from": {"id": calendar_id, "name": name},
                      "calendar": {"id": target.id, "name": target.name}}
            db.execute(update(CalendarMove).where(CalendarMove.id == move_id).values(
                state="done", result=result, target_id=target.id, staging_id=None))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    attachments.remove_placed(files)
