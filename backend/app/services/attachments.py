"""Attachments at events of nextime's own calendars (TIME-9), the way nexnotes keeps attachments of notes (NOTES-10).

**What is where.** The file lies in ``<data>/attachments/<id>`` (a backup carries that folder, and restoring one brings
it back, EC-16), with ``<id>.view`` (a WebP every browser shows, a HEIC too, EC-9) and ``<id>.thumb`` (the preview)
beside it where there are such. A row in ``attachments`` says what the server found the file to be (``media.py``: by
its content, never by its name, EC-6) and the event it hangs at.

**Coming in** (``begin``, ``receive``, ``finish``): only who may change the event (the owner, "edit"), only at an event
of an own calendar (AC-5); the id comes from the browser, which shows the upload at once (the same id twice is the same
file). The operator's limit per file (``upload_max_mb``) is checked against the length the browser declares and again
while the body comes (AC-2, EC-15); the account's storage before. The body goes to a work file (``.up-…``, never in a
backup) and becomes the attachment only once it is whole and prepared: an upload that broke off, a closed browser, a
restart leave nothing at the event (EC-11), and the cleanup round takes the work file away. A full disk or a folder
that cannot be written ends only the upload, with a sentence (EC-12).

**A new event** has no id while its upload runs: the upload names the ``client_ref`` the event will be made with
(``pending_ref``) and hangs itself at the event once it exists (AC-10). One never made goes after a day.

**Going out**: to whoever sees the event's calendar, but not with "busy only" and not while the event is in the trash
(AC-3, EC-13); everybody else gets the same "not found". A guest link never shows one (EC-1). The headers are the
router's. The calendar account hands every device the attachments as ``ATTACH`` with nextime's address (AC-4,
RFC 8607 ``MANAGED-ID``); what a device sends back of them is taken off before the event is stored.

**Going away**: × removes an attachment for good, not through the trash (AC-9); an event in the trash keeps them for
when it comes back (EC-3); an event deleted for good takes its rows along, and the cleanup round its files (EC-2). An
event moved to a calendar of iCloud, Google or a CalDAV server leaves its attachments behind (the page warns first).
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import re
import secrets
import threading
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ..errors import error
from ..models import Account, Attachment, Calendar, Event, EventChange, utcnow
from . import backups, calendars, content, media, settings_service

logger = logging.getLogger("nextime.attachments")

MB = 1024 * 1024
VIEW = ".view"
THUMB = ".thumb"
WORK_PREFIX = ".up-"
#: How long an upload for an event never made, and a work file, may stay.
PENDING_AGE = timedelta(days=1)
_ID = re.compile(r"^[A-Za-z0-9_-]{16,32}$")
_CHUNK = MB
NAME_MAX = 255
#: Putting files in place and writing the row, one upload at a time (the same id twice at once).
_placing = threading.Lock()


class AttachmentError(Exception):
    def __init__(self, code: str, text: str, status: int, **values: Any) -> None:
        super().__init__(text)
        self.code = code
        self.text = text
        self.status = status
        self.values = values

    def http(self) -> Exception:
        return error(self.code, self.text, self.status, **self.values)


def root() -> Path:
    return backups.media_root()


def path_of(attachment_id: str, suffix: str = "") -> Path:
    if not _ID.match(attachment_id):
        raise ValueError("not an attachment id")
    return root() / f"{attachment_id}{suffix}"


def valid_id(attachment_id: str) -> bool:
    return bool(_ID.match(attachment_id or ""))


_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


def clean_name(name: str | None) -> str:
    """The name as a person reads it: the last part of a path, one line, at most 255 characters (EC-8)."""
    base = re.split(r"[\\/]", name or "")[-1]
    plain = " ".join(_CONTROL.sub(" ", base).split())
    return plain[:NAME_MAX] or "file"


# --- Who may --------------------------------------------------------------------------------------------------


def _own_calendar(found: calendars.Access) -> None:
    if found.calendar.source_id is not None:
        raise AttachmentError("attachments_own_only", "Attachments are kept at events of nextime's calendars only.",
                              409)


def _writable_event(db: Session, account: Account, event_id: int) -> tuple[Event, calendars.Access]:
    from . import events

    try:
        row, found = events.find(db, account, event_id, for_writing=True)
    except HTTPException as exc:
        code = exc.detail.get("code", "not_found") if isinstance(exc.detail, dict) else "not_found"
        raise AttachmentError(code, "Not found." if code == "not_found" else "This event cannot take files.",
                              exc.status_code) from None
    _own_calendar(found)
    return row, found


def readable(db: Session, account: Account, row: Attachment) -> bool:
    """Whoever sees the event's calendar, but not "busy only" and not while the event is in the trash (AC-3, EC-13);
    a pending upload only its uploader."""
    if row.event_id is None:
        return row.uploaded_by == account.id
    event = db.get(Event, row.event_id)
    if event is None or event.deleted_at is not None:
        return False
    found = calendars.access(db, account.id, event.calendar_id)
    return found is not None and found.right != "busy" and found.calendar.source_id is None


def get_for(db: Session, account: Account, attachment_id: str) -> Attachment:
    row = db.get(Attachment, attachment_id) if valid_id(attachment_id) else None
    if row is None or not readable(db, account, row):
        raise error("not_found", "Not found.", 404)
    return row


# --- Coming in ------------------------------------------------------------------------------------------------


def limits(db: Session) -> dict[str, Any]:
    return {"max_mb": settings_service.upload_max_mb(db)}


def _used(db: Session, account_id: int) -> int:
    return content.counts(db, account_id).bytes


@dataclass
class Upload:
    attachment_id: str
    event_id: int | None
    pending_ref: str
    uploader_id: int
    owner_id: int
    name: str
    max_bytes: int
    #: What the owner's storage still takes; ``None`` without a limit (review of TIME-9, finding 3).
    room: int | None = None


def _too_large(name: str, size: int, max_mb: int) -> AttachmentError:
    return AttachmentError("attachment_too_large", "The file is larger than allowed.", 413, name=name, size=size,
                           max_mb=max_mb)


def begin(db: Session, account: Account, *, event_id: int | None, pending_ref: str | None, attachment_id: str,
          name: str | None, declared: int | None) -> Upload | Attachment:
    """Checks an upload before its body is read: the right to change the event, the id, the limit per file, the
    storage. Returns the attachment when it is there already (the same upload sent twice)."""
    if not valid_id(attachment_id):
        raise AttachmentError("invalid_input", "The input is not valid.", 422, fields=["id"])
    owner_id = account.id
    ref = ""
    if event_id is not None:
        event, found = _writable_event(db, account, event_id)
        owner_id = found.calendar.owner_id
    else:
        ref = (pending_ref or "").strip()
        if not ref or len(ref) > 64:
            raise AttachmentError("invalid_input", "The input is not valid.", 422, fields=["ref"])
        made = db.scalar(select(Event).where(Event.created_by == account.id, Event.client_ref == ref))
        if made is not None:
            event, found = _writable_event(db, account, made.id)
            event_id, ref, owner_id = event.id, "", found.calendar.owner_id
    known = db.get(Attachment, attachment_id)
    if known is not None:
        if known.uploaded_by == account.id and (known.event_id == event_id or known.pending_ref == ref and ref):
            return known
        raise AttachmentError("not_found", "Not found.", 404)
    clean = clean_name(name)
    max_mb = settings_service.upload_max_mb(db)
    if declared is not None and declared > max_mb * MB:
        raise _too_large(clean, declared, max_mb)
    room = _room(db, owner_id)
    if room is not None and (declared or 0) > room:
        raise _storage_full()
    return Upload(attachment_id=attachment_id, event_id=event_id, pending_ref=ref, uploader_id=account.id,
                  owner_id=owner_id, name=clean, max_bytes=max_mb * MB, room=room)


def _room(db: Session, owner_id: int) -> int | None:
    """What the owner's storage still takes; ``None`` without a limit. Since konten 1.1.0 nextime has no storage limit
    per account (plan "Einstellungen", E24): a limit an operator set in 0.4.0 stays in its column and counts for
    nothing; the limit per file (``upload_max_mb``) and a full disk still stop an upload."""
    return None


def _storage_full() -> AttachmentError:
    return AttachmentError("storage_limit_reached", "The storage of the account is full.", 507)


def _failed_disk(exc: OSError) -> AttachmentError:
    logger.error("An attachment could not be written reason=%s", errno.errorcode.get(exc.errno or 0, "OSError"))
    return AttachmentError("storage_failed", "The server could not store the file.", 507)


async def receive(upload: Upload, chunks: AsyncIterator[bytes]) -> Path:
    """The body into a work file, counted while it comes: past the limit per file it stops and the work file goes."""
    try:
        root().mkdir(parents=True, exist_ok=True)
        work = root() / f"{WORK_PREFIX}{secrets.token_hex(16)}"
        sink = await asyncio.to_thread(open, work, "wb")
    except OSError as exc:
        raise _failed_disk(exc) from None
    received = 0
    waiting: list[bytes] = []
    try:
        async for chunk in chunks:
            received += len(chunk)
            if received > upload.max_bytes:
                raise _too_large(upload.name, received, upload.max_bytes // MB)
            if upload.room is not None and received > upload.room:
                # Also without a declared length, the storage of the account is counted while the file comes.
                raise _storage_full()
            waiting.append(chunk)
            if sum(map(len, waiting)) >= _CHUNK:
                await asyncio.to_thread(sink.write, b"".join(waiting))
                waiting.clear()
        if waiting:
            await asyncio.to_thread(sink.write, b"".join(waiting))
        await asyncio.to_thread(sink.close)
    except OSError as exc:
        sink.close()
        work.unlink(missing_ok=True)
        raise _failed_disk(exc) from None
    except BaseException:
        sink.close()
        work.unlink(missing_ok=True)
        raise
    return work


def _write(path: Path, data: bytes) -> None:
    temporary = path.with_name(f"{WORK_PREFIX}{secrets.token_hex(8)}")
    temporary.write_bytes(data)
    os.replace(temporary, path)


def _remove_files(ids: Iterable[str]) -> None:
    for attachment_id in ids:
        for suffix in ("", VIEW, THUMB):
            try:
                path_of(attachment_id, suffix).unlink(missing_ok=True)
            except (OSError, ValueError):
                logger.warning("An attachment's file could not be removed attachment=%s", attachment_id)


def _devices_learn(db: Session, event: Event) -> None:
    """The calendar account's devices fetch the event again (its attachments changed, AC-4)."""
    from . import live

    db.add(EventChange(calendar_id=event.calendar_id, href=event.href, deleted=False))
    live.touch_calendar(db, event.calendar_id)


