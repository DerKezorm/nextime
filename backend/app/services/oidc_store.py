"""nextime's side of the shared sign-in module ``vendor/nexoidc``: the ``Store`` over its tables, the start-up
configuration, and the address an account takes from a provider.

The module (the shared blueprint "Anmeldung mit OIDC und authentik") runs the protocol, decides who an identity is
and keeps the provider list; it never sees a database. What is nextime's own lives here: accounts and their names,
the role an invitation brings, the address an account takes from its provider, and the encryption of the client
secrets with the server secret. nextime was born with the provider list: there is no older single provider to migrate
from and no way back to write.

A ``SqlStore`` works inside one SQLAlchemy session, one request. The module commits when an outcome is settled and
rolls back when it refuses after a write (a consumed invitation comes back that way). A store made with ``hold``
only flushes where the module commits: its caller commits once, together with what it writes itself.
"""

from __future__ import annotations

import logging
import re
import unicodedata

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..db import SessionLocal
from ..models import (
    MEMBER,
    SIGN_IN_OIDC,
    SIGN_IN_PASSWORD,
    Account,
    OidcLink,
    OidcProvider,
    utcnow,
)
from ..models import Invite as InviteRow
from ..security import decrypt_secret, encrypt_secret
from ..vendor import nexkonten, nexoidc
from ..vendor.nexoidc import (
    AccountState,
    Invite,
    Link,
    Provider,
    ProviderValues,
    secret_context,
)
from . import accounts, settings_service
from .konten_store import KontenStore
from .konten_store import provider_picture as konten_provider_picture

logger = logging.getLogger("nextime.oidc")

APP_NAME = "nextime"
#: Where nextime's sign-in and account pages are, as the module sends the browser there.
LOGIN_PAGE = "/login"
ACCOUNT_PAGE = "/account"
HOME = "/"
#: Longest account name (``accounts.NAME_PATTERN``).
NAME_MAX = 64
_NUMBERED = re.compile(r"^(.*?)(-\d+)$")
#: Where ``Account.email`` came from when a provider gave it.
FROM_PROVIDER = "provider"


def _public_url() -> str:
    with SessionLocal() as db:
        return settings_service.public_url(db)


def save_public_url(url: str) -> None:
    """The address the operator confirmed in the authentik card becomes the public address (the setting
    ``public_url`` reads first), in a transaction of its own and committed at once: it outlives a restart, and a run
    of the button that fails later keeps it."""
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": url})


def _server_secret() -> str:
    return get_settings().resolved_secret_key()


def configure() -> None:
    """Tell the module who it runs in; called once at start-up (and by the tests)."""
    nexoidc.configure(
        nexoidc.AppConfig(
            app_name=APP_NAME,
            server_secret=_server_secret,
            public_url=_public_url,
            save_public_url=save_public_url,
            login_page=LOGIN_PAGE,
            account_page=ACCOUNT_PAGE,
            home=HOME,
            # Two instances on one host (say :8500 and :8505) keep their attempts apart, as their sessions.
            cookie_suffix=get_settings().cookie_name_suffix(),
        )
    )


def clean_name(raw: str) -> str:
    """Since ``oidc`` 1.1.0 the naming rules of the shared account module (the same in every app): lower case,
    ``a-z 0-9 . _ -``, umlauts written out (``Müller`` becomes ``mueller``), a number at the end survives the cut; ""
    when nothing usable is left."""
    return nexkonten.names.clean_username(raw)


def clean_address(value: str | None) -> str:
    """A provider's address as nextime keeps addresses: one line, no control or format characters, the form of
    ``accounts.EMAIL_PATTERN``, at most 255 characters; "" when it is none of that."""
    text = str(value or "").strip()
    if not text or len(text) > 255 or any(unicodedata.category(char) in ("Cc", "Cf") for char in text):
        return ""
    return text if accounts.EMAIL_PATTERN.match(text) else ""


def address_taken(db: Session, address: str, own_id: int) -> bool:
    """Another account has this address already: a provider's address never moves to a second account."""
    found = db.scalar(
        select(Account.id).where(func.lower(Account.email) == address.lower(), Account.id != own_id).limit(1)
    )
    return found is not None


