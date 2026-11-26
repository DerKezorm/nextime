"""Setting up the first account, signing in and out, a forgotten password, and the own account.

Setup, sign-in, the profile and a forgotten password run through the shared account module (``vendor/nexkonten``,
store ``services/konten_store.py``), the same in every nex app; the routes and their field names stay as 0.4.0 had
them (``name`` is the username, at sign-in "Username or email"). The operator's list of accounts is in
``routers/accounts.py``, invitations and links in ``routers/invites.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..deps import (
    Account,
    DbSession,
    behind_unknown_proxy,
    client_ip,
    reauth_failed,
    reauth_guard,
    reauth_succeeded,
)
from ..errors import detail, error
from ..models import OPERATOR, SIGN_IN_PASSWORD
from ..models import Account as AccountRow
from ..security import (
    DEVICE_COOKIE,
    DEVICE_DAYS,
    MIN_PASSWORD,
    SESSION_COOKIE,
    brake,
    device_of,
    end_all_sessions,
    end_session,
    server_key,
    session_account,
    session_cookie_age,
    session_uid,
    sessions_ended,
    start_session,
)
from ..services import accounts, display, kontenpost, locales, mailer, mounts, settings_service, storage, totp
from ..services.accounts import AccountError
from ..services.konten_store import KontenStore
from ..services.oidc_store import SqlStore, offered_address
from ..vendor import nexkonten
from ..vendor.nexkonten import login as konten_login
from ..vendor.nexkonten import profiles, resets
from ..vendor.nexkonten import sessions as konten_sessions
from ..vendor.nexkonten import setup as konten_setup
from ..vendor.nexkonten.brake import DOOR_FORGOT, door_key
from ..vendor.nexoidc import providers

logger = logging.getLogger("nextime.auth")

router = APIRouter(prefix="/api", tags=["auth"])

#: The languages inside the frontend; others come as files from the operator.
SHIPPED = ("en", "de")
#: Names a sign-in waiting for its second factor (``services/totp.py``), and nothing else.
PENDING_COOKIE = "nextime_2fa" + get_settings().cookie_name_suffix()


class SetupIn(BaseModel):
    display_name: str = Field(max_length=200)
    email: str = Field(default="", max_length=255)
    password: str = Field(max_length=200)
    #: The password typed a second time; checked when given (see ``check_confirm``).
    password_confirm: str | None = Field(default=None, max_length=200)
    language: str = Field(default="", max_length=16)
    #: The setup code from the server's log (or NEXTIME_SETUP_TOKEN).
    code: str = Field(default="", max_length=200)
    #: The username (since the shared account module); left out, a suggestion from the display name.
    name: str | None = Field(default=None, max_length=64)


class LoginIn(BaseModel):
    #: "Username or email" (the field kept its name): an address is looked up by its @.
    name: str = Field(max_length=255)
    password: str = Field(max_length=200)
    #: "Stay signed in" (on from the start): 30 days from the last use; without it the browser's session only.
    remember: bool = True


class ForgotIn(BaseModel):
    #: "Username or email".
    name: str = Field(max_length=255)


class PasswordChangeIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)
    #: The new password typed a second time; checked when given (see ``check_confirm``).
    password_confirm: str | None = Field(default=None, max_length=200)


class ThemeIn(BaseModel):
    #: ``light``, ``dark`` or ``system`` (``nexkonten.profiles.THEMES``); for ``adopt`` whatever the browser kept.
    theme: str = Field(max_length=32)


class LanguageIn(BaseModel):
    language: str = Field(max_length=16)


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=255)
    #: A new username (since the shared account module).
    name: str | None = Field(default=None, max_length=64)
    #: The own password, for a new username or address of an account that has one.
    password: str = Field(default="", max_length=200)


class SwitchIn(BaseModel):
    on: bool


def secure_cookie(request: Request) -> bool:
    mode = get_settings().cookie_secure.lower()
    if mode == "true":
        return True
    if mode == "false":
        return False
    # The scheme as ``ForwardedProtoMiddleware`` left it: the connection's own, or the rightmost value of
    # ``X-Forwarded-Proto`` from a proxy in ``NEXTIME_TRUSTED_PROXIES``, never the header of anybody else.
    return request.url.scheme == "https"


def _set_cookie(response: Response, request: Request, token: str, remember: bool = True) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=session_cookie_age(remember),
        httponly=True,
        samesite="lax",
        secure=secure_cookie(request),
        path="/",
    )


def fail(exc: AccountError | nexkonten.AccountError) -> HTTPException:
    """A refusal of nextime's own account code or of the shared module, in nextime's form: the module's codes
    answer under the names 0.4.0 used where it had others (``public_code``); a brake answers 429 with
    ``Retry-After``."""
    code = getattr(exc, "public_code", exc.code)
    values = dict(exc.extra)
    if code != exc.code:
        # The module's own code beside the older one (konten 0.3.10): the page still tells an expired link apart.
        values["account_code"] = exc.code
    if exc.code == "too_many_attempts":
        wait = int(values.get("retry_after") or 1)
        return HTTPException(status_code=429, detail=detail(code, exc.message, **values),
                             headers={"Retry-After": str(wait)})
    return error(code, exc.message, exc.status, **values)


def too_many(wait: float) -> HTTPException:
    seconds = max(1, int(wait + 0.999))
    return HTTPException(
        status_code=429,
        detail=detail("too_many_attempts", "Too many attempts. Try again later.", retry_after=seconds),
        headers={"Retry-After": str(seconds)},
    )


def check_password(password: str) -> None:
    try:
        accounts.check_password_rule(password)
    except AccountError as exc:
        raise fail(exc) from exc


def check_confirm(password: str, confirm: str | None) -> None:
    """A new password typed twice must be the same twice. The web pages always send the second one; a client that
    leaves it out (a script, an app with one field) is not checked, since only a person typing can mistype."""
    if confirm is not None and confirm != password:
        raise error("password_mismatch", "The passwords do not match.", 422, field="password_confirm")


def check_language(language: str) -> str:
    """Empty (the browser decides) or a language nextime has."""
    if language and language not in SHIPPED and language not in {item.code for item in locales.available()}:
        raise error("unknown_language", "nextime does not have this language.", 422)
    return language


def former_name(db: Session | None, account: AccountRow) -> str:
    """The username the account had before its last change ("formerly …" under the field), "" for none."""
    if db is None:
        return ""
    from ..models_accounts import FormerName

    return db.scalar(select(FormerName.name).where(FormerName.account_id == account.id)
                     .order_by(FormerName.id.desc()).limit(1)) or ""


def pending_email(db: Session | None, account: AccountRow) -> str:
    if db is None:
        return ""
    from ..models_accounts import EmailChange

    return db.scalar(select(EmailChange.address).where(EmailChange.account_id == account.id)) or ""


def account_view(account: AccountRow, db: Session | None = None) -> dict[str, Any]:
    return {
        "id": account.id,
        # When the profile picture was set (``routers/avatars.py``), the version in its address; none without one.
        "avatar": account.avatar_at.isoformat() if account.avatar_at else None,
        "name": account.name,
        "display_name": account.display_name,
        # The color of the circle with the initial (TIME-2 EC-11); given at the first look with a database at hand.
        "color": accounts.person_color(db, account) if db is not None else account.color,
        # "What's new": the running version and the one the account has read.
        "version": __version__,
        "whats_new_seen": account.whats_new_seen,
        "role": account.role,
        "sign_in": account.sign_in,
        "email": account.email,
        # The address counts (signing in, a forgotten password); proven by a link; one waiting for its link.
        "email_confirmed": bool(account.email_confirmed) if account.email else True,
        "email_proven": bool(account.email_proven),
        "email_source": account.email_source or "",
        "pending_email": pending_email(db, account),
        "former_name": former_name(db, account),
        # The address a sign-in provider knows the account by, offered on the account page.
        "provider_email": offered_address(db, account) if db is not None else "",
        # Whose address it is, as on the provider's button ("Use the address from <provider>?", konten 0.3.4).
        "provider_email_from": (account.provider_email_from or "") if db is not None else "",
        "new_sign_in_mail": bool(account.new_sign_in_mail),
        # A password set through a link: the account page lists the ways in that stayed, until put away.
        "new_password_origin": account.new_password_origin or "",
        "language": account.language,
        # Light, dark or as the system, at the account on every device ("" chose nothing yet, konten 1.1.0).
        "theme": account.theme or "",
        # A mail server and the public address: a link in a mail can reach the person (the box "Address not
        # confirmed yet" offers one only then, konten 1.1.0).
        "mail_server": kontenpost.can_mail(db) if db is not None else False,
        "two_factor": bool(account.totp_secret_enc),
        # "On" or "Off" beside the heading of the second factor (``SECOND_FACTOR_STATE``, konten 0.3.6).
        "factor_state": nexkonten.factor.state(KontenStore(db), account.id) if db is not None else (
            nexkonten.SECOND_FACTOR_ACTIVE if account.totp_secret_enc else nexkonten.SECOND_FACTOR_NONE),
        # NEXTIME_SECOND_FACTOR_OFF: the emergency hammer holds, no second factor is asked of anybody (the page says
        # so plainly, ``account.secondFactorOff``).
        "second_factor_off": nexkonten.factor.switched_off(),
        "two_factor_recovery_left": len(totp.load_recovery(account.totp_recovery)) if account.totp_secret_enc else 0,
        "password_changed_at": account.password_changed_at.isoformat() if account.password_changed_at else None,
        # The password was set through the operator's reset link; the page says so until it is put away.
        "password_reset_notice": bool(account.password_reset_notice),
        "created_at": account.created_at.isoformat(),
        "last_seen_at": account.last_seen_at.isoformat() if account.last_seen_at else None,
    }


def known_device(db: DbSession, request: Request, response: Response, account: AccountRow) -> None:
    """Marks this browser as one the account signed in with (the cookie of known browsers, the shared module's
    marks: random per account, signed, no account id in it; up to five accounts per browser)."""
    store = KontenStore(db)
    mark = konten_sessions.mark(server_key(), store.device_key(account.id))
    db.commit()
    value = konten_sessions.add_mark(request.cookies.get(DEVICE_COOKIE, ""), mark)
    response.set_cookie(DEVICE_COOKIE, value, max_age=DEVICE_DAYS * 86400, httponly=True,
                        samesite="lax", secure=secure_cookie(request), path="/api/auth")


def browser_mark(db: Session, request: Request, account_id: int) -> str:
    """The mark of a browser that signed in as this account before, "" for none: a mark of the shared module, or a
    cookie of 0.4.0 (``<id>.<nonce>.<mark>``, read for one version so that owners stay known)."""
    cookie = request.cookies.get(DEVICE_COOKIE, "")
    if not cookie:
        return ""
    store = KontenStore(db)
    try:
        key = store.device_key(account_id)
    except LookupError:
        return ""
    return konten_sessions.known_mark(cookie, server_key(), key, older=lambda entry: device_of(db, entry) == account_id)


def browser_known(db: Session, request: Request, account_id: int) -> bool:
    return bool(browser_mark(db, request, account_id))


def sign_in(db: DbSession, request: Request, response: Response, account: AccountRow, *,
            provider_checked: bool = False, remember: bool = True, tell: bool = False) -> dict[str, Any]:
    """The session starts. ``tell``: a sign-in by its owner (password, provider, passkey), not right after a setup
    or an invitation; from a browser that never signed in as the account, a confirmed address hears of it ("A new
    sign-in", unless the person turned that off)."""
    mailer.note_browser_language(account, request.headers.get("accept-language", ""))
    known = browser_known(db, request, account.id)
    token = start_session(db, account, client_ip(request), request.headers.get("user-agent", ""),
                          provider_checked=provider_checked, remember=remember)
    _set_cookie(response, request, token, remember)
    # This browser is known from now on: a lock that strangers cause by guessing does not keep it out, and the brake
    # per sender lets it pass.
    if not known:
        known_device(db, request, response, account)
        if tell:
            store = KontenStore(db)
            konto = store.get_account(account.id)
            wanted = bool(account.new_sign_in_mail)
            mail = konten_sessions.new_sign_in(konto, known=False, wanted=wanted) if konto else None
            if kontenpost.queue(db, mail, where=request.headers.get("user-agent", "")):
                logger.info("Mail about a new sign-in queued name=%s", account.name)
    logger.info("Signed in name=%s", account.name)
    return account_view(account, db)


@router.get("/setup", summary="Does nextime still need its first account, and is this browser signed in?")
def setup_state(request: Request, db: DbSession) -> dict[str, Any]:
    # ``signed_in`` lets the page ask for the account only when there is one: a 401 would show as an error in the
    # browser's console on every visit of the sign-in page.
    needs_setup = accounts.count(db) == 0
    return {
        "needs_setup": needs_setup,
        # The first account needs the setup code from the server's log.
        "code_required": True,
        "signed_in": session_account(db, request.cookies.get(SESSION_COOKIE)) is not None,
        # Reached over HTTPS (directly or through a trusted proxy): without it the setup warns (TIME-1 AC-6).
        "https": request.url.scheme == "https" if needs_setup else None,
        "version": __version__,
        "min_password": MIN_PASSWORD,
        # Only while nobody has set it up: the setup page warns when the data folder lies on a share.
        "data_on_share": mounts.locate(get_settings().data_dir).network if needs_setup else False,
        "data_free": storage.usage(get_settings().data_dir).free if needs_setup else None,
        # The folder NEXTIME_DATA_DIR names, as the container sees it: whoever sets up checks that the volume is
        # where they meant it. Gone once set up, like the two values above.
        "data_dir": str(get_settings().data_dir) if needs_setup else None,
    }


@router.post("/setup", summary="Create the operator account")
def setup(payload: SetupIn, request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    key = "setup:" + client_ip(request)
    wait = brake.wait_seconds(key)
    if wait:
        raise too_many(wait)
    language = check_language(payload.language)
    check_confirm(payload.password, payload.password_confirm)
    store = KontenStore(db)
    username = payload.name if payload.name is not None else nexkonten.names.suggest_username(payload.display_name)
    try:
        done = konten_setup.setup(
            store, code=payload.code, display_name=payload.display_name, username=username, email=payload.email,
            password=payload.password, language=language,
        )
    except nexkonten.AccountError as exc:
        if exc.code == "setup_code_wrong":
            brake.failed(key)
            logger.warning("Setup refused: wrong setup code")
        raise fail(exc) from exc
    account = db.get(AccountRow, int(done.account.id))
    assert account is not None
    return sign_in(db, request, response, account)


@router.get("/auth/methods", summary="How one can sign in here (no sign-in needed)")
def methods(db: DbSession) -> dict[str, Any]:
    # The buttons of the sign-in page: slug and label of each active provider, nothing else (vendor/nexoidc).
    password = bool(settings_service.get(db, "password_login"))
    return {
        "password": password,
        "providers": providers.public_list(SqlStore(db)),
        # "Sign in with a passkey" (the operator's switch, on from the start).
        "passkeys": bool(settings_service.get(db, "passkeys_allowed")),
        # "Forgot your password?" by mail: a mail server, the public address and signing in by password.
        "forgot_mail": password and kontenpost.can_mail(db),
    }


#: Wrong passwords one sender may give across all names before it waits: room for a household behind one address.
LOGIN_FREE_PER_SENDER = 30


@router.post("/auth/login", summary="Sign in with username or email and password")
def login(payload: LoginIn, request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    """The shared account module checks (``login.sign_in``, konten 0.3.10): one answer for everything wrong, as long
    for an unknown name as for a wrong password, failures counted at the account and its lock after ten (a browser
    that signed in as the account before passes the lock). The brake counts in two pots, the sender and the account
    (or what was typed when it names none); while only the account's pot waits, the answer is the one for a wrong
    password; 429 only when the sender's pot is full. A known browser counts in a pot of its own."""
    ip = client_ip(request)
    behind_unknown_proxy(request)
    store = KontenStore(db)
    meant = konten_login.resolve_login(store, payload.name)
    # A browser that signed in as this account before counts in a pot of its own (the module's brake, five free):
    # neither a stranger guessing its name nor one spraying from the same address (a household behind one router)
    # holds it, and its own wrong passwords still wait.
    mark = browser_mark(db, request, int(meant.id)) if meant is not None else ""
    try:
        konto = konten_login.sign_in(store, payload.name, payload.password, sender=ip, browser=mark)
    except nexkonten.AccountError as exc:
        raise fail(exc) from exc
    account = db.get(AccountRow, int(konto.id))
    assert account is not None
    if not settings_service.get(db, "password_login") and account.role != OPERATOR:
        # The operator keeps the password as the way in when the provider is down.
        raise error("password_login_off", "Sign-in with a password is turned off.", 403)
    step = nexkonten.common.next_step(store, konto)
    if step in (nexkonten.NEXT_SECOND_FACTOR, nexkonten.NEXT_PASSKEY):
        # Nothing opens yet: the browser gets a short-lived cookie that names the waiting sign-in and nothing else.
        # Where the operator asks a second factor of everybody, a passkey is one: then it is the only second step.
        response.set_cookie(
            PENDING_COOKIE,
            totp.start_pending(account.id, remember=payload.remember),
            max_age=totp.PENDING_SECONDS,
            httponly=True,
            samesite="lax",
            secure=secure_cookie(request),
            path="/api/auth",
        )
        logger.info("Password accepted, second factor waiting name=%s", account.name)
        # The answer of 0.4.0 stays as it was; ``code: false`` is the module's ``next`` = ``passkey`` (the dialog
        # "Session expired" reads it the same way, ``sessionExpiredParts``).
        answer: dict[str, Any] = {"second_factor": True}
        if store.passkeys_of(account.id):
            # The code step offers "Use a passkey" (only then: the answer of 0.4.0 stays as it was).
            answer["passkey"] = True
        if step == nexkonten.NEXT_PASSKEY:
            # No authenticator: the passkey is the second step, there is no code to ask.
            answer["code"] = False
        return answer
    konten_login.succeeded(store, account.id)
    return sign_in(db, request, response, account, remember=payload.remember, tell=True)


#: Answered at once, the same whatever was typed: the search, the link and the mail run after the answer.
@router.post("/auth/forgot", status_code=202, summary="Forgot your password? A link by mail (with a mail server)")
def forgot(payload: ForgotIn, request: Request, db: DbSession) -> dict[str, Any]:
    # The requests of one sender count at the module's brake (``forgot:<sender>``, ten free, konten 0.3.4), the same
    # for every input and also without a mail server; the account counts in the worker (``mail:<id>``).
    sender = client_ip(request)
    typed = payload.name

    def send(mail: nexkonten.Mail) -> None:
        # At once, never through the outbox (konten 0.3.10): a mail that fails takes its link with it.
        from ..db import SessionLocal

        with SessionLocal() as own:
            kontenpost.deliver_now(own, mail)

    try:
        if settings_service.get(db, "password_login") and kontenpost.can_mail(db):
            resets.forgot_in_background(typed, open_store=_own_store, send=send, later=_later, sender=sender)
        else:
            nexkonten.common.counted((door_key(DOOR_FORGOT, sender),))
    except nexkonten.AccountError as exc:
        raise fail(exc) from exc
    return {"sent": True}


def _own_store():
    from ..services.konten_store import own_store

    return own_store()


def _later(job) -> None:
    """The rest of "Forgot your password?" after the answer, in a thread of its own (the time of the answer must
    tell nobody whether there was an account). Tests run it at once (``FORGOT_AT_ONCE``)."""
    if FORGOT_AT_ONCE:
        job()
        return
    import threading

    threading.Thread(target=job, name="nextime-forgot", daemon=True).start()


#: Tests run the work of "Forgot your password?" before the answer, to see its mail.
FORGOT_AT_ONCE = False


@router.post("/auth/logout", status_code=204, summary="Sign out in this browser")
def logout(request: Request, response: Response, db: DbSession) -> None:
    token = request.cookies.get(SESSION_COOKIE)
    account = session_account(db, token)
    end_session(db, token)
    if account is not None:
        logger.info("Signed out name=%s", account.name)
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.post("/auth/logout-all", status_code=204, summary="Sign out every other browser of the own account")
def logout_everywhere(request: Request, response: Response, account: Account, db: DbSession) -> None:
    end_all_sessions(db, account.id, except_token=request.cookies.get(SESSION_COOKIE))
    # The other browsers are not known any more; this one stays known.
    row = db.get(AccountRow, account.id)
    if row is not None:
        db.refresh(row)
        known_device(db, request, response, row)
    logger.info("All other sessions ended by their owner name=%s", account.name)


#: The device's time zone, sent by the page with every request (an IANA name such as "Europe/Berlin").
ZONE_HEADER = "x-nextime-zone"


@router.get("/auth/me", summary="The signed-in account, and what this server offers it")
def me(request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    row = db.get(AccountRow, account.id)
    assert row is not None
    # The first device that asks names the home time zone (TIME-3 AC-11); it reaches here after every sign-in, also
    # after a provider's redirect, which carries no header of the page.
    display.learn_home_zone(db, row, request.headers.get(ZONE_HEADER, "").strip())
    return me_view(row, db, session_of=account)


def me_view(account: AccountRow, db: DbSession, *, session_of: AccountRow | None = None) -> dict[str, Any]:
    """``session_of``: the account as the session found it, which knows whether the sign-in came through a provider
    that checks the second factor itself."""
    return {
        **account_view(account, db),
        "mail": mailer.configured(db),
        "second_factor_setup_required": totp.setup_required(db, session_of or account),
        "min_password": MIN_PASSWORD,
        # First day of the week, week numbers, clock, time zone and home zone (TIME-3), on every device.
        "display": display.view(account),
    }


@router.put("/auth/password", status_code=204, summary="Change the own password")
def change_password(payload: PasswordChangeIn, request: Request, response: Response, account: Account,
                    db: DbSession) -> None:
    row = db.get(AccountRow, account.id)
    assert row is not None
    if row.sign_in != SIGN_IN_PASSWORD:
        raise error("oidc_account", "This account signs in through OIDC.", 409)
    check_password(payload.new)
    check_confirm(payload.new, payload.password_confirm)
    reauth_guard(request, row)
    try:
        accounts.change_password(db, row, payload.current, payload.new)
    except AccountError as exc:
        if exc.code == "wrong_password":
            reauth_failed(request, db, row)
        raise fail(exc) from exc
    reauth_succeeded(request, db, row)
    # Other browsers must sign in again and are not known any more; this one stays, and stays known.
    end_all_sessions(db, row.id, except_token=request.cookies.get(SESSION_COOKIE))
    db.refresh(row)
    known_device(db, request, response, row)


def _confirmed_without_password(request: Request, db: Session, row: AccountRow) -> bool:
    """An account without a password of its own here proves it is still them by a fresh sign-in at its provider
    (``deps._fresh_from_provider``, the same as for an operator act); then the module asks for nothing more."""
    from ..deps import _fresh_from_provider

    if row.sign_in == SIGN_IN_PASSWORD and row.password_hash:
        return False
    _fresh_from_provider(request, db, row)
    return True


@router.put("/me/profile", summary="The own display name, username and address")
def set_profile(payload: ProfileIn, request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    """Field by field through the shared module: the display name without a password (it may be empty: then the
    username shows), a new username and a new address with the own password (an account without one: a fresh
    sign-in at its provider). With a mail server a new address waits for the link sent there; without one it counts
    at once. A paired device (Bearer) changes the display name only, never the username or the address."""
    row = db.get(AccountRow, account.id)
    assert row is not None
    changes_email = payload.email is not None and payload.email.strip().lower() != (row.email or "").lower()
    changes_name = payload.name is not None and payload.name.strip().lower() != row.name.lower()
    store = KontenStore(db)
    try:
        if payload.display_name is not None and payload.display_name != row.display_name:
            profiles.change_display_name(store, row.id, payload.display_name, actor_id=row.id)
        fresh = _confirmed_without_password(request, db, row) if (changes_email or changes_name) else False
        if changes_name:
            notice = profiles.change_username(store, row.id, payload.name or "", actor_id=row.id,
                                              password=payload.password, actor_confirmed=fresh)
            kontenpost.queue(db, notice)
            logger.info("Username changed by its owner account_id=%s", row.id)
        if changes_email:
            changed = profiles.change_email(store, row.id, payload.email or "", password=payload.password,
                                            actor_confirmed=fresh, mail=kontenpost.can_mail(db))
            kontenpost.queue(db, changed.mail)
            kontenpost.queue(db, changed.notice)
    except nexkonten.AccountError as exc:
        db.rollback()
        raise fail(exc) from exc
    db.refresh(row)
    return account_view(row, db)


@router.post("/me/email/resend", summary="Send the link for the address that waits once more")
def resend_email_link(account: Account, db: DbSession) -> dict[str, Any]:
    try:
        mail = profiles.resend_email_link(KontenStore(db), account.id)
    except nexkonten.AccountError as exc:
        raise fail(exc) from exc
    kontenpost.queue(db, mail)
    row = db.get(AccountRow, account.id)
    assert row is not None
    return account_view(row, db)


@router.post("/me/email/prove", summary="A link that proves the address that counts already is the own one")
def prove_email(account: Account, db: DbSession) -> dict[str, Any]:
    """"Address not confirmed yet" › "Send link" (konten 1.1.0, ``profiles.prove_email``): the address stays as it is
    until the link is used."""
    if not kontenpost.can_mail(db):
        raise error("mail_off", "No mail server is set up.", 409)
    try:
        mail = profiles.prove_email(KontenStore(db), account.id)
    except nexkonten.AccountError as exc:
        raise fail(exc) from exc
    kontenpost.queue(db, mail)
    row = db.get(AccountRow, account.id)
    assert row is not None
    return account_view(row, db)


@router.delete("/me/email/pending", summary="Withdraw the address that waits for its link")
def withdraw_email(account: Account, db: DbSession) -> dict[str, Any]:
    profiles.withdraw_email_change(KontenStore(db), account.id)
    row = db.get(AccountRow, account.id)
    assert row is not None
    return account_view(row, db)


@router.get("/email/{token}", summary="Which address a confirmation link confirms (no sign-in needed)")
def email_link_state(token: str, db: DbSession) -> dict[str, Any]:
    try:
        change = profiles.email_change_state(KontenStore(db), token[:200])
    except nexkonten.AccountError as exc:
        raise fail(exc) from exc
    return {"email": change.address}


@router.post("/email/{token}", summary="Confirm a new address with the link sent there (no sign-in needed)")
def confirm_email(token: str, request: Request, db: DbSession) -> dict[str, Any]:
    key = "email-link:" + client_ip(request)
    wait = brake.wait_seconds(key)
    if wait:
        raise too_many(wait)
    try:
        changed = profiles.confirm_email(KontenStore(db), token[:200])
    except nexkonten.AccountError as exc:
        if exc.code in ("email_link_invalid", "email_link_expired"):
            brake.failed(key)
        raise fail(exc) from exc
    kontenpost.queue(db, changed.notice)
    return {"email": changed.account.email}


@router.put("/me/new-sign-in-mail", summary="A mail when a browser signs in for the first time: on or off")
def set_new_sign_in_mail(payload: SwitchIn, account: Account, db: DbSession) -> dict[str, Any]:
    row = db.get(AccountRow, account.id)
    assert row is not None
    row.new_sign_in_mail = payload.on
    db.commit()
    return account_view(row, db)


# --- Where am I signed in, and what still has access ---------------------------------------------------------------


@router.get("/auth/sessions", summary="Where the own account is signed in: browsers and paired devices")
def my_sessions(request: Request, account: Account, db: DbSession) -> list[dict[str, Any]]:
    current = session_uid(db, request.cookies.get(SESSION_COOKIE))
    # Browsers by name ("Firefox on Linux") in the account's language, never the stored User-Agent.
    language = (account.language or account.browser_language or "en")[:2].lower()
    return konten_sessions.listing(KontenStore(db), account.id, current, language=language)


@router.delete("/auth/sessions/{uid}", status_code=204, summary="Sign one browser or device of the own account out")
def end_one_session(uid: str, request: Request, account: Account, db: DbSession) -> None:
    if uid == session_uid(db, request.cookies.get(SESSION_COOKIE)):
        raise error("use_logout", "Sign out of this browser with Sign out.", 409)
    if not konten_sessions.sign_out(KontenStore(db), account.id, uid[:40]):
        raise error("not_found", "Not found.", 404)
    db.commit()
    sessions_ended()
    logger.info("Session ended by its owner name=%s", account.name)


@router.get("/auth/access", summary="What still has access after a password set through a link")
def remaining_access(account: Account, db: DbSession) -> dict[str, Any]:
    row = db.get(AccountRow, account.id)
    assert row is not None
    return {
        "origin": row.new_password_origin or "",
        "access": [
            {"id": str(item.id), "kind": item.kind, "label": item.label,
             "last_used_at": item.last_used_at.isoformat() if item.last_used_at else None}
            for item in KontenStore(db).list_access(account.id)
        ],
    }


@router.post("/auth/access/disconnect", summary="Disconnect all: every device, API key and passkey of the account")
def disconnect_all(request: Request, account: Account, db: DbSession) -> dict[str, Any]:
    from ..deps import confirm_fresh_session

    confirm_fresh_session(request, db, account)
    count = resets.disconnect_all(KontenStore(db), account.id)
    row = db.get(AccountRow, account.id)
    assert row is not None
    row.new_password_origin = ""
    db.commit()
    sessions_ended()
    return {"disconnected": count}


@router.post("/auth/access/seen", summary="The list of what still has access is put away")
def remaining_access_seen(account: Account, db: DbSession) -> dict[str, Any]:
    row = db.get(AccountRow, account.id)
    assert row is not None
    row.new_password_origin = ""
    db.commit()
    return account_view(row, db)


@router.post("/me/whats-new/seen", summary="What's new of the running version is read or put away")
def whats_new_seen(account: Account, db: DbSession) -> dict[str, Any]:
    row = db.get(AccountRow, account.id)
    assert row is not None
    row.whats_new_seen = __version__
    db.commit()
    return account_view(row)


@router.post("/me/password-reset-notice/seen",
             summary="The notice that the password was set through the operator's reset link is read")
def password_reset_notice_seen(account: Account, db: DbSession) -> dict[str, Any]:
    row = db.get(AccountRow, account.id)
    assert row is not None
    row.password_reset_notice = False
    db.commit()
    return account_view(row)


class DisplayIn(BaseModel):
    week_start: StrictInt | None = None
    week_numbers: StrictBool | None = None
    #: The same event in two calendars of one person once (TIME-14 AC-4).
    merge_duplicates: StrictBool | None = None
    #: Events in the month: "dot" or "tint".
    month_style: str | None = Field(default=None, max_length=8)
    clock: str | None = Field(default=None, max_length=8)
    #: "" follows the device; otherwise an IANA name.
    time_zone: str | None = Field(default=None, max_length=64)


@router.put("/me/display", summary="First day of the week, week numbers, clock and time zone of the own account")
def set_display(payload: DisplayIn, account: Account, db: DbSession) -> dict[str, Any]:
    row = db.get(AccountRow, account.id)
    assert row is not None
    try:
        display.save(db, row, payload.model_dump(exclude_none=True))
    except display.DisplayError as exc:
        db.rollback()
        raise error("invalid_display", exc.message, 422, field=exc.field) from exc
    return me_view(row, db, session_of=account)


@router.put("/me/theme", summary="Light, dark or as the system, for the own account on every device")
def set_theme(payload: ThemeIn, account: Account, db: DbSession) -> dict[str, Any]:
    try:
        profiles.change_theme(KontenStore(db), account.id, payload.theme)
    except nexkonten.AccountError as exc:
        raise fail(exc) from exc
    row = db.get(AccountRow, account.id)
    assert row is not None
    return account_view(row, db)


@router.post("/me/theme/adopt", summary="Take the browser's theme of before once, while the account has none")
def adopt_theme(payload: ThemeIn, account: Account, db: DbSession) -> dict[str, Any]:
    """The first load after konten 1.1.0: an account that never chose takes what this browser kept
    (``nextime.theme``); one with a choice keeps it (``profiles.adopt_theme``)."""
    theme = profiles.adopt_theme(KontenStore(db), account.id, payload.theme)
    return {"theme": theme}


@router.put("/me/language", summary="The language of the own account, on every device; empty follows the browser")
def set_language(payload: LanguageIn, account: Account, db: DbSession) -> dict[str, Any]:
    row = db.get(AccountRow, account.id)
    assert row is not None
    row.language = check_language(payload.language)
    db.commit()
    return account_view(row)
