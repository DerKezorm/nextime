"""The nextime app for iPhone and iPad (TIME-29): the devices of an account, as the rest of nextime needs them. Since
the shared module geraete 0.1.0 the rules live in ``vendor/nexgeraete`` and its ``Store`` over nextime's tables
(``services/geraete.py``); this file keeps the calls the rest of nextime and the old routes make.

**A device is a key** (``ntd_…``, a row of ``app_devices``) with its name, its kind (``ios``, ``ipados``, ``mac``)
and the way it came in. It acts as its account in ``/api/app/1``, never with more rights. It goes when its owner
disconnects it (in the browser or in the app), with "sign out everywhere", a new password, "disconnect all", the
operator's reset of the second factor with its devices, after 365 days without use (G27) or with the purge of its
account. While the account is blocked or deleted with its days to come back the device rests: it hears
``account_blocked`` and works again afterwards without pairing anew (G26). A key an older version deleted with a block
left its hash in ``app_gone_devices``; it hears ``account_blocked`` while the account is out (G42).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, AppDevice
from ..vendor import nexgeraete
from ..vendor.nexgeraete import DeviceRefused
from . import geraete

logger = logging.getLogger("nextime.app")

#: The prefix of a device's key.
PREFIX = geraete.CONFIG.token_prefix
#: The longest relay ticket taken.
TICKET_MAX = nexgeraete.push.TICKET_MAX


def iso(value: datetime | None) -> str | None:
    return nexgeraete.devices.iso(value)


def device_out(row: AppDevice) -> dict[str, Any]:
    """A device as the old routes showed it (``/api/app-devices``, ``/api/app/1/devices``)."""
    return {"id": row.id, "name": row.name, "kind": row.kind or "ios", "created_at": iso(row.created_at),
            "last_seen_at": iso(row.last_used_at)}


def make_device(db: Session, account: Account, name: str | None, kind: str, via: str = nexgeraete.VIA_QR
                ) -> tuple[str, AppDevice]:
    """A device of the account as a pairing makes one (the module's ``devices.add``); its key, once."""
    data = geraete.Store(db)
    holder = data.holder(account.id)
    assert holder is not None
    device, token = nexgeraete.devices.add(data, geraete.CONFIG, holder, name=name, kind=kind, via=via,
                                           now=geraete.now())
    db.commit()
    row = db.get(AppDevice, device.id)
    assert row is not None
    return token, row


def of_account(db: Session, account_id: int) -> list[AppDevice]:
    return list(db.scalars(select(AppDevice).where(AppDevice.account_id == account_id).order_by(AppDevice.id)))


def disconnect(db: Session, account_id: int, device_id: int) -> bool:
    """The device has no access from this moment on (AC-4); another account's: False."""
    try:
        nexgeraete.devices.disconnect(geraete.Store(db), geraete.CONFIG, account_id, device_id)
    except DeviceRefused:
        db.rollback()
        return False
    return True


def end_all(db: Session, account_id: int) -> None:
    """Every device of the account goes (a new password, "sign out everywhere", "disconnect all", the purge). Never
    for a block or a deletion: then they rest (G26). The caller commits."""
    gone = geraete.remove_all(db, account_id)
    if gone:
        logger.info("App devices of an account disconnected count=%s", gone)


@dataclass
class Caller:
    """Who calls ``/api/app/1``: the account and its device, both detached, and the module's view of them."""

    account: Account
    device: AppDevice
    found: nexgeraete.Caller


class Refusal(Exception):
    def __init__(self, refused: DeviceRefused) -> None:
        super().__init__(refused.code)
        self.code = refused.public_code
        self.refused = refused


def authenticate(db: Session, token: str | None, client: nexgeraete.Client | None = None) -> Caller:
    """The device behind a key (the module's ``devices.authenticate``, G12); else ``Refusal``: ``account_blocked``
    (blocked or deleted, the device rests), ``device_disconnected``, ``second_factor_required``, ``app_disabled``."""
    data = geraete.Store(db)
    try:
        found = nexgeraete.devices.authenticate(data, geraete.CONFIG, token, client=client, now=geraete.now())
    except DeviceRefused as refused:
        db.rollback()
        raise Refusal(refused) from None
    account = db.get(Account, int(found.holder.id))
    row = db.get(AppDevice, int(found.device.id))
    assert account is not None and row is not None
    db.expunge(account)
    db.expunge(row)
    return Caller(account=account, device=row, found=found)


def server_id() -> str:
    """A fixed mark of this server (``me.server_id``): a keyed hash, nothing to learn from it."""
    from ..security import server_key

    return hmac.new(server_key(), b"nextime-server-id", hashlib.sha256).hexdigest()[:16]


def forget() -> None:
    """For the tests. Since the module the devices brought in over iCloud are counted in the database (G9); nothing
    is kept in memory any more."""
