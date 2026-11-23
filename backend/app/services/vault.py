"""The access to iCloud, Google, other CalDAV servers and subscriptions, sealed (TIME-15 EC-21, TIME-16 EC-12).

Fernet with a key drawn from the server's secret (``NEXTIME_SECRET_KEY``, else ``secret.key`` in the data folder):
whoever has the database alone reads nothing. Each value is sealed with the place it belongs to (``source:7``), so a
value copied into another row does not open there. A backup restored next to another secret cannot open the values;
the sources then stand on "Access expired" with "Reconnect", their calendars and events stay (TIME-15 EC-22).
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings


def _fernet() -> Fernet:
    secret = get_settings().resolved_secret_key().encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"nextime-sources:" + secret).digest()))


def seal(value: dict[str, Any], place: str) -> str:
    return _fernet().encrypt(json.dumps({"place": place, "value": value}).encode("utf-8")).decode("ascii")


def open_(sealed: str, place: str) -> dict[str, Any] | None:
    """The value, or ``None`` when it cannot be opened here (another key, another row, nothing stored)."""
    if not sealed:
        return None
    try:
        content = json.loads(_fernet().decrypt(sealed.encode("ascii")))
    except (InvalidToken, ValueError, TypeError):
        return None
    if not isinstance(content, dict) or content.get("place") != place or not isinstance(content.get("value"), dict):
        return None
    return content["value"]


def identity(*parts: str) -> str:
    """A hash that tells the same access apart from another (TIME-15 EC-14) without keeping a subscription link with
    its secret readable; the same after a restore next to another key."""
    joined = "\x00".join(part.strip().casefold() for part in parts).encode("utf-8")
    return hashlib.sha256(b"nextime-source-identity:" + joined).hexdigest()
