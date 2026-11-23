"""The operator's list of accounts: roles, blocking, deleting and bringing back, storage limits, reset links.

The operator sees name, address, way of signing in, role, used storage with its limit and last use; never a calendar,
an event or stored access of somebody else (TIME-2 AC-3), and never sets somebody else's password (a reset link lets
the person set their own, ``routers/invites.py``). Every act that a stolen session must not be enough for asks for
the operator's own password once more (``confirm_operator``).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from ..deps import DbSession, OperatorAccount, confirm_operator
from ..errors import error
from ..models import DELETE_GRACE_DAYS, OPERATOR
from ..models import Account as AccountRow
from ..security import end_all_sessions
from ..services import accounts, content, settings_service, totp
from ..services.accounts import AccountError
from ..services.oidc_store import linked_providers
from .auth import account_view, fail

logger = logging.getLogger("nextime.auth")

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

#: The largest storage limit the page offers, in bytes (10 TB).
LIMIT_MAX = 10 * 1024**4


class RoleIn(BaseModel):
    role: str = Field(max_length=16)
    #: The operator's own password once more; empty for an operator from a provider.
    current_password: str = Field(default="", max_length=200)


class ResetLinkIn(BaseModel):
    #: How long the link is valid, as for an invitation: 1, 7 or 30 days.
    days: int = 7
    current_password: str = Field(default="", max_length=200)


class OperatorConfirmIn(BaseModel):
    current_password: str = Field(default="", max_length=200)


class LimitIn(BaseModel):
    #: Bytes; None removes the limit.
    limit: int | None = Field(default=None, ge=1, le=LIMIT_MAX)


def _row(db: DbSession, account_id: int) -> AccountRow:
    row = db.get(AccountRow, account_id)
    if row is None:
        raise error("not_found", "No such account.", 404)
    return row


def _listed(db: DbSession, row: AccountRow, linked: dict[int, list[dict[str, object]]]) -> dict[str, Any]:
    used = content.counts(db, row.id)
    view = account_view(row, db)
    view.pop("two_factor_recovery_left", None)
    view.pop("password_reset_notice", None)
    view.pop("provider_email", None)
    return {
        **view,
        "locked": accounts.is_locked(row),
        "blocked": row.blocked_at is not None,
        "blocked_at": row.blocked_at.isoformat() if row.blocked_at else None,
        "deleted_at": row.deleted_at.isoformat() if row.deleted_at else None,
        "purge_at": (row.deleted_at + timedelta(days=DELETE_GRACE_DAYS)).isoformat() if row.deleted_at else None,
        "has_password": bool(row.password_hash),
        "providers": linked.get(row.id, []),
        "storage_used": used.bytes,
        "storage_limit": row.storage_limit,
    }


@router.get("", summary="All accounts, with storage and last use (no contents)")
def list_accounts(_operator: OperatorAccount, db: DbSession) -> list[dict[str, Any]]:
    linked = linked_providers(db)
    return [_listed(db, row, linked) for row in db.scalars(select(AccountRow).order_by(AccountRow.created_at))]


@router.get("/{account_id}/impact", summary="What deleting the account takes away: numbers only")
def delete_impact(account_id: int, _operator: OperatorAccount, db: DbSession) -> dict[str, int]:
    row = _row(db, account_id)
    found = content.counts(db, row.id)
    return {
        "calendars": found.calendars, "events": found.events, "shared": found.shared, "days": DELETE_GRACE_DAYS,
    }


@router.delete("/{account_id}", status_code=204, summary="Delete an account (brought back within 30 days)")
def delete_account(
    account_id: int, payload: OperatorConfirmIn, request: Request, operator: OperatorAccount, db: DbSession,
) -> None:
    confirm_operator(request, db, operator, payload.current_password)
    if account_id == operator.id:
        raise error("cannot_delete_self", "You cannot delete your own account.", 409)
    row = _row(db, account_id)
    try:
        accounts.delete_account(db, row)
    except AccountError as exc:
        raise fail(exc) from exc
    totp.forget_account(account_id)
    logger.warning("Account deleted name=%s by=%s", row.name, operator.name)


@router.post("/{account_id}/restore", summary="Bring a deleted account back")
def restore_account(
    account_id: int, payload: OperatorConfirmIn, request: Request, operator: OperatorAccount, db: DbSession,
) -> dict[str, Any]:
    confirm_operator(request, db, operator, payload.current_password)
    row = _row(db, account_id)
    try:
        accounts.restore_account(db, row)
    except AccountError as exc:
        raise fail(exc) from exc
    return _listed(db, row, linked_providers(db))


@router.post("/{account_id}/sign-out", status_code=204, summary="End every session of an account")
def sign_out_account(account_id: int, operator: OperatorAccount, db: DbSession) -> None:
    row = _row(db, account_id)
    end_all_sessions(db, row.id)
    logger.warning("All sessions ended name=%s by=%s", row.name, operator.name)


@router.post("/{account_id}/block", status_code=204, summary="Block an account (out on every device at once)")
def block_account(
    account_id: int, payload: OperatorConfirmIn, request: Request, operator: OperatorAccount, db: DbSession,
) -> None:
    confirm_operator(request, db, operator, payload.current_password)
    if account_id == operator.id:
        raise error("cannot_block_self", "You cannot block yourself.", 409)
    row = _row(db, account_id)
    try:
        accounts.block(db, row)
    except AccountError as exc:
        raise fail(exc) from exc
    logger.warning("Account blocked name=%s by=%s", row.name, operator.name)


@router.post("/{account_id}/unblock", status_code=204, summary="Let a blocked account in again")
def unblock_account(
    account_id: int, payload: OperatorConfirmIn, request: Request, operator: OperatorAccount, db: DbSession,
) -> None:
    confirm_operator(request, db, operator, payload.current_password)
    row = _row(db, account_id)
    accounts.unblock(db, row)
    logger.warning("Account unblocked name=%s by=%s", row.name, operator.name)


@router.put("/{account_id}/role", summary="Make an account operator or member")
def set_role(
    account_id: int, payload: RoleIn, request: Request, operator: OperatorAccount, db: DbSession,
) -> dict[str, Any]:
    confirm_operator(request, db, operator, payload.current_password)
    row = _row(db, account_id)
    if row.id == operator.id and payload.role != OPERATOR:
        raise error("cannot_demote_self", "You cannot take the operator role from yourself.", 409)
    try:
        accounts.set_role(db, row, payload.role)
    except AccountError as exc:
        raise fail(exc) from exc
    logger.warning("Role changed name=%s role=%s by=%s", row.name, payload.role, operator.name)
    return _listed(db, row, linked_providers(db))


@router.put("/{account_id}/storage-limit", summary="Set or remove the storage limit of an account")
def set_limit(account_id: int, payload: LimitIn, operator: OperatorAccount, db: DbSession) -> dict[str, Any]:
    row = _row(db, account_id)
    row.storage_limit = payload.limit
    db.commit()
    logger.info("Storage limit set name=%s limit=%s by=%s", row.name, payload.limit or "none", operator.name)
    return _listed(db, row, linked_providers(db))


@router.post("/{account_id}/reset-link", status_code=201,
             summary="A one-time link with which the person sets a new password themselves (shown once)")
def reset_link(
    account_id: int, payload: ResetLinkIn, request: Request, operator: OperatorAccount, db: DbSession,
) -> dict[str, Any]:
    confirm_operator(request, db, operator, payload.current_password)
    row = _row(db, account_id)
    try:
        made, token = accounts.create_reset(db, operator, row, days=payload.days)
    except AccountError as exc:
        raise fail(exc) from exc
    base = settings_service.public_url(db) or str(request.base_url).rstrip("/")
    return {"link": f"{base}/reset/{token}", "expires_at": made.expires_at.isoformat()}
