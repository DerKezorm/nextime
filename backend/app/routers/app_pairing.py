"""The browser's side of the nextime app (TIME-29), as nexnotes has it: "iPhone oder iPad verbinden" with its QR code
(AC-1), the page ``/app-connect`` that hands the app a one-time code after signing in (AC-5), and the list of devices
with "Trennen" (AC-4, AC-7). Only with a browser session. The rules are in ``services/appdevices.py``, the app's side
in ``routers/app1.py``.

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

import io
import logging
from typing import Annotated, Any
from urllib.parse import quote, urlsplit

import segno
from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, Field

from ..deps import DbSession, require_account
from ..errors import error
from ..models import Account as AccountRow
from ..services import appdevices, settings_service
from ..services.appdevices import PairingError

logger = logging.getLogger("nextime.app")

router = APIRouter(prefix="/api", tags=["app"])

#: The scheme the app listens to: the camera opens the QR code's link right in nextime.
CONNECT_LINK = "nextime://connect"
CONNECTED_LINK = "nextime://connected"

PairingId = Annotated[str, Path(min_length=1, max_length=32)]


def browser_account(request: Request, account: Annotated[AccountRow, Depends(require_account)]) -> AccountRow:
    """The signed-in account of a browser: a token makes no pairings and disconnects nobody here."""
    if request.headers.get("authorization", "")[:7].lower() == "bearer ":
        raise error("browser_only", "Only in the browser.", 403)
    return account


Browser = Annotated[AccountRow, Depends(browser_account)]


def _fail(exc: PairingError) -> Exception:
    return error(exc.code, exc.text, exc.status, **exc.values)


class PairingIn(BaseModel):
    #: Where the browser sees nextime now. Counts only when the operator has set no public address; that one wins.
    address: str = Field(default="", max_length=600)


def _address(db: DbSession, from_browser: str, request: Request) -> str:
    """The public address from the settings wins: whoever opens nextime at home by its IP address still wants the app
    to know the one that works on the way."""
    for candidate in (settings_service.public_url(db), from_browser.strip().rstrip("/"),
                      str(request.base_url).rstrip("/")):
        parts = urlsplit(candidate)
        if parts.scheme in ("http", "https") and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    raise error("app_address_missing", "nextime does not know its address. The operator sets it.", 422)


def _qr_svg(link: str) -> str:
    """Dark on white with a quiet zone, readable on a dark page too; with a ``viewBox`` so that it scales."""
    out = io.BytesIO()
    segno.make(link, error="m").save(out, kind="svg", scale=4, dark="#14141a", light="#ffffff", border=4,
                                     xmldecl=False, svgns=True, omitsize=True)
    return out.getvalue().decode("utf-8")


@router.post("/app-pairing", status_code=201, summary="A QR code for the app (valid 10 minutes, one device)")
def pair(account: Browser, db: DbSession, request: Request, body: PairingIn | None = None) -> dict[str, Any]:
    address = _address(db, body.address if body else "", request)
    try:
        made = appdevices.prepare(db, account)
    except PairingError as exc:
        raise _fail(exc) from exc
    link = (f"{CONNECT_LINK}?server={quote(address, safe='')}&pairing={quote(made.id, safe='')}"
            f"&secret={quote(made.secret, safe='')}")
    logger.info("Pairing made for the app")
    return {"id": made.id, "secret": made.secret, "expires_at": appdevices.iso(made.expires_at), "link": link,
            "address": address, "qr_svg": _qr_svg(link)}


@router.get("/app-pairing/{pairing_id}", summary="Whether the app used the QR code")
def pairing_state(pairing_id: PairingId, account: Browser, db: DbSession) -> dict[str, Any]:
    try:
        state, device = appdevices.state(db, pairing_id, account)
    except PairingError as exc:
        raise _fail(exc) from exc
    return {"state": state, "device": appdevices.device_out(device) if device is not None else None}


@router.delete("/app-pairing/{pairing_id}", status_code=204, summary="Withdraw a QR code")
def withdraw(pairing_id: PairingId, account: Browser, db: DbSession) -> Response:
    try:
        appdevices.withdraw(db, pairing_id, account)
    except PairingError as exc:
        raise _fail(exc) from exc
    return Response(status_code=204)


class ConnectIn(BaseModel):
    state: str = Field(max_length=200)


@router.post("/app-connect", summary="Connect the app that opened /app-connect: a one-time code for it")
def connect(body: ConnectIn, account: Browser, db: DbSession) -> dict[str, str]:
    """"Verbinden" on the page the app opened in its sign-in sheet (AC-5): the browser goes on to the link, which
    hands the code (five minutes, once) to the app."""
    try:
        state = appdevices.clean_state(body.state)
        code = appdevices.make_code(db, account, state)
    except PairingError as exc:
        raise _fail(exc) from exc
    logger.info("Code made for the app")
    return {"link": f"{CONNECTED_LINK}?code={quote(code, safe='')}&state={quote(state, safe='')}"}


@router.get("/app-devices", summary="The own connected iPhones and iPads")
def own_devices(account: Browser, db: DbSession) -> list[dict[str, Any]]:
    return [{**appdevices.device_out(row), "push": bool(row.push_ticket)}
            for row in appdevices.of_account(db, account.id)]


@router.delete("/app-devices/{device_id}", status_code=204, summary="Disconnect a device")
def disconnect(device_id: int, account: Browser, db: DbSession) -> Response:
    if not appdevices.disconnect(db, account.id, device_id):
        raise error("not_found", "Not found.", 404)
    return Response(status_code=204)
