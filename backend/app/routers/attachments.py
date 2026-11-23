"""Attachments (TIME-9) over HTTP: uploading, the files themselves, removing, and the attachments of events from
iCloud and CalDAV. The rules are in ``services/attachments.py``; this module adds the headers a file goes out with.

| Method | Path | What |
|---|---|---|
| POST | ``/api/attachments?event_id=&id=&name=`` | a file (the body) at an event; ``ref=`` for one being made |
| GET | ``/api/attachments/{id}/file`` | the file; ``?download=1`` always as a download |
| GET | ``/api/attachments/{id}/view`` | a picture as every browser shows it (WebP), a HEIC too |
| GET | ``/api/attachments/{id}/thumb`` | the preview (pictures, the first page of a PDF) |
| DELETE | ``/api/attachments/{id}`` | removed for good (owner, "edit") |
| GET | ``/api/events/{id}/remote-attachments/{index}`` | an attachment of an iCloud or CalDAV event, through nextime |

**Nothing an attachment holds runs in the page's context** (EC-6), as in nexnotes: every file carries
``X-Content-Type-Options: nosniff`` and a policy of its own (``default-src 'none'`` and ``sandbox``; a PDF without
``sandbox``, which Chrome's viewer refuses), and only what the server drew anew (pictures) or a PDF may be shown in the
page (``inline``). HTML, SVG and everything else come as a download, whatever their name says; the name goes out only
as ``filename*`` (UTF-8, percent-encoded) and a plain ASCII stand-in.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, Query, Request, Response
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect

from ..db import SessionLocal
from ..deps import Account, DbSession
from ..errors import error
from ..models import Attachment
from ..services import attachments

logger = logging.getLogger("nextime.attachments")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    attachments.register()
    yield


router = APIRouter(tags=["attachments"], lifespan=lifespan)

#: The policy of a file shown in the page or opened in its own tab: nothing of it runs.
SANDBOXED = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; sandbox; frame-ancestors 'self'"
#: A PDF in the browser's own viewer: no scripts of the page, but no ``sandbox`` (Chrome would not show it).
PDF_POLICY = "default-src 'none'; object-src 'self'; img-src 'self' data: blob:; style-src 'unsafe-inline'; " \
             "frame-ancestors 'self'"
#: An attachment's content never changes under its id.
CACHE = "private, max-age=31536000, immutable"


def _ascii(name: str) -> str:
    plain = re.sub(r"[^A-Za-z0-9._ -]", "_", name).strip() or "file"
    return plain[:150]


def disposition(kind: str, name: str) -> str:
    return f"{kind}; filename=\"{_ascii(name)}\"; filename*=UTF-8''{quote(name, safe='')}"


def headers_for(name: str, *, shown: bool, pdf: bool = False) -> dict[str, str]:
    return {
        "Content-Disposition": disposition("inline" if shown else "attachment", name),
        "Content-Security-Policy": PDF_POLICY if pdf and shown else SANDBOXED,
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": CACHE,
    }


def _send(path: Path, mime: str, *, shown: bool, name: str, pdf: bool = False) -> FileResponse:
    if not path.is_file():
        raise error("not_found", "Not found.", 404)
    # Whatever is only downloaded goes out as bytes, never as a type a browser would render.
    return FileResponse(path, media_type=mime if shown else "application/octet-stream",
                        headers=headers_for(name, shown=shown, pdf=pdf))


@router.post("/api/attachments", status_code=201, summary="Upload a file to an event (the file as the body)")
async def upload(
    request: Request,
    response: Response,
    account: Account,
    attachment_id: Annotated[str, Query(alias="id", max_length=32)],
    event_id: Annotated[int | None, Query()] = None,
    ref: Annotated[str | None, Query(max_length=64)] = None,
    name: Annotated[str, Query(max_length=2000)] = "",
) -> dict[str, Any]:
    declared_text = request.headers.get("content-length")
    declared = int(declared_text) if declared_text and declared_text.isdigit() else None
    try:
        with SessionLocal() as db:
            started = attachments.begin(db, account, event_id=event_id, pending_ref=ref, attachment_id=attachment_id,
                                        name=name, declared=declared)
        if isinstance(started, Attachment):
            response.status_code = 200
            return attachments.out(started)
        work = await attachments.receive(started, request.stream())
        row, new = await asyncio.to_thread(attachments.finish, started, work)
    except attachments.AttachmentError as exc:
        raise exc.http() from exc
    except ClientDisconnect:
        # The connection went while the file came (EC-11): nothing was kept, the page offers to upload again.
        logger.info("An upload broke off")
        raise error("upload_broken", "The upload broke off.", 400) from None
    if not new:
        response.status_code = 200
    return attachments.out(row)


@router.get("/api/attachments/{attachment_id}/file", summary="The file of an attachment")
def file(attachment_id: str, account: Account, db: DbSession,
         download: Annotated[bool, Query()] = False) -> FileResponse:
    row = attachments.get_for(db, account, attachment_id)
    return _send(attachments.path_of(row.id), row.mime, shown=row.inline and not download, name=row.name,
                 pdf=row.mime == "application/pdf")


@router.get("/api/attachments/{attachment_id}/view", summary="The picture as every browser shows it")
def view(attachment_id: str, account: Account, db: DbSession) -> FileResponse:
    row = attachments.get_for(db, account, attachment_id)
    if not row.has_view:
        if row.kind != "image" or not row.inline:
            raise error("not_found", "Not found.", 404)
        return _send(attachments.path_of(row.id), row.mime, shown=True, name=row.name)
    return _send(attachments.path_of(row.id, attachments.VIEW), "image/webp", shown=True, name=row.name + ".webp")


@router.get("/api/attachments/{attachment_id}/thumb", summary="The preview of an attachment")
def thumb(attachment_id: str, account: Account, db: DbSession) -> FileResponse:
    row = attachments.get_for(db, account, attachment_id)
    if not row.has_thumb:
        raise error("not_found", "Not found.", 404)
    return _send(attachments.path_of(row.id, attachments.THUMB), "image/webp", shown=True, name=row.name + ".webp")


@router.delete("/api/attachments/{attachment_id}", status_code=204, summary="Remove an attachment for good")
def remove(attachment_id: str, account: Account, db: DbSession) -> None:
    attachments.remove(db, account, attachment_id)


@router.get("/api/events/{event_id}/remote-attachments/{index}",
            summary="An attachment of an event from iCloud or a CalDAV server")
def remote(event_id: int, index: int, account: Account, db: DbSession) -> Response:
    try:
        work, name, _mime = attachments.remote_file(db, account, event_id, index)
    except attachments.AttachmentError as exc:
        raise exc.http() from exc
    # Never shown in the page: whatever the source holds goes out as a download, from the work file, which goes
    # after it was sent.
    return FileResponse(work, media_type="application/octet-stream",
                        headers=headers_for(name, shown=False) | {"Cache-Control": "private, no-store"},
                        background=BackgroundTask(work.unlink, missing_ok=True))
