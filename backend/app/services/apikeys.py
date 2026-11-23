"""Keys of members for programs and AI tools (TIME-26), the one kind of key nextime has.

A key is made under Settings, My account, Connections with a name, a right (``read`` or ``write``), its calendars (all,
later ones too, or chosen ones), a run-out and the tick "for MCP" (AC-1). The key itself is shown once; only its SHA-256
is stored, with its first characters to tell keys apart. It travels in ``Authorization: Bearer``, never in an address,
and never reaches the log: the log names a key by its number and its name (AC-10).

A key acts as its account and never sees more (``services/apiv1.py``). A blocked or deleted account, or one that still
owes the second factor the operator requires, has no working key (EC-13); its keys stay stored and work again once the
account may sign in. An expired key stays in the member's list, marked, until the member removes it (EC-1).
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..errors import error
from ..models import Account, ApiKey, utcnow
from ..security import account_out
from . import calendars, settings_service, totp

logger = logging.getLogger("nextime.api")

LEVELS = ("read", "write")
PREFIX = "nt_api_"
#: Keys an account may hold, expired ones included until removed (EC-15).
MAX_KEYS = 20
#: The choices of "Ablauf" in days, ``None`` for never; 90 chosen from the start (AC-11).
EXPIRY_DAYS = (30, 90, 365, None)
DEFAULT_DAYS = 90
NAME_MAX = 100
#: Calendars one key may be limited to.
MAX_CALENDARS = 500
#: ``last_used_at`` is written at most this often.
USED_EVERY = timedelta(minutes=1)


def clock() -> datetime:
    """Now, by the server's clock; the tests set it (EC-1)."""
    return utcnow()


def digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def api_on(db: Session) -> bool:
    """The operator's switch "Programmschnittstelle (API) für Mitglieder" (AC-5), on from the start (AC-11)."""
    return bool(settings_service.get(db, "api_enabled"))


def mcp_on(db: Session) -> bool:
    """The operator's switch "MCP-Zugang" (AC-5), on from the start (AC-11)."""
    return bool(settings_service.get(db, "mcp_enabled"))


def clean_name(raw: str) -> str:
    """Blanks at the edges go and runs inside become one; empty is refused (EC-15). The same name twice is fine."""
    name = " ".join((raw or "").split())
    if not name:
        raise error("key_name_missing", "Give the key a name.", 422, field="name")
    if len(name) > NAME_MAX:
        raise error("key_name_too_long", f"At most {NAME_MAX} characters.", 422, field="name", max=NAME_MAX)
    return name


def make(db: Session, account: Account, *, name: str, level: str, mcp: bool, calendar_ids: list[int] | None,
         days: int | None, client_ref: str | None = None) -> tuple[ApiKey, str | None]:
    """A new key and the key itself, once. The same ``client_ref`` again (a second click, EC-16) gives the key made
    the first time, without the key: it was shown then and is never shown again."""
    if level not in LEVELS:
        raise error("invalid_input", "The input is not valid.", 422, fields=["level"])
    if days not in EXPIRY_DAYS:
        raise error("invalid_input", "The input is not valid.", 422, fields=["days"])
    if not api_on(db):
        raise error("api_off", "The operator has switched the API off.", 403)
    if mcp and not mcp_on(db):
        raise error("mcp_off", "The operator has switched MCP off.", 403)
    name = clean_name(name)
    if client_ref:
        known = db.scalar(select(ApiKey).where(ApiKey.account_id == account.id, ApiKey.client_ref == client_ref))
        if known is not None:
            return known, None
    chosen: list[int] | None = None
    if calendar_ids is not None:
        seen = {item.calendar.id for item in calendars.accesses(db, account.id)}
        chosen = sorted({int(found) for found in calendar_ids})
        if not chosen or len(chosen) > MAX_CALENDARS or any(found not in seen for found in chosen):
            raise error("invalid_input", "The input is not valid.", 422, fields=["calendar_ids"])
    if count(db, account.id) >= MAX_KEYS:
        raise error("keys_too_many", f"An account holds at most {MAX_KEYS} keys.", 409, max=MAX_KEYS)
    token = PREFIX + secrets.token_urlsafe(32)
    now = clock()
    row = ApiKey(account_id=account.id, name=name, level=level, allow_mcp=bool(mcp), calendar_ids=chosen,
                 token_hash=digest(token), prefix=token[: len(PREFIX) + 4], client_ref=client_ref or None,
                 created_at=now, expires_at=now + timedelta(days=days) if days else None)
    db.add(row)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        known = db.scalar(select(ApiKey).where(ApiKey.account_id == account.id, ApiKey.client_ref == client_ref))
        if known is None:
            raise
        return known, None
    # The key's number, right and reach; never a piece of the key itself (AC-10).
    logger.info("Key made key_id=%s level=%s mcp=%s calendars=%s", row.id, row.level, row.allow_mcp,
                "all" if chosen is None else len(chosen))
    return row, token


