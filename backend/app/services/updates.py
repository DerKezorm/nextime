"""Whether a newer nextime is out: the shared module ``updates`` (``vendor/nexupdates``, bauplaene/updates 0.1.3).

The module asks GitHub at most once a day for the newest release, only while the operator's switch is on
(``update_check`` in the settings, as before), says nothing to members (U2, U15) and keeps nextime's routes
(``/api/about…``) and the names of their description (E53). nextime has no bell (``on_newer`` None). The operators'
iPhones and iPads hear of a newer version once (U24, G56): while a device of an operator wants ``update``
(``devices_want`` → ``geraete.update_wanted``), the background (``run_forever`` in ``main.py``, a look an hour) asks
at most once a day, and only while the switch is on; ``on_devices`` → ``geraete.newer`` tells them. Without such a
device the background asks nothing (U19); the question goes out when an operator opens "Über nextime" or the page
"Server" (``/api/server/state``, a thin wrapper, U16).

Nothing goes out but the question itself: no names, no calendars, no settings, no version of this server.
"""

from __future__ import annotations

import httpx  # noqa: F401  (tests put a stand-in in place of its client, as before)

from .. import __version__
from ..config import get_settings
from ..db import SessionLocal
from ..models import Setting
from ..vendor import nexupdates

# The names of the old services/updates.py (parse, is_newer, state, forget, State ...); not a star import, which would
# bring the module's own __version__ along.
from ..vendor.nexupdates import (
    INTERVAL,
    MAX_ANSWER,
    RETRY,
    State,
    check,
    forget,
    is_newer,
    look_once,
    parse,
    run_forever,
    state,
)

REPO = "DerKezorm/nextime"
REPO_URL = f"https://github.com/{REPO}"
RELEASES_URL = f"{REPO_URL}/releases"
#: The project page: empty while the project pages are switched off (U18), so the about page leaves the row out.
PROJECT_URL = ""
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
#: The setting the switch lives in, with its value from before (a JSON true or false).
SWITCH = "update_check"


def _read_switch() -> bool | None:
    """The switch as stored; None while nothing is stored (an installation never switched: on, as before, U9)."""
    with SessionLocal() as db:
        row = db.get(Setting, SWITCH)
        return None if row is None else bool(row.value)


def _write_switch(on: bool) -> None:
    from . import settings_service

    with SessionLocal() as db:
        settings_service.save(db, {SWITCH: bool(on)})


#: The version the operators' devices were last told about (``on_devices``), so nobody hears of one twice.
TOLD = "update_told"


def _read_told() -> str | None:
    with SessionLocal() as db:
        row = db.get(Setting, TOLD)
        return str(row.value) if row is not None and row.value else None


def _write_told(version: str) -> None:
    from . import settings_service

    with SessionLocal() as db:
        settings_service.save(db, {TOLD: version})


def _devices_want() -> bool:
    from . import geraete

    return geraete.update_wanted()


def _on_devices(version: str) -> None:
    from . import geraete

    geraete.newer(version)


def configure() -> nexupdates.AppConfig:
    """The module's settings for nextime (at import, and again in tests): no bell, the operators' devices (U24)."""
    return nexupdates.configure(nexupdates.AppConfig(
        app_name="nextime",
        version=lambda: __version__,
        repo=REPO,
        project_url=PROJECT_URL,
        update_url=lambda: get_settings().update_url,
        read_switch=_read_switch,
        write_switch=_write_switch,
        read_told=_read_told,
        write_told=_write_told,
        on_newer=None,
        devices_want=_devices_want,
        on_devices=_on_devices,
    ))


configure()

__all__ = [
    "API_URL", "INTERVAL", "MAX_ANSWER", "PROJECT_URL", "RELEASES_URL", "REPO", "REPO_URL", "RETRY", "State", "check",
    "configure", "forget", "is_newer", "look_once", "parse", "run_forever", "state",
]
