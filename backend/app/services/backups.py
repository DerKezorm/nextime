"""Backups of the whole of nextime: the database (calendars, events, accounts, shares, settings, the encrypted access
to iCloud and Google) and the attachments, in one ZIP.

How it works:

* **The database is copied with SQLite's backup API**, never as a file: it runs in WAL mode, and a file copy misses
  what still sits in the ``-wal`` side file. The attachments are copied after; they never change once written, so
  one added in between is at worst an extra file nobody points at.
* **The manifest lists every file with its size and sha256**, so the check before a restore can tell a damaged
  archive from a good one, and say what a restore would change.
* **Only automatic copies are pruned** (``scheduled`` and ``update``); one made by hand stays until deleted, and so
  does one brought in from elsewhere (``receive``, named ``…-upload.zip``), whatever kind its manifest names.
* **A backup of a newer nextime is refused** before anything changes; one of an older version is restored as it
  is and brought up to date by the start that follows (new columns, ``db._add_missing_columns``).
* **A restore happens at the next start.** ``stage_restore`` checks the archive, makes an ``update`` copy of the
  current state (the way back), unpacks into ``backups/restore-pending/`` and ends the process; Docker starts it
  again and ``apply_pending`` swaps the files before anything opens the database. A pending folder without its
  manifest is a half-written one and is thrown away, never applied.

The archive is not encrypted: it lies in the data folder next to the database it copies. Downloading it asks for
the operator's password once more.
"""

from __future__ import annotations

import asyncio
import errno
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import sqlite3
import threading
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, tzinfo
from pathlib import Path
from typing import Any

from .. import __version__, private
from ..config import get_settings, server_zone
from . import settings_service, storage

logger = logging.getLogger("nextime.backups")

FOLDER_NAME = "backups"
MANIFEST = "nextime-backup.json"
DATABASE_ENTRY = "database/nextime.db"
MEDIA_PREFIX = "attachments/"
#: The key the server encrypts its own secrets with (OIDC client secret, mail password): without it a restore on
#: another machine would bring those back unreadable. Whoever holds a backup holds the database anyway.
SECRET_ENTRY = "secret.key"
#: Attachment files are named by their id, with an optional suffix for a derived copy (a preview); nothing else that
#: may lie in the folder is backed up.
MEDIA_NAME = re.compile(r"^[A-Za-z0-9_-]{8,64}(\.[a-z0-9]{1,8})?$")
#: The folder of the attachments below the data folder.
ATTACHMENTS = "attachments"
PENDING = "restore-pending"
MANUAL = "manual"
SCHEDULED = "scheduled"
UPDATE = "update"
AUTOMATIC_KINDS = (SCHEDULED, UPDATE)
SCHEDULES = ("off", "daily")
INTERVALS = {"daily": 1}
#: When the daily copy is made, in the server's time zone (TIME-1 AC-2), and how many automatic copies stay.
DEFAULT_TIME = "03:00"
DEFAULT_KEEP = 14
TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
#: How often the schedule looks whether a copy is due.
INTERVAL_SECONDS = 60
NAME = re.compile(r"^nextime-\d{4}-\d{2}-\d{2}-\d{6}(-\d+)?(-upload)?\.zip$")
#: The end of the name of an archive brought in from elsewhere: listed as uploaded and never pruned.
UPLOADED = "-upload.zip"
SQLITE_HEADER = b"SQLite format 3\x00"
_CHUNK = 1024 * 1024
_lock = threading.Lock()


class BackupError(Exception):
    def __init__(self, code: str, text: str) -> None:
        super().__init__(text)
        self.code = code
        self.text = text


@dataclass
class Manifest:
    version: str
    created: str
    kind: str
    note: str = ""
    calendars: int = 0
    events: int = 0
    files: int = 0
    bytes: int = 0
    #: name in the media folder -> [size, sha256]
    media: dict[str, list[Any]] = field(default_factory=dict)


@dataclass
class Entry:
    name: str
    size: int
    created: str
    kind: str
    note: str
    events: int
    files: int
    version: str
    uploaded: bool = False