def finish(upload: Upload, work: Path) -> tuple[Attachment, bool]:
    """Prepares the received file (``media.prepare``, in a worker), puts its files in place and writes its row.
    Returns the attachment and whether it is new."""
    from ..db import SessionLocal

    try:
        if work.stat().st_size == 0:
            raise AttachmentError("attachment_empty", "The file is empty.", 400)
        prepared = media.prepare(work)
        name = upload.name
        if prepared.suffix and not name.lower().endswith(prepared.suffix):
            name = clean_name(re.sub(r"\.[A-Za-z0-9]{1,8}$", "", name) + prepared.suffix)
        with _placing, SessionLocal() as db:
            known = db.get(Attachment, upload.attachment_id)
            if known is not None:
                return known, False
            event_id, ref = upload.event_id, upload.pending_ref
            if event_id is None:
                # The event may have been made while the file came (AC-10).
                made = db.scalar(select(Event).where(Event.created_by == upload.uploader_id,
                                                     Event.client_ref == ref))
                if made is not None:
                    event_id, ref = made.id, ""
            event = db.get(Event, event_id) if event_id is not None else None
            if event_id is not None and event is None:
                raise AttachmentError("not_found", "Not found.", 404)
            if event is not None:
                # What held when the upload began is asked again: the right to change the event, a nextime calendar
                # (not moved into iCloud meanwhile), the room in the storage with the real size (review of TIME-9,
                # finding 3).
                uploader = db.get(Account, upload.uploader_id)
                if uploader is None:
                    raise AttachmentError("not_found", "Not found.", 404)
                _event, found = _writable_event(db, uploader, event.id)
                owner_id = found.calendar.owner_id
            else:
                owner_id = upload.owner_id
            room = _room(db, owner_id)
            if room is not None and work.stat().st_size > room:
                raise _storage_full()
            target = path_of(upload.attachment_id)
            try:
                if prepared.redrawn is not None:
                    _write(target, prepared.redrawn)
                    work.unlink(missing_ok=True)
                else:
                    os.replace(work, target)
                size = target.stat().st_size
                stored = size
                for suffix, data in ((VIEW, prepared.view), (THUMB, prepared.thumb)):
                    if data is not None:
                        _write(path_of(upload.attachment_id, suffix), data)
                        stored += len(data)
            except OSError as exc:
                _remove_files([upload.attachment_id])
                raise _failed_disk(exc) from None
            row = Attachment(id=upload.attachment_id, event_id=event_id, uploaded_by=upload.uploader_id,
                             pending_ref=ref, kind=prepared.kind, name=name, mime=prepared.mime, size=size,
                             stored=stored, width=prepared.width, height=prepared.height, pages=prepared.pages,
                             has_view=prepared.view is not None, has_thumb=prepared.thumb is not None,
                             inline=prepared.inline, created_at=utcnow())
            db.add(row)
            if event is not None:
                _devices_learn(db, event)
            db.commit()
            logger.info("Attachment stored event=%s attachment=%s kind=%s size=%s", event_id, row.id, row.kind,
                        row.size)
        return row, True
    finally:
        work.unlink(missing_ok=True)


