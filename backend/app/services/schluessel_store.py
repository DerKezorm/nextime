"""nextime's side of the shared key module ``vendor/nexschluessel`` (bauplaene/schluessel 1.1.0): its configuration
and the ``Store`` over the tables ``api_keys`` (TIME-26) and ``mcp_keys`` (the shared module mcp 1.1.0).

Nothing of a key changes: the rows of ``api_keys`` stay as 0.4.0 wrote them (``nt_api_`` and 32 random bytes, SHA-256
of the whole key, the first four characters after the prefix to tell keys apart, ``read`` or ``write``, ``allow_mcp``,
the chosen calendars or ``None`` for all, later ones too, ``client_ref`` against a double click, 30, 90 or 365 days or
never). New is the column ``blocked_at`` (the operator locks a key, E27), and the table ``mcp_keys`` for MCP keys made
by hand (``nt_mcp_``) and the programs that connected over OAuth (MCP decision 6; E50 is done with it). A program's row
never stands in ``api_keys``: nextime 0.4.0 (the way back) reads that table and would take it for an API key.

The module's ids are ``api-<row>`` and ``mcp-<row>``: two tables, one list. The tick "also for AI tools"
of an API key is retired (MCP decisions 6, 11): the page offers it on no new key and only switches it off on an old
one; nextime's older way ``POST /api/keys`` still sets it (decision 12).

The old ways (``/api/keys``, ``/api/admin/keys``) answer in their old shape (``routers/keys.py``); the cards use the
module's ways under ``/api/connections``. API keys are still checked at ``/api/v1`` by nextime's own
``services/apikeys.authenticate``; at ``/api/mcp`` the shared module mcp asks ``services/mcp_store.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Account, ApiKey, McpKey, McpSpent, McpToolRight
from ..security import account_out
from ..vendor.nexschluessel import AppConfig, KeyKind
from ..vendor.nexschluessel.model import ALL_AREAS, Area, Holder, Key, KeyChange, NewKey
from . import apikeys, calendars, settings_service, totp

#: The prefix of an MCP key made by hand and of a program's access token, and of a program's refresh token.
MCP_PREFIX = "nt_mcp_"
REFRESH_PREFIX = "ntr_"

#: As 0.4.0 offered API keys (TIME-26): ``read`` or ``write``, calendars, 30, 90 or 365 days or never (90 first), 20
#: per account, names up to 100 characters; the tick "also for AI tools (MCP)" retired (decisions 6, 11, 12). MCP keys
#: alike, with programs counted (decision 6). The operator's switches ``api_enabled`` and ``mcp_enabled``.
CONFIG = AppConfig(
    app_name="nextime",
    kinds=(
        KeyKind(id="api", prefix=apikeys.PREFIX, options=("mcp",), retired_options=("mcp",), areas=True,
                expiry_days=apikeys.EXPIRY_DAYS, default_days=apikeys.DEFAULT_DAYS, max_keys=apikeys.MAX_KEYS,
                name_max=apikeys.NAME_MAX),
        KeyKind(id="mcp", prefix=MCP_PREFIX, areas=True, expiry_days=apikeys.EXPIRY_DAYS,
                default_days=apikeys.DEFAULT_DAYS, max_keys=apikeys.MAX_KEYS, name_max=apikeys.NAME_MAX,
                switch="mcp", programs_count=True),
    ),
    switches=("api", "mcp", "app", "pairing"),
    programs=True,
    mcp_path="/api/mcp",
    key_id=str,
    error_codes={
        "too_many_keys": "keys_too_many",
        "name_missing": "key_name_missing",
        "name_too_long": "key_name_too_long",
    },
)

#: The module's switches under nextime's names.
SWITCHES = {"api": "api_enabled", "mcp": "mcp_enabled", "app": "app_enabled", "pairing": "app_pairing_enabled"}
API, MCP = "api-", "mcp-"
EARLIEST = datetime.min.replace(tzinfo=UTC)


def split(key_id: Any) -> tuple[str, int] | None:
    """``api-12`` -> ``("api", 12)``; anything else -> None."""
    text = str(key_id)
    for kind, start in (("api", API), ("mcp", MCP)):
        number = text[len(start):]
        if text.startswith(start) and number.isdigit() and len(number) <= 18:
            return kind, int(number)
    return None


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


class KeyStore:
    """``nexschluessel.Store`` over ``api_keys`` and ``mcp_keys``, inside the request's session."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- reading rows ---------------------------------------------------------------------------------------------

    @staticmethod
    def _api(row: ApiKey) -> Key:
        return Key(
            id=f"{API}{row.id}",
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

    @staticmethod
    def _mcp(row: McpKey) -> Key:
        program = row.client_id is not None
        return Key(
            id=f"{MCP}{row.id}",
            account_id=row.account_id,
            kind="mcp",
            name=row.name,
            right=row.level,
            prefix=row.prefix,
            created_at=_aware(row.created_at),
            last_used_at=_aware(row.last_used_at),
            # A program's access token runs out every hour and renews itself (the module's page shows no end for it).
            expires_at=_aware(row.expires_at),
            blocked_at=_aware(row.blocked_at),
            areas=list(row.calendar_ids) if row.calendar_ids is not None else None,
            options={},
            program=program,
        )

    def _all(self, account_id: object | None = None) -> list[Key]:
        api = select(ApiKey)
        mcp = select(McpKey)
        if account_id is not None:
            api = api.where(ApiKey.account_id == int(account_id))  # type: ignore[call-overload]
            mcp = mcp.where(McpKey.account_id == int(account_id))  # type: ignore[call-overload]
        found = [self._api(row) for row in self.db.scalars(api)] + [self._mcp(row) for row in self.db.scalars(mcp)]
        return sorted(found, key=lambda key: key.created_at or EARLIEST, reverse=True)

    def keys_of(self, account_id: object) -> list[Key]:
        return self._all(account_id)

    def all_keys(self) -> list[Key]:
        return self._all()

    def row(self, key_id: object) -> ApiKey | McpKey | None:
        found = split(key_id)
        if found is None:
            return None
        kind, number = found
        return self.db.get(ApiKey if kind == "api" else McpKey, number)

    def key(self, key_id: object) -> Key | None:
        row = self.row(key_id)
        if row is None:
            return None
        return self._api(row) if isinstance(row, ApiKey) else self._mcp(row)

    def key_by_hash(self, token_hash: str) -> Key | None:
        row = self.db.scalar(select(ApiKey).where(ApiKey.token_hash == token_hash))
        if row is not None:
            return self._api(row)
        other = self.db.scalar(select(McpKey).where(McpKey.token_hash == token_hash))
        return self._mcp(other) if other is not None else None

    def key_by_client_ref(self, account_id: object, client_ref: str) -> Key | None:
        row = self.db.scalar(select(ApiKey).where(ApiKey.account_id == int(account_id),  # type: ignore[call-overload]
                                                  ApiKey.client_ref == client_ref))
        return self._api(row) if row is not None else None

    # --- writing ---------------------------------------------------------------------------------------------------

    def add_key(self, new: NewKey) -> Key:
        chosen = sorted(int(area) for area in new.areas) if new.areas is not None else None  # type: ignore[call-overload]
        if new.kind == "api":
            row: ApiKey | McpKey = ApiKey(
                account_id=int(new.account_id),  # type: ignore[call-overload]
                name=new.name,
                level=new.right,
                allow_mcp=bool(new.options.get("mcp")),
                calendar_ids=chosen,
                token_hash=new.token_hash,
                prefix=new.prefix,
                client_ref=new.client_ref or None,
                created_at=new.created_at,
                expires_at=new.expires_at,
            )
        else:
            row = McpKey(
                account_id=int(new.account_id),  # type: ignore[call-overload]
                name=new.name,
                level=new.right,
                calendar_ids=chosen,
                token_hash=new.token_hash,
                prefix=new.prefix[:16],
                created_at=new.created_at,
                expires_at=new.expires_at,
            )
        self.db.add(row)
        self.db.flush()
        return self._api(row) if isinstance(row, ApiKey) else self._mcp(row)

    def change_key(self, key_id: object, change: KeyChange) -> Key:
        row = self.row(key_id)
        if row is None:
            raise LookupError(key_id)
        if change.name is not None:
            row.name = change.name
        if change.right is not None:
            row.level = change.right
        if change.areas is not None:
            row.calendar_ids = None if change.areas == ALL_AREAS else sorted(int(area) for area in change.areas)  # type: ignore[call-overload]
        if isinstance(row, ApiKey) and change.options is not None and "mcp" in change.options:
            row.allow_mcp = bool(change.options["mcp"])
        self.db.flush()
        return self._api(row) if isinstance(row, ApiKey) else self._mcp(row)

    def remove_key(self, key_id: object) -> None:
        row = self.row(key_id)
        if row is None:
            return
        # What the shared module mcp kept of the key goes with it: its rights per tool, a program's spent tokens.
        for right in self.db.scalars(select(McpToolRight).where(McpToolRight.key_id == str(key_id))):
            self.db.delete(right)
        for spent in self.db.scalars(select(McpSpent).where(McpSpent.key_id == str(key_id))):
            self.db.delete(spent)
        self.db.delete(row)
        self.db.flush()

    def set_blocked(self, key_id: object, when: datetime | None) -> None:
        row = self.row(key_id)
        if row is not None:
            row.blocked_at = when
            self.db.flush()

    def touch(self, key_id: object, when: datetime) -> None:
        row = self.row(key_id)
        if row is not None:
            row.last_used_at = when
            self.db.flush()

    # --- accounts, calendars, switches -----------------------------------------------------------------------------

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
