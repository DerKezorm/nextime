"""A note to an event in nexnotes (plan "Apps verbinden" V2/V19, pair nextime → nexnotes, purpose ``notes.add``; draft
of the smallest form, questions file): over a connection made by a click, "Notiz in nexnotes" in an event's panel
makes a note in nexnotes' inbox with the event's title, its time, place and notes, through nexnotes' own
``POST /api/v1/notes``. The same event (or occurrence) again gives the same note there (``external_ref``). Nothing is
stored here; without a connection there is no button (V20).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..errors import error
from ..models import Account
from ..vendor import nexverbinden
from . import events, verbinden_store

logger = logging.getLogger("nextime.nexnotes")


def connected(db: Session, account_id: int) -> bool:
    return verbinden_store.has_connection(db, account_id, "nexnotes")


def _when(found: dict[str, Any]) -> str:
    start, end = str(found.get("start") or ""), str(found.get("end") or "")
    if found.get("all_day"):
        return start if not end or end == start else f"{start} – {end}"
    zone = str(found.get("time_zone") or "")
    shown = f"{start.replace('T', ' ')} – {end.replace('T', ' ')}" if end else start.replace("T", " ")
    return f"{shown} ({zone})" if zone else shown


def markdown(found: dict[str, Any]) -> str:
    """The note's text: when, where, and the event's notes, as Markdown."""
    lines = [f"**{_when(found)}**"]
    if found.get("location"):
        lines.append(str(found["location"]))
    if found.get("notes"):
        lines += ["", str(found["notes"])]
    return "\n".join(lines)


def note_for_event(db: Session, account: Account, event_id: int, occurrence: str | None) -> dict[str, Any]:
    """Makes the note (or finds the one made before for the same event) and answers ``{"made", "title"}``."""
    if not connected(db, account.id):
        raise error("nexnotes_not_connected", "nextime is not connected with nexnotes.", 409)
    found = events.detail(db, account, event_id, occurrence)
    title = str(found.get("title") or "").strip()[:120]
    if not title or found.get("busy_only"):
        raise error("not_found", "Not found.", 404)
    body = {"title": title, "markdown": markdown(found),
            "external_ref": f"nextime:{account.id}:{event_id}:{occurrence or ''}"[:200]}
    try:
        answer = nexverbinden.call(verbinden_store.Store(db), verbinden_store.CONFIG, account.id, "nexnotes", "POST",
                                   "/api/v1/notes", datetime.now(UTC), body=body)
    except nexverbinden.Refused as refusal:
        logger.info("nexnotes did not take a note account_id=%s reason=%s", account.id, refusal.code)
        raise error("nexnotes_refused", "The note did not reach nexnotes.", 502) from None
    if answer.status not in (200, 201):
        logger.info("nexnotes refused a note account_id=%s status=%s", account.id, answer.status)
        raise error("nexnotes_refused", "The note did not reach nexnotes.", 502)
    logger.info("Note made in nexnotes account_id=%s event_id=%s new=%s", account.id, event_id, answer.status == 201)
    return {"made": answer.status == 201, "title": title}