@dataclass
class Brought:
    """A file brought along by an import or a move (TIME-24), read and prepared before the transaction that makes its
    event: the work file and what the browser gets of it."""

    work: Path
    name: str
    prepared: Any


def bring(work: Path, name: str) -> Brought:
    """Prepares a file an import or a move brings along (in the media worker, outside any transaction)."""
    prepared = media.prepare(work)
    clean = clean_name(name)
    if prepared.suffix and not clean.lower().endswith(prepared.suffix):
        clean = clean_name(re.sub(r"\.[A-Za-z0-9]{1,8}$", "", clean) + prepared.suffix)
    return Brought(work=work, name=clean, prepared=prepared)


def place_brought(db: Session, event: Event, uploader_id: int, brought: Brought) -> str:
    """Puts a brought file in place and adds its row to the caller's transaction; returns its id. The caller commits,
    or removes the files with ``remove_placed`` when the transaction fails, so nothing stays half."""
    attachment_id = secrets.token_hex(16)
    prepared = brought.prepared
    target = path_of(attachment_id)
    root().mkdir(parents=True, exist_ok=True)
    try:
        if prepared.redrawn is not None:
            _write(target, prepared.redrawn)
        else:
            _write(target, brought.work.read_bytes())
        size = target.stat().st_size
        stored = size
        for suffix, data in ((VIEW, prepared.view), (THUMB, prepared.thumb)):
            if data is not None:
                _write(path_of(attachment_id, suffix), data)
                stored += len(data)
    except OSError as exc:
        _remove_files([attachment_id])
        raise _failed_disk(exc) from None
    db.add(Attachment(id=attachment_id, event_id=event.id, uploaded_by=uploader_id, pending_ref="",
                      kind=prepared.kind, name=brought.name, mime=prepared.mime, size=size, stored=stored,
                      width=prepared.width, height=prepared.height, pages=prepared.pages,
                      has_view=prepared.view is not None, has_thumb=prepared.thumb is not None,
                      inline=prepared.inline, created_at=utcnow()))
    return attachment_id


