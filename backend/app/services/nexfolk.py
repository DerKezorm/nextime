"""nexfolk over a connection made by a click (plan "Apps verbinden"): the names of attendees from nexfolk's people
(V19, V29). The birthdays come as a calendar, the subscription the connection makes itself (V1 8a,
``services/subscriptions.py``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..vendor import nexverbinden
from . import verbinden_store

logger = logging.getLogger("nextime.nexfolk")

MAX_NAME = 200



def forget() -> None:
    """Nothing kept between requests (the birthdays come as a calendar since V1 8a, ``services/subscriptions.py``)."""


def _text(value: Any, size: int) -> str:
    return value.strip()[:size] if isinstance(value, str) else ""


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
