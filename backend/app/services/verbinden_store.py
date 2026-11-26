"""nextime's side of the shared module verbinden (``vendor/nexverbinden``, bauplaene/verbinden 0.1.1): its
configuration and the ``Store`` over the tables ``vb_*``.

nextime connects with six apps of the catalog: it reads tasks of nextasks (and ticks them), deadlines of nexpaper,
birthdays and contacts of nexfolk, and adds notes in nexnotes; nexmail reads and writes its calendars and answers
invitations, nexdiary reads its calendars. The calendars are the areas a partner may be limited to.

Nothing of before changes: the connections typed in by hand (``nextasks_connections``, ``nexpaper_connections``)
stay in their tables with their routes (``PUT /api/nextasks``, ``PUT /api/nexpaper``) and show in the cards as
connections by API key (``legacy``). After "Neu verbinden" the old key of such a connection ends itself at the
partner (``old_key``); a key of nextime brought to ``/api/connect/retire`` is removed here (``retire_key``). The
tokens of a connection have their own prefixes (``ntv_``, ``ntvr_``, ``ntvc_``), never one of an API key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import __version__
from ..models import (
    Account,
    ApiKey,
    AppConnection,
    AppConnectionCode,
    AppConnectionPending,
    AppConnectionSpent,
    AppConnectionWaiting,
    NexpaperConnection,
    NextasksConnection,
)
from ..security import account_out
from ..vendor import nexverbinden
from . import apikeys, calendars, settings_service, vault

CONFIG = nexverbinden.AppConfig(
    app_name="nextime",
    version=__version__,
    access_prefix="ntv_",
    refresh_prefix="ntvr_",
    code_prefix="ntvc_",
    # V20: the partners nextime has a use for. As guest: tasks (nextasks), deadlines (nexpaper), birthdays (nexfolk),
    # a note to an event (nexnotes); as host: nexmail and nexdiary read its calendars.
    in_use=("nextasks", "nexpaper", "nexfolk", "nexnotes", "nexmail", "nexdiary"),
)

#: The module's settings under nextime's names (``settings_service.DEFAULTS``).
SETTINGS = {"connections": "connections_enabled", "addresses": "connections_addresses"}
#: Where the sealed parts of the module are bound (``vault``): one place for all, so a copy moved elsewhere opens not.
PLACE = "verbinden"


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _connection(row: AppConnection | None) -> Any:
    if row is None:
        return None
    return nexverbinden.Connection(
        id=row.id, account_id=row.account_id, partner=row.partner, partner_base=row.partner_base,
        partner_account=row.partner_account, uses=tuple(row.uses or ()), grants=tuple(row.grants or ()),
        areas_granted=list(row.areas_granted) if row.areas_granted is not None else None,
        areas_used=list(row.areas_used) if row.areas_used is not None else None, held=row.held,
        issued_access_hash=row.issued_access_hash, issued_refresh_hash=row.issued_refresh_hash,
        issued_access_expires=_aware(row.issued_access_expires), created_at=_aware(row.created_at),
        used_at=_aware(row.used_at), ok_at=_aware(row.ok_at), state=row.state, stale_since=_aware(row.stale_since),
        problem=row.problem, partner_version=row.partner_version or "",
    )


def _by_click(sealed: str, place: str) -> bool:
    return bool((vault.open_(sealed, place) or {}).get("connection"))


def _shown(sealed: str, place: str) -> str:
    """The start of an old key, as the card names it (``ntk_AbCd``); empty when it does not open here."""
    opened = vault.open_(sealed, place) or {}
    token = str(opened.get("token") or "")
    start = token.find("_", 3)
    return token[: start + 5] if start > 0 else token[:8]


class Store:
    """``nexverbinden.Store`` over the tables ``vb_*`` and nextime's own connections, inside one session."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- connections ----------------------------------------------------------------------------------------------

    def connections(self, account_id: Any) -> list[Any]:
        rows = self.db.scalars(select(AppConnection).where(AppConnection.account_id == int(account_id))
                               .order_by(AppConnection.partner))
        return [_connection(row) for row in rows]

    def connection(self, account_id: Any, partner: str) -> Any:
        return _connection(self.db.scalar(select(AppConnection).where(
            AppConnection.account_id == int(account_id), AppConnection.partner == partner)))

    def by_issued_access(self, token_hash: str) -> Any:
        return _connection(self.db.scalar(select(AppConnection).where(AppConnection.issued_access_hash == token_hash)))

    def by_issued_refresh(self, token_hash: str) -> Any:
        return _connection(self.db.scalar(select(AppConnection).where(
            AppConnection.issued_refresh_hash == token_hash)))

    def save(self, c: Any) -> None:
        row = self.db.get(AppConnection, c.id) or AppConnection(id=c.id)
        row.account_id = int(c.account_id)
        row.partner, row.partner_base, row.partner_account = c.partner, c.partner_base, c.partner_account
        row.uses, row.grants = list(c.uses), list(c.grants)
        row.areas_granted = list(c.areas_granted) if c.areas_granted is not None else None
        row.areas_used = list(c.areas_used) if c.areas_used is not None else None
        row.held, row.issued_access_hash, row.issued_refresh_hash = c.held, c.issued_access_hash, c.issued_refresh_hash
        row.issued_access_expires, row.created_at = c.issued_access_expires, c.created_at
        row.used_at, row.ok_at, row.state, row.stale_since = c.used_at, c.ok_at, c.state, c.stale_since
        row.problem, row.partner_version = c.problem, c.partner_version or ""
        self.db.add(row)
        self.db.flush()

    def remove(self, connection_id: str) -> None:
        self.db.execute(delete(AppConnection).where(AppConnection.id == connection_id))
        self.db.execute(delete(AppConnectionSpent).where(AppConnectionSpent.connection_id == connection_id))
        self.db.flush()

    # --- started connections and codes ------------------------------------------------------------------------------

    def add_pending(self, p: Any) -> None:
        self.db.add(AppConnectionPending(
            state_hash=p.state_hash, account_id=int(p.account_id), partner=p.partner, partner_base=p.partner_base,
            verifier=p.verifier, areas_granted=list(p.areas_granted) if p.areas_granted is not None else None,
            created_at=p.created_at))
        self.db.flush()

    def take_pending(self, state_hash: str) -> Any:
        row = self.db.get(AppConnectionPending, state_hash)
        if row is None:
            return None
        found = nexverbinden.Pending(
            state_hash=row.state_hash, account_id=row.account_id, partner=row.partner, partner_base=row.partner_base,
            verifier=row.verifier, areas_granted=list(row.areas_granted) if row.areas_granted is not None else None,
            created_at=_aware(row.created_at))
        self.db.delete(row)
        self.db.flush()
        return found

    def add_code(self, c: Any) -> None:
        self.db.add(AppConnectionCode(
            code_hash=c.code_hash, account_id=int(c.account_id), partner=c.partner, partner_base=c.partner_base,
            redirect_uri=c.redirect_uri, challenge=c.challenge,
            areas_granted=list(c.areas_granted) if c.areas_granted is not None else None, expires_at=c.expires_at))
        self.db.flush()

    def take_code(self, code_hash: str) -> Any:
        row = self.db.get(AppConnectionCode, code_hash)
        if row is None:
            return None
        found = nexverbinden.Code(
            code_hash=row.code_hash, account_id=row.account_id, partner=row.partner, partner_base=row.partner_base,
            redirect_uri=row.redirect_uri, challenge=row.challenge,
            areas_granted=list(row.areas_granted) if row.areas_granted is not None else None,
            expires_at=_aware(row.expires_at))
        self.db.delete(row)
        self.db.flush()
        return found

    # --- spent ------------------------------------------------------------------------------------------------------

    def spend(self, connection_id: str, token_hash: str, keep: int) -> None:
        if self.db.scalar(select(AppConnectionSpent).where(AppConnectionSpent.token_hash == token_hash)) is None:
            self.db.add(AppConnectionSpent(token_hash=token_hash, connection_id=connection_id))
            self.db.flush()
        kept = (select(AppConnectionSpent.id).where(AppConnectionSpent.connection_id == connection_id)
                .order_by(AppConnectionSpent.id.desc()).limit(keep))
        self.db.execute(delete(AppConnectionSpent).where(AppConnectionSpent.connection_id == connection_id,
                                                         AppConnectionSpent.id.not_in(kept)))
        self.db.flush()

    def spent_by(self, token_hash: str) -> str | None:
        row = self.db.scalar(select(AppConnectionSpent).where(AppConnectionSpent.token_hash == token_hash))
        return row.connection_id if row is not None else None

    # --- revocations that wait ---------------------------------------------------------------------------------------

    def revoke_later(self, account_id: Any, partner_base: str, sealed_token: str, now: datetime) -> None:
        self.db.add(AppConnectionWaiting(account_id=int(account_id), partner_base=partner_base, token=sealed_token,
                                         since=now))
        self.db.flush()

    def waiting(self) -> list[Any]:
        rows = self.db.scalars(select(AppConnectionWaiting).order_by(AppConnectionWaiting.id))
        return [nexverbinden.Waiting(row.id, row.partner_base, row.token, _aware(row.since)) for row in rows]

    def done_waiting(self, waiting_id: Any) -> None:
        self.db.execute(delete(AppConnectionWaiting).where(AppConnectionWaiting.id == int(waiting_id)))
        self.db.flush()

    # --- the account and the app -------------------------------------------------------------------------------------

    def account_may(self, account_id: Any) -> bool:
        row = self.db.get(Account, int(account_id))
        return row is not None and not account_out(row)

    def account_name(self, account_id: Any) -> str:
        row = self.db.get(Account, int(account_id))
        return row.name if row is not None else "?"

    def areas(self, account_id: Any) -> list[dict[str, Any]]:
        """The calendars the account sees, its own first (the same list as for API keys)."""
        return [{"id": item.calendar.id, "name": item.calendar.name}
                for item in calendars.accesses(self.db, int(account_id))]

    def legacy(self, account_id: Any) -> list[Any]:
        """nextime's connections typed in by hand (TIME-25 nextasks, nexpaper), as the cards show them."""
        found = []
        tasks = self.db.get(NextasksConnection, int(account_id))
        # A row nextime keeps for a connection made by a click is no connection of before (V18).
        if tasks is not None and not _by_click(tasks.token_sealed, f"nextasks:{account_id}"):
            found.append(nexverbinden.Legacy(
                partner="nextasks", partner_base=tasks.url,
                shown=_shown(tasks.token_sealed, f"nextasks:{account_id}"),
                ok_at=_aware(tasks.checked_at),
                problem="refused" if tasks.problem == "nextasks_token_invalid" else None))
        paper = self.db.get(NexpaperConnection, int(account_id))
        if paper is not None and not _by_click(paper.token_sealed, f"nexpaper:{account_id}"):
            found.append(nexverbinden.Legacy(
                partner="nexpaper", partner_base=paper.url,
                shown=_shown(paper.token_sealed, f"nexpaper:{account_id}"),
                ok_at=_aware(paper.checked_at),
                problem="refused" if paper.problem == "nexpaper_token_invalid" else None))
        return found

    def retire_key(self, token: str) -> bool:
        """An API key of nextime, brought by the partner after it connected anew, ends itself."""
        if not token.startswith(apikeys.PREFIX):
            return False
        row = self.db.scalar(select(ApiKey).where(ApiKey.token_hash == apikeys.digest(token)))
        if row is None:
            return False
        self.db.delete(row)
        self.db.flush()
        return True

    def setting(self, name: str) -> Any:
        return settings_service.get(self.db, SETTINGS[name])

    def set_setting(self, name: str, value: Any) -> None:
        settings_service.save(self.db, {SETTINGS[name]: value}, commit=False)

    def local_allowed(self) -> bool:
        return bool(settings_service.get(self.db, "connections_local_network"))

    def seal(self, text: str) -> str:
        return vault.seal({"text": text}, PLACE)

    def unseal(self, sealed: str) -> str:
        opened = vault.open_(sealed, PLACE) or {}
        return str(opened.get("text") or "")

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