def remove_placed(ids: Iterable[str]) -> None:
    """The files of ``place_brought`` after their transaction failed."""
    _remove_files(ids)


def attach_pending(db: Session, account_id: int, client_ref: str | None, event: Event) -> None:
    """``events.create``: the uploads made for this event before it existed hang themselves at it (AC-10); in a
    calendar of a source they go (attachments only at nextime's own calendars, AC-5)."""
    if not client_ref:
        return
    rows = list(db.scalars(select(Attachment).where(Attachment.uploaded_by == account_id,
                                                    Attachment.pending_ref == client_ref,
                                                    Attachment.event_id.is_(None))))
    if not rows:
        return
    calendar = db.get(Calendar, event.calendar_id)
    if calendar is None or calendar.source_id is not None:
        drop([row.id for row in rows], db)
        return
    for row in rows:
        row.event_id, row.pending_ref = event.id, ""


def drop(ids: list[str], db: Session) -> None:
    """Rows away now, their files once the transaction is through."""
    if not ids:
        return
    db.execute(delete(Attachment).where(Attachment.id.in_(ids)))
    _after_commit(db, ids)


def files_gone(db: Session, ids: list[str]) -> None:
    """The rows went with their event (deleted for good, TIME-23): the files follow once the transaction is through."""
    if ids:
        _after_commit(db, ids)