def count(db: Session, account_id: int) -> int:
    return int(db.scalar(select(func.count()).select_from(ApiKey).where(ApiKey.account_id == account_id)) or 0)


def keys_of(db: Session, account_id: int) -> list[ApiKey]:
    return list(db.scalars(select(ApiKey).where(ApiKey.account_id == account_id).order_by(ApiKey.id)))


def revoke(db: Session, account: Account, key_id: int) -> None:
    """Gone at once (AC-4); an expired key is removed the same way (EC-1)."""
    row = db.get(ApiKey, key_id)
    if row is None or row.account_id != account.id:
        raise error("not_found", "Not found.", 404)
    db.delete(row)
    db.commit()
    logger.info("Key revoked key_id=%s", key_id)


def authenticate(db: Session, token: str | None) -> tuple[Account, ApiKey] | None:
    """The account and the key behind a bearer key, or None: unknown, expired, revoked, or its account blocked,
    deleted or owing the second factor (EC-1, EC-13)."""
    if not token or len(token) > 200 or not token.startswith(PREFIX):
        return None
    row = db.scalar(select(ApiKey).where(ApiKey.token_hash == digest(token)))
    if row is None:
        return None
    now = clock()
    if row.expires_at is not None and row.expires_at <= now:
        return None
    account = db.get(Account, row.account_id)
    if account is None or account_out(account) or totp.setup_required(db, account):
        return None
    if row.last_used_at is None or now - row.last_used_at > USED_EVERY:
        row.last_used_at = now
        db.commit()
    return account, row


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def key_out(db: Session, row: ApiKey, now: datetime | None = None,
            names: dict[int, str] | None = None) -> dict[str, Any]:
    """A key as the list shows it (AC-4): never the key, never its hash. Chosen calendars with their names, those the
    account does not see any more left out."""
    now = now or clock()
    chosen = None
    if row.calendar_ids is not None:
        if names is None:
            names = {item.calendar.id: item.calendar.name for item in calendars.accesses(db, row.account_id)}
        chosen = [{"id": found, "name": names[found]} for found in row.calendar_ids if found in names]
    return {
        "id": row.id,
        "name": row.name,
        "level": row.level,
        "mcp": bool(row.allow_mcp),
        "calendars": chosen,
        "prefix": row.prefix,
        "created_at": _iso(row.created_at),
        "expires_at": _iso(row.expires_at),
        "expired": row.expires_at is not None and row.expires_at <= now,
        "last_used_at": _iso(row.last_used_at),
    }


def overview(db: Session) -> list[dict[str, Any]]:
    """The operator's overview (AC-5): per account how many keys, how many of them for MCP. No name of a key, no
    calendar, nothing of what the keys read."""
    rows = db.execute(
        select(ApiKey.account_id, func.count(), func.sum(case((ApiKey.allow_mcp.is_(True), 1), else_=0)))
        .group_by(ApiKey.account_id)
    ).all()
    if not rows:
        return []
    owners = {row.id: row for row in db.scalars(select(Account).where(Account.id.in_([row[0] for row in rows])))}
    out = []
    for account_id, total, mcp in rows:
        owner = owners.get(account_id)
        out.append({"account": {"id": account_id, "name": owner.shown_name if owner else ""},
                    "keys": int(total), "mcp": int(mcp or 0)})
    return sorted(out, key=lambda entry: entry["account"]["name"].casefold())