def old_key(store: Store, account_id: Any, partner: str) -> str | None:
    """The API key of nextime's connection typed in by hand with ``partner``: after "Neu verbinden" it ends itself
    there (the module brings it to the partner's ``/api/connect/retire``)."""
    model, place = {"nextasks": (NextasksConnection, "nextasks"), "nexpaper": (NexpaperConnection, "nexpaper")}.get(
        partner, (None, ""))
    if model is None:
        return None
    row = store.db.get(model, int(account_id))
    if row is None:
        return None
    opened = vault.open_(row.token_sealed, f"{place}:{account_id}") or {}
    # A row for a connection made by a click holds no key (``connection``).
    return str(opened.get("token") or "") or None


def connected(store: Store, account_id: Any, partner: str) -> None:
    """``on_connected`` of the module: nextime's services take the new connection as they took a key (V18)."""
    from . import nexpaper, nextasks

    if partner == "nextasks":
        nextasks.connected_by_click(store.db, int(account_id))
    elif partner == "nexpaper":
        nexpaper.connected_by_click(store.db, int(account_id))



#: How often the revocations a partner has not heard yet are tried again.
RETRY_EVERY = 300.0


async def run_forever(stop: Any) -> None:
    """Every five minutes the revocations that wait (``nexverbinden.retry_waiting``); a side matter that never ends
    the loop."""
    import asyncio
    import logging

    from ..db import SessionLocal

    def once() -> None:
        with SessionLocal() as db:
            nexverbinden.retry_waiting(Store(db), CONFIG, datetime.now(UTC))

    while not stop.is_set():
        try:
            await asyncio.to_thread(once)
        except Exception:
            logging.getLogger("nextime.verbinden").exception("Waiting revocations could not be tried again")
        try:
            await asyncio.wait_for(stop.wait(), timeout=RETRY_EVERY)
        except TimeoutError:
            continue