def _after_commit(db: Session, ids: list[str]) -> None:
    from sqlalchemy import event as orm_event

    def gone(_session: Session) -> None:
        _remove_files(ids)

    orm_event.listen(db, "after_commit", gone, once=True)


def event_moved(db: Session, event: Event, calendar_id: int) -> None:
    """``events.update``: an event moving to a calendar of iCloud, Google or a CalDAV server leaves its attachments
    behind (they cannot go there, TIME-5 AC-7)."""
    calendar = db.get(Calendar, calendar_id)
    if calendar is not None and calendar.source_id is not None:
        drop(list(db.scalars(select(Attachment.id).where(Attachment.event_id == event.id))), db)


# --- Going out, going away ------------------------------------------------------------------------------------


def out(row: Attachment) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "kind": row.kind, "mime": row.mime, "size": row.size,
            "pages": row.pages, "width": row.width, "height": row.height, "view": row.has_view,
            "thumb": row.has_thumb, "inline": row.inline,
            "created_at": row.created_at.isoformat() if row.created_at else None}


def of_event(db: Session, event: Event) -> list[dict[str, Any]]:
    return [out(row) for row in db.scalars(select(Attachment).where(Attachment.event_id == event.id)
                                           .order_by(Attachment.created_at, Attachment.id))]


def detail(db: Session, event: Event, found: calendars.Access) -> dict[str, Any]:
    """What the side panel shows: the attachments (not for "busy only"), whether files may be added, the limit."""
    own = found.calendar.source_id is None
    shown = own and found.right != "busy"
    return {"attachments": of_event(db, event) if shown else [],
            "attachments_allowed": own and found.writable,
            "attachment_max_mb": settings_service.upload_max_mb(db),
            "remote_attachments": [] if own or found.right == "busy" else remote_list(event)}


def remove(db: Session, account: Account, attachment_id: str) -> None:
    """× at an attachment (AC-9): the owner and "edit", for good; a pending one its uploader."""
    row = db.get(Attachment, attachment_id) if valid_id(attachment_id) else None
    if row is None:
        raise error("not_found", "Not found.", 404)
    if row.event_id is None:
        if row.uploaded_by != account.id:
            raise error("not_found", "Not found.", 404)
        drop([row.id], db)
        db.commit()
        return
    if not readable(db, account, row):
        raise error("not_found", "Not found.", 404)
    event, _found = _writable_event_for_remove(db, account, row.event_id)
    drop([row.id], db)
    _devices_learn(db, event)
    db.commit()
    logger.info("Attachment removed event=%s attachment=%s", event.id, attachment_id)


def _writable_event_for_remove(db: Session, account: Account, event_id: int) -> tuple[Event, calendars.Access]:
    try:
        return _writable_event(db, account, event_id)
    except AttachmentError as exc:
        raise exc.http() from None


