"""Birthdays from nexfolk over a connection made by a click (``services/nexfolk.py``): for "Heute"."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..deps import Account, DbSession
from ..services import nexfolk

router = APIRouter(tags=["nexfolk"])


@router.get("/api/birthdays", summary="Birthdays and anniversaries of the next days from nexfolk (a connection)")
def birthdays(account: Account, db: DbSession) -> dict[str, Any]:
    return nexfolk.birthdays(db, account.id)
