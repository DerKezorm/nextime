"""Accounts: the first is the operator, the others come by invitation or through OIDC.

An invitation is a link with an end date; whoever follows it gets an account with the invitation's role. People sign
in with their address or their account name. The name is checked before the password, and an unknown name costs the
same time as a wrong password, so that neither answer nor timing tells which accounts exist.

The operator blocks and deletes accounts. A deleted account is blocked at once and gone for everybody else; for 30
days the operator can bring it back, then ``purge_deleted`` removes it with everything it owns.
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import unicodedata
from datetime import timedelta
from functools import lru_cache

from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..models import (
    DELETE_GRACE_DAYS,
    MEMBER,
    OPERATOR,
    ROLES,
    SIGN_IN_PASSWORD,
    Account,
    Invite,
    PasswordReset,
    utcnow,
)
from ..security import LOCK_MINUTES, MAX_FAILURES, MIN_PASSWORD, hash_password, hash_token, verify_password
from ..vendor import nexwasneu

logger = logging.getLogger("nextime.auth")

NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
#: Enough to catch a typo, not a validation of the address: the mail server has the last word.
#: No control character (NUL, ESC, DEL and C1 went through into the mail before), and ``\Z``: ``$`` lets a
#: closing line break pass.
_PART = r"[^@\s<>,;\x00-\x1f\x7f-\x9f]+"
EMAIL_PATTERN = re.compile(rf"^{_PART}@{_PART}\.{_PART}\Z")
#: How long an invitation may run, in days; the one who invites picks within this.
INVITE_DAYS = (1, 7, 30)
DISPLAY_NAME_MAX = 80


class AccountError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, **extra: object) -> None:
        super().__init__(message)
        self.code, self.message, self.status, self.extra = code, message, status, extra


def count(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(Account)) or 0)


def by_name(db: Session, name: str) -> Account | None:
    return db.scalar(select(Account).where(Account.name == name.strip().lower()))


def by_email(db: Session, email: str) -> Account | None:
    cleaned = email.strip().lower()
    if not cleaned:
        return None
    return db.scalar(select(Account).where(func.lower(Account.email) == cleaned).limit(1))


def by_login(db: Session, login: str) -> Account | None:
    """What people type into the sign-in form: their address, or their account name."""
    return by_email(db, login) if "@" in login else by_name(db, login)


def check_password_rule(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise AccountError("password_too_short", f"Use at least {MIN_PASSWORD} characters.", 422,
                           minimum=MIN_PASSWORD)


def clean_display_name(raw: str) -> str:
    """A name as people typed it: umlauts and emoji stay, blanks at the edges go, no control characters."""
    text = unicodedata.normalize("NFC", raw or "")
    text = "".join(char for char in text if unicodedata.category(char) not in ("Cc", "Cf") or char == "‍")
    return text.strip()[:DISPLAY_NAME_MAX].strip()


def clean_email(db: Session, raw: str, *, own_id: int | None = None) -> str:
    """An address for an account: the right form, and nobody else's (case does not count)."""
    address = (raw or "").strip()
    if not address:
        return ""
    if len(address) > 255 or not EMAIL_PATTERN.match(address):
        raise AccountError("invalid_email", "This is not an email address.", 422)
    other = by_email(db, address)
    if other is not None and other.id != own_id:
        raise AccountError("email_taken", "Another account already uses this email address.", 409)
    return address


def check_name(db: Session, name: str) -> str:
    cleaned = name.strip().lower()
    if not NAME_PATTERN.match(cleaned):
        raise AccountError("invalid_account_name", "Use 2 to 64 letters, digits, dots, dashes or underscores.", 422)
    if by_name(db, cleaned) is not None:
        raise AccountError("name_taken", "This name is already taken.", 409)
    return cleaned


def free_name(db: Session, *candidates: str) -> str:
    """An account name nobody has, made from the address or the name: ``anna``, then ``anna-2`` and so on."""
    stem = ""
    for candidate in candidates:
        base = candidate.split("@")[0] if "@" in candidate else candidate
        folded = unicodedata.normalize("NFKD", base.lower())
        plain = "".join(char for char in folded if not unicodedata.combining(char)).replace("ß", "ss")
        stem = re.sub(r"[^a-z0-9._-]+", "-", plain).strip("-._")[:56]
        if len(stem) >= 2:
            break
    if len(stem) < 2:
        stem = "person"
    name, number = stem, 1
    while by_name(db, name) is not None:
        number += 1
        name = f"{stem}-{number}"
    return name