@dataclass
class Brief:
    """What a restore of an archive would do: its data, and how the media folder would change."""

    name: str
    version: str
    created: str
    kind: str
    events: int
    files: int
    database_ok: bool
    files_ok: bool
    damaged: list[str]
    would_add: int
    would_change: int
    would_remove: int
    #: Events a restore brings back (in the archive, not here or in the trash), changes (another ``updated_at``) and
    #: takes away (made since), and the same for accounts: numbers only, never a title (the operator never sees
    #: calendars or events of others, TIME-2 AC-3).
    events_add: int = 0
    events_change: int = 0
    events_remove: int = 0
    accounts_add: int = 0
    accounts_remove: int = 0

    @property
    def usable(self) -> bool:
        return self.database_ok and self.files_ok


def folder() -> Path:
    return get_settings().data_dir / FOLDER_NAME


def pending_folder() -> Path:
    return folder() / PENDING


def media_root() -> Path:
    return get_settings().data_dir / ATTACHMENTS


def version_tuple(text: str) -> tuple[int, ...]:
    """``1.2.10`` as (1, 2, 10); anything that is not a number counts as 0."""
    parts = []
    for part in re.split(r"[.+-]", text)[:3]:
        parts.append(int(part) if part.isdigit() else 0)
    return tuple(parts + [0] * (3 - len(parts)))


def newer_than_running(version: str) -> bool:
    return version_tuple(version) > version_tuple(__version__)


def path_of(name: str) -> Path:
    if not NAME.match(name):
        raise BackupError("not_found", "no such backup")
    path = folder() / name
    if not path.is_file():
        raise BackupError("not_found", "no such backup")
    return path


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%d-%H%M%S")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _media_files(root: Path) -> list[tuple[str, Path]]:
    """Every media file, by name; nothing else that may lie there (temporary files of an upload, links)."""
    if not root.is_dir():
        return []
    return sorted(
        (entry.name, entry) for entry in root.iterdir()
        if MEDIA_NAME.match(entry.name) and entry.is_file() and not entry.is_symlink()
    )


def _count(connection: sqlite3.Connection, table: str) -> int:
    """Rows of a table that are not in the trash; 0 while the table does not exist yet."""
    try:
        columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        if not columns:
            return 0
        alive = " WHERE deleted_at IS NULL" if "deleted_at" in columns else ""
        return int(connection.execute(f'SELECT count(*) FROM "{table}"{alive}').fetchone()[0])  # noqa: S608
    except sqlite3.DatabaseError:
        return 0


def _database_copy(target: Path) -> tuple[int, int]:
    """The database through SQLite's backup API. Returns how many calendars and events it holds (not in the
    trash)."""
    source = sqlite3.connect(get_settings().database_path)
    destination = sqlite3.connect(target)
    try:
        source.backup(destination)
        destination.execute("PRAGMA journal_mode=DELETE")
        destination.commit()
        return _count(destination, CALENDARS_TABLE), _count(destination, EVENTS_TABLE)
    finally:
        destination.close()
        source.close()


def create(*, kind: str = MANUAL, note: str = "") -> Path:
    """A new backup archive. Returns its path."""
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    private.tighten(base)
    moment = datetime.now(UTC)
    name = f"nextime-{_stamp(moment)}.zip"
    number = 2
    while (base / name).exists():
        name = f"nextime-{_stamp(moment)}-{number}.zip"
        number += 1
    partial = base / f".{name}.part"
    database = base / f".{name}.db"
    try:
        # Both only the owner's before a byte is in them: the archive holds the key and everything else.
        private.new_file(database)
        private.new_file(partial)
        calendars, events = _database_copy(database)
        manifest = Manifest(version=__version__, created=moment.isoformat(timespec="seconds"), kind=kind, note=note,
                            calendars=calendars, events=events)
        with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(database, DATABASE_ENTRY)
            key = get_settings().data_dir / "secret.key"
            if key.is_file():
                archive.write(key, SECRET_ENTRY)
            for rel, full in _media_files(media_root()):
                try:
                    size = full.stat().st_size
                    # Photos and PDFs are packed already; deflating them again only costs time.
                    archive.write(full, MEDIA_PREFIX + rel, compress_type=zipfile.ZIP_STORED)
                    manifest.media[rel] = [size, _hash_file(full)]
                except OSError as exc:
                    logger.warning("A file could not be backed up and was left out: %s", exc.strerror)
                    continue
                manifest.files += 1
                manifest.bytes += size
            # Last: an archive without its manifest is recognisably incomplete.
            archive.writestr(MANIFEST, json.dumps(asdict(manifest), ensure_ascii=False))
        os.replace(partial, base / name)
    finally:
        partial.unlink(missing_ok=True)
        database.unlink(missing_ok=True)
        # The page shows what nextime takes; a new archive should be in it at once, not a minute later.
        storage.forget_footprint()
    logger.info("Backup made kind=%s events=%s files=%s bytes=%s", kind, manifest.events, manifest.files,
                manifest.bytes)
    return base / name


