"""Passwords, browser sessions, the brake against guessing, and the secrets the server keeps for itself.

Built after nextrmnl's: Argon2id for passwords, sessions as random tokens of which the database knows only the
hash, a brake per sender in memory and a lock per account in the database (ten failures, a quarter of an hour).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time
from datetime import UTC, datetime

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from .config import get_settings
from .models import Account, AuthSession, new_device_key, utcnow

SESSION_COOKIE = "nextime_session" + get_settings().cookie_name_suffix()
MIN_PASSWORD = 12
#: After this many failures in a row an account waits a quarter of an hour. Not configurable on purpose.
MAX_FAILURES = 10
LOCK_MINUTES = 15


def _hasher() -> PasswordHasher:
    settings = get_settings()
    return PasswordHasher(
        time_cost=settings.argon2_time, memory_cost=settings.argon2_memory_kib, parallelism=settings.argon2_parallelism
    )


#: Argon2 takes 64 MB per check. Sign-in needs no account, so without a limit a crowd of guesses at once could ask for
#: gigabytes (about 40 threads at a time); a small NAS would run out of memory.
HASHING_AT_ONCE = 4
#: Seconds a check waits for a free slot before the request answers busy.
HASH_WAIT = 15
_hashing = threading.BoundedSemaphore(HASHING_AT_ONCE)


class HashingBusy(Exception):
    """Every slot for checking passwords stayed taken: the request answers 503, the browser tries again."""


def _hash_slot() -> None:
    if not _hashing.acquire(timeout=HASH_WAIT):
        raise HashingBusy


def hash_password(password: str) -> str:
    _hash_slot()
    try:
        return _hasher().hash(password)
    finally:
        _hashing.release()


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    _hash_slot()
    try:
        return _hasher().verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    finally:
        _hashing.release()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def start_session(db: Session, account: Account, ip: str, user_agent: str, *, provider_checked: bool = False,
                  remember: bool = True) -> str:
    """Creates a browser session (the shared account module, ``sessions.start``) and returns the token for the
    cookie. ``remember``: "Stay signed in", 30 days from the last use; without it at most 12 hours and a cookie that
    ends with the browser (``session_cookie_age``). ``provider_checked``: the sign-in came through a provider that
    checks the second factor itself."""
    from .services.konten_store import KontenStore
    from .vendor.nexkonten import sessions

    started = sessions.start(
        KontenStore(db),
        account.id,
        cookie_name=SESSION_COOKIE,
        secure=False,
        remember=remember,
        provider_checked=provider_checked,
        label=user_agent[:255],
        address=ip[:64],
    )
    account.last_seen_at = utcnow()
    db.commit()
    return started.token


def session_cookie_age(remember: bool) -> int | None:
    """How long the browser keeps the session cookie: "Stay signed in" outlives the session (the server decides),
    otherwise the cookie ends with the browser."""
    from .vendor.nexkonten import sessions

    return sessions.REMEMBER_COOKIE_DAYS * 86400 if remember else None


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def account_out(account: Account | None) -> bool:
    """Blocked, or deleted by the operator (which blocks it too): no session and no token counts for it."""
    return account is None or account.blocked_at is not None or account.deleted_at is not None


def session_owner(db: Session, token: str | None) -> Account | None:
    """The account a live session belongs to, blocked or not: to tell an open window why it is out."""
    if not token:
        return None
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    if session is None or session.expires_at <= utcnow():
        return None
    return db.get(Account, session.account_id)


def session_account(db: Session, token: str | None) -> Account | None:
    """The account of a browser session (``sessions.current``: run out is out, "Stay signed in" slides), or None.
    A blocked or deleted account is out at once."""
    if not token:
        return None
    from .services.konten_store import KontenStore
    from .vendor.nexkonten import model2, sessions

    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    if row is not None and _aware(row.expires_at) <= utcnow():
        # A session that ran out goes at its next showing (and in the hourly sweep).
        db.delete(row)
        db.commit()
        return None
    session = sessions.current(KontenStore(db), token)
    if session is None or session.kind != model2.KIND_BROWSER:
        if db.dirty:
            db.commit()
        return None
    account = db.get(Account, int(session.account_id))
    if account is None or account_out(account):
        return None
    if db.dirty or db.new:
        # ``current`` wrote the last use (at most every five minutes); the account's last use goes with it.
        account.last_seen_at = utcnow()
        db.commit()
    account.provider_checked = bool(session.provider_checked)
    return account


def session_uid(db: Session, token: str | None) -> str:
    """The id of the session of this cookie in the list "Where you are signed in", "" for none."""
    if not token:
        return ""
    row = db.scalar(select(AuthSession).where(AuthSession.token_hash == hash_token(token)))
    return (row.uid or f"s{row.id}") if row is not None else ""


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == hash_token(token)))
        db.commit()
        _sessions_ended()


#: Called whenever sessions end (sign-out, a block, a new password): open live connections look again at once.
SESSIONS_ENDED: list = []


def sessions_ended() -> None:
    for callback in SESSIONS_ENDED:
        callback()


_sessions_ended = sessions_ended


def end_all_sessions(db: Session, account_id: int, except_token: str | None = None, *, devices: bool = True) -> None:
    """Every browser session of the account but ``except_token``: a new password or "sign out everywhere" is what
    one does when the account was taken. Tokens made by hand in the interface stay; they are revoked there. With
    ``devices=False`` (unblocking, bringing an account back) the paired iPhones and iPads stay: they rested while the
    account was out and work again (geraete G26)."""
    statement = delete(AuthSession).where(AuthSession.account_id == account_id)
    if except_token:
        statement = statement.where(AuthSession.token_hash != hash_token(except_token))
    db.execute(statement)
    # The browsers known from before are forgotten too (their device cookies); the one that stays gets a new one.
    db.execute(update(Account).where(Account.id == account_id).values(device_key=new_device_key()))
    # The paired iPhones and iPads go as well (TIME-29): they sign in again.
    if devices:
        from .services import appdevices

        appdevices.end_all(db, account_id)
    db.commit()
    _sessions_ended()


def purge_sessions(db: Session) -> int:
    result = db.execute(delete(AuthSession).where(AuthSession.expires_at <= utcnow()))
    db.commit()
    return int(getattr(result, "rowcount", 0) or 0)


class Brake:
    """Waiting time after wrong passwords, per sender. In memory only; the per-account lock is in the database.

    A count is forgotten an hour after its last failure, and the table is thinned out when it grows: every new address
    (cheap with IPv6) would otherwise stay in memory for as long as the server runs.
    """

    FREE = 5
    MAX_WAIT = LOCK_MINUTES * 60
    FORGET_AFTER = 3600
    MAX_KEYS = 50_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._fails: dict[str, tuple[int, float]] = {}
        #: Seconds on a clock that only goes forward; a test holds it still.
        self.clock = time.monotonic

    def _current(self, key: str, now: float) -> tuple[int, float]:
        count, last = self._fails.get(key, (0, 0.0))
        return (0, 0.0) if count and now - last > self.FORGET_AFTER else (count, last)

    def wait_seconds(self, key: str, free: int | None = None) -> int:
        """Seconds to wait; ``free`` wrong tries pass without (``FREE`` when not given)."""
        free = self.FREE if free is None else free
        now = self.clock()
        with self._lock:
            count, last = self._current(key, now)
        if count < free:
            return 0
        wait = min(self.MAX_WAIT, 2 ** min(count - free, 20) * 5)
        remaining = last + wait - now
        return max(0, int(remaining + 0.999))

    def failed(self, key: str) -> None:
        now = self.clock()
        with self._lock:
            count, _ = self._current(key, now)
            self._fails[key] = (count + 1, now)
            if len(self._fails) > self.MAX_KEYS:
                self._fails = {k: v for k, v in self._fails.items() if now - v[1] <= self.FORGET_AFTER}
                while len(self._fails) > self.MAX_KEYS * 0.9:
                    self._fails.pop(next(iter(self._fails)))

    def succeeded(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)

    def forget(self) -> None:
        """For the tests: this brake and the one of the shared account module (sign-in, forgotten password, setup
        code)."""
        from .vendor.nexkonten.brake import BRAKE

        with self._lock:
            self._fails.clear()
        self.clock = time.monotonic
        BRAKE.forget()


brake = Brake()


# --- Secrets the server reads on its own (OIDC client secret, mail password) ---------------------------------------

_AAD = b"nextime-server-secret-v1"
_NONCE = 12


def _server_key() -> bytes:
    secret = get_settings().resolved_secret_key().encode("utf-8")
    return hashlib.sha256(b"nextime-secrets:" + secret).digest()


def server_key() -> bytes:
    """The key derived from the server's secret, for marks only the server can make (a keyed hash)."""
    return _server_key()


