"""Operator settings in the database, with defaults."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Setting
from ..vendor.nexoidc import flow

#: Bounds of the operator's limits (TIME-1 AC-4, EC-11): whole numbers above 0, and not absurdly large.
UPLOAD_MAX_MB = (1, 10_000)
TRASH_DAYS = (1, 3650)

DEFAULTS: dict[str, Any] = {
    #: The version that ran last on this data: a different one at the start makes a backup first.
    "running_version": "",
    "log_mode": "normal",
    "log_mode_until": None,
    #: off | daily. Daily at 03:00 in the server's time zone, the last 14 kept (TIME-1 AC-2).
    "backup_schedule": "daily",
    "backup_time": "03:00",
    "backup_keep": 14,
    #: The last backup that failed, with its reason; gone with the next one that works (TIME-1 EC-7).
    "backup_failure": None,
    #: Largest single attachment in MB: 25 from the start (TIME-1 AC-4).
    "upload_max_mb": 25,
    #: How long deleted things stay in the trash, in days: 30 from the start (TIME-1 AC-4).
    "trash_days": 30,
    #: Members may make read links for guests without an account (TIME-1 AC-5, TIME-19).
    "share_links": True,
    #: Google calendars for every member, once the operator has set Google up (TIME-1 AC-5, TIME-16).
    "google_enabled": True,
    #: The Google Cloud client the operator set up (TIME-16); empty while Google is not set up.
    "google_client_id": "",
    #: Its secret, sealed with the server's key, and the redirect address registered with it (TIME-16 EC-13).
    "google_client_secret_enc": "",
    "google_redirect": "",
    #: Programs may ask under the programming interface (TIME-26), and MCP tools may (its own switch, TIME-26 AC-11).
    "api_enabled": True,
    "mcp_enabled": True,
    #: Typing a place asks a free OpenStreetMap service for addresses; only the typed text goes out (TIME-1 AC-10).
    "address_suggestions": True,
    #: A member's nextasks may stand in the operator's own network (LAN, VPN); off from the start, as nexnotes and
    #: nexmail have it (TIME-25): with it on, every member can have nextime ask addresses there.
    "connections_local_network": False,
    #: The address people use to reach nextime: invitation and share links, the OIDC redirect. Empty means
    #: ``NEXTIME_PUBLIC_URL``, and without that the request's own.
    "public_url": "",
    #: Members may sign in with a password. The operator always may: the emergency exit when OIDC fails.
    "password_login": True,
    #: Every account that signs in with a password needs a second factor; until it has one, it reaches only its
    #: account page. Accounts from OIDC bring their provider's.
    "two_factor_required": False,
    #: Ask GitHub once a day whether a newer nextime is out (services/updates). Only the question goes out, so on.
    "update_check": True,
    #: Invitation mail: without a host nothing is sent, the link to copy is enough.
    "smtp_host": "",
    "smtp_port": 587,
    #: starttls | tls | none
    "smtp_security": "starttls",
    "smtp_user": "",
    "smtp_password_enc": "",
    "smtp_from": "",
}


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    if row is None:
        return DEFAULTS.get(key)
    return row.value


def get_all(db: Session) -> dict[str, Any]:
    values = dict(DEFAULTS)
    for row in db.scalars(select(Setting)):
        values[row.key] = row.value
    return values


def save(db: Session, changes: dict[str, Any], *, commit: bool = True) -> None:
    for key, value in changes.items():
        if key not in DEFAULTS:
            raise KeyError(key)
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
    if commit:
        db.commit()
    else:
        db.flush()


def normalize_public_url(value: str) -> str:
    """``scheme://host[:port]`` and nothing else, or empty. ``ValueError`` for anything people could not type into
    a browser: no other schemes, no credentials, no path, no query, nothing the shared sign-in module refuses."""
    text = value.strip()
    if not text:
        return ""
    parts = urlsplit(text)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise ValueError("scheme or host")
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ("", "/"):
        raise ValueError("path, query or credentials")
    try:
        parts.port  # noqa: B018 - raises ValueError when the port is not a number
    except ValueError as exc:
        raise ValueError("port") from exc
    # Never more than the sign-in module takes (the host's letters, at most 199 characters): what it refuses is no
    # address here either, or the authentik card and the sign-in would disagree about it.
    return flow.clean_public_url(f"{parts.scheme.lower()}://{parts.netloc}")


def public_url(db: Session) -> str:
    """The public address without a trailing slash: the setting, otherwise the environment, otherwise empty."""
    stored = str(get(db, "public_url") or "").strip().rstrip("/")
    return stored or get_settings().public_url.strip().rstrip("/")


def upload_max_mb(db: Session) -> int:
    """The largest attachment in MB, as the operator set it; read at every upload, so a change counts at once and only
    for what comes after (TIME-1 EC-12)."""
    return max(1, int(get(db, "upload_max_mb") or 1))


def trash_days(db: Session) -> int:
    """How long something newly deleted stays in the trash; what lies there already keeps the date it got."""
    return max(1, int(get(db, "trash_days") or 30))


def google_configured(db: Session) -> bool:
    return bool(str(get(db, "google_client_id") or "").strip())


def google_on(db: Session) -> bool:
    """Google calendars are offered: set up, and the switch on (on from the start once set up)."""
    return google_configured(db) and bool(get(db, "google_enabled"))