def _manifest(archive: zipfile.ZipFile) -> Manifest:
    try:
        raw = json.loads(archive.read(MANIFEST).decode("utf-8"))
        known = {key: value for key, value in raw.items() if key in Manifest.__dataclass_fields__}
        return Manifest(**known)
    except (KeyError, ValueError, TypeError) as exc:
        raise BackupError("backup_invalid", "the archive has no readable manifest") from exc


def entries() -> list[Entry]:
    base = folder()
    if not base.is_dir():
        return []
    found = []
    for path in base.iterdir():
        if not NAME.match(path.name):
            continue
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = _manifest(archive)
        except (zipfile.BadZipFile, BackupError, OSError):
            continue
        found.append(
            Entry(path.name, path.stat().st_size, manifest.created, manifest.kind, manifest.note, manifest.events,
                  manifest.files, manifest.version, path.name.endswith(UPLOADED))
        )
    return sorted(found, key=lambda entry: entry.created, reverse=True)


def remove(name: str) -> None:
    path_of(name).unlink()
    storage.forget_footprint()
    logger.info("Backup deleted")


def prune(keep: int) -> int:
    automatic = [entry for entry in entries() if entry.kind in AUTOMATIC_KINDS and not entry.uploaded]
    removed = 0
    for entry in automatic[max(keep, 1) :]:
        (folder() / entry.name).unlink(missing_ok=True)
        removed += 1
    if removed:
        storage.forget_footprint()
        logger.info("Old automatic backups removed count=%s", removed)
    return removed


def temporary_upload() -> Path:
    """Where an upload lands while it arrives: in the backups folder, only the owner's, under a name no listing
    takes for a backup."""
    base = folder()
    base.mkdir(parents=True, exist_ok=True)
    private.tighten(base)
    path = base / f".upload-{os.getpid()}-{time.time_ns()}.part"
    private.new_file(path)
    return path


def receive(received: Path) -> str:
    """An archive brought in from elsewhere, for a move to a new server: if it is one of ours (a ZIP with a manifest
    and a database), it joins the list under a name of its own and waits there to be checked and restored like any
    other. Nothing is unpacked here; the full check is the trial run before a restore."""
    try:
        with zipfile.ZipFile(received) as archive:
            manifest = _manifest(archive)
            if DATABASE_ENTRY not in archive.namelist():
                raise BackupError("backup_invalid", "the archive holds no database")
    except zipfile.BadZipFile as exc:
        raise BackupError("backup_invalid", "not a ZIP archive") from exc
    try:
        moment = datetime.fromisoformat(manifest.created)
    except (TypeError, ValueError) as exc:
        raise BackupError("backup_invalid", "the archive has no readable date") from exc
    base = folder()
    stamp = _stamp(moment)
    name = f"nextime-{stamp}{UPLOADED}"
    number = 2
    while (base / name).exists():
        name = f"nextime-{stamp}-{number}{UPLOADED}"
        number += 1
    os.replace(received, base / name)
    storage.forget_footprint()
    logger.info("Backup received name=%s version=%s events=%s files=%s", name, manifest.version, manifest.events,
                manifest.files)
    return name


# --- Checking and restoring -----------------------------------------------------------------------------------------


def _safe_member(name: str) -> str | None:
    """The media name of an archive member, or None when it is not a plain media file."""
    if not name.startswith(MEDIA_PREFIX):
        return None
    rel = name[len(MEDIA_PREFIX) :]
    return rel if MEDIA_NAME.match(rel) else None


#: The tables the check compares by id and last change (TIME-1 AC-8: what comes, changes and goes).
CALENDARS_TABLE = "calendars"
EVENTS_TABLE = "events"
#: A row as both databases store it: last change (as stored text), in the trash.
_Row = tuple[str, bool]


