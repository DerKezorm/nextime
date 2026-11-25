"""Logging, from the shared module ``log`` (``app/vendor/nexlog``, bauplaene/log 1.0.0).

nextime writes its log to ``data/logs/nextime.log``. The file rolls over at a size limit, three older files are kept,
and files older than 14 days are deleted. Four levels (``quiet``, ``normal``, ``detailed``, ``trace``); the two deep
ones switch themselves off; ``NEXTIME_LOG_LEVEL`` overrides the stored level. All of it lives in the module now; this
file only tells it what is nextime's own and keeps the names every caller used (``from app.services import logs``).

Messages are English on purpose: log lines end up in bug reports and web searches. ``tests/test_log_language.py``
keeps that rule.

⚠️ What never goes into the log, at any level: the content of an event or an attachment (title, place, notes),
search terms, passwords, tokens, keys, session identifiers, access to iCloud or Google (TIME-1 AC-3). Paths, sizes,
counts, accounts and results do. ``tests/test_log_secrets.py`` scans the code for the obvious mistakes; the module's
blacking out is the net below that rule.
"""

from __future__ import annotations

from pathlib import Path

from .. import private
from ..config import get_settings
from ..vendor import nexlog

# The names of the module, as nextime's callers know them.
from ..vendor.nexlog import *

#: The keys nextime's log blacked out before the module (a calendar subscription's key travels in its address), and
#: the prefixes of nextime's own tokens (API keys, the app's devices): what follows them never reaches the log.
KEY_PREFIXES = ("nxa_", "nxc_", "nxl_", "nxr_", "nt_api_", "ntd_")
#: The read links of guests (``/abo/<token>``, ``/abo/<token>.ics``, ``/api/guest/<token>``, TIME-19): whoever reads
#: the log must not be able to read a calendar with them.
TOKEN_PATHS = ("abo", "guest")
#: The CalDAV library with its HTTP stack (addresses, bodies and Authorization headers of iCloud and Google calls,
#: TIME-15 EC-21): never finer than WARNING.
SILENT_LOGGERS = ("caldav", "niquests", "urllib3", "urllib3_future")


def configure(log_dir: Path | None = None) -> None:
    """What the module needs of nextime: its name, folder, environment variable and rules. Done once on import, so
    every caller (the start, the tests) finds it configured. ``log_dir``: another folder (the contract tests)."""
    nexlog.configure(
        nexlog.AppConfig(
            app_name="nextime",
            log_dir=log_dir if log_dir is not None else (lambda: get_settings().data_dir / "logs"),
            env_level=lambda: get_settings().log_level or "",
            key_prefixes=KEY_PREFIXES,
            token_paths=TOKEN_PATHS,
            silent_loggers=SILENT_LOGGERS,
            tighten=private.tighten,
        )
    )


configure()
