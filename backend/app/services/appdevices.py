"""The nextime app for iPhone and iPad (TIME-29): pairing a device, knowing it again, the devices of an account. As
nexnotes does it (NOTES-24, ``services/devices.py`` there); the interface is ``docs/vertrag-app1.md``.

**A device is a token** (``ntd_…``, a row of ``app_devices``) with its name, its kind (``ios``, ``ipados``, ``mac``)
and, for one that came through iCloud, the device that brought it (``via_id``). It acts as its account in
``/api/app/1``, never with more rights. It does not run out; it goes when its owner disconnects it (in the browser or
in the app), with "sign out everywhere", a new password, a reset second factor, a block or a deletion of the account
(``security.end_all_sessions`` calls ``end_all``).

**Three ways in, one result** (``{token, account, device}``):

* the QR code of "iPhone oder iPad verbinden" (AC-1): a pairing of ten minutes for one device, its id and a secret of
  256 bits in the link. Unknown, wrong, expired and used answer the same (``410 pairing_gone``), and every failure
  counts at the sign-in brake per sender (EC-3);
* the page ``/app-connect`` (AC-5): after signing in as always, "Verbinden" hands a one-time code to the app (five
  minutes, once), which trades it for its token;
* another device of the same account: its token asks for one of its own for the new device.

A token gone with a block or deletion of its account leaves its hash behind (``app_gone_devices``): such a device hears
``account_blocked``, every other unknown token ``device_disconnected`` (EC-1, EC-5).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..models import Account, AppCode, AppDevice, AppGoneDevice, AppPairing, Device, utcnow
from ..security import account_out
from . import live, totp

logger = logging.getLogger("nextime.app")

#: How long a QR code works (AC-1), and a code of ``/app-connect`` (AC-5).
PAIRING_MINUTES = 10
CODE_MINUTES = 5
#: Pairings of one account that may wait at once.
OPEN_AT_MOST = 5
NAME_CHARS = 100
#: What ``device_kind`` may be; ``ios`` when the app names none.
DEVICE_KINDS = ("ios", "ipados", "mac")
#: Devices one account may bring in through a device it has, per hour: a token is no factory of tokens.
SIBLINGS_PER_HOUR = 5
#: What the app sends along to ``/app-connect`` (it gets it back beside the code).
STATE_CHARS = (8, 128)
#: The prefix of a device's token.
PREFIX = "ntd_"
#: How often the last contact is written at most.
USED_EVERY = timedelta(minutes=1)
#: The longest relay ticket taken (the relay's are far shorter).
TICKET_MAX = 2000


class PairingError(Exception):
    def __init__(self, code: str, text: str, status: int, **values: Any) -> None:
        super().__init__(text)
        self.code = code
        self.text = text
        self.status = status
        self.values = values


def gone() -> PairingError:
    return PairingError("pairing_gone", "This code has expired, was used already, or is not valid.", 410)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def device_name(raw: str | None) -> str:
    return "".join(char for char in (raw or "") if char.isprintable()).strip()[:NAME_CHARS] or "iPhone"


def device_kind(raw: str | None) -> str:
    if raw in (None, ""):
        return DEVICE_KINDS[0]
    if raw not in DEVICE_KINDS:
        raise PairingError("invalid_input", "The input is not valid.", 422, fields=["device_kind"])
    return raw


# --- The QR code (AC-1) ---------------------------------------------------------------------------------------------


@dataclass
class Prepared:
    id: str
    secret: str
    expires_at: datetime


def prepare(db: Session, account: Account) -> Prepared:
    """A new pairing of the account; its secret exists in plain text only here."""
    now = utcnow()
    db.execute(delete(AppPairing).where(AppPairing.expires_at < now - timedelta(days=1)))
    waiting = db.scalar(select(func.count()).select_from(AppPairing).where(
        AppPairing.account_id == account.id, AppPairing.used_at.is_(None), AppPairing.expires_at > now)) or 0
    if waiting >= OPEN_AT_MOST:
        raise PairingError("too_many_pairings", "At most 5 codes may wait at once.", 409, max=OPEN_AT_MOST)
    secret = secrets.token_urlsafe(32)
    row = AppPairing(id=secrets.token_urlsafe(12), account_id=account.id, secret_hash=digest(secret), created_at=now,
                     expires_at=now + timedelta(minutes=PAIRING_MINUTES))
    db.add(row)
    db.commit()
    return Prepared(row.id, secret, row.expires_at)


def _own(db: Session, pairing_id: str, account: Account) -> AppPairing:
    row = db.get(AppPairing, pairing_id[:32])
    if row is None or row.account_id != account.id:
        raise PairingError("not_found", "Not found.", 404)
    return row


def state(db: Session, pairing_id: str, account: Account) -> tuple[str, AppDevice | None]:
    """``waiting``, ``used`` or ``expired``, and the device once used."""
    row = _own(db, pairing_id, account)
    if row.used_at is not None:
        return "used", db.get(AppDevice, row.device_id) if row.device_id else None
    return ("expired" if row.expires_at <= utcnow() else "waiting"), None


def withdraw(db: Session, pairing_id: str, account: Account) -> None:
    row = _own(db, pairing_id, account)
    if row.used_at is None:
        db.delete(row)
        db.commit()


def by_secret(db: Session, pairing_id: str, secret: str | None, name: str | None, kind: str | None
              ) -> tuple[str, Account, AppDevice]:
    """The token for the id and secret of the QR code. Compared even without a row, so that an unknown id does not
    answer faster than a wrong secret. A conditional UPDATE, not a look first: of two pickups at the same moment
    exactly one hits."""
    kind = device_kind(kind)
    row = db.get(AppPairing, (pairing_id or "")[:32])
    expected = row.secret_hash if row is not None else digest("")
    right = hmac.compare_digest(expected, digest(secret or ""))
    if row is None or not right or not secret:
        raise gone()
    now = utcnow()
    hit = db.execute(update(AppPairing).where(AppPairing.id == row.id, AppPairing.used_at.is_(None),
                                              AppPairing.expires_at > now).values(used_at=now)).rowcount
    db.commit()
    account = db.get(Account, row.account_id)
    if not hit or account is None or account_out(account):
        raise gone()
    token, device = make_device(db, account, name, kind)
    row.device_id = device.id
    db.commit()
    return token, account, device


# --- The sign-in page (AC-5) ----------------------------------------------------------------------------------------


def clean_state(raw: str | None) -> str:
    value = raw or ""
    low, high = STATE_CHARS
    if not (low <= len(value) <= high) or any(not (char.isascii() and (char.isalnum() or char in "-_")) for char in
                                               value):
        raise PairingError("invalid_input", "The input is not valid.", 422, fields=["state"])
    return value


def make_code(db: Session, account: Account, app_state: str) -> str:
    """The one-time code "Verbinden" on ``/app-connect`` hands to the app; only its hash is kept."""
    now = utcnow()
    db.execute(delete(AppCode).where(AppCode.expires_at < now - timedelta(days=1)))
    code = secrets.token_urlsafe(32)
    db.add(AppCode(code_hash=digest(code), account_id=account.id, state=clean_state(app_state), created_at=now,
                   expires_at=now + timedelta(minutes=CODE_MINUTES)))
    db.commit()
    return code


def by_code(db: Session, code: str | None, name: str | None, kind: str | None) -> tuple[str, Account, AppDevice]:
    kind = device_kind(kind)
    if not code or len(code) > 200:
        raise gone()
    now = utcnow()
    hashed = digest(code)
    hit = db.execute(update(AppCode).where(AppCode.code_hash == hashed, AppCode.used_at.is_(None),
                                           AppCode.expires_at > now).values(used_at=now)).rowcount
    db.commit()
    row = db.get(AppCode, hashed)
    account = db.get(Account, row.account_id) if row is not None else None
    if not hit or account is None or account_out(account):
        raise gone()
    token, device = make_device(db, account, name, kind)
    return token, account, device


# --- Devices --------------------------------------------------------------------------------------------------------


def make_device(db: Session, account: Account, name: str | None, kind: str, via: int | None = None
                ) -> tuple[str, AppDevice]:
    token = PREFIX + secrets.token_urlsafe(32)
    row = AppDevice(account_id=account.id, name=device_name(name), kind=kind, token_hash=digest(token), via_id=via)
    db.add(row)
    db.commit()
    logger.info("App device paired device_id=%s kind=%s", row.id, kind)
    return token, row


_siblings_lock = threading.Lock()
_siblings: dict[int, deque[float]] = defaultdict(deque)


def sibling(db: Session, account: Account, via: AppDevice, name: str | None, kind: str | None,
            now: float | None = None) -> tuple[str, AppDevice]:
    """A token of its own for another device of the same account, asked for by a device it has: the app hands its
    connection on through iCloud, and each device comes in under its own name and can be disconnected alone."""
    kind = device_kind(kind)
    now = time.monotonic() if now is None else now
    with _siblings_lock:
        seen = _siblings[account.id]
        while seen and now - seen[0] > 3600:
            seen.popleft()
        if len(seen) >= SIBLINGS_PER_HOUR:
            raise PairingError("too_many_attempts", "Too many new devices in an hour. Try again later.", 429,
                               retry_after=3600)
        seen.append(now)
    row = db.get(Account, account.id)
    if row is None or account_out(row):
        raise PairingError("account_blocked", "This account is blocked.", 401)
    token, device = make_device(db, row, name, kind, via=via.id)
    logger.info("App device brought in by device_id=%s new device_id=%s", via.id, device.id)
    return token, device


def of_account(db: Session, account_id: int) -> list[AppDevice]:
    return list(db.scalars(select(AppDevice).where(AppDevice.account_id == account_id).order_by(AppDevice.id)))


def disconnect(db: Session, account_id: int, device_id: int) -> bool:
    """The device has no access from this moment on (AC-4): its token goes, with its push ticket. A calendar account
    set up from it stays and gets its alarms again (TIMEIOS-4 EC-15)."""
    gone_rows = db.execute(delete(AppDevice).where(AppDevice.id == device_id,
                                                   AppDevice.account_id == account_id)).rowcount
    if gone_rows:
        db.execute(update(Device).where(Device.app_device_id == device_id).values(app_device_id=None))
        # A ``wait`` of the device going on right now ends at once (as nexnotes closes its open connections).
        live.touch_accounts(db, {account_id})
    db.commit()
    if gone_rows:
        logger.info("App device disconnected device_id=%s", device_id)
    return bool(gone_rows)


def end_all(db: Session, account_id: int) -> None:
    """Every device of the account goes (a new password, "sign out everywhere", a block or a deletion). The devices of
    a blocked or deleted account are to hear why (EC-5); unblocked, its devices of before hear "disconnected". The
    caller commits."""
    account = db.get(Account, account_id)
    out = account is None or account_out(account)
    now = utcnow()
    ids = []
    for row in db.scalars(select(AppDevice).where(AppDevice.account_id == account_id)):
        ids.append(row.id)
        if out and db.get(AppGoneDevice, row.token_hash) is None:
            db.add(AppGoneDevice(token_hash=row.token_hash, account_id=account_id, created_at=now))
    if not out:
        db.execute(delete(AppGoneDevice).where(AppGoneDevice.account_id == account_id))
    if ids:
        db.execute(update(Device).where(Device.app_device_id.in_(ids)).values(app_device_id=None))
        db.execute(delete(AppDevice).where(AppDevice.id.in_(ids)))
        live.touch_accounts(db, {account_id})
        logger.info("App devices of an account disconnected count=%s", len(ids))


@dataclass
class Caller:
    """Who calls ``/api/app/1``: the account and its device, both detached."""

    account: Account
    device: AppDevice


class Refusal(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def authenticate(db: Session, token: str | None) -> Caller:
    """The device behind a token; else ``Refusal`` with ``account_blocked`` (its account blocked or deleted),
    ``device_disconnected`` (no such device: disconnected, or never one) or ``second_factor_required``."""
    if not token or len(token) > 200 or not token.startswith(PREFIX):
        raise Refusal("device_disconnected")
    hashed = digest(token)
    row = db.scalar(select(AppDevice).where(AppDevice.token_hash == hashed))
    if row is None:
        went = db.get(AppGoneDevice, hashed)
        # Gone with a block or deletion: "blocked" while it lasts; unblocked, the device was disconnected all the same.
        if went is not None and account_out(db.get(Account, went.account_id)):
            raise Refusal("account_blocked")
        raise Refusal("device_disconnected")
    account = db.get(Account, row.account_id)
    if account is None or account_out(account):
        raise Refusal("account_blocked")
    if totp.setup_required(db, account):
        raise Refusal("second_factor_required")
    now = utcnow()
    if row.last_used_at is None or now - row.last_used_at > USED_EVERY:
        row.last_used_at = now
        db.commit()
    db.expunge(account)
    db.expunge(row)
    return Caller(account=account, device=row)


def iso(value: datetime | None) -> str | None:
    return value.isoformat().replace("+00:00", "Z") if value else None


def device_out(row: AppDevice) -> dict[str, Any]:
    return {"id": row.id, "name": row.name, "kind": row.kind or DEVICE_KINDS[0], "created_at": iso(row.created_at),
            "last_seen_at": iso(row.last_used_at)}


# --- Notifications: kept, not sent yet --------------------------------------------------------------------------------


def key_valid(value: str) -> bool:
    """A public X25519 key one can agree with: 32 bytes, and no point of small order."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey

    try:
        raw = base64.b64decode(value, validate=True)
        X25519PrivateKey.generate().exchange(X25519PublicKey.from_public_bytes(raw))
    except (binascii.Error, ValueError):
        return False
    return True


def remember_push(db: Session, device_id: int, ticket: str, key: str) -> None:
    """Ticket and key of a paired device. The same ticket belongs to one device of the person: a device paired again
    brings its ticket, and the old pairing must not get every message twice."""
    if not ticket or len(ticket) > TICKET_MAX or not key_valid(key):
        raise ValueError("ticket or key not usable")
    row = db.get(AppDevice, device_id)
    if row is None:
        raise ValueError("no such device")
    db.execute(update(AppDevice).where(AppDevice.account_id == row.account_id, AppDevice.id != device_id,
                                       AppDevice.push_ticket == ticket).values(push_ticket="", push_key=""))
    row.push_ticket, row.push_key = ticket, key
    db.commit()


def forget_push(db: Session, device_id: int) -> None:
    db.execute(update(AppDevice).where(AppDevice.id == device_id).values(push_ticket="", push_key=""))
    db.commit()


def server_id() -> str:
    """A fixed mark of this server (``me.server_id``): a keyed hash, nothing to learn from it."""
    from ..security import server_key

    return hmac.new(server_key(), b"nextime-server-id", hashlib.sha256).hexdigest()[:16]


def forget() -> None:
    """For the tests."""
    with _siblings_lock:
        _siblings.clear()