def end_all(db: Session, account_id: int) -> int:
    """The account is gone for good: every connection ends, the partners hear it (or later)."""
    return int(nexverbinden.end_all(Store(db), CONFIG, account_id, datetime.now(UTC)))


# --- The guest's side: nextime's own services over a connection made by a click (V18) ---------------------------------


class NotConnected(Exception):
    """The connection is gone, refused or cannot reach the partner: ``code`` is the module's (``not_connected``,
    ``unreachable``, ``refused``, ``resting``, ``partner_off``)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def access(db: Session, account_id: int, partner: str) -> tuple[str, str]:
    """The partner's address and a valid access token of the account's connection with it, renewed when it runs out
    (``nexverbinden.access``, V18), so that nextime's services (``services/nextasks.py``, ``services/nexpaper.py``)
    talk to the partner as they did with a key typed in by hand: the same routes, the same rules for the address."""
    try:
        found = nexverbinden.access(Store(db), CONFIG, account_id, partner, datetime.now(UTC))
    except nexverbinden.Refused as refusal:
        raise NotConnected(refusal.code) from None
    return found.base, found.token


def reached(db: Session, account_id: int, partner: str, problem: str | None) -> None:
    """How a request of nextime's own service went, for the card (``nexverbinden.reached``): ``None`` it answered,
    ``unreachable``, or the partner's refusal (``token_invalid``)."""
    nexverbinden.reached(Store(db), account_id, partner, datetime.now(UTC), problem=problem)


def has_connection(db: Session, account_id: int, partner: str) -> bool:
    return Store(db).connection(account_id, partner) is not None


def disconnect(db: Session, account_id: int, partner: str) -> None:
    """"Trennen" from nextime's own card of a connection made by a click: both sides end (V3)."""
    try:
        nexverbinden.disconnect(Store(db), CONFIG, account_id, partner, datetime.now(UTC))
    except nexverbinden.Refused:
        pass
