"""nextime's side of the shared key module ``vendor/nexschluessel`` (bauplaene/schluessel 1.0.0): its configuration
and the ``Store`` over the table ``api_keys`` of TIME-26.

Nothing of a key changes: the rows stay as 0.4.0 wrote them (``nt_api_`` and 32 random bytes, SHA-256 of the whole key,
the first four characters after the prefix to tell keys apart, ``read`` or ``write``, ``allow_mcp``, the chosen
calendars or ``None`` for all, later ones too, ``client_ref`` against a double click, 30, 90 or 365 days or never).
New is the column ``blocked_at`` (the operator locks a key, E27); the way back is in ``docs/konten-anbindung.md``.

The old ways (``/api/keys``, ``/api/admin/keys``) answer in their old shape (``routers/keys.py``); the cards use the
module's ways under ``/api/connections``. Keys are still checked where they are presented by nextime's own
``services/apikeys.authenticate`` (``/api/v1``, ``/api/mcp``), which refuses a locked key too.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, ApiKey
from ..security import account_out
from ..vendor.nexschluessel import AppConfig, KeyKind
from ..vendor.nexschluessel.model import ALL_AREAS, Area, Holder, Key, KeyChange, NewKey
from . import apikeys, calendars, settings_service, totp

#: As 0.4.0 offered keys (TIME-26): one kind, ``read`` or ``write``, the tick "also for AI tools (MCP)", calendars,
#: 30, 90 or 365 days or never (90 first), 20 per account, names up to 100 characters; the operator's switches
#: ``api_enabled`` and ``mcp_enabled``, both on from the start. nextime's codes of before stay (``keys_too_many`` …).
CONFIG = AppConfig(
    app_name="nextime",
    kinds=(
        KeyKind(id="api", prefix=apikeys.PREFIX, options=("mcp",), areas=True, expiry_days=apikeys.EXPIRY_DAYS,
                default_days=apikeys.DEFAULT_DAYS, max_keys=apikeys.MAX_KEYS, name_max=apikeys.NAME_MAX),
    ),
    switches=("api", "mcp"),
    mcp_path="/api/mcp",
    error_codes={
        "too_many_keys": "keys_too_many",
        "name_missing": "key_name_missing",
        "name_too_long": "key_name_too_long",
    },
)

#: The module's switches under nextime's names.
SWITCHES = {"api": "api_enabled", "mcp": "mcp_enabled"}


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class KeyStore:
    """``nexschluessel.Store`` over ``api_keys``, inside the request's session."""

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _key(row: ApiKey) -> Key:
        return Key(
            id=row.id,
            account_id=row.account_id,
            kind="api",
            name=row.name,
            right=row.level,
            prefix=row.prefix,
            created_at=_aware(row.created_at),
            last_used_at=_aware(row.last_used_at),
            expires_at=_aware(row.expires_at),
            blocked_at=_aware(row.blocked_at),
            areas=list(row.calendar_ids) if row.calendar_ids is not None else None,
            options={"mcp": bool(row.allow_mcp)},
        )

    def keys_of(self, account_id: object) -> list[Key]:
        rows = self.db.scalars(select(ApiKey).where(ApiKey.account_id == int(account_id))  # type: ignore[call-overload]
                               .order_by(ApiKey.id.desc()))
        return [self._key(row) for row in rows]

    def all_keys(self) -> list[Key]:
        return [self._key(row) for row in self.db.scalars(select(ApiKey).order_by(ApiKey.id.desc()))]

    def key(self, key_id: object) -> Key | None:
        row = self.db.get(ApiKey, int(key_id))  # type: ignore[call-overload]
        return self._key(row) if row is not None else None

    def key_by_hash(self, token_hash: str) -> Key | None:
        row = self.db.scalar(select(ApiKey).where(ApiKey.token_hash == token_hash))
        return self._key(row) if row is not None else None

    def key_by_client_ref(self, account_id: object, client_ref: str) -> Key | None:
        row = self.db.scalar(select(ApiKey).where(ApiKey.account_id == int(account_id),  # type: ignore[call-overload]
                                                  ApiKey.client_ref == client_ref))
        return self._key(row) if row is not None else None

    def add_key(self, new: NewKey) -> Key:
        row = ApiKey(
            account_id=int(new.account_id),  # type: ignore[call-overload]
            name=new.name,
            level=new.right,
            allow_mcp=bool(new.options.get("mcp")),
            calendar_ids=sorted(int(area) for area in new.areas) if new.areas is not None else None,  # type: ignore[call-overload]
            token_hash=new.token_hash,
            prefix=new.prefix,
            client_ref=new.client_ref or None,
            created_at=new.created_at,
            expires_at=new.expires_at,
        )
        self.db.add(row)
        self.db.flush()
        return self._key(row)

    def change_key(self, key_id: object, change: KeyChange) -> Key:
        row = self.db.get(ApiKey, int(key_id))  # type: ignore[call-overload]
        if row is None:
            raise LookupError(key_id)
        if change.name is not None:
            row.name = change.name
        if change.right is not None:
            row.level = change.right
        if change.areas is not None:
            row.calendar_ids = None if change.areas == ALL_AREAS else sorted(int(area) for area in change.areas)  # type: ignore[call-overload]
        if change.options is not None and "mcp" in change.options:
            row.allow_mcp = bool(change.options["mcp"])
        self.db.flush()
        return self._key(row)

    def remove_key(self, key_id: object) -> None:
        row = self.db.get(ApiKey, int(key_id))  # type: ignore[call-overload]
        if row is not None:
            self.db.delete(row)
            self.db.flush()

    def set_blocked(self, key_id: object, when: datetime | None) -> None:
        row = self.db.get(ApiKey, int(key_id))  # type: ignore[call-overload]
        if row is not None:
            row.blocked_at = when
            self.db.flush()

    def touch(self, key_id: object, when: datetime) -> None:
        row = self.db.get(ApiKey, int(key_id))  # type: ignore[call-overload]
        if row is not None:
            row.last_used_at = when
            self.db.flush()

    def holder(self, account_id: object) -> Holder | None:
        row = self.db.get(Account, int(account_id))  # type: ignore[call-overload]
        if row is None:
            return None
        return Holder(id=row.id, name=row.name, display_name=row.display_name or "", out=account_out(row))

    def areas_of(self, account_id: object, kind: str) -> list[Area]:
        """Every calendar the account sees, its own first, as the dialog of 0.4.0 offered them."""
        return [Area(item.calendar.id, item.calendar.name)
                for item in calendars.accesses(self.db, int(account_id))]  # type: ignore[call-overload]

    def keys_wait(self, account_id: object) -> bool:
        row = self.db.get(Account, int(account_id))  # type: ignore[call-overload]
        return row is not None and totp.setup_required(self.db, row)

    def switch(self, name: str) -> bool:
        return bool(settings_service.get(self.db, SWITCHES[name]))

    def set_switch(self, name: str, on: bool) -> None:
        settings_service.save(self.db, {SWITCHES[name]: bool(on)}, commit=False)

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()
