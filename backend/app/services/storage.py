"""The data folder: is it there and writable, how full is it, where does it lie.

* At the start ``check_data_dir`` makes sure nextime can write there. A folder that was named on purpose
  (``NEXTIME_DATA_DIR``) but is missing, or one nextime may not write to, stops the start with a clear line in the log
  instead of a server that runs halfway and fails at the first event.
* ``usage`` is the used and free space of the disk under the data folder, for the server settings; ``footprint`` is
  what nextime itself takes there (database, attachments, backups, exports, the rest), measured at most once a minute.
* ``FULL_CODES`` are the errors a full disk shows: the request answers ``storage_full`` and nothing existing is touched
  (SQLite rolls the transaction back, a backup in progress is thrown away).
"""

from __future__ import annotations

import errno
import logging
import os
import secrets
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import StartupError

logger = logging.getLogger("nextime.storage")

FULL_CODES = frozenset({errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)})


def check_data_dir(path: Path, *, named: bool) -> None:
    """The start goes on only when nextime can write into ``path``. ``named``: the operator set the folder; a missing
    one is a mistake (a volume not mounted), not something to create silently."""
    if not path.exists():
        if named:
            logger.critical("The data folder %s does not exist. Mount a folder there or set NEXTIME_DATA_DIR.", path)
            raise StartupError(3)
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            logger.critical("The data folder %s cannot be created: %s", path, error.strerror or error)
            raise StartupError(3) from error
    if not path.is_dir():
        logger.critical("The data folder %s is not a folder.", path)
        raise StartupError(3)
    probe = path / f".write-check-{secrets.token_hex(4)}"
    try:
        with open(probe, "wb") as handle:
            handle.write(b"ok")
    except OSError as error:
        logger.critical(
            "nextime may not write to the data folder %s (%s). Give the user with PUID/PGID write access.",
            path, error.strerror or error,
        )
        raise StartupError(3) from error
    finally:
        try:
            probe.unlink()
        except OSError:
            pass


@dataclass(frozen=True)
class Usage:
    total: int
    used: int
    free: int


def usage(path: Path) -> Usage:
    found = shutil.disk_usage(path)
    return Usage(total=found.total, used=found.used, free=found.free)


@dataclass(frozen=True)
class Footprint:
    """What nextime itself takes in its data folder, in bytes, by part."""

    database: int = 0
    attachments: int = 0
    backups: int = 0
    exports: int = 0
    #: Log, caches of the text recognition, own language files, the server's key.
    other: int = 0

    @property
    def total(self) -> int:
        return self.database + self.attachments + self.backups + self.exports + self.other


#: How long a measured footprint is good for: walking a folder of many thousand attachments costs, the page does not
#: need it to the byte, and a backup made or deleted here drops it at once (``forget_footprint``).
FOOTPRINT_SECONDS = 60.0
_DATABASE_NAMES = ("nextime.db", "nextime.db-wal", "nextime.db-shm", "nextime.db-journal")
_PARTS = {"attachments": "attachments", "backups": "backups", "exports": "exports"}
_footprints: dict[Path, tuple[float, Footprint]] = {}
_footprint_lock = threading.Lock()


def _size_of(path: str) -> int:
    """The bytes of a file, or of everything below a folder; links are not followed, what vanishes meanwhile counts
    as nothing."""
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
        except NotADirectoryError:
            try:
                total += os.stat(current, follow_symlinks=False).st_size
            except OSError:
                pass
        except OSError:
            continue
    return total


def _measure(path: Path) -> Footprint:
    parts = {"database": 0, "attachments": 0, "backups": 0, "exports": 0, "other": 0}
    try:
        with os.scandir(path) as entries:
            top = [(entry.name, entry.path) for entry in entries if not entry.is_symlink()]
    except OSError:
        return Footprint()
    for name, full in top:
        if name in _DATABASE_NAMES:
            part = "database"
        else:
            part = _PARTS.get(name, "other")
        parts[part] += _size_of(full)
    return Footprint(**parts)


def footprint(path: Path) -> Footprint:
    """What nextime takes in ``path``, measured at most once a minute."""
    key = path.resolve()
    with _footprint_lock:
        found = _footprints.get(key)
        if found is not None and time.monotonic() - found[0] < FOOTPRINT_SECONDS:
            return found[1]
        measured = _measure(key)
        _footprints[key] = (time.monotonic(), measured)
        return measured


def forget_footprint() -> None:
    """The next ``footprint`` measures again (a backup was made or deleted; the tests)."""
    with _footprint_lock:
        _footprints.clear()


def disk_full(error: BaseException) -> bool:
    """A full disk or quota, however deep in the chain of causes: SQLite says "database or disk is full"."""
    seen: BaseException | None = error
    while seen is not None:
        if isinstance(seen, OSError) and seen.errno in FULL_CODES:
            return True
        if isinstance(seen, sqlite3.OperationalError) and "full" in str(seen):
            return True
        seen = getattr(seen, "orig", None) or seen.__cause__ or seen.__context__
    return False


def named_data_dir() -> bool:
    return bool(os.environ.get("NEXTIME_DATA_DIR", "").strip())
