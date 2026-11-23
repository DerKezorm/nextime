"""Setting up the calendar account on devices (TIME-20).

| Method | Path | What |
|---|---|---|
| GET | ``/api/devices`` | the own devices, the address and name to type, HTTPS, the calendars that come along |
| POST | ``/api/devices`` | ``{"name"}``: a device with its password (shown this once) and a setup link (QR, 10 min) |
| DELETE | ``/api/devices/{id}`` | lock a device out |
| GET | ``/api/device-setup/{code}`` | without sign-in: what the setup page on the phone shows |
| GET | ``/api/device-setup/{code}/profile?name=`` | without sign-in: the profile for Apple devices; uses the link |
| POST | ``/api/device-setup/{code}/credentials`` | without sign-in: address, name, password to type (Android) |
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import segno
from fastapi import APIRouter, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import Account, DbSession
from ..errors import error
from ..models import Account as AccountRow
from ..models import Device
from ..services import calendars, devices, settings_service

router = APIRouter(tags=["devices"])


class DeviceIn(BaseModel):
    name: str = Field(max_length=400)


class SetupNameIn(BaseModel):
    name: str | None = Field(default=None, max_length=400)


def base_url(request: Request, db: Session) -> str:
    """The address devices reach nextime under: the public address when set, else this request's (behind a
    trusted reverse proxy that is the outside one, EC-22)."""
    return settings_service.public_url(db) or str(request.base_url).rstrip("/")


def https(request: Request, db: Session) -> bool:
    return base_url(request, db).lower().startswith("https://") and request.url.scheme == "https"


#: Calendars the owner's phone has from their source already (TIME-15 Out of Scope); shared, they come along.
PASSED_BY = ("icloud", "google", "caldav")


def coming_along(db: Session, account: AccountRow) -> list[dict[str, Any]]:
    """The calendars a device gets: own and shared ones with their right (AC-9); hidden ones not (TIME-4 AC-3)."""
    calendars.ensure_seeded(db, account)
    return [{"id": item.calendar.id, "name": item.calendar.name, "color": item.calendar.color,
             "right": item.right} for item, setting in calendars.ordered(db, account.id)
            if not setting.hidden and not (item.right == calendars.OWNER and item.kind in PASSED_BY)]


@router.get("/api/devices", summary="Own devices and what to type into a calendar program")
def list_devices(request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    calendars.ensure_seeded(db, account)
    result = {
        "devices": devices.listing(db, account),
        "https": https(request, db),
        "address": base_url(request, db) + "/dav/",
        "user": account.name,
        "calendars": coming_along(db, account),
        "minutes": devices.SETUP_MINUTES,
    }
    db.commit()
    return result


@router.post("/api/devices", status_code=201, summary="A device with its own password and a setup link")
def create_device(payload: DeviceIn, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    if not https(request, db):
        raise error("https_required", "Calendar programs connect only over HTTPS.", 409)
    device, password, token = devices.create(db, account, payload.name)
    link = f"{base_url(request, db)}/setup/{token}"
    return {
        "device": {"id": device.id, "name": device.name, "client": "", "created_at": device.created_at.isoformat(),
                   "last_used_at": None},
        "password": password,
        "address": base_url(request, db) + "/dav/",
        "user": account.name,
        "code": token,
        "link": link,
        "qr": segno.make(link, error="m").svg_data_uri(scale=4, border=2),
        "minutes": devices.SETUP_MINUTES,
        "devices": devices.listing(db, account),
    }


@router.delete("/api/devices/{device_id}", status_code=204, summary="Lock a device out")
def lock_out(device_id: int, account: Account, db: DbSession) -> None:
    devices.lock_out(db, account, device_id)


@router.get("/api/device-setup/{code}", summary="What the setup page on the phone shows")
def setup_page(code: str, db: DbSession) -> dict[str, Any]:
    row = devices.setup_of(db, code)
    account = db.get(AccountRow, row.account_id)
    device = db.get(Device, row.device_id)
    assert account is not None and device is not None
    return {"device": device.name, "account": account.shown_name, "calendars": coming_along(db, account),
            "expires_at": row.expires_at.isoformat()}


@router.get("/api/device-setup/{code}/profile", summary="The profile for Apple devices (uses the link)")
def setup_profile(code: str, request: Request, db: DbSession,
                  name: str | None = Query(default=None, max_length=400)) -> Response:
    account, device, password = devices.use(db, code, name)
    parts = urlsplit(base_url(request, db))
    secure = parts.scheme == "https"
    body = devices.mobileconfig(host=parts.hostname or "", port=parts.port or (443 if secure else 80), https=secure,
                                base=base_url(request, db), account=account, device=device, password=password,
                                description="nextime")
    return Response(content=body, media_type="application/x-apple-aspen-config",
                    headers={"Content-Disposition": 'attachment; filename="nextime.mobileconfig"',
                             "Cache-Control": "no-store"})


@router.post("/api/device-setup/{code}/credentials", summary="Address, name and password to type (uses the link)")
def setup_credentials(code: str, payload: SetupNameIn, request: Request, db: DbSession) -> dict[str, Any]:
    account, device, password = devices.use(db, code, payload.name)
    return {"address": base_url(request, db) + "/dav/", "user": account.name, "password": password,
            "device": device.name}