def from_provider(db: Session, account: Account, raw: str | None, provider: str = "") -> None:
    """What a provider says about the address at a sign-in or a link (the caller commits), through the shared module
    (``nexkonten.profiles.provider_email``): ``email_verified`` is never read, the address finds nobody. An account
    through providers only follows it (it does not count and is not proven, and an own address stays); an account
    with a password is only offered it and takes it in its profile, with its password, like a change of address. Never
    an address another account has, never one the account declined ("No, thanks" holds for that address for good).
    ``provider``: the provider's name as on its button, for "Use the address from <provider>?" (konten 0.3.4)."""
    address = clean_address(raw)
    if not address:
        return
    db.flush()
    nexkonten.profiles.provider_email(KontenStore(db), account.id, address, provider=provider)
    db.refresh(account)


def offered_address(db: Session, account: Account) -> str:
    """The address the profile offers ("Use the address from <provider>?"); "" for none."""
    konto = KontenStore(db).get_account(account.id)
    offer = konto.offered_email if konto is not None else ""
    return "" if not offer or address_taken(db, offer, account.id) else offer


def _own_links(db: Session, account_id: int) -> bool:
    """The account still has a link to a provider."""
    found = db.scalar(select(OidcLink.id).where(OidcLink.account_id == account_id).limit(1))
    return found is not None


def has_own_links(db: Session, account_id: int) -> bool:
    return _own_links(db, account_id)


def links_gone(db: Session, account_id: int) -> None:
    """No provider of its own left for the account: an address that came from one goes too (also the offer), an own
    one stays. Not while another link holds."""
    if _own_links(db, account_id):
        return
    account = db.get(Account, account_id)
    if account is None:
        return
    if account.email_source == FROM_PROVIDER:
        account.email = ""
        account.email_source = ""
        account.email_confirmed = True
        account.email_proven = False
    account.provider_email = ""
    account.provider_email_from = ""
    account.provider_email_off = ""