def _rows_in(connection: sqlite3.Connection, table: str) -> dict[str, _Row]:
    """The rows of a table in a database file, read with plain SQL: an archive from an older version may lack the
    table or the columns; then it has none, or none in the trash."""
    columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
    if "id" not in columns:
        return {}
    changed = "updated_at" if "updated_at" in columns else "''"
    trashed = "deleted_at IS NOT NULL" if "deleted_at" in columns else "0"
    return {
        str(row[0]): (str(row[1] or ""), bool(row[2]))
        for row in connection.execute(f'SELECT id, {changed}, {trashed} FROM "{table}"')  # noqa: S608
    }


def _contents(connection: sqlite3.Connection) -> dict[str, dict[str, _Row]]:
    return {table: _rows_in(connection, table) for table in (EVENTS_TABLE, "accounts")}


def _live_contents() -> dict[str, dict[str, _Row]]:
    """The rows of the running database, through the app's own connection (never a second one from outside)."""
    from ..db import engine

    raw = engine.raw_connection()
    try:
        return _contents(raw.driver_connection)  # type: ignore[arg-type]
    finally:
        raw.close()


def _compare(archived: dict[str, dict[str, _Row]], current: dict[str, dict[str, _Row]]) -> dict[str, int]:
    """Alive in the archive but not here: comes back. Alive here but not in the archive: goes. Alive in both with
    another last change: changes. Numbers only."""
    found: dict[str, int] = {}
    for table, prefix in ((EVENTS_TABLE, "events"), ("accounts", "accounts")):
        then = {key: row for key, row in archived.get(table, {}).items() if not row[1]}
        now = {key: row for key, row in current.get(table, {}).items() if not row[1]}
        found[f"{prefix}_add"] = sum(1 for key in then if key not in now)
        found[f"{prefix}_remove"] = sum(1 for key in now if key not in then)
        if prefix == "events":
            found["events_change"] = sum(1 for key, row in now.items() if key in then and then[key][0] != row[0])
    return found


#: How often the check reads the archive's database when the system does not let it for a moment, and the pause between.
DATABASE_TRIES = 3
DATABASE_PAUSE_SECONDS = 0.25


def _archived_database(archive: zipfile.ZipFile) -> tuple[bool, dict[str, dict[str, _Row]]]:
    """Whether the archive's database is whole, and its rows, read from a copy beside the backups.

    What SQLite reads and finds broken (not a database, a malformed page, a failed integrity check) is damaged at
    once. A file the system does not let SQLite open or read for a moment (a virus scanner still holding the fresh
    copy, a busy disk: ``disk I/O error``, ``database is locked``, a sharing violation) is not damage: it is read again
    from a new copy; only when that keeps failing does the check say "damaged". Each time the reason goes to the log,
    so a refused restore says why."""
    problem: BaseException | None = None
    for attempt in range(DATABASE_TRIES):
        if attempt:
            time.sleep(DATABASE_PAUSE_SECONDS * attempt)
        scratch = folder() / f".check-{os.getpid()}-{time.time_ns()}.db"
        try:
            with archive.open(DATABASE_ENTRY) as source, open(scratch, "wb") as sink:
                shutil.copyfileobj(source, sink, _CHUNK)
            if scratch.read_bytes()[:16] != SQLITE_HEADER:
                logger.warning("The database in the backup is not an SQLite file")
                return False, {}
            connection = sqlite3.connect(scratch)
            try:
                found = [row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()]
                if found != ["ok"]:
                    logger.warning("The database in the backup is damaged: %s", found[0])
                    return False, {}
                return True, _contents(connection)
            finally:
                connection.close()
        except (sqlite3.OperationalError, OSError) as exc:
            problem = exc
            logger.info("The database in the backup could not be read yet, try %s: %s", attempt + 1, exc)
        except (sqlite3.DatabaseError, zipfile.BadZipFile) as exc:
            logger.warning("The database in the backup is damaged: %s", exc)
            return False, {}
        finally:
            try:
                scratch.unlink(missing_ok=True)
            except OSError:
                # Still held: a hidden name nobody lists; the next check's copy has a name of its own.
                pass
    logger.warning("The database in the backup could not be read: %s", problem)
    return False, {}


