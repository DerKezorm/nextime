"""A note to an event in nexnotes over a connection made by a click (``services/nexnotes.py``, draft)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..deps import Account, DbSession
from ..services import nexnotes

router = APIRouter(tags=["nexnotes"])


@router.get("/api/nexnotes", summary="Whether nextime is connected with nexnotes (a connection made by a click)")
def state(account: Account, db: DbSession) -> dict[str, Any]:
    return {"connected": nexnotes.connected(db, account.id)}


@router.post("/api/events/{event_id}/nexnotes", summary="A note to this event in nexnotes' inbox")
def note(event_id: int, account: Account, db: DbSession,
         occurrence: str | None = Query(default=None, max_length=20)) -> dict[str, Any]:
    return nexnotes.note_for_event(db, account, event_id, occurrence)
