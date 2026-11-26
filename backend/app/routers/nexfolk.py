"""nexfolk over a connection made by a click (``services/nexfolk.py``): names of attendees (V29)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..deps import Account, DbSession
from ..services import nexfolk

router = APIRouter(tags=["nexfolk"])


@router.get("/api/people/names", summary="Names nexfolk knows for attendees' addresses (a connection, people only)")
def names(account: Account, db: DbSession,
          addresses: str = Query(default="", max_length=7000)) -> dict[str, Any]:
    return nexfolk.names_for(db, account.id, [one.strip() for one in addresses.split(",") if one.strip()])