def check(name: str) -> Brief:
    """The trial run: is the archive whole, and what would a restore change, in events, accounts and the media
    folder?"""
    path = path_of(name)
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise BackupError("backup_invalid", "not a ZIP archive") from exc
    with archive:
        manifest = _manifest(archive)
        if newer_than_running(manifest.version):
            raise BackupError("backup_too_new",
                              f"the archive comes from nextime {manifest.version}, newer than this one")
        members = {info.filename: info for info in archive.infolist()}
        database_ok = False
        archived: dict[str, dict[str, _Row]] = {}
        if DATABASE_ENTRY in members:
            database_ok, archived = _archived_database(archive)
        damaged: list[str] = []
        for rel, (size, digest) in manifest.media.items():
            member = members.get(MEDIA_PREFIX + rel)
            if member is None or _safe_member(member.filename) != rel or member.file_size != size:
                damaged.append(rel)
                continue
            hasher = hashlib.sha256()
            with archive.open(member) as handle:
                while chunk := handle.read(_CHUNK):
                    hasher.update(chunk)
            if hasher.hexdigest() != digest:
                damaged.append(rel)
        damaged.extend(
            info.filename for info in archive.infolist()
            if not info.is_dir() and info.filename not in (MANIFEST, DATABASE_ENTRY, SECRET_ENTRY)
            and _safe_member(info.filename) not in manifest.media
        )
    current = dict(_media_files(media_root()))
    add = [rel for rel in manifest.media if rel not in current]
    remove_ = [rel for rel in current if rel not in manifest.media]
    change = [
        rel for rel, full in current.items()
        if rel in manifest.media
        and (full.stat().st_size != manifest.media[rel][0] or _hash_file(full) != manifest.media[rel][1])
    ]
    return Brief(
        name=name, version=manifest.version, created=manifest.created, kind=manifest.kind, events=manifest.events,
        files=manifest.files, database_ok=database_ok, files_ok=not damaged, damaged=sorted(damaged)[:20],
        would_add=len(add), would_change=len(change), would_remove=len(remove_),
        **_compare(archived, _live_contents()),
    )


def stage_restore(name: str) -> Brief:
    """Check, keep the current state as a backup, and lay the archive out for the next start."""
    with _lock:
        brief = check(name)
        if not brief.usable:
            raise BackupError("backup_damaged", "the archive is damaged; nothing was changed")
        create(kind=UPDATE, note=f"before restoring {name}")
        pending = pending_folder()
        shutil.rmtree(pending, ignore_errors=True)
        pending.mkdir(parents=True)
        try:
            with zipfile.ZipFile(path_of(name)) as archive:
                manifest = _manifest(archive)
                with archive.open(DATABASE_ENTRY) as source, open(pending / "nextime.db", "wb") as sink:
                    shutil.copyfileobj(source, sink, _CHUNK)
                if SECRET_ENTRY in archive.namelist():
                    (pending / "secret.key").write_bytes(archive.read(SECRET_ENTRY))
                (pending / ATTACHMENTS).mkdir()
                for rel in manifest.media:
                    with archive.open(MEDIA_PREFIX + rel) as source, open(pending / ATTACHMENTS / rel, "wb") as sink:
                        shutil.copyfileobj(source, sink, _CHUNK)
                # Last: the manifest says the pending folder is complete.
                (pending / MANIFEST).write_bytes(json.dumps(asdict(manifest), ensure_ascii=False).encode())
        except BaseException:
            shutil.rmtree(pending, ignore_errors=True)
            raise
    logger.info("Backup staged for the next start created=%s", brief.created)
    return brief


def _end_restored_sessions(database: Path) -> None:
    """Sign-ins stored in the backup would come back to life, even ones ended since: everybody signs in anew."""
    connection = sqlite3.connect(database)
    try:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_sessions'").fetchone():
            connection.execute("DELETE FROM auth_sessions")
            connection.commit()
    finally:
        connection.close()


def _new_sync_mark(database: Path) -> None:
    """Devices with the calendar account hold sync tokens and collection tags of the database before; a new mark
    makes every one of them invalid, so each device syncs anew instead of skipping what the restore changed
    (TIME-20 EC-20)."""
    connection = sqlite3.connect(database)
    try:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
            connection.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('sync_epoch', ?)",
                               (json.dumps(secrets.token_hex(4)),))
            connection.commit()
    finally:
        connection.close()