# --- The calendar account (AC-4) --------------------------------------------------------------------------------


def _param(value: str) -> str:
    return '"' + value.replace('"', "'").replace("\\", "/") + '"'


def dav_lines(db: Session, access: calendars.Access, base: str) -> dict[int, str]:
    """``ATTACH`` lines of the attachments of every event of an own calendar, for the calendar account."""
    if access.calendar.source_id is not None or access.right == "busy":
        return {}
    found: dict[int, str] = {}
    for row in db.scalars(select(Attachment).join(Event, Event.id == Attachment.event_id)
                          .where(Event.calendar_id == access.calendar.id).order_by(Attachment.created_at)):
        name = re.sub(r"[\x00-\x1f\x7f;:,]", "_", row.name)[:NAME_MAX]
        line = (f"ATTACH;MANAGED-ID={row.id};FILENAME={_param(name)};FMTTYPE={row.mime};SIZE={row.size}:"
                f"{base}/dav/attachments/{row.id}")
        found[row.event_id or 0] = found.get(row.event_id or 0, "") + _fold(line) + "\r\n"
    return found


def _fold(line: str) -> str:
    """Lines of 75 octets at most (RFC 5545 3.1), cut between characters."""
    out, current = [], ""
    for char in line:
        if len((current + char).encode("utf-8")) > (75 if not out else 74):
            out.append(current)
            current = char
        else:
            current += char
    out.append(current)
    return "\r\n ".join(out)


_ATTACH_LINE = re.compile(r"^ATTACH[;:][^\r\n]*(?:\r?\n[ \t][^\r\n]*)*\r?\n", re.MULTILINE | re.IGNORECASE)


def drop_dav_lines(text: str) -> str:
    """What a device sends back of nextime's own attachments comes off before the event is stored."""
    if "/dav/attachments/" not in text.replace("\r\n ", "").replace("\n ", "").replace("\r\n\t", ""):
        return text

    def ours(found: re.Match[str]) -> str:
        unfolded = re.sub(r"\r?\n[ \t]", "", found.group(0))
        return "" if "/dav/attachments/" in unfolded else found.group(0)

    return _ATTACH_LINE.sub(ours, text)


def dav_file(db: Session, account: Account, attachment_id: str) -> Attachment | None:
    """The attachment a device asks for at ``/dav/attachments/<id>``, with the account's right to it."""
    row = db.get(Attachment, attachment_id) if valid_id(attachment_id) else None
    if row is None or row.event_id is None or not readable(db, account, row):
        return None
    return row


# --- Attachments of iCloud, Google and CalDAV events (AC-5, EC-14) -----------------------------------------------


def remote_list(event: Event) -> list[dict[str, Any]]:
    """The attachments an event of a source carries (``ATTACH``): shown with their name; never added to."""
    from . import ical

    try:
        parsed = ical.parse(event.ical)
    except ical.IcalError:
        return []
    found = []
    master = ical.master_of(parsed)
    values = master.get("ATTACH")
    if values is None:
        return []
    for index, value in enumerate(values if isinstance(values, list) else [values]):
        params = getattr(value, "params", {}) or {}
        inline = str(params.get("VALUE", "")).upper() == "BINARY" or str(params.get("ENCODING", "")).upper() == \
            "BASE64"
        address = "" if inline else str(value)
        name = str(params.get("FILENAME") or params.get("X-FILENAME") or params.get("X-APPLE-FILENAME") or "")
        if not name:
            name = address.rstrip("/").rsplit("/", 1)[-1].split("?")[0] if address else "file"
        size = params.get("SIZE")
        found.append({"index": index, "name": clean_name(name), "mime": str(params.get("FMTTYPE", "")),
                      "size": int(size) if str(size or "").isdigit() else None,
                      "link": address if address.lower().startswith("https://") else ""})
    return found


def work_file() -> Path:
    """A new work file next to the attachments (the cleanup round takes any that stay)."""
    root().mkdir(parents=True, exist_ok=True)
    return root() / f"{WORK_PREFIX}{secrets.token_hex(16)}"


