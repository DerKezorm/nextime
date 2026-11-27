"""The data model.

Everything lives in SQLite. This frame holds accounts, sessions, sign-in providers, invitations and the operator's
settings. Calendars and events come with their own tables (see ``services/content.py`` for where they report back to
the accounts).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    event,
    false,
    text,
    true,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """SQLite forgets the time zone. Stored as UTC, read back as UTC with the zone attached."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    pass


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)


OPERATOR = "operator"
MEMBER = "member"
ROLES = (OPERATOR, MEMBER)

SIGN_IN_PASSWORD = "password"
SIGN_IN_OIDC = "oidc"

#: How long a deleted account can be brought back by the operator before it goes for good.
DELETE_GRACE_DAYS = 30


class Account(Base):
    """A person. The first account is the operator; the others come by invitation or through OIDC."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: The username (the shared account module): lower case, unique without regard to case, chosen at setup or
    #: invitation and changeable in the profile; a former one stays reserved for good (``former_names``). People sign
    #: in with it or with their address. The field keeps its name ``name`` in every route.
    name: Mapped[str] = mapped_column(String(64), unique=True)
    #: The calendar account's fixed name in every CalDAV address (``/dav/principals/<dav_name>/`` ...): the username
    #: when the account was made and never changed after, also not by a rename, so that no calendar program loses its
    #: calendars. Accounts of 0.4.0 get their name of then at the first start (``services/konten_wanderung``); while
    #: it is empty the name counts (``devices.dav_name_of``).
    dav_name: Mapped[str] = mapped_column(String(64), default="", server_default="")
    #: What people see: the name as typed (umlauts and emoji kept, blanks at the edges gone).
    display_name: Mapped[str] = mapped_column(String(80), default="")
    #: The version whose "What's new" the account has read or put away.
    whats_new_seen: Mapped[str] = mapped_column(String(32), default="")
    role: Mapped[str] = mapped_column(String(16), default=MEMBER)
    sign_in: Mapped[str] = mapped_column(String(16), default=SIGN_IN_PASSWORD)
    #: Argon2id. Empty for accounts that sign in through OIDC only.
    password_hash: Mapped[str] = mapped_column(String(255), default="")
    password_changed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Unique ignoring case; empty for an account without one.
    email: Mapped[str] = mapped_column(String(255), default="")
    #: Where ``email`` came from: "" (typed or an invitation) or ``provider`` (a sign-in provider's address). An
    #: address from a provider goes with the account's last link (the shared sign-in module).
    email_source: Mapped[str] = mapped_column(String(16), default="", server_default="")
    #: The address **counts** (signing in, a forgotten password, notices; the shared account module). Every address
    #: of 0.4.0 counts, also one taken over from a provider: whoever signed in with it keeps doing so.
    email_confirmed: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    #: Somebody opened a link mailed to the address (a confirmation link). 0.4.0 mailed none: false for the Bestand.
    email_proven: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: The address a provider knows the account by, offered on the account page to an account with a password (it
    #: takes it only when it says so); ``provider_email_off``: the address the account said "don't ask again" to.
    provider_email: Mapped[str] = mapped_column(String(255), default="", server_default="")
    provider_email_off: Mapped[str] = mapped_column(String(255), default="", server_default="")
    #: The name of the provider whose address is offered, as on its button (konten 0.3.4, ``offered_email_provider``).
    provider_email_from: Mapped[str] = mapped_column(String(80), default="", server_default="")
    #: The interface language chosen under "My account"; empty: the browser's. Travels to every device.
    language: Mapped[str] = mapped_column(String(16), default="")
    #: The browser's language at the last sign-in: what a mail to the account is written in while ``language`` is
    #: empty.
    browser_language: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Set when the operator blocked the account: no sign-in, sessions and device tokens end at once.
    blocked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Set when the operator deleted the account. It counts as blocked and as gone for everybody else; for 30 days the
    #: operator can bring it back, then it goes with everything it owns.
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The password was set through a reset link of the operator: the account hears so at its next sign-in, until it
    #: puts the notice away (the operator never signs in as somebody else unnoticed).
    password_reset_notice: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: The most this account could keep, in bytes, until konten 1.1.0; None: no limit. Since then nextime has no storage
    #: limit per account (plan "Einstellungen", E24): the column stays as it is (nothing is deleted), nothing reads it.
    storage_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Light, dark or as the system (``light``, ``dark``, ``system``; "" chose nothing yet), kept at the account on
    #: every device since konten 1.1.0; the first load takes the browser's value of before once
    #: (``profiles.adopt_theme``).
    theme: Mapped[str] = mapped_column(String(16), default="", server_default="")
    #: The second factor (``services/totp.py``): the seed encrypted with the server secret, empty while off; the
    #: recovery codes as a JSON list of SHA-256 hashes; the time step of the last code taken (no replay).
    totp_secret_enc: Mapped[str] = mapped_column(Text, default="")
    totp_recovery: Mapped[str] = mapped_column(Text, default="")
    totp_last_step: Mapped[int] = mapped_column(Integer, default=0)
    #: An authenticator being set up (the shared account module, since 0.3.3): its seed sealed in a context of its
    #: own, waiting ten minutes for its first code; it survives a restart between the QR code and that code. Never a
    #: factor while it waits.
    totp_pending: Mapped[str] = mapped_column(Text, default="", server_default="")
    totp_pending_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The profile picture (``services/avatars.py``): a square WebP drawn anew, loaded only when asked for.
    avatar: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    avatar_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The color of the initial's circle, an index into the eleven calendar colors (TIME-2 EC-11): given once, the
    #: one fewest others have, so that up to eleven people never share one. ``None`` until first shown.
    color: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: "" for a picture the person set (and every picture of 0.4.0), ``provider`` for one taken from a sign-in
    #: provider: a provider's picture never replaces an own one.
    avatar_source: Mapped[str] = mapped_column(String(16), default="", server_default="")
    #: The random user handle of the account's passkeys (WebAuthn), made with the first passkey and kept.
    passkey_handle: Mapped[str] = mapped_column(String(64), default="", server_default="")
    #: "A mail at a new sign-in" (on from the start): a browser that never signed in as the account before.
    new_sign_in_mail: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    #: The password was set through a link: where it came from (``self``, ``operator``, ``server``), "" for none.
    #: The account page then lists the ways in that stayed ("These devices still have access"), until put away.
    new_password_origin: Mapped[str] = mapped_column(String(16), default="", server_default="")
    #: The account's own preferences; only what it chose.
    preferences: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: What the device cookies of this account are signed with (``security.device_token``): drawn anew on a new
    #: password, a block or unblock, "sign out everywhere" and a reset second factor, so no browser from before counts
    #: as known any more.
    device_key: Mapped[str] = mapped_column(String(64), default="")
    #: Not stored. True on the account of a session that came through a provider which checks the second factor.
    provider_checked: ClassVar[bool] = False

    @property
    def shown_name(self) -> str:
        return self.display_name or self.name


def new_device_key() -> str:
    return secrets.token_urlsafe(24)


@event.listens_for(Account, "before_insert")
def _first_device_key(_mapper: Any, _connection: Any, target: Account) -> None:
    if not target.device_key:
        target.device_key = new_device_key()
    # The calendar account's fixed name: the first username, whichever way the account is made (setup, invitation,
    # provider, tests); a rename never touches it.
    if not target.dav_name:
        target.dav_name = target.name


@event.listens_for(Account.password_hash, "set")
@event.listens_for(Account.blocked_at, "set")
@event.listens_for(Account.deleted_at, "set")
def _forget_devices(target: Account, value: Any, old: Any, _initiator: Any) -> None:
    """A new password, a block or an unblock: the browsers known before are not known any more, whatever the way it
    happens."""
    if value != old:
        target.device_key = new_device_key()


class AuthSession(Base):
    """A browser session. Only the hash of the token is stored; the token itself lives in the cookie."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(255), default="")
    #: This sign-in came through a provider that checks the second factor itself (the entry's switch was on): the
    #: operator's "second factor required" does not force a setup for this session, whatever the account is.
    provider_checked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: The id the list "Where you are signed in" names the session by (the token's hash never leaves the server).
    #: Sessions of 0.4.0 get one at the first start (``services/konten_wanderung``).
    uid: Mapped[str | None] = mapped_column(String(32), nullable=True, unique=True, index=True)
    #: "Stay signed in": 30 days from the last use, sliding; without it the session ends with the browser and lives
    #: at most 12 hours. Sessions of 0.4.0 were all remembered (30 days).
    remember: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    #: ``full``, or ``setup``: the session may only set up the second factor the operator requires (15 minutes).
    stage: Mapped[str] = mapped_column(String(8), default="full", server_default="full")
    #: ``browser``; ``waiting`` a sign-in that waits for its second factor, ``device`` a paired device's session
    #: (the shared account module's kinds; nextime's paired apps hold their own rows, ``app_devices``). 0.4.0:
    #: browsers only.
    kind: Mapped[str] = mapped_column(String(8), default="browser", server_default="browser")


class OidcProvider(Base):
    """A sign-in provider (OpenID Connect), one row each; the columns as the shared sign-in blueprint names them. Read
    and written only through ``services/oidc_store.py`` for the shared module ``vendor/nexoidc``."""

    __tablename__ = "oidc_providers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Part of the callback address ``/api/oidc/<slug>/callback``; fixed once made.
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    #: The name on the sign-in button.
    label: Mapped[str] = mapped_column(String(64))
    #: Stored without blanks and without a slash at the end.
    issuer: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(255))
    #: Encrypted with the server secret under the context ``oidc-provider:<id>``; empty for a public client.
    client_secret_enc: Mapped[str] = mapped_column(Text, default="")
    scopes: Mapped[str] = mapped_column(String(500), default="openid profile email")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: "New people get an account": off from the start.
    auto_create: Mapped[bool] = mapped_column(Boolean, default=False)
    #: "The provider checks the second factor itself": on from the start.
    trusts_second_factor: Mapped[bool] = mapped_column(Boolean, default=True)
    #: "" by hand, "authentik" made by the authentik button, and the coupled value from the coupling.
    managed: Mapped[str] = mapped_column(String(20), default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    #: A callback path the provider knew before (a migration or a coupling sets it); "" is the standard one.
    redirect_path: Mapped[str] = mapped_column(String(300), default="")
    #: The redirect address the authentik button registered at authentik the last time ("" for none; an entry of
    #: before 1.0.10 has none). The card reports ``stale`` when today's address is another.
    registered_redirect: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class OidcLink(Base):
    """One identity at one provider, bound to one account. Found only by ``(provider_id, subject)``, never by an
    address: ``email`` is for display."""

    __tablename__ = "oidc_links"
    __table_args__ = (
        UniqueConstraint("provider_id", "subject"),
        UniqueConstraint("provider_id", "account_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider_id: Mapped[int] = mapped_column(ForeignKey("oidc_providers.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(255))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    #: The ``iss`` of the token that made the link (with Entra ``common`` the real tenant).
    issuer: Mapped[str] = mapped_column(String(500))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class Invite(Base):
    """A link that lets somebody in as a new account. Only the hash of the token is stored; used once, then gone."""

    __tablename__ = "invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    #: The role the new account gets.
    role: Mapped[str] = mapped_column(String(16), default=MEMBER)
    email: Mapped[str] = mapped_column(String(255), default="")
    #: What the operator gave as the display name (the form shows it); invitations of 0.4.0 have none.
    display_name: Mapped[str] = mapped_column(String(80), default="", server_default="")
    #: The link went by mail to ``email``: whoever comes through it proved the address.
    mailed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)


class PasswordReset(Base):
    """A link the operator makes for an account that forgot its password: the person sets a new one themselves, the
    operator never knows it. Only the hash of the token is stored; used once, then gone; one per account."""

    __tablename__ = "password_resets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    #: ``self`` ("Forgot your password?"), ``operator`` (under Accounts) or ``server`` (the emergency command). Every
    #: link of 0.4.0 was the operator's.
    origin: Mapped[str] = mapped_column(String(16), default="operator", server_default="operator")


# --- Calendars and events (TIME-4, TIME-5, TIME-6, TIME-20) -----------------------------------------------------------

#: The eleven calendar colors of the design system, by key; slate is the default (TIME-4 AC-1).
CALENDAR_COLORS = ("slate", "red", "orange", "yellow", "green", "teal", "blue", "violet", "pink", "brown", "gray")
DEFAULT_COLOR = "slate"

#: What a share lets the other account do. TIME-19 builds the sharing itself; the rights are checked from here on.
RIGHT_EDIT = "edit"
RIGHT_READ = "read"
RIGHT_BUSY = "busy"
RIGHTS = (RIGHT_EDIT, RIGHT_READ, RIGHT_BUSY)


#: Where a calendar comes from when it is not one of nextime's own (TIME-15, TIME-16, TIME-17).
SOURCE_ICLOUD = "icloud"
SOURCE_CALDAV = "caldav"
SOURCE_GOOGLE = "google"
SOURCE_SUBSCRIPTION = "subscription"
SOURCE_HOLIDAYS = "holidays"
SOURCE_KINDS = (SOURCE_ICLOUD, SOURCE_CALDAV, SOURCE_GOOGLE, SOURCE_SUBSCRIPTION, SOURCE_HOLIDAYS)
#: The kinds nextime writes into (and whose invitations it answers).
WRITABLE_SOURCES = (SOURCE_ICLOUD, SOURCE_CALDAV, SOURCE_GOOGLE)
#: An event a program entered from an invitation mail (``Event.origin``).
ORIGIN_IMIP = "imip"

#: The state of a source: syncing; its access no longer works ("Access expired since …"); not answering (the last
#: known events stay, changes wait); resting while the operator switched Google off.
STATE_OK = "ok"
STATE_EXPIRED = "expired"
STATE_UNREACHABLE = "unreachable"
STATE_PAUSED = "paused"


class Source(Base):
    """An account at iCloud, Google or another CalDAV server, a subscription link, or the holidays of one country,
    belonging to one member (TIME-15 to TIME-17). Its calendars are ordinary ``calendars`` rows with ``source_id``;
    disconnecting removes the row and with it calendars, events and the stored access (AC-5)."""

    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("account_id", "kind", "identity"),
        UniqueConstraint("account_id", "client_ref"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    #: What the row on "Calendars and accounts" names: the Apple ID, the user at the server, the Google address, the
    #: subscription link without its secret parts, the country.
    label: Mapped[str] = mapped_column(String(500), default="")
    #: The same access twice is one connection (TIME-15 EC-14): a keyed hash of server and user, of the link, or the
    #: country.
    identity: Mapped[str] = mapped_column(String(64))
    #: The CalDAV address the account was found at (not secret); empty for the other kinds.
    server: Mapped[str] = mapped_column(String(1000), default="")
    username: Mapped[str] = mapped_column(String(320), default="")
    #: The access as JSON, sealed with ``services/vault.py``: a password, a Google grant, a subscription link with
    #: its login. Never shown again, never logged, never in an answer (TIME-15 EC-21).
    secret_enc: Mapped[str] = mapped_column(Text, default="")
    client_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default=STATE_OK)
    #: Since when the state is what it is (for "Access expired since …").
    state_since: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Why, as a code: ``credentials``, ``key`` (a restored backup with another key), ``not_found`` …
    problem: Mapped[str] = mapped_column(String(64), default="")
    #: The last round that worked: "synced 1 minute ago", "shows the state of 15:52".
    synced_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: When the next round is due.
    due_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Rounds in a row that found the source not answering: the wait grows with them.
    failures: Mapped[int] = mapped_column(Integer, default=0)
    #: The calendars the account has there, as last seen: ``[{url, name, color, read_only, new}]``.
    remote: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: What the kind needs besides: the holidays' country, regions and school holidays; a subscription's ETag and
    #: Last-Modified; the hrefs of events that could not be read.
    options: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: The member's own addresses there (``mailto:…``), to find its invitations (TIME-18).
    addresses: Mapped[Any] = mapped_column(JSON, nullable=True)
    #: The server sends the answers to invitations itself (CalDAV scheduling, iCloud); Google answers through its API.
    schedules: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: The first fetch is still running (TIME-15 EC-12).
    loading: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class RemoteChange(Base):
    """A change made in nextime that still has to reach its source ("waiting", TIME-15 EC-2). Kept in the database,
    so that it survives a restart (EC-7); gone once the source took it or refused it for good (EC-6)."""

    __tablename__ = "remote_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"), index=True)
    href: Mapped[str] = mapped_column(String(255))
    #: ``put`` (the event as it is now), ``delete``, or ``reply`` (an answer through Google's API, TIME-18); ``silent``
    #: and ``quiet`` are a ``put`` and a ``reply`` that make the source send nothing (an answer that went out by mail,
    #: TIME-26).
    op: Mapped[str] = mapped_column(String(8), default="put")
    #: The version at the source the change was made on (for a delete: the one to delete).
    remote_etag: Mapped[str] = mapped_column(String(255), default="")
    #: For a reply: the answer and the occurrence it is for ("" for the whole series).
    answer: Mapped[str] = mapped_column(String(16), default="")
    occurrence: Mapped[str] = mapped_column(String(20), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class RemoteNotice(Base):
    """A change the source refused for good (TIME-15 EC-6, TIME-5 EC-5): the event shows the source's state again,
    and the member decides what becomes of the lost change ("Create as a new event", "Discard")."""

    __tablename__ = "remote_notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    calendar_id: Mapped[int | None] = mapped_column(ForeignKey("calendars.id", ondelete="SET NULL"), nullable=True)
    title: Mapped[str] = mapped_column(Text, default="")
    #: The change that did not arrive, as an iCalendar object; empty for a refused delete.
    lost_ical: Mapped[str] = mapped_column(Text, default="")
    source_kind: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class Calendar(Base):
    """A calendar on this server. What each account chooses for itself (hidden, position) lives in
    ``CalendarSetting``, so that a shared calendar changes only for the one who chose (TIME-4 EC-8)."""

    __tablename__ = "calendars"
    __table_args__ = (UniqueConstraint("owner_id", "client_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    #: The last part of its CalDAV address; unique on the server, chosen by a device that makes the calendar.
    dav_name: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    color: Mapped[str] = mapped_column(String(16), default=DEFAULT_COLOR)
    #: Its symbol, a name of ``services/symbols.py``, drawn in its color; "" for none (its color dot, as before). Only
    #: here, never sent to a source.
    icon: Mapped[str] = mapped_column(String(32), default="", server_default="")
    #: "Show as background": small at the top of a day (TIME-14 gives it its effect).
    background: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: Minutes before the start for new events; 0: none (TIME-7 gives it its effect).
    reminder: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: What the page sent along with "Create": the same request twice makes one calendar (EC-6).
    client_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Counts changes of name and color: part of the collection tag a device compares.
    props_rev: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: In the trash (TIME-23): gone for everybody, with its events.
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    #: When it goes for good (TIME-23): the deadline that held when it was deleted (EC-13); ``None`` before.
    trash_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The name of who deleted it, as it was then: the account may be gone by now (EC-12).
    deleted_by_name: Mapped[str] = mapped_column(String(80), default="", server_default="")
    #: Kept only for events in the trash after its source went (disconnected, no longer chosen, gone there; TIME-23
    #: EC-9): shown nowhere, not even as an entry of the trash, and gone with its last event.
    orphan: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: Where an import or a move puts its events, in short steps, until they are all there (TIME-24): deleted and an
    #: orphan, so nothing shows them, and kept by the cleanup of orphans until the job ends.
    staging: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: The source it mirrors (TIME-15 to TIME-17); ``None`` for nextime's own calendars.
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"), nullable=True,
                                                  index=True)
    #: Its address at the source (a CalDAV collection, a Google calendar id, ``holidays:DE:public``).
    remote_url: Mapped[str] = mapped_column(String(1000), default="", server_default="")
    #: Read only at the source (shared for reading in iCloud, a subscription, holidays, TIME-15 AC-6).
    read_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: Not to be changed while its source's access expired or Google is switched off (TIME-15 EC-1, TIME-16 EC-5).
    locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: What the source said last: its sync token and collection tag.
    sync_token: Mapped[str] = mapped_column(Text, default="", server_default="")
    ctag: Mapped[str] = mapped_column(String(255), default="", server_default="")


class CalendarSetting(Base):
    """What one account chose for one calendar it sees: hidden, its place in its group, whether its days stand out."""

    __tablename__ = "calendar_settings"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"), primary_key=True)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: "Notify me when others change something here" (TIME-21 AC-1): per account and calendar, off from the start.
    notify: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: The account's reminder for events of this calendar it chose nothing for (TIME-7 AC-3, AC-5, AC-9): "" as from
    #: the start (``services/reminders.py`` says what that is), ``none``, ``source`` (as the calendar's source has it),
    #: or minutes before the start.
    reminder_default: Mapped[str] = mapped_column(String(16), default="", server_default="")
    #: What a new event of this calendar starts with for this account (TIME-4, "Vorgaben für neue Termine"): whole day,
    #: length, repeat, reminders, busy; a key left out is as from the start. Only here: never sent to a source.
    new_defaults: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    #: "Highlight its days" (the days of its events tinted as holidays are): ``None`` as from the start, which is on
    #: for the built-in public holidays (``holidays:<country>:public``) and off for everything else (see
    #: ``services/calendars.highlighted``); a choice made wins, "off" for the public holidays too. Only here.
    highlight: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


class CalendarPrefs(Base):
    """One row per account: its default calendar, and that "Personal" was made (once, TIME-4 AC-10)."""

    __tablename__ = "calendar_prefs"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    default_calendar_id: Mapped[int | None] = mapped_column(
        ForeignKey("calendars.id", ondelete="SET NULL"), nullable=True)
    seeded: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class CalendarShare(Base):
    """A calendar shared with another account and the right it has (TIME-19 makes them)."""

    __tablename__ = "calendar_shares"

    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True,
                                            index=True)
    right: Mapped[str] = mapped_column(String(8), default=RIGHT_READ)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ShareNotice(Base):
    """A calendar shared with the account is no longer there: the owner ended the share, deleted the calendar,
    disconnected its source, or the owner's account was deleted (TIME-19 AC-14, EC-3, EC-17, EC-18). Shown under
    "Shared with you" until the account puts it away; the names are kept as they were, the calendar may be gone."""

    __tablename__ = "share_notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    calendar_id: Mapped[int | None] = mapped_column(ForeignKey("calendars.id", ondelete="SET NULL"), nullable=True,
                                                    index=True)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    calendar_name: Mapped[str] = mapped_column(Text, default="")
    owner_name: Mapped[str] = mapped_column(String(80), default="")
    color: Mapped[str] = mapped_column(String(16), default=DEFAULT_COLOR)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class GuestLink(Base):
    """The read link of an own calendar for guests without an account (TIME-19 AC-5): a page and a subscription.
    Found by the hash of its token; the token itself is kept sealed with the server's key, so that the owner can copy
    the same address again later (a new address would end every guest's subscription). One per calendar; revoked, the
    row goes, and switching it on again makes a new address (EC-20)."""

    __tablename__ = "guest_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"), unique=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    token_enc: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    #: The first instant it no longer holds (the day after the chosen last day, on the owner's clock); ``None``: until
    #: revoked. Compared with the server's clock (EC-19).
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The last day as the owner chose it, to show it again.
    expires_on: Mapped[str] = mapped_column(String(10), default="")


