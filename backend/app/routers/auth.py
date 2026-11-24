"""Setting up the first account, signing in and out, and the own account.

The operator's list of accounts is in ``routers/accounts.py``, invitations in ``routers/invites.py``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, StrictBool, StrictInt
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
    Brake,
    brake,
    device_of,
    device_token,
    end_all_sessions,
    end_session,
    session_account,
    start_session,
)
from ..services import accounts, display, locales, mailer, mounts, settings_service, storage, totp
from ..services.accounts import AccountError
from ..services.oidc_store import SqlStore, offered_address
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


class LoginIn(BaseModel):
    #: The address, or the account name.
    name: str = Field(max_length=255)
    password: str = Field(max_length=200)


class PasswordChangeIn(BaseModel):
    current: str = Field(max_length=200)
    new: str = Field(max_length=200)
    #: The new password typed a second time; checked when given (see ``check_confirm``).
    password_confirm: str | None = Field(default=None, max_length=200)


class LanguageIn(BaseModel):
    language: str = Field(max_length=16)


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=255)


def secure_cookie(request: Request) -> bool:
    mode = get_settings().cookie_secure.lower()
    if mode == "true":
        return True
    if mode == "false":
        return False
    # The scheme as ``ForwardedProtoMiddleware`` left it: the connection's own, or the rightmost value of
    # ``X-Forwarded-Proto`` from a proxy in ``NEXTIME_TRUSTED_PROXIES``, never the header of anybody else.
    return request.url.scheme == "https"


def _set_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=get_settings().session_days * 86400,
        httponly=True,
        samesite="lax",
        secure=secure_cookie(request),
        path="/",
    )


def fail(exc: AccountError) -> HTTPException:
    return error(exc.code, exc.message, exc.status, **exc.extra)


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
        # The address a sign-in provider knows the account by, offered on the account page.
        "provider_email": offered_address(db, account) if db is not None else "",
        "language": account.language,
        "two_factor": bool(account.totp_secret_enc),
        "two_factor_recovery_left": len(totp.load_recovery(account.totp_recovery)) if account.totp_secret_enc else 0,
        "password_changed_at": account.password_changed_at.isoformat() if account.password_changed_at else None,
        # The password was set through the operator's reset link; the page says so until it is put away.
        "password_reset_notice": bool(account.password_reset_notice),
        "created_at": account.created_at.isoformat(),
        "last_seen_at": account.last_seen_at.isoformat() if account.last_seen_at else None,
    }


def known_device(db: DbSession, request: Request, response: Response, account: AccountRow) -> None:
    """Marks this browser as one the account signed in with (the device cookie)."""
    response.set_cookie(DEVICE_COOKIE, device_token(db, account), max_age=DEVICE_DAYS * 86400, httponly=True,
                        samesite="lax", secure=secure_cookie(request), path="/api/auth")


def sign_in(db: DbSession, request: Request, response: Response, account: AccountRow, *,
            provider_checked: bool = False) -> dict[str, Any]:
    mailer.note_browser_language(account, request.headers.get("accept-language", ""))
    token = start_session(db, account, client_ip(request), request.headers.get("user-agent", ""),
                          provider_checked=provider_checked)
    _set_cookie(response, request, token)
    # This browser is known from now on: a lock that strangers cause by guessing does not keep it out, and the brake
    # per sender lets it pass.
    if device_of(db, request.cookies.get(DEVICE_COOKIE)) != account.id:
        known_device(db, request, response, account)
    logger.info("Signed in name=%s", account.name)
    return account_view(account)


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
        raise HTTPException(
            status_code=429,
            detail=detail("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
            headers={"Retry-After": str(wait)},
        )
    language = check_language(payload.language)
    check_confirm(payload.password, payload.password_confirm)
    try:
        account = accounts.create_operator(
            db, display_name=payload.display_name, email=payload.email, password=payload.password, code=payload.code
        )
    except AccountError as exc:
        if exc.code == "setup_code_wrong":
            brake.failed(key)
            logger.warning("Setup refused: wrong setup code")
        raise fail(exc) from exc
    account.language = language
    db.commit()
    return sign_in(db, request, response, account)


@router.get("/auth/methods", summary="How one can sign in here (no sign-in needed)")
def methods(db: DbSession) -> dict[str, Any]:
    # The buttons of the sign-in page: slug and label of each active provider, nothing else (vendor/nexoidc).
    return {
        "password": bool(settings_service.get(db, "password_login")),
        "providers": providers.public_list(SqlStore(db)),
    }


#: Wrong passwords one sender may give across all names before it waits: room for a household behind one address.
LOGIN_FREE_PER_SENDER = 30


@router.post("/auth/login", summary="Sign in with address (or name) and password")
def login(payload: LoginIn, request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    # The brake first, then the password, then the rules: an unknown address costs the time of a wrong password, so
    # neither the answer nor its timing tells which accounts exist.
    #
    # Two counts: per sender and name, and per sender alone with more room. The second is not reset by a sign-in that
    # works. A browser that signed in as this very account before (its device cookie, signed by the server) is
    # counted on its own, so that a stranger guessing cannot keep the owner out.
    ip = client_ip(request)
    behind_unknown_proxy(request)
    name = payload.name.strip().lower()[:255]
    cookie = request.cookies.get(DEVICE_COOKIE)
    device = device_of(db, cookie)
    known = accounts.by_login(db, payload.name) if device is not None else None
    if known is not None and known.id == device and cookie:
        keys = [("login-dev:" + cookie.split(".")[1] + "|" + name, Brake.FREE)]
    else:
        keys = [("login:" + ip + "|" + name, Brake.FREE), ("login-ip:" + ip, LOGIN_FREE_PER_SENDER)]
    wait = max(brake.wait_seconds(key, free) for key, free in keys)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=detail("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
            headers={"Retry-After": str(wait)},
        )
    try:
        account = accounts.authenticate(db, payload.name, payload.password, device)
    except AccountError as exc:
        for key, _free in keys:
            brake.failed(key)
        raise fail(exc) from exc
    brake.succeeded(keys[0][0])
    if not settings_service.get(db, "password_login") and account.role != OPERATOR:
        # The operator keeps the password as the way in when the provider is down.
        raise error("password_login_off", "Sign-in with a password is turned off.", 403)
    if account.totp_secret_enc:
        # Nothing opens yet: the browser gets a short-lived cookie that names the waiting sign-in and nothing else.
        response.set_cookie(
            PENDING_COOKIE,
            totp.start_pending(account.id),
            max_age=totp.PENDING_SECONDS,
            httponly=True,
            samesite="lax",
            secure=secure_cookie(request),
            path="/api/auth",
        )
        logger.info("Password accepted, second factor waiting name=%s", account.name)
        return {"second_factor": True}
    return sign_in(db, request, response, account)


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


@router.put("/me/profile", summary="The own name and address")
def set_profile(payload: ProfileIn, account: Account, db: DbSession) -> dict[str, Any]:
    row = db.get(AccountRow, account.id)
    assert row is not None
    try:
        if payload.display_name is not None:
            shown = accounts.clean_display_name(payload.display_name)
            if not shown:
                raise AccountError("display_name_empty", "Enter a name.", 422)
            row.display_name = shown
        if payload.email is not None:
            address = accounts.clean_email(db, payload.email, own_id=row.id)
            if address.lower() != (row.email or "").lower():
                row.email_source = ""
            row.email = address
    except AccountError as exc:
        db.rollback()
        raise fail(exc) from exc
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


@router.put("/me/language", summary="The language of the own account, on every device; empty follows the browser")
def set_language(payload: LanguageIn, account: Account, db: DbSession) -> dict[str, Any]:
    row = db.get(AccountRow, account.id)
    assert row is not None
    row.language = check_language(payload.language)
    db.commit()
    return account_view(row)