def remote_file(db: Session, account: Account, event_id: int, index: int) -> tuple[Path, str, str]:
    """An attachment of an event of a source, through nextime: an embedded one from the event, one on the source's
    own server with the source's access, in a work file the caller sends and removes (never all of it in memory,
    review of TIME-9, finding 2). ``AttachmentError`` ``remote_unavailable`` when it cannot be had (EC-14)."""
    from . import events, ical, remote_files

    try:
        event, found = events.find(db, account, event_id, for_writing=False)
    except HTTPException:
        raise AttachmentError("not_found", "Not found.", 404) from None
    if found.calendar.source_id is None or found.right == "busy":
        raise AttachmentError("not_found", "Not found.", 404)
    listed = remote_list(event)
    if not 0 <= index < len(listed):
        raise AttachmentError("not_found", "Not found.", 404)
    entry = listed[index]
    master = ical.master_of(ical.parse(event.ical))
    values = master.get("ATTACH")
    value = (values if isinstance(values, list) else [values])[index]
    params = getattr(value, "params", {}) or {}
    if str(params.get("VALUE", "")).upper() == "BINARY" or str(params.get("ENCODING", "")).upper() == "BASE64":
        try:
            data = value.to_ical()
            import base64

            decoded = base64.b64decode(data)
        except Exception:  # noqa: BLE001 - a broken embedded file is one that cannot be had
            raise AttachmentError("remote_unavailable", "The attachment cannot be fetched right now.", 502) from None
        work = work_file()
        work.write_bytes(decoded)
        return work, entry["name"], entry["mime"]
    max_bytes = settings_service.upload_max_mb(db) * MB
    work = work_file()
    try:
        remote_files.fetch(db, found.calendar, str(value), max_bytes, work)
    except BaseException:
        work.unlink(missing_ok=True)
        raise
    return work, entry["name"], entry["mime"]


# --- Storage, cleaning up -------------------------------------------------------------------------------------


def _count(db: Session, account_id: int) -> content.Counts:
    own = select(Calendar.id).where(Calendar.owner_id == account_id)
    size = db.scalar(select(func.coalesce(func.sum(Attachment.stored), 0)).join(Event, Event.id == Attachment.event_id)
                     .where(Event.calendar_id.in_(own))) or 0
    pending = db.scalar(select(func.coalesce(func.sum(Attachment.stored), 0)).where(
        Attachment.event_id.is_(None), Attachment.uploaded_by == account_id)) or 0
    return content.Counts(bytes=int(size) + int(pending))


content.register_counter(_count)


def sweep(now: Any = None) -> int:
    """Uploads for events never made (after a day), files without a row (an event or account deleted for good, an
    upload that broke off), work files of uploads that never ended."""
    from ..db import SessionLocal

    now = now or utcnow()
    with SessionLocal() as db:
        stale = list(db.scalars(select(Attachment.id).where(Attachment.event_id.is_(None),
                                                            Attachment.created_at < now - PENDING_AGE)))
        if stale:
            drop(stale, db)
            db.commit()
        known = set(db.scalars(select(Attachment.id)))
    removed = 0
    folder = root()
    if not folder.is_dir():
        return 0
    for entry in folder.iterdir():
        if not entry.is_file() or entry.is_symlink():
            continue
        if entry.name.startswith(WORK_PREFIX):
            try:
                if now.timestamp() - entry.stat().st_mtime > PENDING_AGE.total_seconds():
                    entry.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                pass
            continue
        base = entry.name.split(".", 1)[0]
        try:
            young = now.timestamp() - entry.stat().st_mtime < 3600
        except OSError:
            continue
        # A file of an upload in progress is younger than its row: an hour of grace.
        if _ID.match(base) and base not in known and not young:
            entry.unlink(missing_ok=True)
            removed += 1
    if removed:
        logger.info("Attachment files removed count=%s", removed)
    return removed


def register() -> None:
    from . import cleanup

    cleanup.register("attachments", sweep)