class SqlStore:
    """``nexoidc.Store`` over ``oidc_providers`` and ``oidc_links``, inside one session."""

    def __init__(self, db: Session, *, hold: bool = False) -> None:
        self.db = db
        self.hold = hold

    # --- Providers -------------------------------------------------------------------------------------------------

    def _provider(self, row: OidcProvider | None) -> Provider | None:
        if row is None:
            return None
        return Provider(
            id=row.id,
            slug=row.slug,
            label=row.label,
            issuer=row.issuer,
            client_id=row.client_id,
            client_secret=decrypt_secret(row.client_secret_enc, secret_context(row.id)),
            scopes=row.scopes,
            enabled=row.enabled,
            auto_create=row.auto_create,
            trusts_second_factor=row.trusts_second_factor,
            managed=row.managed,
            position=row.position,
            redirect_path=row.redirect_path,
            created_at=row.created_at,
            registered_redirect=row.registered_redirect or "",
        )

    def list_providers(self) -> list[Provider]:
        rows = self.db.scalars(select(OidcProvider).order_by(OidcProvider.position, OidcProvider.id))
        return [provider for provider in (self._provider(row) for row in rows) if provider is not None]

    def get_provider(self, provider_id: int) -> Provider | None:
        return self._provider(self.db.get(OidcProvider, provider_id))

    def provider_by_slug(self, slug: str) -> Provider | None:
        return self._provider(self.db.scalar(select(OidcProvider).where(OidcProvider.slug == slug)))

    @staticmethod
    def _apply(row: OidcProvider, values: ProviderValues) -> None:
        row.slug, row.label, row.issuer, row.client_id = values.slug, values.label, values.issuer, values.client_id
        row.scopes, row.enabled, row.auto_create = values.scopes, values.enabled, values.auto_create
        row.trusts_second_factor, row.managed, row.position = (
            values.trusts_second_factor,
            values.managed,
            values.position,
        )
        row.redirect_path = values.redirect_path
        if values.registered_redirect is not None:
            # None keeps what the button registered last; only the button writes it.
            row.registered_redirect = values.registered_redirect

    def insert_provider(self, values: ProviderValues) -> Provider:
        row = OidcProvider()
        self._apply(row, values)
        self.db.add(row)
        self.db.flush()
        row.client_secret_enc = encrypt_secret(values.client_secret or "", secret_context(row.id))
        self.db.flush()
        provider = self._provider(row)
        assert provider is not None
        return provider

    def update_provider(self, provider_id: int, values: ProviderValues) -> Provider:
        row = self.db.get(OidcProvider, provider_id)
        assert row is not None
        self._apply(row, values)
        if values.client_secret is not None:
            row.client_secret_enc = encrypt_secret(values.client_secret, secret_context(row.id))
        self.db.flush()
        provider = self._provider(row)
        assert provider is not None
        return provider

    def delete_provider(self, provider_id: int) -> None:
        # Foreign keys are on (db.py), the links would go anyway; said here so that nothing depends on a pragma, and
        # so that an address that came with the last of an account's links goes along.
        self.drop_links(provider_id)
        self.db.execute(delete(OidcProvider).where(OidcProvider.id == provider_id))
        self.db.flush()

    # --- Links -----------------------------------------------------------------------------------------------------

    @staticmethod
    def _link(row: OidcLink) -> Link:
        return Link(row.provider_id, row.subject, row.account_id, row.issuer, row.email, row.created_at,
                    row.last_used_at)

    def find_link(self, provider_id: int, subject: str) -> Link | None:
        row = self.db.scalar(select(OidcLink).where(OidcLink.provider_id == provider_id, OidcLink.subject == subject))
        return self._link(row) if row else None

    def links_of_provider(self, provider_id: int) -> list[Link]:
        rows = self.db.scalars(select(OidcLink).where(OidcLink.provider_id == provider_id).order_by(OidcLink.id))
        return [self._link(row) for row in rows]

    def links_of_account(self, account_id: int) -> list[Link]:
        rows = self.db.scalars(select(OidcLink).where(OidcLink.account_id == account_id).order_by(OidcLink.id))
        return [self._link(row) for row in rows]

    def add_link(self, link: Link) -> None:
        self.db.add(OidcLink(provider_id=link.provider_id, subject=link.subject, account_id=link.account_id,
                             issuer=link.issuer, email=link.email))
        self.db.flush()
        self._address(link.provider_id, link.account_id, link.email)

    def remove_link(self, provider_id: int, account_id: int) -> bool:
        result = self.db.execute(
            delete(OidcLink).where(OidcLink.provider_id == provider_id, OidcLink.account_id == account_id)
        )
        removed = bool(result.rowcount)  # type: ignore[attr-defined]
        if removed:
            self.db.flush()
            links_gone(self.db, account_id)
        return removed

    def drop_links(self, provider_id: int) -> int:
        holders = set(self.db.scalars(select(OidcLink.account_id).where(OidcLink.provider_id == provider_id)))
        result = self.db.execute(delete(OidcLink).where(OidcLink.provider_id == provider_id))
        self.db.flush()
        for account_id in holders:
            links_gone(self.db, account_id)
        return int(result.rowcount or 0)  # type: ignore[attr-defined]

    def touch_link(self, provider_id: int, subject: str, email: str | None) -> None:
        self.db.execute(
            update(OidcLink)
            .where(OidcLink.provider_id == provider_id, OidcLink.subject == subject)
            .values(last_used_at=utcnow(), email=email)
        )
        account_id = self.db.scalar(
            select(OidcLink.account_id).where(OidcLink.provider_id == provider_id, OidcLink.subject == subject)
        )
        if account_id is not None:
            self._address(provider_id, account_id, email)

    def _address(self, provider_id: int, account_id: int, email: str | None) -> None:
        """What the provider says about the address treats it (``from_provider``)."""
        if not email:
            return
        account = self.db.get(Account, account_id)
        if account is not None:
            entry = self.db.get(OidcProvider, provider_id)
            from_provider(self.db, account, email, provider=entry.label if entry is not None else "")

    # --- Accounts --------------------------------------------------------------------------------------------------

    def account_state(self, account_id: int) -> AccountState | None:
        row = self.db.get(Account, account_id)
        if row is None:
            return None
        return AccountState(
            id=row.id,
            name=row.name,
            # Blocked or deleted by the operator: no way in through a provider either.
            blocked=row.blocked_at is not None or row.deleted_at is not None,
            has_password=row.sign_in == SIGN_IN_PASSWORD and bool(row.password_hash),
            has_second_factor=bool(row.totp_secret_enc),
        )

    def clean_name(self, raw: str) -> str:
        return clean_name(raw)

    def name_taken(self, name: str) -> bool:
        """Taken: another account has the name now or had it, it belonged to an account deleted for good, or it is
        somebody else's display name (the shared account module)."""
        return nexkonten.names.username_taken(KontenStore(self.db), name)

    def create_account(
        self, name: str, *, invite: Invite | None, email: str | None, display_name: str = "", picture: str = ""
    ) -> int:
        """A new account through a provider only (no password).

        - **Display name:** the provider's (claim ``name``), else the invitation's, else empty; one another account
          shows stays empty (unique, the account module).
        - **Address:** the invitation's (the operator typed it; it counts, proven when the link went by mail), else
          the provider's when no other account has it: then it follows the provider and does not count. It never
          finds an account.
        - **Picture:** comes through ``provider_picture``, not here."""
        store = KontenStore(self.db)
        invitation = invite.rights if invite is not None and isinstance(invite.rights, InviteRow) else None
        shown = nexkonten.names.provider_display_name(store, display_name or "")
        if not shown and invitation is not None:
            shown = nexkonten.names.provider_display_name(store, invitation.display_name or "")
        address = ""
        if invitation is not None and invitation.email and not address_taken(self.db, invitation.email, 0):
            address = invitation.email
        row = Account(
            name=name,
            display_name=shown,
            role=MEMBER,
            sign_in=SIGN_IN_OIDC,
            password_hash="",
            email=address,
            email_confirmed=True,
            email_proven=bool(address and invitation is not None and invitation.mailed),
            # Since E45: a new account through a provider starts with "system" as every new one.
            theme="system",
            whats_new_seen=__version__,
        )
        self.db.add(row)
        self.db.flush()
        if not address and clean_address(email):
            nexkonten.profiles.provider_email(store, row.id, clean_address(email))
            self.db.refresh(row)
        if invitation is not None:
            accounts.redeem(self.db, invitation, row, consumed=True, commit=False)
        logger.info("Account created name=%s role=%s sign_in=oidc", row.name, row.role)
        return row.id

    def provider_picture(self, account_id: int, picture: str, issuer: str) -> None:
        """After every sign-in through a provider that sends a picture: an account **without** a picture gets the
        provider's, in a thread of its own (a fetch takes up to five seconds). An own picture is never replaced."""
        row = self.db.get(Account, account_id)
        if row is None or row.avatar_at is not None:
            return
        konten_provider_picture(row.id, picture, issuer)

    # --- Invitations -----------------------------------------------------------------------------------------------

    def find_invite(self, key: str) -> Invite | None:
        row = accounts.find_invite(self.db, key)
        return Invite(key=key, id=row.id, rights=row) if row is not None else None

    def consume_invite(self, invite: Invite) -> bool:
        row = invite.rights
        return isinstance(row, InviteRow) and accounts.consume(self.db, row)

    def accept_invite(self, account_id: int, invite: Invite) -> None:
        """A known identity that came through an invitation: the invitation's role comes on top of what the account
        has (raised, never lowered); the invitation is used up already (``consume_invite``)."""
        row = invite.rights
        account = self.db.get(Account, account_id)
        if account is not None and isinstance(row, InviteRow):
            accounts.redeem(self.db, row, account, consumed=True, commit=False)

    # --- Transaction -----------------------------------------------------------------------------------------------

    def commit(self) -> None:
        if self.hold:
            self.db.flush()
        else:
            self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


def linked_providers(db: Session) -> dict[int, list[dict[str, object]]]:
    """Per account the providers it is linked to, in the order of the list (marks in the operator's account list)."""
    linked: dict[int, list[dict[str, object]]] = {}
    for link, entry in db.execute(
        select(OidcLink, OidcProvider)
        .join(OidcProvider, OidcProvider.id == OidcLink.provider_id)
        .order_by(OidcProvider.position, OidcProvider.id)
    ):
        linked.setdefault(link.account_id, []).append(
            {"id": entry.id, "slug": entry.slug, "label": entry.label, "managed": entry.managed}
        )
    return linked