def create_with_password(
    db: Session,
    password: str,
    *,
    display_name: str = "",
    email: str = "",
    name: str = "",
    role: str = MEMBER,
    commit: bool = True,
) -> Account:
    if role not in ROLES:
        raise ValueError("unknown role")
    check_password_rule(password)
    shown = clean_display_name(display_name)
    if not shown:
        raise AccountError("display_name_empty", "Enter a name.", 422)
    address = clean_email(db, email)
    login = check_name(db, name) if name else free_name(db, address, shown)
    account = Account(
        name=login,
        display_name=shown,
        email=address,
        role=role,
        sign_in=SIGN_IN_PASSWORD,
        password_hash=hash_password(password),
        password_changed_at=utcnow(),
        # Since E45: a new account starts with "system".
        theme="system",
        whats_new_seen=nexwasneu.seen_for_new_account(),
    )
    db.add(account)
    if commit:
        db.commit()
    else:
        db.flush()
    logger.info("Account created name=%s role=%s sign_in=password", account.name, role)
    return account


def setup_code() -> str:
    """What the first account must bring: ``NEXTIME_SETUP_TOKEN`` (16 characters at least), else a code made at
    start and written to the log (the shared account module, ``setup.setup_code``)."""
    from ..vendor.nexkonten import setup

    return setup.setup_code()


def announce_setup_code(db: Session) -> None:
    """At start, while nobody has set nextime up: the code in the log (the shared account module)."""
    from ..vendor.nexkonten import setup
    from .konten_store import KontenStore

    setup.announce_setup(KontenStore(db))


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """A hash to verify against for an unknown name: the answer takes as long as for a wrong password."""
    return hash_password(secrets.token_urlsafe(24))


def is_locked(account: Account) -> bool:
    return account.locked_until is not None and account.locked_until > utcnow()


def note_failure(db: Session, account: Account) -> None:
    """A wrong password: counted per account, locked after too many, whoever the sender is."""
    account.failed_logins += 1
    if account.failed_logins >= MAX_FAILURES:
        account.locked_until = utcnow() + timedelta(minutes=LOCK_MINUTES)
        account.failed_logins = 0
        logger.warning("Account locked after %s failures name=%s minutes=%s", MAX_FAILURES, account.name, LOCK_MINUTES)
    else:
        logger.warning(
            "Check failed name=%s (%s of %s before lockout)", account.name, account.failed_logins, MAX_FAILURES
        )
    db.commit()


def note_success(db: Session, account: Account) -> None:
    account.failed_logins = 0
    account.locked_until = None
    account.last_seen_at = utcnow()
    db.commit()


_account_locks: dict[int, threading.Lock] = {}
_account_locks_guard = threading.Lock()


def _one_check_at_a_time(account_id: int) -> threading.Lock:
    """Checks of one account's password in turn: many wrong ones at once all passed the lock test before the first
    failure was counted."""
    with _account_locks_guard:
        return _account_locks.setdefault(account_id, threading.Lock())


def authenticate(db: Session, login: str, password: str, device: int | None = None) -> Account:
    """Checks address or name and password; counts failures and locks the account after too many.

    A locked account answers like a wrong password (no hint that it exists), except to a browser that signed in as it
    before (``device``, the account its cookie names): a stranger guessing can lock the account against the world,
    not against its owner. Failures from that browser do not count towards the lock.

    A blocked or deleted account hears that it is blocked and whom to ask, but only after its right password; a wrong
    one answers as for anybody.
    """
    account = by_login(db, login)
    if account is None or account.sign_in != SIGN_IN_PASSWORD:
        verify_password(password, _dummy_hash())
        logger.warning("Sign-in failed for an unknown account")
        raise AccountError("wrong_credentials", "Address or password is not right.", 401)
    known_device = device == account.id
    with _one_check_at_a_time(account.id):
        db.refresh(account)
        if is_locked(account) and not known_device:
            verify_password(password, _dummy_hash())
            logger.warning("Sign-in refused, account locked name=%s", account.name)
            raise AccountError("wrong_credentials", "Address or password is not right.", 401)
        if not verify_password(password, account.password_hash):
            if not known_device:
                note_failure(db, account)
            raise AccountError("wrong_credentials", "Address or password is not right.", 401)
        if account.blocked_at is not None or account.deleted_at is not None:
            logger.warning("Sign-in refused, account blocked name=%s", account.name)
            raise AccountError("account_blocked", "This account is blocked. Ask the operator to unblock it.", 403)
        if not account.totp_secret_enc:
            # With a second factor, only the code resets the count of failures.
            note_success(db, account)
    return account