class Collection(Base):
    """A collection of calendars of one account (TIME-13), the same on every device. "All" is not stored: it is
    always every calendar the account shows. Which collection is chosen stays on the device."""

    __tablename__ = "collections"
    __table_args__ = (UniqueConstraint("account_id", "client_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    #: What the page sent along with "Save": the same request twice makes one collection (EC-10).
    client_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: "Meine Aufgaben · nextasks" is chosen in it (TIME-25 AC-7).
    with_tasks: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    #: The areas of nextasks in it (a JSON list of ids). Unset: every area when ``with_tasks`` (a collection from
    #: before areas, read so instead of rewritten, so no change of the schema can stop halfway).
    task_areas: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class CollectionCalendar(Base):
    """A calendar in a collection. A calendar in the trash keeps its place, so that it is back where it was when it
    comes back (TIME-13 EC-16); the views leave it out meanwhile."""

    __tablename__ = "collection_calendars"

    collection_id: Mapped[int] = mapped_column(ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"), primary_key=True,
                                             index=True)


class Event(Base):
    """One event as an iCalendar object (a VCALENDAR with its VEVENT and the exceptions of a series), and columns
    derived from it for quick questions. What a device brought along stays in ``ical`` untouched."""

    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_live_href", "calendar_id", "href", unique=True, sqlite_where=text("deleted_at IS NULL")),
        Index("ix_events_live_uid", "calendar_id", "uid", unique=True, sqlite_where=text("deleted_at IS NULL")),
        Index("ix_events_client_ref", "created_by", "client_ref", unique=True,
              sqlite_where=text("client_ref IS NOT NULL")),
        # An event by its UID across calendars: the API's lookup, a copy from a mail meeting its source (TIME-26).
        Index("ix_events_uid", "uid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"), index=True)
    uid: Mapped[str] = mapped_column(String(255))
    #: The last part of its CalDAV address (``<uid>.ics`` when made here).
    href: Mapped[str] = mapped_column(String(255))
    ical: Mapped[str] = mapped_column(Text)
    etag: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, default="")
    #: The first start; an all-day event's date at midnight UTC.
    starts_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    #: The last end; for a series the end of its last occurrence, ``None`` for a series without end.
    ends_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True, index=True)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    time_zone: Mapped[str] = mapped_column(String(64), default="")
    recurring: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    busy: Mapped[bool] = mapped_column(Boolean, default=True)
    location: Mapped[str] = mapped_column(Text, default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    #: What the page sent along with "Save": the same request twice makes one event (TIME-5 EC-8).
    client_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The version before the last change, to tell an edit made on an older one (TIME-5 EC-2, TIME-6 EC-10).
    prev_etag: Mapped[str] = mapped_column(String(64), default="", server_default="")
    prev_ical: Mapped[str] = mapped_column(Text, default="", server_default="", deferred=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    #: When it goes for good, and who deleted it by the name of then (TIME-23 EC-12, EC-13).
    trash_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    deleted_by_name: Mapped[str] = mapped_column(String(80), default="", server_default="")
    #: What the search looks in (TIME-22): title, place and notes of every part of the object, folded for case;
    #: ``None`` for a row written before (the search folds it itself, the start fills it in).
    search_text: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    #: In a calendar from a source: the version there, and the calendar that version belongs to (an event moved to
    #: another calendar is new there).
    remote_etag: Mapped[str] = mapped_column(String(255), default="", server_default="")
    remote_of: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: A change of it waits to reach its source ("waiting", TIME-15 EC-2).
    waiting: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: The lead times of the alarms the event's own text carries (an iCloud, Google or CalDAV event, TIME-7 AC-5),
    #: read when it is written, and the longest of them; ``None`` where it has none or was written before.
    alarm_leads: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    alarm_lead_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Written through a key of the API or MCP (TIME-26 AC-10): the key's name as it was then; "" for the web, a device
    #: or a source.
    updated_via: Mapped[str] = mapped_column(String(100), default="", server_default="")
    deleted_via: Mapped[str] = mapped_column(String(100), default="", server_default="")
    #: Where it came from when that matters later: ``imip`` for an invitation a program entered from a mail
    #: (``POST /api/v1/events/ical``); "" for everything else. A copy from a mail never sends anything itself, and it
    #: gives way once a source brings the same UID (TIME-26, for nexmail).
    origin: Mapped[str] = mapped_column(String(16), default="", server_default="")
    #: For a copy from a mail: the member's own address among its attendees, and the organizer's SEQUENCE it holds.
    origin_address: Mapped[str] = mapped_column(String(320), default="", server_default="")
    origin_sequence: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    #: Why it went into the trash when nobody deleted it: ``source_has_it`` (its source brought the same event),
    #: ``cancelled`` (the organizer cancelled an invitation entered from a mail); "" otherwise.
    deleted_reason: Mapped[str] = mapped_column(String(32), default="", server_default="")


class TrashedPart(Base):
    """A part of a series deleted on its own (TIME-23, TIME-5 AC-6; review of TIME-23, finding 2): one occurrence
    ("Only this one") or this one and all the following. The series stays; the part waits in the trash of the
    calendar's owner and goes back into the series when restored. ``removed`` keeps the changed occurrences it took
    along (as a VCALENDAR), ``before`` the whole object before cutting off the following ones and ``etag_after`` the
    version right after: unchanged since, restoring puts that object back; changed, the part comes back as a series of
    its own."""

    __tablename__ = "trashed_parts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    #: ``occurrence`` or ``following``.
    kind: Mapped[str] = mapped_column(String(12))
    #: The occurrence by its original start, as the API names it (``20261126T173000Z`` or ``20261126``).
    occurrence: Mapped[str] = mapped_column(String(20))
    title: Mapped[str] = mapped_column(Text, default="")
    #: Where it began (a moved occurrence at its new time), for the list.
    starts_at: Mapped[datetime] = mapped_column(UtcDateTime)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    time_zone: Mapped[str] = mapped_column(String(64), default="")
    removed: Mapped[str] = mapped_column(Text, default="", server_default="")
    before: Mapped[str] = mapped_column(Text, default="", server_default="", deferred=True)
    etag_after: Mapped[str] = mapped_column(String(64), default="", server_default="")
    #: Changed or cancelled occurrences went along ("samt Ausnahmen").
    exceptions: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    deleted_at: Mapped[datetime] = mapped_column(UtcDateTime)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    deleted_by_name: Mapped[str] = mapped_column(String(80), default="", server_default="")
    trash_until: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Deleted through a key of the API or MCP: its name (TIME-26 AC-10).
    deleted_via: Mapped[str] = mapped_column(String(100), default="", server_default="")


class EventChange(Base):
    """Every change to an event of a calendar, numbered: what ``sync-collection`` hands a device since its token."""

    __tablename__ = "event_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"), index=True)
    href: Mapped[str] = mapped_column(String(255))
    deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class EventConflict(Base):
    """Somebody saved over a change of this account a moment after it (TIME-5 EC-2): the account sees both versions
    the next time it opens the event, and may bring its own back."""

    __tablename__ = "event_conflicts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    by_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True)
    mine_ical: Mapped[str] = mapped_column(Text)
    theirs_etag: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class Device(Base):
    """A device with the calendar account (TIME-20): its own password, of which only the hash is kept."""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    password_hash: Mapped[str] = mapped_column(String(64), unique=True)
    #: The program, from what it calls itself ("Apple Calendar", "DAVx5"); empty until it first synced.
    client: Mapped[str] = mapped_column(String(80), default="")
    #: ``iphone``, ``ipad`` or ``mac`` for Apple Calendar (which reminds by itself there, TIME-7 AC-7), "" for others.
    platform: Mapped[str] = mapped_column(String(8), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Set up from the nextime app on the same iPhone or iPad (TIME-29, TIMEIOS-4 AC-6): the app device it belongs to.
    #: Such a device gets no alarms, the app reminds by itself (AC-7); ``None`` once that app device is gone.
    app_device_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)