DEVICE_COOKIE = "nextime_device" + get_settings().cookie_name_suffix()
DEVICE_DAYS = 365


def _device_mark(account_id: int, key: str, nonce: str) -> str:
    return hashlib.sha256(_server_key() + f"device:{account_id}:{key}:{nonce}".encode()).hexdigest()[:32]


def device_token(db: Session, account: Account) -> str:
    """A browser that signed in once as this account: it may still sign in while the account is locked against the
    rest of the world, and it passes the brake per sender. Signed with the server's key and the account's own
    ``device_key`` (drawn anew on a new password, a block or unblock, "sign out everywhere" and a reset second
    factor); nothing about the browser is stored."""
    if not account.device_key:
        account.device_key = new_device_key()
        db.commit()
    nonce = secrets.token_urlsafe(12)
    return f"{account.id}.{nonce}.{_device_mark(account.id, account.device_key, nonce)}"


def device_of(db: Session, token: str | None) -> int | None:
    """The account a device cookie was given to, or None when it is missing, not signed by this server, or from
    before the account's browsers were forgotten (or from another account that had the same id)."""
    if not token or token.count(".") != 2:
        return None
    account, nonce, mark = token.split(".")
    if not account.isascii() or not account.isdigit() or len(account) > 18:
        return None
    row = db.get(Account, int(account))
    if row is None or not row.device_key:
        return None
    expected = _device_mark(row.id, row.device_key, nonce)
    return row.id if secrets.compare_digest(mark.encode("utf-8", "replace"), expected.encode()) else None


def _aad(context: str) -> bytes:
    # A context of its own per kind of secret: a secret sealed for one purpose cannot be passed off as one of another.
    # Without one, the secrets of the frame (OIDC client secret, mail password) read as before.
    return _AAD + b":" + context.encode("utf-8") if context else _AAD


def encrypt_secret(text: str, context: str = "") -> str:
    """AES-256-GCM with a key from ``secret.key``, stored as hex."""
    if not text:
        return ""
    nonce = os.urandom(_NONCE)
    return (nonce + AESGCM(_server_key()).encrypt(nonce, text.encode("utf-8"), _aad(context))).hex()


def decrypt_secret(stored: str, context: str = "") -> str:
    if not stored:
        return ""
    try:
        sealed = bytes.fromhex(stored)
        return AESGCM(_server_key()).decrypt(sealed[:_NONCE], sealed[_NONCE:], _aad(context)).decode("utf-8")
    except (InvalidTag, ValueError):
        # A different secret.key than the one that encrypted it: the value is lost, not the app.
        return ""
