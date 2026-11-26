"""nextime's ``Store`` for the shared modules schluessel and mcp together (``vendor/nexschluessel``,
``vendor/nexmcp``, bauplaene/mcp 1.1.0), as the module's reference app has it: one class over nextime's tables.

- Keys: ``services/schluessel_store.KeyStore`` (``api_keys`` and ``mcp_keys``).
- Who calls ``/api/mcp``: the shared key module's ``authenticate`` with the kind ``mcp`` (an MCP key made by hand, a
  program's access token, an API key of 0.4.0 with the tick "also for AI tools"). The tools get nextime's own
  ``apiv1.Caller`` in ``Caller.account``: the account, the key's calendars, its name for the history (AC-10).
- Programs (OAuth): ``mcp_clients``, ``mcp_codes``, the program's row in ``mcp_keys`` (``client_id``), spent tokens in
  ``mcp_spent``.
- Rights per tool (``mcp_tool_rights``), requests that wait for a yes (``mcp_requests``), the operator's settings
  ``mcp_programs``, ``mcp_max_level``, ``mcp_blocked_tools``.

nextime has no "KI erlaubt" per account: ``ai_allowed`` is always yes (decision 10 concerns other apps).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..models import OPERATOR, Account, McpClient, McpCode, McpKey, McpRequest, McpSpent, McpToolRight
from ..security import account_out
from ..vendor import nexschluessel
from ..vendor.nexmcp import Caller, Client, Code, Grant, Program
from ..vendor.nexmcp import Request as McpRequestData
from ..vendor.nexschluessel.model import Key
from . import apiv1, settings_service
from .schluessel_store import CONFIG, MCP, KeyStore, split

#: The module's settings under nextime's names (``settings_service.DEFAULTS``).
SETTINGS = {"programs": "mcp_programs", "max_level": "mcp_max_level", "blocked_tools": "mcp_blocked_tools"}


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


class Store(KeyStore):
    """``nexschluessel.Store`` and ``nexmcp.Store`` over one session."""

    def __init__(self, db: Session) -> None:
        super().__init__(db)

    # --- switches and settings --------------------------------------------------------------------------------------

    def mcp_on(self) -> bool:
        return self.switch("mcp")

    def setting(self, name: str) -> Any:
        return settings_service.get(self.db, SETTINGS[name])

    def set_setting(self, name: str, value: Any) -> None:
        settings_service.save(self.db, {SETTINGS[name]: value}, commit=False)

    # --- accounts --------------------------------------------------------------------------------------------------

    def ai_allowed(self, account_id: object) -> bool:
        return True

    def account_may(self, account_id: object) -> bool:
        row = self.db.get(Account, int(account_id))  # type: ignore[call-overload]
        return row is not None and not account_out(row) and not self.keys_wait(account_id)

    def key_areas(self, account_id: object) -> list[dict[str, Any]]:
        return [{"id": area.id, "name": area.name} for area in self.areas_of(account_id, "mcp")]

    def count_keys(self, account_id: object) -> int:
        return int(self.db.scalar(select(func.count()).select_from(McpKey)
                                  .where(McpKey.account_id == int(account_id))) or 0)  # type: ignore[call-overload]

    # --- who calls ----------------------------------------------------------------------------------------------------

    def _caller(self, key: Key) -> Caller | None:
        account = self.db.get(Account, int(key.account_id))  # type: ignore[call-overload]
        if account is None:
            return None
        chosen = [int(found) for found in key.areas] if key.areas is not None else None  # type: ignore[call-overload]
        # nextime's own view of the key for its tools: the account, the key's calendars, its name for the history.
        own = apiv1.Caller(account=account, key_id=key.id, key_name=key.name or "",  # type: ignore[arg-type]
                           level=key.right if key.right in apiv1.LEVELS else "read", mcp=True, calendar_ids=chosen,
                           expires_at=None if key.program else key.expires_at)
        self.db.expunge(account)
        return Caller(
            key_id=key.id,
            key_name=key.name or "",
            account_id=key.account_id,
            account_name=account.name,
            level=key.right,
            areas=chosen,
            options={},
            rights=self.key_rights(key.id),
            program=key.program,
            admin=account.role == OPERATOR,
            ai_allowed=True,
            account=own,
        )

    def caller(self, token: str) -> Caller | None:
        """The shared key module checks the key (kind ``mcp``: MCP keys, programs and API keys with the tick):
        unknown, locked, expired, an account that is out or owes its second factor, all answer "no valid key"."""
        try:
            found = nexschluessel.authenticate(self, CONFIG, token, ("mcp",))
        except nexschluessel.KeyRefused:
            return None
        return self._caller(found.key)

    def caller_of_key(self, key_id: object) -> Caller | None:
        key = self.key(key_id)
        holder = self.holder(key.account_id) if key else None
        if key is None or holder is None or holder.out or key.blocked_at is not None:
            return None
        if key.kind == "api" and not key.options.get("mcp"):
            return None
        return self._caller(key)

    def key_rights(self, key_id: object) -> dict[str, str]:
        rows = self.db.scalars(select(McpToolRight).where(McpToolRight.key_id == str(key_id)))
        return {row.tool: row.value for row in rows}

    def set_key_rights(self, key_id: object, rights: dict[str, str]) -> None:
        self.db.execute(delete(McpToolRight).where(McpToolRight.key_id == str(key_id)))
        for tool, value in rights.items():
            self.db.add(McpToolRight(key_id=str(key_id), tool=tool, value=value))
        self.db.flush()

    # --- programs (OAuth) ---------------------------------------------------------------------------------------------

    def _client(self, row: McpClient) -> Client:
        in_use = self.db.scalar(select(McpKey.id).where(McpKey.client_id == row.id).limit(1)) is not None
        return Client(id=row.id, name=row.name, redirect_uris=tuple(row.redirect_uris or ()),
                      created_at=_aware(row.created_at) or _now(), last_used_at=_aware(row.last_used_at),
                      in_use=in_use)

    def add_client(self, client: Client) -> None:
        self.db.add(McpClient(id=client.id, name=client.name, redirect_uris=list(client.redirect_uris),
                              created_at=client.created_at))
        self.db.flush()

    def client(self, client_id: str) -> Client | None:
        row = self.db.get(McpClient, client_id)
        return self._client(row) if row is not None else None

    def clients(self) -> list[Client]:
        return [self._client(row) for row in self.db.scalars(select(McpClient))]

    def remove_client(self, client_id: str) -> None:
        self.db.execute(delete(McpClient).where(McpClient.id == client_id))
        self.db.flush()

    def touch_client(self, client_id: str, when: datetime) -> None:
        self.db.execute(update(McpClient).where(McpClient.id == client_id).values(last_used_at=when))
        self.db.flush()

    def add_code(self, code: Code) -> None:
        self.db.add(McpCode(code_hash=code.code_hash, client_id=code.client_id, account_id=int(code.account_id),  # type: ignore[call-overload]
                            redirect_uri=code.redirect_uri, challenge=code.challenge, level=code.level,
                            calendar_ids=[int(area) for area in code.areas] if code.areas is not None else None,  # type: ignore[call-overload]
                            options=dict(code.options), expires_at=code.expires_at, resource=code.resource))
        self.db.flush()

    def take_code(self, code_hash: str) -> Code | None:
        row = self.db.get(McpCode, code_hash)
        if row is None:
            return None
        gone = self.db.execute(delete(McpCode).where(McpCode.code_hash == code_hash)).rowcount  # type: ignore[attr-defined]
        self.db.flush()
        if gone != 1:
            return None
        return Code(code_hash=row.code_hash, client_id=row.client_id, account_id=row.account_id,
                    redirect_uri=row.redirect_uri, challenge=row.challenge, level=row.level,
                    areas=list(row.calendar_ids) if row.calendar_ids is not None else None,
                    options=dict(row.options or {}), expires_at=_aware(row.expires_at) or _now(),
                    resource=row.resource or "")

    def has_program(self, account_id: object, client_id: str) -> bool:
        return self.db.scalar(select(McpKey.id).where(McpKey.account_id == int(account_id),  # type: ignore[call-overload]
                                                      McpKey.client_id == client_id).limit(1)) is not None

    def program_signed_in(self, account_id: object, client: Client, *, level: str, areas: Any, options: dict,
                          grant: Grant, now: datetime) -> Any:
        chosen = [int(area) for area in areas] if areas is not None else None
        row = self.db.scalar(select(McpKey).where(McpKey.account_id == int(account_id),  # type: ignore[call-overload]
                                                  McpKey.client_id == client.id))
        if row is None:
            row = McpKey(account_id=int(account_id), name=client.name or "Program", client_id=client.id,  # type: ignore[call-overload]
                         created_at=now)
            self.db.add(row)
        row.level = level
        row.calendar_ids = chosen
        row.token_hash = grant.access_hash
        row.prefix = grant.access_prefix[:16]
        row.expires_at = grant.access_expires
        row.refresh_hash = grant.refresh_hash
        row.signed_in_at = now
        row.refreshed_at = now
        self.db.flush()
        return f"{MCP}{row.id}"

    def _program(self, row: McpKey | None) -> Program | None:
        if row is None or row.client_id is None:
            return None
        signed = _aware(row.signed_in_at) or _aware(row.created_at) or _now()
        return Program(key_id=f"{MCP}{row.id}", account_id=row.account_id, client_id=row.client_id,
                       signed_in_at=signed, refreshed_at=_aware(row.refreshed_at) or signed)

    def program_by_refresh(self, refresh_hash: str) -> Program | None:
        return self._program(self.db.scalar(select(McpKey).where(McpKey.refresh_hash == refresh_hash,
                                                                  McpKey.client_id.is_not(None))))

    def program_of_token(self, token_hash: str) -> Program | None:
        return self._program(self.db.scalar(select(McpKey).where(
            McpKey.client_id.is_not(None), (McpKey.refresh_hash == token_hash) | (McpKey.token_hash == token_hash))))

    def program_refreshed(self, key_id: object, grant: Grant, now: datetime) -> None:
        row = self.row(key_id)
        if isinstance(row, McpKey):
            row.token_hash = grant.access_hash
            row.prefix = grant.access_prefix[:16]
            row.expires_at = grant.access_expires
            row.refresh_hash = grant.refresh_hash
            row.refreshed_at = now
            self.db.flush()

    def spend(self, key_id: object, token_hash: str, keep: int) -> None:
        if self.db.scalar(select(McpSpent).where(McpSpent.token_hash == token_hash)) is None:
            self.db.add(McpSpent(token_hash=token_hash, key_id=str(key_id), spent_at=_now()))
            self.db.flush()
        kept = select(McpSpent.id).where(McpSpent.key_id == str(key_id)).order_by(McpSpent.id.desc()).limit(keep)
        self.db.execute(delete(McpSpent).where(McpSpent.key_id == str(key_id), McpSpent.id.not_in(kept)))
        self.db.flush()

    def spent_by(self, token_hash: str) -> Any:
        row = self.db.scalar(select(McpSpent).where(McpSpent.token_hash == token_hash))
        return row.key_id if row is not None else None

    def end_program(self, key_id: object) -> None:
        self.remove_key(key_id)

    # --- requests that wait for a yes ---------------------------------------------------------------------------------

    @staticmethod
    def _request(row: McpRequest | None) -> McpRequestData | None:
        if row is None:
            return None
        return McpRequestData(
            id=row.id, account_id=row.account_id, key_id=row.key_id, key_name=row.key_name, tool=row.tool,
            arguments=row.arguments, digest=row.digest, status=row.status,
            created_at=_aware(row.created_at) or _now(), expires_at=_aware(row.expires_at) or _now(),
            decided_at=_aware(row.decided_at), decided_by=row.decided_by or "", result=row.result,
            summary=row.summary or "", dangerous=bool(row.dangerous),
        )

    def add_request(self, request: McpRequestData) -> McpRequestData:
        row = McpRequest(account_id=int(request.account_id), key_id=str(request.key_id),  # type: ignore[call-overload]
                         key_name=request.key_name, tool=request.tool, arguments=request.arguments,
                         digest=request.digest, status=request.status, created_at=request.created_at,
                         expires_at=request.expires_at, summary=request.summary, dangerous=request.dangerous)
        self.db.add(row)
        self.db.flush()
        found = self._request(row)
        assert found is not None
        return found

    def request(self, request_id: object) -> McpRequestData | None:
        try:
            number = int(request_id)  # type: ignore[call-overload]
        except (TypeError, ValueError):
            return None
        return self._request(self.db.get(McpRequest, number))

    def requests_of(self, account_id: object) -> list[McpRequestData]:
        rows = self.db.scalars(select(McpRequest).where(McpRequest.account_id == int(account_id))  # type: ignore[call-overload]
                               .order_by(McpRequest.id.desc()))
        return [found for row in rows if (found := self._request(row)) is not None]

    def count_waiting(self, account_id: object, key_id: object = None) -> int:
        query = select(func.count()).select_from(McpRequest).where(
            McpRequest.account_id == int(account_id), McpRequest.status == "waiting")  # type: ignore[call-overload]
        if key_id is not None:
            query = query.where(McpRequest.key_id == str(key_id))
        return int(self.db.scalar(query) or 0)

    def claim(self, request_id: object, status: str, *, now: datetime, by: str = "") -> bool:
        changed = self.db.execute(update(McpRequest).where(McpRequest.id == int(request_id),  # type: ignore[call-overload]
                                                           McpRequest.status == "waiting")
                                  .values(status=status, decided_at=now, decided_by=by))
        self.db.flush()
        return bool(changed.rowcount == 1)  # type: ignore[attr-defined]

    def finish(self, request_id: object, status: str, result: str | None, now: datetime) -> None:
        self.db.execute(update(McpRequest).where(McpRequest.id == int(request_id))  # type: ignore[call-overload]
                        .values(status=status, result=result, decided_at=now))
        self.db.flush()

    def expire(self, now: datetime) -> int:
        changed = self.db.execute(update(McpRequest).where(McpRequest.status == "waiting",
                                                           McpRequest.expires_at <= now)
                                  .values(status="expired", decided_at=now))
        self.db.flush()
        return int(changed.rowcount or 0)  # type: ignore[attr-defined]


def key_number(key_id: object) -> int | None:
    """The row of an MCP key from the module's id (``mcp-12`` -> 12)."""
    found = split(key_id)
    return found[1] if found is not None and found[0] == "mcp" else None