def check_password(account: Account, password: str) -> bool:
    return account.sign_in == SIGN_IN_PASSWORD and verify_password(password, account.password_hash)


def change_password(db: Session, account: Account, current: str, new: str) -> None:
    if not verify_password(current, account.password_hash):
        raise AccountError("wrong_password", "The current password is wrong.", 401)
    check_password_rule(new)
    if new == current:
        raise AccountError("password_unchanged", "The new password is the same as the current one.", 422)
    account.password_hash = hash_password(new)
    account.password_changed_at = utcnow()
    db.commit()
    logger.info("Password changed name=%s", account.name)


# --- Reset links ----------------------------------------------------------------------------------------------------
#
# The operator never gives an account a password: whoever knows it could read every calendar of that account. A reset
# link lets the person set one themselves; the account hears about it at its next sign-in, whoever used the link.


def create_reset(db: Session, by: Account, account: Account, *, days: int) -> tuple[PasswordReset, str]:
    """A new link for ``account``, valid ``days`` (as an invitation); an earlier one of the account stops counting.
    Making it changes nothing about the account: its password and its way of signing in stay as they are."""
    if days not in INVITE_DAYS:
        raise AccountError("invalid_days", "Choose 1, 7 or 30 days.", 422)
    if account.deleted_at is not None:
        raise AccountError("not_found", "No such account.", 404)
    db.execute(delete(PasswordReset).where(PasswordReset.account_id == account.id))
    token = secrets.token_urlsafe(24)
    row = PasswordReset(token_hash=hash_token(token), account_id=account.id, created_by=by.id,
                        expires_at=utcnow() + timedelta(days=days))
    db.add(row)
    db.commit()
    logger.warning("Password reset link made name=%s by=%s", account.name, by.name)
    return row, token


def find_reset(db: Session, token: str) -> tuple[PasswordReset, Account] | None:
    """A usable link: not run out, its account not deleted, made by somebody who is still an operator."""
    row = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == hash_token(token)))
    if row is None or row.expires_at <= utcnow():
        return None
    account = db.get(Account, row.account_id)
    creator = db.get(Account, row.created_by) if row.created_by else None
    if account is None or account.deleted_at is not None:
        return None
    if creator is None or creator.role != OPERATOR or creator.blocked_at is not None or creator.deleted_at is not None:
        return None
    return row, account


def reset_expired(db: Session, token: str) -> bool:
    """Whether the link is still there and only expired (to say so instead of "not valid any more")."""
    row = db.scalar(select(PasswordReset).where(PasswordReset.token_hash == hash_token(token)))
    return row is not None and row.expires_at <= utcnow()


def use_reset(db: Session, token: str, new: str) -> Account:
    """The person behind the link sets a new password. Every session and paired device of the account ends, the
    account signs in with a password from now on (a provider stays linked), and it hears about it at the next
    sign-in."""
    from ..security import end_all_sessions

    found = find_reset(db, token)
    if found is None:
        if reset_expired(db, token):
            raise AccountError("reset_expired", "This link has expired.", 404)
        raise AccountError("reset_invalid", "This link has been used already or is not valid any more.", 404)
    reset, account = found
    check_password_rule(new)
    # The link goes first, in the same transaction: two requests at once set one password, not two.
    try:
        gone = int(db.execute(delete(PasswordReset).where(PasswordReset.id == reset.id)).rowcount or 0) == 1
    except OperationalError:
        gone = False
    if not gone:
        db.rollback()
        raise AccountError("reset_invalid", "This link has been used already or is not valid any more.", 404)
    account.password_hash = hash_password(new)
    account.password_changed_at = utcnow()
    account.sign_in = SIGN_IN_PASSWORD
    account.failed_logins = 0
    account.locked_until = None
    account.password_reset_notice = True
    db.commit()
    end_all_sessions(db, account.id)
    logger.warning("Password set through a reset link name=%s", account.name)
    return account


# --- Roles, blocking, deleting ---------------------------------------------------------------------------------------


