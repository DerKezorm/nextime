"""Backups over HTTP; operator only."""

from __future__ import annotations

import base64
import binascii
import logging
from dataclasses import asdict
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..config import server_zone
from ..db import SessionLocal
from ..deps import OperatorAccount, confirm_operator
from ..errors import error
from ..services import backups, settings_service

logger = logging.getLogger("nextime.backups")

router = APIRouter(prefix="/api/backups", tags=["backups"])
BackupName = Annotated[str, PathParam(max_length=64, pattern=backups.NAME.pattern)]


class CreateIn(BaseModel):
    note: str = Field(default="", max_length=200)


def _fail(exc: backups.BackupError) -> Exception:
    return error(exc.code, exc.text, 404 if exc.code == "not_found" else 400)


@router.get("")
def listing(_operator: OperatorAccount) -> list[dict[str, Any]]:
    return [asdict(entry) for entry in backups.entries()]


@router.get("/summary", summary="Rhythm, last backup, how many are kept, a failure, whether one is running")
def summary(_operator: OperatorAccount) -> dict[str, Any]:
    """The box "Backups" on the page "Server" (TIME-1 AC-8, EC-7): the time is the server's, and so is its zone."""
    with SessionLocal() as db:
        every = backups.schedule(db)
        at = backups.time_of_day(db)
        keep = backups.keep(db)
        failure = settings_service.get(db, "backup_failure")
    listed = backups.entries()
    last = listed[0] if listed else None
    return {
        "schedule": every,
        "time": at,
        "zone": server_zone().name,
        "keep": keep,
        "kept": sum(1 for entry in listed if entry.kind in backups.AUTOMATIC_KINDS and not entry.uploaded),
        "count": len(listed),
        "last": {"created": last.created, "size": last.size} if last else None,
        "failure": failure if isinstance(failure, dict) else None,
        "running": backups.running(),
    }


@router.post("", status_code=201)
def create(body: CreateIn, _operator: OperatorAccount) -> dict[str, str]:
    """One at a time: a second click while a backup is being made makes none (TIME-1 EC-15)."""
    try:
        made = backups.create_now(kind=backups.MANUAL, note=body.note)
    except backups.BackupError as exc:
        raise error(exc.code, "The backup could not be written. The older backups stay.", 507) from exc
    if made is None:
        raise error("backup_running", "A backup is being made already.", 409)
    return {"name": made.name}


#: The password for an upload: the body is the archive itself, so the password comes in a header, as base64 of its
#: UTF-8 (a header carries no umlauts).
PASSWORD_HEADER = "x-nextime-password"


def _header_password(request: Request) -> str:
    raw = request.headers.get(PASSWORD_HEADER, "")
    try:
        return base64.b64decode(raw, validate=True).decode("utf-8")[:200] if raw else ""
    except (binascii.Error, UnicodeDecodeError):
        return ""


@router.post("/upload", status_code=201, summary="Bring in an archive from elsewhere (the body is the ZIP); needs "
             "the password again")
async def upload(request: Request, operator: OperatorAccount) -> dict[str, str]:
    """For a move to a new server: the archive joins the list, to be checked and restored like any other. Asks for the
    password first: a restore of it brings back other passwords and keys, and a stolen session must not lay one out."""
    with SessionLocal() as db:
        confirm_operator(request, db, operator, _header_password(request))
    received = backups.temporary_upload()
    size = 0
    try:
        async with await anyio.open_file(received, "wb") as handle:
            async for chunk in request.stream():
                size += len(chunk)
                await handle.write(chunk)
        if not size:
            raise error("empty", "The file is empty.")
        try:
            name = await anyio.to_thread.run_sync(backups.receive, received)
        except backups.BackupError as exc:
            raise _fail(exc) from exc
    finally:
        received.unlink(missing_ok=True)
    logger.warning("Backup uploaded name=%s by=%s bytes=%s", name, operator.name, size)
    return {"name": name}


@router.post("/{name}/check")
def check(name: BackupName, _operator: OperatorAccount) -> dict[str, Any]:
    try:
        brief = backups.check(name)
    except backups.BackupError as exc:
        raise _fail(exc) from exc
    return {**asdict(brief), "usable": brief.usable}


class PasswordIn(BaseModel):
    #: The operator's password once more; an account from a provider has none and needs none.
    password: str = Field(default="", max_length=200)


@router.post("/{name}/restore", status_code=202)
def restore(name: BackupName, body: PasswordIn, request: Request, operator: OperatorAccount) -> dict[str, Any]:
    """Checks, keeps the current state as a backup, and restarts; the restore happens at the next start. Asks for
    the password again: going back brings back old passwords and keys (review before 1.0.0)."""
    with SessionLocal() as db:
        confirm_operator(request, db, operator, body.password)
    try:
        brief = backups.stage_restore(name)
    except backups.BackupError as exc:
        raise _fail(exc) from exc
    backups.restart_soon()
    return {**asdict(brief), "usable": brief.usable, "restarting": True}


class DownloadIn(BaseModel):
    #: The operator's password once more; an account from a provider has none and needs none.
    password: str = Field(default="", max_length=200)


@router.post("/{name}/download", summary="The archive itself, to keep a copy elsewhere; needs the password again")
def download(name: BackupName, body: DownloadIn, request: Request, operator: OperatorAccount) -> FileResponse:
    """The archive holds everything: the database, every event, ``secret.key``. A stolen session alone must not be
    enough to carry it away, so the password is asked again and counted like a sign-in."""
    with SessionLocal() as db:
        # The archive holds secret.key and the emergency account's password hash and second factor: connected, only
        # the emergency account carries it away.
        confirm_operator(request, db, operator, body.password)
    try:
        path = backups.path_of(name)
    except backups.BackupError as exc:
        raise _fail(exc) from exc
    logger.warning("Backup downloaded name=%s by=%s", name, operator.name)
    return FileResponse(path, media_type="application/zip", filename=name,
                        headers={"Cache-Control": "no-store"})


@router.delete("/{name}", status_code=204)
def delete(name: BackupName, body: PasswordIn, request: Request, operator: OperatorAccount) -> None:
    """Asks for the password again: a stolen session must not throw every copy away (review before 1.0.0)."""
    with SessionLocal() as db:
        confirm_operator(request, db, operator, body.password)
    try:
        backups.remove(name)
    except backups.BackupError as exc:
        raise _fail(exc) from exc