class DeviceSetup(Base):
    """The link behind the QR code (TIME-20 AC-8, AC-13): ten minutes, once. Until then it carries the device's
    password encrypted with the server's key, for the profile; used or run out, the password goes."""

    __tablename__ = "device_setups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), nullable=True)
    password_enc: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


# --- Notifications (TIME-21) -----------------------------------------------------------------------------------------


class WebPushSubscription(Base):
    """One browser that allowed notifications (TIME-21 AC-5): its address at its push service and its two keys. It
    belongs to the session it was made in: signing out, a new password and the session's end take it along (EC-12);
    a blocked or deleted account keeps it for its return, and nothing reaches it while it is out (konten 1.1.0,
    E38). TIME-7 reminders use the same rows."""

    __tablename__ = "webpush_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(100))
    auth: Mapped[str] = mapped_column(String(40))
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    #: Signed up from the installed web app (the page said so), not from a browser tab.
    installed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: "Remind me on this device" (TIME-7 AC-6): on from the start.
    reminders: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    #: What the page says it runs on: ``iphone``, ``ipad``, ``mac``, or ``other`` (TIME-7 AC-7).
    platform: Mapped[str] = mapped_column(String(8), default="", server_default="")
    #: A random name of this sign-up, which no other browser ever gets (the row's number may come again): the mark of
    #: "Again in 5 min" is bound to it (review of TIME-7, finding 7). Made at the first need.
    key: Mapped[str] = mapped_column(String(32), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_ok_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class NotifyChange(Base):
    """A change of an event in a calendar since its last notification (TIME-21 AC-3): one row per event, widened by
    every change; the rows of a calendar go into one bundle once the calendar was quiet for two minutes and are removed
    with it. Kept in the database, so a restart in the quiet time still sends the bundle, once (EC-11). It keeps how
    the event looks after the last change (EC-6), so that a deleted one can still be named."""

    __tablename__ = "notify_changes"
    __table_args__ = (UniqueConstraint("calendar_id", "event_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("calendars.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(Integer)
    #: ``created``, ``changed`` or ``deleted``: what the bundle tells, all changes of the quiet time together.
    kind: Mapped[str] = mapped_column(String(8))
    #: Who changed it, in order, each once; 0 for a change that came from the calendar's source (EC-3).
    actors: Mapped[Any] = mapped_column(JSON, default=list)
    first_at: Mapped[datetime] = mapped_column(UtcDateTime)
    last_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)
    #: Where it began before the first change of the bundle: "moved to 11:30" (AC-3).
    before_start: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    title: Mapped[str] = mapped_column(Text, default="")
    starts_at: Mapped[datetime] = mapped_column(UtcDateTime)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)
    busy: Mapped[bool] = mapped_column(Boolean, default=True)


class NotifyOutbox(Base):
    """One message to one browser, sent in the background and tried again after a failure for a while (TIME-21
    EC-10). The text is encrypted for its browser only when it is sent; sent or given up, the text goes."""

    __tablename__ = "notify_outbox"
    __table_args__ = (Index("ix_notify_outbox_due", "sent_at", "failed_at", "next_try_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    #: ``change`` (TIME-21) or ``reminder`` (TIME-7): what is asked again before each try, and how often it is tried.
    kind: Mapped[str] = mapped_column(String(12), default="change", server_default="change")
    target_id: Mapped[int] = mapped_column(Integer)
    #: The calendar the message tells of: dropped before sending once the account no longer sees it (EC-1).
    calendar_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[Any] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    next_try_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(String(40), default="")


# --- Reminders (TIME-7) -----------------------------------------------------------------------------------------------


class ReminderChoice(Base):
    """The reminders one account chose for one event (TIME-7 AC-2, AC-9): only for that account. No row: the event
    follows the account's default for its calendar; an empty list: none at all. ``alarms`` keeps the VALARMs a device
    sent as they were, to hand them back to it (Apple Calendar keeps fields of its own there)."""

    __tablename__ = "reminder_choices"

    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True,
                                            index=True)
    #: Minutes before the start, each once, in order (``services/reminders.py`` says how whole days count).
    minutes: Mapped[Any] = mapped_column(JSON, default=list)
    alarms: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Single occurrences of a series with alarms of their own a device set (review of TIME-7, finding 5): by the
    #: occurrence's name, ``{"line": <its RECURRENCE-ID line>, "minutes": [...]}``.
    occurrences: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class UsedMark(Base):
    """A mark of "Again in 5 min" that was used: each works once (review of TIME-7, finding 7)."""

    __tablename__ = "used_marks"

    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    used_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)


class ReminderLog(Base):
    """A reminder that went into the outbox: never twice for the same occurrence and lead time (TIME-7 EC-1), also
    after a restart. Old rows go after two days."""

    __tablename__ = "reminder_log"
    __table_args__ = (UniqueConstraint("account_id", "event_id", "occurrence", "minutes"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int] = mapped_column(Integer)
    #: The start of the occurrence as an instant (``2026-11-26T15:30:00Z``) or a date for a whole day.
    occurrence: Mapped[str] = mapped_column(String(32))
    minutes: Mapped[int] = mapped_column(Integer)
    fire_at: Mapped[datetime] = mapped_column(UtcDateTime, index=True)


# --- Attachments (TIME-9) ---------------------------------------------------------------------------------------------


class Attachment(Base):
    """A file at an event of one of nextime's own calendars (TIME-9). The file lies in ``<data>/attachments/<id>``,
    with ``.view`` (a WebP every browser shows) and ``.thumb`` beside it where there are such
    (``services/attachments.py``). An upload for an event not saved yet waits with ``pending_ref`` until the event
    with that ``client_ref`` is made (AC-10)."""

    __tablename__ = "attachments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), nullable=True,
                                                 index=True)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), nullable=True,
                                                    index=True)
    #: The ``client_ref`` of the event being made; empty once the attachment hangs at its event.
    pending_ref: Mapped[str] = mapped_column(String(64), default="", server_default="")
    #: ``image``, ``pdf`` or ``file`` (only downloaded), from the content, never from the name (EC-6).
    kind: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(Text)
    mime: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    #: Bytes on disk with the view and the preview.
    stored: Mapped[int] = mapped_column(Integer, default=0)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    has_view: Mapped[bool] = mapped_column(Boolean, default=False)
    has_thumb: Mapped[bool] = mapped_column(Boolean, default=False)
    #: The file itself may be shown in the page (a picture drawn anew, a PDF); otherwise it is only downloaded.
    inline: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ImportFile(Base):
    """An ICS file being loaded into an own calendar (TIME-24 AC-2): it waits in ``<data>/imports/<id>.ics`` between the
    preview and "übernehmen". "Übernehmen" is a job in the background (``state`` ``running``, ``done`` of ``total``):
    the events go in short steps into a staging calendar nobody sees and, in one short last transaction, into the
    calendar chosen, where ``state`` becomes ``done`` with its ``result``. A second click finds it running or done
    (EC-10); a job cut off is ``failed`` with its ``problem``, its staging calendar goes, and it can start again (EC-9).
    Rows and files go after a day."""

    __tablename__ = "imports"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), default="")
    #: The preview: objects, first and last day, skipped entries, embedded files too large.
    summary: Mapped[Any] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(8), default="ready")
    calendar_id: Mapped[int | None] = mapped_column(ForeignKey("calendars.id", ondelete="SET NULL"), nullable=True)
    result: Mapped[Any] = mapped_column(JSON, nullable=True)
    done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    staging_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class CalendarMove(Base):
    """A calendar from iCloud or Google moved into an own one (TIME-24 AC-3), as a job in the background like an import
    (``state``, ``done`` of ``total``, a staging calendar). The same ``client_ref`` gives back the same move, for the
    same calendar only (EC-10); moving the same calendar again goes into ``target_id`` and skips what is there
    (EC-11)."""

    __tablename__ = "calendar_moves"
    __table_args__ = (UniqueConstraint("account_id", "client_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    source_calendar_id: Mapped[int | None] = mapped_column(ForeignKey("calendars.id", ondelete="SET NULL"),
                                                           nullable=True, index=True)
    target_id: Mapped[int | None] = mapped_column(ForeignKey("calendars.id", ondelete="SET NULL"), nullable=True)
    client_ref: Mapped[str] = mapped_column(String(64))
    result: Mapped[Any] = mapped_column(JSON, nullable=True)
    state: Mapped[str] = mapped_column(String(8), default="running", server_default="done")
    done: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    staging_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    problem: Mapped[str] = mapped_column(String(64), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class ApiKey(Base):
    """A key of a member for programs and AI tools (TIME-26): ``/api/v1/`` and, with ``allow_mcp``, ``/api/mcp``. Only
    SHA-256 of the key is kept, with its first characters to tell keys apart; the key itself is shown once. It acts as
    its account and never sees more: its calendars are ``calendar_ids`` (``None`` for all, later ones too) within what
    the account sees at the time of the request."""

    __tablename__ = "api_keys"
    __table_args__ = (UniqueConstraint("account_id", "client_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    #: ``read`` or ``write``.
    level: Mapped[str] = mapped_column(String(8), default="read")
    allow_mcp: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    calendar_ids: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16), default="")
    #: What the dialog sent with "Anlegen": a second click makes no second key (EC-16).
    client_ref: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: Locked by the operator (the shared key module, E27): refused until unlocked. New with schluessel 1.0.0; an older
    #: version does not read it, so a locked key works there again (the way back: ``docs/konten-anbindung.md``).
    blocked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class NextasksConnection(Base):
    """A member's own nextasks (TIME-25): where it is, the key sealed with the server's key (``vault``), who the key is
    there, its zone and right, whether "Meine Aufgaben" is hidden, and what went wrong last (for "Verbindungen")."""

    __tablename__ = "nextasks_connections"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    url: Mapped[str] = mapped_column(String(500))
    token_sealed: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    time_zone: Mapped[str] = mapped_column(String(64), default="")
    level: Mapped[str] = mapped_column(String(8), default="write")
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    #: Areas switched off before lists could be chosen (JSON list of ids); moved into ``task_choice`` at the start.
    areas_off: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: What the member chose to see: ``{"all", "except", "areas", "lists"}`` (JSON); unset: every area whole.
    task_choice: Mapped[str] = mapped_column(Text, default="", server_default="")
    #: Rows of the sidebar (an area or a list) hidden by their eye (JSON list of ids).
    areas_hidden: Mapped[str] = mapped_column(Text, default="", server_default="")
    problem: Mapped[str] = mapped_column(String(64), default="", server_default="")
    checked_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class TaskTick(Base):
    """A tick on a task of nextasks (TIME-25 AC-3, AC-9): what the member wanted, waiting until nextasks has it
    (EC-3, EC-12). ``ref`` per tap makes a double click one (EC-10). For a repeating task nextasks moves on and keeps a
    completed copy: ``copy_id`` and the due date before, to take a tick back."""

    __tablename__ = "task_ticks"
    __table_args__ = (UniqueConstraint("account_id", "ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    task_id: Mapped[str] = mapped_column(String(100), index=True)
    ref: Mapped[str] = mapped_column(String(64))
    done: Mapped[bool] = mapped_column(Boolean, default=True)
    #: ``waiting``, ``sent``, ``failed`` (refused for good, said once), ``dropped`` (taken back before it went).
    state: Mapped[str] = mapped_column(String(8), default="waiting")
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    next_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="")
    copy_id: Mapped[str] = mapped_column(String(100), default="", server_default="")
    due_before: Mapped[str] = mapped_column(String(10), default="", server_default="")
    time_before: Mapped[str] = mapped_column(String(5), default="", server_default="")
    problem: Mapped[str] = mapped_column(String(64), default="", server_default="")
    seen: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class NexpaperConnection(Base):
    """A member's own nexpaper: its deadlines show in the calendar, to look at only. Where it is, the key sealed with
    the server's key (``vault``), who the key is there and nexpaper's version, whether "Meine Fristen" is hidden, and
    what went wrong last (for "Verbindungen")."""

    __tablename__ = "nexpaper_connections"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), primary_key=True)
    url: Mapped[str] = mapped_column(String(500))
    token_sealed: Mapped[str] = mapped_column(Text, default="")
    name: Mapped[str] = mapped_column(String(200), default="")
    version: Mapped[str] = mapped_column(String(32), default="", server_default="")
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    problem: Mapped[str] = mapped_column(String(64), default="", server_default="")
    checked_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


# --- The nextime app for iPhone and iPad (TIME-29) --------------------------------------------------------------------


class AppDevice(Base):
    """A paired iPhone or iPad with the nextime app (TIME-29, ``/api/app/1``): its token (only the SHA-256 is kept),
    name, kind and last contact. It acts as its account, never with more rights; it goes when its owner disconnects it
    (in the browser or in the app), with a new password, "sign out everywhere", a reset second factor, a block or a
    deletion of the account."""

    __tablename__ = "app_devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    #: ``ios``, ``ipados`` or ``mac``.
    kind: Mapped[str] = mapped_column(String(8), default="ios")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    #: The device whose token brought this one in (the connection handed on through iCloud).
    via_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: The push relay's ticket and the device's X25519 public key (``PUT /push``); empty while off.
    push_ticket: Mapped[str] = mapped_column(Text, default="", server_default="")
    push_key: Mapped[str] = mapped_column(String(100), default="", server_default="")
    #: The app shows notifications with text (it said so with ``PUT /push``, ``alerts``): only then do the changes of
    #: shared calendars go to it (TIME-21 through the relay); every device with a ticket gets the silent nudge.
    push_alerts: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Since the shared module geraete (0.1.0): what the app says of itself in ``X-Nex-App`` (model, system, its
    # version), the way the device came in (``qr``, ``code``, ``browser``, ``icloud``), the kinds of notification it
    # understands (JSON list; ``NULL``: the published app, which gets ``changed`` and, with ``push_alerts``, the
    # changes of shared calendars) and the kinds switched on or off on it (JSON object). Rows of before keep ``NULL``.
    model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    via: Mapped[str | None] = mapped_column(String(16), nullable=True)
    push_understands: Mapped[str | None] = mapped_column(Text, nullable=True)
    push_kinds: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppPairing(Base):
    """A QR code of "iPhone oder iPad verbinden" (TIME-29 AC-1): ten minutes, one device. Only the SHA-256 of the
    secret is kept; ``device_id`` is the device it made."""

    __tablename__ = "app_pairings"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    secret_hash: Mapped[str] = mapped_column(String(64))
    #: The code to type beside the QR code (module geraete), its SHA-256; ``NULL`` for pairings of before.
    code_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    device_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class AppCode(Base):
    """The one-time code the page ``/app-connect`` hands back to the app (TIME-29 AC-5): five minutes, once. Only its
    SHA-256 is kept."""

    __tablename__ = "app_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    state: Mapped[str] = mapped_column(String(128), default="")
    #: The app's PKCE challenge (S256, module geraete); ``NULL``: the app sent none.
    challenge: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class AppGoneDevice(Base):
    """The SHA-256 of a device token that went with its account's block or deletion: such a device hears
    ``account_blocked`` instead of ``device_disconnected`` (TIME-29 EC-5)."""

    __tablename__ = "app_gone_devices"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class AppGoneSibling(Base):
    """When a device that came in over iCloud was made, kept after it was disconnected (geraete 0.1.5, G33 anew): the
    limit of siblings per hour counts it still, else disconnecting and asking again would pass the limit. Rows older
    than an hour go with the next disconnection."""

    __tablename__ = "app_gone_siblings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)


class AppOp(Base):
    """A change the app sent from its queue (``POST /api/app/1/ops``) and the answer it got: the same ``ref`` again
    gets the same answer and changes nothing (TIME-29 EC-6). Kept 30 days."""

    __tablename__ = "app_ops"
    __table_args__ = (UniqueConstraint("account_id", "ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    ref: Mapped[str] = mapped_column(String(64))
    op: Mapped[str] = mapped_column(String(16))
    answer: Mapped[Any] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow, index=True)


# --- The shared module mcp (bauplaene/mcp 1.1.0) ---------------------------------------------------------------------
# New tables only: an older nextime (0.4.0, the way back) does not read them, and never takes a program's row for an
# API key, because ``api_keys`` stays as it was.


class McpKey(Base):
    """An MCP key made by hand (``nt_mcp_``, module schluessel, kind ``mcp``) or the access of a program that connected
    over OAuth (``client_id`` set; its access token ``nt_mcp_`` and its refresh token ``ntr_``). Only SHA-256 of a key
    or token is kept. It acts as its account and never sees more: its calendars are ``calendar_ids`` (``None`` for all,
    later ones too)."""

    __tablename__ = "mcp_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    #: ``read`` or ``write``.
    level: Mapped[str] = mapped_column(String(8), default="read")
    calendar_ids: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16), default="")
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    #: A program: the client it signed in with, its current refresh token (hash), when it signed in and refreshed.
    client_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    refresh_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    signed_in_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    refreshed_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class McpClient(Base):
    """A program that registered itself (RFC 7591): public, with its addresses to return to."""

    __tablename__ = "mcp_clients"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    redirect_uris: Mapped[Any] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class McpCode(Base):
    """A code of the consent page (10 minutes, once): only its hash."""

    __tablename__ = "mcp_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    challenge: Mapped[str] = mapped_column(String(200))
    level: Mapped[str] = mapped_column(String(8))
    calendar_ids: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    options: Mapped[Any] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    resource: Mapped[str] = mapped_column(String(2048), default="")


class McpSpent(Base):
    """A spent refresh token or code of a program (its hash): brought again, it ends the program. The last 20 per
    program are kept."""

    __tablename__ = "mcp_spent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    key_id: Mapped[str] = mapped_column(String(32), index=True)
    spent_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class McpToolRight(Base):
    """What a key may do with one tool, where it differs from the default (allow, ask, deny). ``key_id`` is the module's
    id of the key: ``api-<n>`` for an API key with the tick for MCP, ``mcp-<n>`` for an MCP key or a program."""

    __tablename__ = "mcp_tool_rights"

    key_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    tool: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(8))


