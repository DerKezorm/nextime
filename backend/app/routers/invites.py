"""Invitations: the operator invites a person, who makes an account with a password or through a provider. And reset
links: the operator makes one for an account that forgot its password, and the person sets a new one themselves.

Both links are shown once (an invitation is also mailed, when a mail server is set up). Only the hash of a token is
stored; used once, then gone. An expired link says so; a used or withdrawn one says it is not valid any more, and
nothing is made.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi import Path as PathParam
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..deps import DbSession, OperatorAccount, client_ip
from ..errors import detail, error
from ..models import MEMBER, Invite
from ..models import Account as AccountRow
from ..security import MIN_PASSWORD, SESSION_COOKIE, brake, session_account
from ..services import accounts, mailer, settings_service
from ..services.accounts import AccountError
from .auth import check_confirm, check_password, fail, sign_in

logger = logging.getLogger("nextime.auth")

router = APIRouter(prefix="/api", tags=["invites"])

Token = Annotated[str, PathParam(min_length=20, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")]

#: Invitation mails the operator may send in an hour.
MAILS_PER_HOUR = 20
#: Failed account attempts one sender may make with invitation links before it waits.
NAME_TRIES = 8


class InviteIn(BaseModel):
    days: int = 7
    email: str = Field(default="", max_length=255)
    role: str = Field(default=MEMBER, max_length=16)
    send: bool = False
    #: The language the operator's page shows: the mail's, when neither the receiver nor the operator has one.
    language: str = Field(default="", max_length=16)


class ResetIn(BaseModel):
    password: str = Field(max_length=200)
    password_confirm: str | None = Field(default=None, max_length=200)


class AcceptIn(BaseModel):
    display_name: str = Field(max_length=200)
    email: str = Field(default="", max_length=255)
    password: str = Field(max_length=200)
    password_confirm: str | None = Field(default=None, max_length=200)


def _view(invite: Invite, db: DbSession) -> dict[str, Any]:
    by = db.get(AccountRow, invite.created_by) if invite.created_by else None
    return {
        "id": invite.id,
        "role": invite.role,
        "email": invite.email,
        "by": by.shown_name if by else None,
        "created_at": invite.created_at.isoformat(),
        "expires_at": invite.expires_at.isoformat(),
    }


def _link(db: DbSession, request: Request, token: str) -> str:
    base = settings_service.public_url(db) or str(request.base_url).rstrip("/")
    return f"{base}/invite/{token}"


@router.get("/invites", summary="Open invitations (operator)")
def list_invites(_operator: OperatorAccount, db: DbSession) -> list[dict[str, Any]]:
    return [_view(invite, db) for invite in accounts.open_invites(db)]


@router.post("/invites", status_code=201, summary="Invite a person; the link is shown once (operator)")
def invite(payload: InviteIn, request: Request, operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    email = payload.email.strip()
    if payload.send and not email:
        raise error("invalid_email", "Sending needs an email address.", 422)
    if payload.send:
        if not mailer.configured(db):
            raise error("mail_off", "No mail server is set up.", 409)
        # A mail goes out under the operator's mail server: never with a link to an address the request made up.
        if not settings_service.public_url(db):
            raise error("public_url_missing", "Mail needs the public address of nextime; set it first.", 409)
        key = f"invite-mail:{operator.id}"
        if brake.wait_seconds(key, MAILS_PER_HOUR):
            raise error("too_many_attempts", "Too many invitation mails. Try again later.", 429)
        brake.failed(key)
    if email:
        # One open invitation per address: a second one replaces the first, so no old link stays valid beside it.
        for old in db.scalars(select(Invite).where(func.lower(Invite.email) == email.lower())):
            db.delete(old)
    try:
        row, token = accounts.create_invite(db, operator, days=payload.days, email=email, role=payload.role)
    except AccountError as exc:
        db.rollback()
        raise fail(exc) from exc
    link = _link(db, request, token)
    sent = False
    if payload.send:
        try:
            language = mailer.language_for(db, None, *mailer.languages(operator, payload.language))
            mailer.send_invite(db, email, link, by=operator.shown_name, until=row.expires_at.date(),
                               language=language)
            sent = True
        except mailer.MailError as exc:
            db.delete(row)
            db.commit()
            raise error(exc.code, str(exc), 409 if exc.code == "mail_off" else 502) from exc
    logger.info("Invitation made by=%s mailed=%s", operator.name, sent)
    return {**_view(row, db), "link": link, "sent": sent}


@router.delete("/invites/{invite_id}", status_code=204, summary="Withdraw an invitation (operator)")
def withdraw(invite_id: int, _operator: OperatorAccount, db: DbSession) -> None:
    row = db.get(Invite, invite_id)
    if row is None:
        raise error("not_found", "No such invitation.", 404)
    db.delete(row)
    db.commit()


def _valid(db: DbSession, token: str) -> Invite:
    row = accounts.find_invite(db, token)
    if row is None:
        if accounts.invite_expired(db, token):
            raise error("invite_expired", "This invitation has expired.", 404)
        raise error("invite_invalid", "This invitation has been used already or is not valid any more.", 404)
    return row


@router.get("/invite/{token}", summary="What an invitation offers (no sign-in needed)")
def invite_state(token: Token, request: Request, db: DbSession) -> dict[str, Any]:
    row = _valid(db, token)
    signed_in = session_account(db, request.cookies.get(SESSION_COOKIE))
    by = db.get(AccountRow, row.created_by) if row.created_by else None
    return {
        "by": by.shown_name if by else None,
        "email": row.email,
        "role": row.role,
        "min_password": MIN_PASSWORD,
        "password": bool(settings_service.get(db, "password_login")),
        "signed_in_as": signed_in.name if signed_in else None,
    }


@router.post("/invite/{token}", summary="Accept an invitation with a new account and a password")
def accept(token: Token, payload: AcceptIn, request: Request, response: Response, db: DbSession) -> dict[str, Any]:
    key = "invite-name:" + client_ip(request)
    wait = brake.wait_seconds(key, NAME_TRIES)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=detail("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
            headers={"Retry-After": str(wait)},
        )
    check_password(payload.password)
    check_confirm(payload.password, payload.password_confirm)
    if not settings_service.get(db, "password_login"):
        raise error("password_login_off", "Sign-in with a password is turned off.", 403)
    try:
        account = accounts.accept_invite(
            db, token, display_name=payload.display_name, email=payload.email, password=payload.password
        )
    except AccountError as exc:
        if exc.code in ("email_taken", "invite_invalid"):
            brake.failed(key)
        raise fail(exc) from exc
    return sign_in(db, request, response, account)


# --- Reset links -------------------------------------------------------------------------------------------------


@router.get("/reset/{token}", summary="Whom a reset link is for (no sign-in needed)")
def reset_state(token: Token, db: DbSession) -> dict[str, Any]:
    found = accounts.find_reset(db, token)
    if found is None:
        if accounts.reset_expired(db, token):
            raise error("reset_expired", "This link has expired.", 404)
        raise error("reset_invalid", "This link has been used already or is not valid any more.", 404)
    reset, account = found
    return {"name": account.shown_name, "min_password": MIN_PASSWORD, "expires_at": reset.expires_at.isoformat()}


@router.post("/reset/{token}", status_code=204, summary="Set a new password with a reset link; signs in nowhere")
def reset_password(token: Token, payload: ResetIn, request: Request, db: DbSession) -> None:
    key = "reset:" + client_ip(request)
    wait = brake.wait_seconds(key, NAME_TRIES)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=detail("too_many_attempts", "Too many attempts. Try again later.", retry_after=wait),
            headers={"Retry-After": str(wait)},
        )
    check_password(payload.password)
    check_confirm(payload.password, payload.password_confirm)
    try:
        accounts.use_reset(db, token, payload.password)
    except AccountError as exc:
        if exc.code in ("reset_invalid", "reset_expired"):
            brake.failed(key)
        raise fail(exc) from exc
