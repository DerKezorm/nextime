"""Birthdays from nexfolk (plan "Apps verbinden", V1 8a, V18/V19; draft of the smallest form, questions file): over a
connection made by a click (the shared module verbinden, purpose ``birthdays.read``), the birthdays and anniversaries
of the next days stand in "Heute" under the events, as the deadlines of nexpaper do. Nothing is stored; one answer of
nexfolk serves a minute. Without a connection there is nothing (no card, no block, V20).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..vendor import nexverbinden
from . import verbinden_store

logger = logging.getLogger("nextime.nexfolk")

#: The days ahead "Heute" shows.
DAYS = 14
#: How long one answer of nexfolk serves.
CACHE_SECONDS = 60
MAX_NAME = 200

_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_lock = threading.Lock()


def forget() -> None:
    with _lock:
        _cache.clear()


def _text(value: Any, size: int) -> str:
    return value.strip()[:size] if isinstance(value, str) else ""


def _occasion(item: Any) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    day, name = _text(item.get("date"), 10), _text(item.get("name"), MAX_NAME)
    try:
        date.fromisoformat(day)
    except ValueError:
        return None
    if not name:
        return None
    years = item.get("years")
    return {"day": day, "name": name, "kind": "anniversary" if item.get("kind") == "anniversary" else "birthday",
            "label": _text(item.get("label"), 100),
            "years": years if isinstance(years, int) and not isinstance(years, bool) and 0 <= years < 200 else None}


def birthdays(db: Session, account_id: int) -> dict[str, Any]:
    """``{"connected", "birthdays", "problem"}``: the occasions of the next ``DAYS`` days, soonest first."""
    store = verbinden_store.Store(db)
    if store.connection(account_id, "nexfolk") is None:
        return {"connected": False, "birthdays": [], "problem": None}
    with _lock:
        kept = _cache.get(account_id)
        if kept is not None and time.monotonic() - kept[0] < CACHE_SECONDS:
            return kept[1]
    try:
        answer = nexverbinden.call(store, verbinden_store.CONFIG, account_id, "nexfolk", "GET", "/api/v1/birthdays",
                                   datetime.now(UTC), params={"days": str(DAYS)})
    except nexverbinden.Refused as refusal:
        logger.info("nexfolk did not answer the birthdays account_id=%s reason=%s", account_id, refusal.code)
        return {"connected": True, "birthdays": [], "problem": f"nexfolk_{refusal.code}"}
    if answer.status != 200 or not isinstance(answer.data, dict):
        logger.info("nexfolk refused the birthdays account_id=%s status=%s", account_id, answer.status)
        return {"connected": True, "birthdays": [], "problem": "nexfolk_refused"}
    found = [one for item in answer.data.get("occasions") or [] if (one := _occasion(item)) is not None]
    result = {"connected": True, "birthdays": sorted(found, key=lambda one: (one["day"], one["name"])),
              "problem": None}
    with _lock:
        _cache[account_id] = (time.monotonic(), result)
    return result


# --- Attendees from the contacts (plan V2/V19, V29: nexfolk's ``/api/v1/people``, only id, name and addresses) ------

#: Addresses one question names at most.
MAX_ADDRESSES = 20


def names_for(db: Session, account_id: int, addresses: list[str]) -> dict[str, Any]:
    """``{"connected", "names"}``: for attendees shown only by their address, the name nexfolk knows for it. One search
    per address in nexfolk's ``/api/v1/people`` (V29: id, name and mail addresses, only the address books given);
    nothing else of a contact reaches nextime, nothing is stored."""
    store = verbinden_store.Store(db)
    if store.connection(account_id, "nexfolk") is None:
        return {"connected": False, "names": {}}
    wanted = []
    for raw in addresses[:MAX_ADDRESSES]:
        address = _text(raw, 320).casefold()
        if "@" in address and address not in wanted:
            wanted.append(address)
    names: dict[str, str] = {}
    for address in wanted:
        try:
            answer = nexverbinden.call(store, verbinden_store.CONFIG, account_id, "nexfolk", "GET", "/api/v1/people",
                                       datetime.now(UTC), params={"q": address, "limit": "5"})
        except nexverbinden.Refused as refusal:
            logger.info("nexfolk did not answer the people account_id=%s reason=%s", account_id, refusal.code)
            break
        people = answer.data.get("people") if answer.status == 200 and isinstance(answer.data, dict) else None
        for person in people or []:
            if not isinstance(person, dict):
                continue
            emails = [_text(email, 320).casefold() for email in person.get("emails") or []]
            name = _text(person.get("name"), MAX_NAME)
            if address in emails and name:
                names[address] = name
                break
    return {"connected": True, "names": names}