class McpRequest(Base):
    """A call that waits for the account's yes (the right "ask"): its arguments fixed (canonical JSON and SHA-256)."""

    __tablename__ = "mcp_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    key_id: Mapped[str] = mapped_column(String(32))
    key_name: Mapped[str] = mapped_column(String(200), default="")
    tool: Mapped[str] = mapped_column(String(64))
    arguments: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), index=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(200), default="")
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    dangerous: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


# --- The shared module verbinden (bauplaene/verbinden 0.1.1) ---------------------------------------------------------
# New tables beside nextime's own connections (``nextasks_connections``, ``nexpaper_connections``), which stay as they
# are and show in the cards as connections by API key.


class AppConnection(Base):
    """A connection of an account with another nex app, the same id on both sides: what nextime may there (``uses``),
    what the partner may here (``grants``, with the calendars ``areas_granted``), the partner's tokens for nextime
    sealed (``held``), and only the hashes of the tokens nextime gave the partner."""

    __tablename__ = "vb_connections"
    __table_args__ = (UniqueConstraint("account_id", "partner"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    partner: Mapped[str] = mapped_column(String(32))
    partner_base: Mapped[str] = mapped_column(String(500))
    partner_account: Mapped[str] = mapped_column(String(200), default="")
    uses: Mapped[Any] = mapped_column(JSON, default=list)
    grants: Mapped[Any] = mapped_column(JSON, default=list)
    areas_granted: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    areas_used: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    held: Mapped[str] = mapped_column(Text)
    issued_access_hash: Mapped[str] = mapped_column(String(64), unique=True)
    issued_refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    issued_access_expires: Mapped[datetime] = mapped_column(UtcDateTime)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
    used_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    ok_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="connected")
    stale_since: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    problem: Mapped[str | None] = mapped_column(String(64), nullable=True)
    partner_version: Mapped[str] = mapped_column(String(40), default="")


class AppConnectionPending(Base):
    """A connection nextime started: the account went to the partner's consent page (10 minutes, once)."""

    __tablename__ = "vb_pending"

    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    partner: Mapped[str] = mapped_column(String(32))
    partner_base: Mapped[str] = mapped_column(String(500))
    verifier: Mapped[str] = mapped_column(Text)
    areas_granted: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)


class AppConnectionCode(Base):
    """A code of nextime's consent page for the app that asked (10 minutes, once): only its hash."""

    __tablename__ = "vb_codes"

    code_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"), index=True)
    partner: Mapped[str] = mapped_column(String(32))
    partner_base: Mapped[str] = mapped_column(String(500))
    redirect_uri: Mapped[str] = mapped_column(String(2048))
    challenge: Mapped[str] = mapped_column(String(200))
    areas_granted: Mapped[Any] = mapped_column(JSON(none_as_null=True), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UtcDateTime)


class AppConnectionSpent(Base):
    """A spent code or refresh token of a connection (its hash): brought again, it ends the connection."""

    __tablename__ = "vb_spent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    connection_id: Mapped[str] = mapped_column(String(64), index=True)


class AppConnectionWaiting(Base):
    """A revocation the partner has not heard yet (sealed), tried again for 30 days."""

    __tablename__ = "vb_waiting"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    partner_base: Mapped[str] = mapped_column(String(500))
    token: Mapped[str] = mapped_column(Text)
    since: Mapped[datetime] = mapped_column(UtcDateTime, default=utcnow)