def restart_soon(delay: float = 1.5) -> None:
    """End the process shortly, after the answer went out; Docker starts it again (``restart: unless-stopped``)."""

    def stop() -> None:
        time.sleep(delay)
        logger.info("nextime stops to restore a backup")
        if os.name == "nt":
            os._exit(3)
        try:
            os.kill(os.getpid(), signal.SIGTERM)
        except OSError:
            os._exit(0)
        time.sleep(10)
        os._exit(0)

    threading.Thread(target=stop, name="restart-for-restore", daemon=True).start()


def _remove_tree(folder_path: Path) -> None:
    """A folder and all in it, read-only files too (Windows refuses those to ``rmtree``); a few tries, because a
    virus scanner may still hold a file for a moment. What stays is tried again at the next start."""

    def writable_then_again(function: Any, path: str, _error: BaseException) -> None:
        try:
            os.chmod(path, 0o700)
            function(path)
        except OSError:
            pass

    for attempt in range(5):
        if not folder_path.exists():
            return
        shutil.rmtree(folder_path, onexc=writable_then_again)
        if not folder_path.exists():
            return
        time.sleep(0.2 * (attempt + 1))
    logger.warning("A folder set aside by a restore could not be removed yet name=%s", folder_path.name)


def _step(name: str) -> None:
    """A point between two steps of applying a restore. Nothing happens here; the tests stop the start at each one
    to show that a start that stops anywhere leaves no mixed state (TIME-1 EC-5)."""


def _remove_aside(root: Path) -> None:
    """The attachments a restore set aside, once nothing waits to be applied any more."""
    if not root.parent.is_dir():
        return
    for leftover in root.parent.glob(f"{root.name}.replaced-*"):
        if leftover.is_dir() and not leftover.is_symlink():
            _remove_tree(leftover)


def apply_pending() -> bool:
    """At the start, before anything opens the database: swap in a staged backup. Returns whether one was.

    Every step can be repeated, so a start that stops in the middle (the container killed, the power gone) finishes
    the restore at the next start, and the state is never half the old and half the restored one (TIME-1 EC-5):

    1. The database: copied beside the live one, its sessions ended, then put in place in one rename.
    2. The server's key, copied.
    3. The attachments: the live folder is set aside, the restored one moved in. The pending folder keeps its
       ``attachments`` until that move; without it, the restored attachments are in place already.
    4. Done: the manifest goes first (nothing to apply any more), then the pending folder, and only then what was
       set aside. Set-aside attachments are never removed while a restore still waits.
    """
    settings = get_settings()
    root = media_root()
    pending = pending_folder()
    if not pending.is_dir():
        _remove_aside(root)
        return False
    if not (pending / MANIFEST).is_file() or not (pending / "nextime.db").is_file():
        logger.warning("An incomplete restore was found and thrown away")
        shutil.rmtree(pending, ignore_errors=True)
        _remove_aside(root)
        return False
    manifest = json.loads((pending / MANIFEST).read_text(encoding="utf-8"))

    _step("database")
    target = settings.database_path
    fresh = target.with_name(target.name + ".restoring")
    shutil.copyfile(pending / "nextime.db", fresh)
    _end_restored_sessions(fresh)
    _new_sync_mark(fresh)
    # Left behind, SQLite would read the old database's WAL into the restored one.
    for suffix in ("-wal", "-shm", "-journal"):
        target.with_name(target.name + suffix).unlink(missing_ok=True)
    os.replace(fresh, target)

    _step("secret")
    if (pending / "secret.key").is_file():
        key = settings.data_dir / "secret.key"
        shutil.copyfile(pending / "secret.key", key)
        try:
            key.chmod(0o600)
        except OSError:
            pass

    _step("aside")
    incoming = pending / ATTACHMENTS
    if incoming.is_dir():
        # The attachments folder belongs to nextime alone: it is swapped as a whole, the old one set aside until the
        # restore is done. A folder set aside by a start that stopped here stays aside as well.
        if root.exists():
            os.rename(root, root.with_name(f"{root.name}.replaced-{time.time_ns()}"))
        _step("attachments")
        shutil.move(str(incoming), str(root))
    elif not root.exists():
        root.mkdir(parents=True)

    _step("done")
    (pending / MANIFEST).unlink()
    shutil.rmtree(pending, ignore_errors=True)
    _remove_aside(root)
    logger.info("Backup restored created=%s files=%s", manifest.get("created"), manifest.get("files"))
    return True