#: The colors of the circles of initials, as the browser draws them (``frontend/src/lib/avatar.ts``, the calendar
#: colors of the design system, darker tone); the account keeps an index into them.
PERSON_HEX = ("#4a6491", "#c62f47", "#c2410c", "#a16207", "#2f7d4f", "#0e7f74", "#2563a8", "#6d4bc2", "#be3d78",
              "#8a5a3b", "#61616f")
#: How many colors the circles of initials have.
PERSON_COLORS = len(PERSON_HEX)


def person_hex(account: Account) -> str:
    """The CSS color of the account's circle as nextime's own pages draw it: its index, or its number while it has
    none yet (``personColor(color ?? accountId)``); nothing is given here, the database stays as it is."""
    index = account.color if account.color is not None else account.id
    return PERSON_HEX[index % PERSON_COLORS]


def person_color(db: Session, account: Account) -> int:
    """The color index of the account's circle; given the first time, the one fewest other accounts have (the lowest
    of those), and kept from then on (TIME-2 EC-11)."""
    if account.color is not None:
        return account.color
    used = [0] * PERSON_COLORS
    for (index,) in db.execute(select(Account.color).where(Account.color.is_not(None), Account.id != account.id)):
        if 0 <= index < PERSON_COLORS:
            used[index] += 1
    account.color = used.index(min(used))
    db.commit()
    return account.color


def active_operators(db: Session) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(Account)
            .where(Account.role == OPERATOR, Account.blocked_at.is_(None), Account.deleted_at.is_(None))
        )
        or 0
    )


def _keep_an_operator(db: Session, account: Account) -> None:
    """The last operator that can still sign in is never blocked, deleted or made a member."""
    active = account.role == OPERATOR and account.blocked_at is None and account.deleted_at is None
    if active and active_operators(db) <= 1:
        raise AccountError("last_operator", "nextime needs at least one operator.", 409)


def set_role(db: Session, account: Account, role: str) -> None:
    if role not in ROLES:
        raise AccountError("invalid_role", "Unknown role.", 422)
    if role == account.role:
        return
    if role == MEMBER:
        _keep_an_operator(db, account)
    account.role = role
    db.commit()
    logger.info("Role changed name=%s role=%s", account.name, role)


def block(db: Session, account: Account) -> None:
    from ..security import sessions_ended

    if account.blocked_at is not None:
        return
    _keep_an_operator(db, account)
    account.blocked_at = utcnow()
    _app_devices_out(db, account)
    db.commit()
    # The sessions stay as they are but count for nothing (``security.account_out``): an open window hears that it
    # is blocked instead of being sent to sign in. Live connections look again at once.
    sessions_ended()
    logger.info("Account blocked name=%s", account.name)


def unblock(db: Session, account: Account) -> None:
    if account.blocked_at is None:
        return
    from ..security import end_all_sessions

    # The sessions from before the block do not come back to life; the paired devices rested and work again (G26).
    end_all_sessions(db, account.id, devices=False)
    account.blocked_at = None
    account.failed_logins = 0
    account.locked_until = None
    db.commit()
    logger.info("Account unblocked name=%s", account.name)


def _app_devices_out(db: Session, account: Account) -> None:
    """Since geraete 0.1.0 (G26) the paired iPhones and iPads rest with a block or deletion: they hear
    ``account_blocked`` while it lasts (TIME-29 EC-5) and work again afterwards without pairing anew. Nothing to do
    here but what the caller commits."""
    db.flush()


def delete_account(db: Session, account: Account) -> None:
    """Gone for everybody at once, blocked, and for ``DELETE_GRACE_DAYS`` days brought back by the operator."""
    from ..security import sessions_ended
    from . import content

    if account.deleted_at is not None:
        return
    _keep_an_operator(db, account)
    account.deleted_at = utcnow()
    _app_devices_out(db, account)
    db.commit()
    sessions_ended()
    content.account_deleted(db, account.id)
    logger.info("Account deleted name=%s (restorable for %s days)", account.name, DELETE_GRACE_DAYS)


def restore_account(db: Session, account: Account) -> None:
    from . import content

    if account.deleted_at is None:
        return
    if account.deleted_at + timedelta(days=DELETE_GRACE_DAYS) <= utcnow():
        raise AccountError("not_found", "Not found.", 404)
    from ..security import end_all_sessions

    end_all_sessions(db, account.id, devices=False)
    account.deleted_at = None
    db.commit()
    content.account_restored(db, account.id)
    logger.info("Account restored name=%s", account.name)


