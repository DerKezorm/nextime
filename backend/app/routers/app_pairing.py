"""The browser's side of the nextime app (TIME-29).

Since the shared module geraete 0.1.0 the page's cards call the module's browser routes (``module_router`` under
``/api/geraete``: pairing with QR code and code to type, ``/app-connect``, the devices with renaming, the kinds of
notification per device, the operator's view of an account). The routes of before stay here as thin shells over the
module, with their answers of before (the old QR link ``nextime://connect?server=…&pairing=…&secret=…``, ``link`` from
``/app-connect``, the list of devices as a list). Only with a browser session.

| Method | Path | What |
|---|---|---|
| POST | ``/api/app-pairing`` | a QR code for one device, ten minutes |
| GET | ``/api/app-pairing/{id}`` | whether the app used it |
| DELETE | ``/api/app-pairing/{id}`` | withdraw it |
| POST | ``/api/app-connect`` | "Verbinden" on the page the app opened: the link with the one-time code |
| GET | ``/api/app-devices`` | the own paired devices |
| DELETE | ``/api/app-devices/{id}`` | disconnect one |
"""

from __future__ import annotations

import logging
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field

from ..deps import DbSession, require_account, require_operator
from ..errors import error
from ..models import Account as AccountRow
from ..models import AppDevice
from ..services import appdevices, geraete, settings_service
from ..vendor import nexgeraete
from ..vendor.nexgeraete import DeviceRefused
from ..vendor.nexgeraete import api as geraete_api
from ..vendor.nexgeraete import qr as geraete_qr

logger = logging.getLogger("nextime.app")

router = APIRouter(prefix="/api", tags=["app"])

#: The scheme the app listens to: the camera opens the QR code's link right in nextime.
CONNECT_LINK = "nextime://connect"

PairingId = Annotated[str, Path(min_length=1, max_length=32)]


def browser_account(request: Request, account: Annotated[AccountRow, Depends(require_account)]) -> AccountRow:
    """The signed-in account of a browser: a token makes no pairings and disconnects nobody here."""
    if request.headers.get("authorization", "")[:7].lower() == "bearer ":
        raise error("browser_only", "Only in the browser.", 403)
    return account


Browser = Annotated[AccountRow, Depends(browser_account)]


def _fail(db: DbSession, refused: DeviceRefused) -> HTTPException:
    db.rollback()
    return HTTPException(status_code=refused.status, detail=refused.as_dict(), headers=refused.headers)


def public_address(request: Request) -> str:
    """The public address from the settings: it wins over the browser's (the module's ``address``)."""
    from ..db import SessionLocal

    with SessionLocal() as db:
        return settings_service.public_url(db)


def _address(db: DbSession, from_browser: str, request: Request) -> str:
    """The public address from the settings wins: whoever opens nextime at home by its IP address still wants the app
    to know the one that works on the way."""
    for candidate in (settings_service.public_url(db), from_browser.strip().rstrip("/"),
                      str(request.base_url).rstrip("/")):
        parts = urlsplit(candidate)
        if parts.scheme in ("http", "https") and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    raise error("app_address_missing", "nextime does not know its address. The operator sets it.", 422)


class PairingIn(BaseModel):
    #: Where the browser sees nextime now. Counts only when the operator has set no public address; that one wins.
    address: str = Field(default="", max_length=600)


def _old(device: nexgeraete.Device) -> dict[str, Any]:
    entry = nexgeraete.devices.entry(device)
    return {key: entry[key] for key in ("id", "name", "kind", "created_at", "last_seen_at")}


@router.post("/app-pairing", status_code=201, summary="A QR code for the app (valid 10 minutes, one device)")
def pair(account: Browser, db: DbSession, request: Request, body: PairingIn | None = None) -> dict[str, Any]:
    address = _address(db, body.address if body else "", request)
    data = geraete.Store(db)
    try:
        made = nexgeraete.pairing.start(data, geraete.CONFIG, account.id, address=address, now=geraete.now())
    except DeviceRefused as refused:
        raise _fail(db, refused) from None
    # The link of before: the published app reads ``server``, ``pairing`` and ``secret``.
    link = (f"{CONNECT_LINK}?server={quote(address, safe='')}&pairing={quote(made.pairing.id, safe='')}"
            f"&secret={quote(made.secret, safe='')}")
    logger.info("Pairing made for the app")
    return {"id": made.pairing.id, "secret": made.secret, "code": made.code,
            "expires_at": appdevices.iso(made.pairing.expires_at), "link": link, "address": address,
            "qr_svg": geraete_qr.svg(link)}


@router.get("/app-pairing/{pairing_id}", summary="Whether the app used the QR code")
def pairing_state(pairing_id: PairingId, account: Browser, db: DbSession) -> dict[str, Any]:
    try:
        state, device = nexgeraete.pairing.state(geraete.Store(db), geraete.CONFIG, account.id, pairing_id,
                                                 geraete.now())
    except DeviceRefused as refused:
        raise _fail(db, refused) from None
    return {"state": state, "device": _old(device) if device is not None else None}


@router.delete("/app-pairing/{pairing_id}", status_code=204, summary="Withdraw a QR code")
def withdraw(pairing_id: PairingId, account: Browser, db: DbSession) -> Response:
    try:
        nexgeraete.pairing.withdraw(geraete.Store(db), geraete.CONFIG, account.id, pairing_id)
    except DeviceRefused as refused:
        raise _fail(db, refused) from None
    return Response(status_code=204)


class ConnectIn(BaseModel):
    state: str = Field(max_length=200)


@router.post("/app-connect", summary="Connect the app that opened /app-connect: a one-time code for it")
def connect(body: ConnectIn, account: Browser, db: DbSession) -> dict[str, str]:
    """"Verbinden" on the page the app opened in its sign-in sheet (AC-5): the browser goes on to the link, which
    hands the code (five minutes, once) to the app."""
    try:
        link = nexgeraete.pairing.consent(geraete.Store(db), geraete.CONFIG, account.id, body.state,
                                          now=geraete.now())
    except DeviceRefused as refused:
        raise _fail(db, refused) from None
    logger.info("Code made for the app")
    return {"link": link, "redirect": link}


@router.get("/app-devices", summary="The own connected iPhones and iPads")
def own_devices(account: Browser, db: DbSession) -> list[dict[str, Any]]:
    return [{**appdevices.device_out(row), "push": bool(row.push_ticket)}
            for row in appdevices.of_account(db, account.id)]


@router.delete("/app-devices/{device_id}", status_code=204, summary="Disconnect a device")
def disconnect(device_id: int, account: Browser, db: DbSession) -> Response:
    if db.get(AppDevice, device_id) is None or not appdevices.disconnect(db, account.id, device_id):
        raise error("not_found", "Not found.", 404)
    return Response(status_code=204)


def _operator(account: Annotated[AccountRow, Depends(require_operator)]) -> AccountRow:
    return account


#: The module's browser routes (G2–G28): what the cards "Geräte", "Mitteilungen auf deinen Geräten", the operator's
#: part in Server › Konten and the page ``/app-connect`` call.
module_router = geraete_api.browser_router(
    config=geraete.CONFIG,
    store=geraete.store,
    account=browser_account,
    operator=_operator,
    address=public_address,
    send=geraete.send,
    clock=geraete.now,
    prefix=geraete.BROWSER_PREFIX,
)