# --- Schedule -------------------------------------------------------------------------------------------------------


def schedule(db: Any) -> str:
    value = settings_service.get(db, "backup_schedule")
    return value if value in SCHEDULES else "off"


def keep(db: Any) -> int:
    value = settings_service.get(db, "backup_keep")
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 365 else DEFAULT_KEEP


def time_of_day(db: Any) -> str:
    value = settings_service.get(db, "backup_time")
    return value if isinstance(value, str) and TIME_PATTERN.match(value) else DEFAULT_TIME


def moment_on(day: date, at: str, zone: tzinfo) -> datetime:
    """``at`` on ``day`` in the server's zone. A time that comes twice (the night the clocks go back) counts the first
    time; one the clocks skip (02:30 the night they go forward) falls an hour later, after the gap."""
    hour, minute = (int(part) for part in at.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone, fold=0)


def last_scheduled() -> datetime | None:
    last = next((entry for entry in entries() if entry.kind == SCHEDULED), None)
    if last is None:
        return None
    try:
        return datetime.fromisoformat(last.created)
    except ValueError:
        return None


def due(every: str, at: str = DEFAULT_TIME, *, now: datetime | None = None, zone: tzinfo | None = None) -> bool:
    """Whether the daily copy is due: once the day's time has come in the server's zone, and only when no scheduled
    copy was made on this day yet, so a night the clocks change makes exactly one (EC-16). A server that was off at
    that hour catches up later on the same day. The server's clock decides, right or wrong (EC-14)."""
    if every not in INTERVALS:
        return False
    zone = zone or server_zone().zone
    moment = (now or datetime.now(UTC)).astimezone(zone)
    if moment < moment_on(moment.date(), at, zone):
        return False
    previous = last_scheduled()
    return previous is None or previous.astimezone(zone).date() != moment.date()


def running() -> bool:
    """A backup is being made right now (the button shows it, a second click makes none, EC-15)."""
    return _lock.locked()


def create_now(*, kind: str = MANUAL, note: str = "") -> Path | None:
    """A backup unless one is being made already: then None (EC-15)."""
    if not _lock.acquire(blocking=False):
        return None
    try:
        return _create_recorded(kind=kind, note=note)
    finally:
        _lock.release()


def _create_recorded(*, kind: str, note: str = "") -> Path:
    """``create`` that remembers its failure for the page (EC-7): the reason, never a path or content."""
    from ..db import SessionLocal

    try:
        path = create(kind=kind, note=note)
    except OSError as exc:
        reason = "disk_full" if exc.errno == errno.ENOSPC else "write_failed"
        logger.error("Backup failed reason=%s: %s", reason, exc.strerror or type(exc).__name__)
        with SessionLocal() as db:
            settings_service.save(db, {"backup_failure": {"at": datetime.now(UTC).isoformat(timespec="seconds"),
                                                          "reason": reason}})
        raise BackupError(reason, "the backup could not be written") from exc
    with SessionLocal() as db:
        if settings_service.get(db, "backup_failure"):
            settings_service.save(db, {"backup_failure": None})
    return path


def run_job(*, now: datetime | None = None) -> bool:
    """Makes the scheduled copy when it is due; returns whether it made one."""
    from ..db import SessionLocal

    if not _lock.acquire(blocking=False):
        return False
    try:
        with SessionLocal() as db:
            every = schedule(db)
            count = keep(db)
            at = time_of_day(db)
        for leftover in folder().glob(".*") if folder().is_dir() else []:
            if leftover.is_file() and time.time() - leftover.stat().st_mtime > 6 * 3600:
                leftover.unlink(missing_ok=True)
        if not due(every, at, now=now):
            return False
        try:
            _create_recorded(kind=SCHEDULED)
        except BackupError:
            # The older copies stay (EC-7); the next look tries again.
            return False
        prune(count)
        return True
    finally:
        _lock.release()


async def run_forever(stop: asyncio.Event) -> None:
    """A look every minute, the first a minute after the start."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=INTERVAL_SECONDS)
            return
        except TimeoutError:
            pass
        try:
            await asyncio.to_thread(run_job)
        except Exception:
            logger.exception("Backup job failed")