def purge_deleted(db: Session) -> int:
    """Accounts deleted more than ``DELETE_GRACE_DAYS`` days ago go for good, with everything they own, through the
    shared module (``nexkonten.manage.purge_due``, ``KontenStore.purge_account``): their connections with other nex
    apps end on both sides, their devices go, their user names stay reserved for ever. How many."""
    from ..vendor.nexkonten import manage
    from .konten_store import KontenStore

    return len(manage.purge_due(KontenStore(db)))


# --- Invitations ---------------------------------------------------------------------------------------------------


def create_invite(
    db: Session, by: Account, *, days: int, email: str = "", role: str = MEMBER
) -> tuple[Invite, str]:
    if days not in INVITE_DAYS:
        raise AccountError("invalid_days", "Choose 1, 7 or 30 days.", 422)
    if role not in ROLES:
        raise AccountError("invalid_role", "Unknown role.", 422)
    address = clean_email(db, email)
    token = secrets.token_urlsafe(24)
    invite = Invite(
        token_hash=hash_token(token), role=role, email=address, created_by=by.id,
        expires_at=utcnow() + timedelta(days=days),
    )
    db.add(invite)
    db.commit()
    logger.info("Invite created by=%s role=%s", by.name, role)
    return invite, token


def expired(invite: Invite) -> bool:
    return invite.expires_at <= utcnow()


def find_invite(db: Session, token: str) -> Invite | None:
    """A usable invitation: not run out, and made by somebody who is still an operator."""
    invite = db.scalar(select(Invite).where(Invite.token_hash == hash_token(token)))
    if invite is None or invite.expires_at <= utcnow():
        return None
    creator = db.get(Account, invite.created_by) if invite.created_by else None
    if creator is None or creator.role != OPERATOR or creator.blocked_at is not None or creator.deleted_at is not None:
        return None
    return invite


def invite_expired(db: Session, token: str) -> bool:
    """Whether the link's invitation is still there and only expired (to say so instead of "not valid any more")."""
    invite = db.scalar(select(Invite).where(Invite.token_hash == hash_token(token)))
    return invite is not None and invite.expires_at <= utcnow()


def consume(db: Session, invite: Invite) -> bool:
    """Takes the invitation away, in the open transaction; False when another request took it first. Of two requests
    at the same moment the database lets exactly one delete the row, so a link is used once whatever the timing."""
    try:
        return int(db.execute(delete(Invite).where(Invite.id == invite.id)).rowcount or 0) == 1
    except OperationalError:
        db.rollback()
        return False


def redeem(db: Session, invite: Invite, account: Account, *, consumed: bool = False, commit: bool = True) -> None:
    """The invitation is used: it goes first (``consume``), then its role goes to the account, raised, never
    lowered."""
    if not consumed and not consume(db, invite):
        db.rollback()
        raise AccountError("invite_invalid", "This invitation has been used already or is not valid any more.", 404)
    if invite.role == OPERATOR and account.role != OPERATOR:
        account.role = OPERATOR
    if commit:
        db.commit()
    else:
        db.flush()
    logger.info("Invite used name=%s", account.name)


def accept_invite(db: Session, token: str, *, display_name: str, email: str, password: str) -> Account:
    invite = find_invite(db, token)
    if invite is None:
        if invite_expired(db, token):
            raise AccountError("invite_expired", "This invitation has expired.", 404)
        raise AccountError("invite_invalid", "This invitation has been used already or is not valid any more.", 404)
    check_password_rule(password)
    # The invitation goes before the account comes, in one transaction: two requests with the same link at the same
    # moment make one account, not two.
    if not consume(db, invite):
        db.rollback()
        raise AccountError("invite_invalid", "This invitation has been used already or is not valid any more.", 404)
    try:
        account = create_with_password(
            db, password, display_name=display_name, email=email or invite.email, commit=False
        )
    except AccountError:
        db.rollback()
        raise
    redeem(db, invite, account, consumed=True)
    return account


def open_invites(db: Session) -> list[Invite]:
    """Every invitation not yet used or withdrawn, the expired ones too: the operator's list shows them as "Expired"
    until withdrawn (konten 0.3.9, ``OPEN_INVITES``)."""
    return list(db.scalars(select(Invite).order_by(Invite.created_at)))
