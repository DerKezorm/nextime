"""What goes into a backup of nextime and how it comes back: the hooks of the shared module ``sicherung``
(``app/vendor/nexsicherung``, bauplaene/sicherung 1.0.1). The module runs everything around them (names, list,
schedule, keeping, upload, download, the copy before a restore, staging and the start).

The archive keeps nextime's format, so every backup made before the module stays readable and restorable:

* ``nextime-backup.json`` (the manifest, written last), ``database/nextime.db`` (calendars, events, accounts, shares,
  settings, the encrypted access to iCloud and Google; through SQLite's backup API, never as a file: it runs in WAL
  mode), ``secret.key``, and every attachment under ``attachments/``;
* the manifest lists every attachment with its size and sha256, so the check before a restore tells a damaged archive
  from a good one and says what a restore would change: attachments, events and accounts, by number only (the operator
  never sees calendars or events of others, TIME-2 AC-3);
* a restore at the next start goes step by step, and every step can be repeated: a start that stops in the middle (the
  container killed, the power gone) finishes the restore at the next start, and the state is never half the old and
  half the restored one (TIME-1 EC-5). A step that fails with an error puts the state from before back; the module then
  starts with the data from before and tells the operators. Afterwards every session ends and a new sync mark makes
  every device with the calendar account sync anew (TIME-20 EC-20).

The archive is not encrypted: it lies in the data folder next to the database it copies. Downloading it asks for the
operator's password once more (the module).
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from .. import config as app_config
from .. import private
from ..config import get_settings
from ..vendor import nexsicherung
from ..vendor.nexsicherung import Checked, Described, Schedule, tools
from ..vendor.nexsicherung.errors import BackupError
from . import settings_service, storage

logger = logging.getLogger("nextime.backups")

MANIFEST = "nextime-backup.json"
DATABASE_ENTRY = "database/nextime.db"
MEDIA_PREFIX = "attachments/"
#: The server's key: without it the sealed secrets of the database (iCloud and Google, providers, the mail password,
#: second factors) are lost after a restore on a new server.
SECRET_ENTRY = "secret.key"
#: A media file's name: the attachment's id and its ending. Nothing else is packed or unpacked.
MEDIA_NAME = re.compile(r"^[A-Za-z0-9_-]{8,64}(\.[a-z0-9]{1,8})?$")
ATTACHMENTS = "attachments"
#: Limits of what a check or a restore unpacks: the database and each attachment (the size in an archive may lie).
DATABASE_MAX = 64 * 1024**3
MEMBER_MAX = 64 * 1024**3

#: The schedule as nextime kept it before the module (``backup_schedule``, ``backup_time``, ``backup_keep``; their
#: defaults in ``settings_service.DEFAULTS``): every night at 03:00 in the server's zone, 14 kept.
DEFAULT_EVERY = "daily"
DEFAULT_TIME = "03:00"
DEFAULT_KEEP = 14

#: How often the check reads the archive's database when the system does not let it for a moment (a virus scanner
#: holding the fresh copy, a busy disk), and the pause between: no damage, a new copy after a pause.
DATABASE_TRIES = 3
DATABASE_PAUSE_SECONDS = 0.25

#: The tables the check compares by id and last change (TIME-1 AC-8: what comes, changes and goes).
CALENDARS_TABLE = "calendars"
EVENTS_TABLE = "events"
#: A row as both databases store it: last change (as stored text), in the trash.
_Row = tuple[str, bool]

#: What the state before a restore is set aside as, until the restore is done: put back when a step fails.
DATABASE_ASIDE = ".before-restore"
#: The attachments set aside: the pattern nextime 0.4.0 used (``attachments.replaced-<number>``) and the fixed name of
#: the module's time, both tidied once nothing waits to be applied any more.
ATTACHMENTS_ASIDE = "replaced-restore"


def media_root() -> Path:
    return get_settings().data_dir / ATTACHMENTS


def media_files(root: Path) -> list[tuple[str, Path]]:
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


def _manifest(archive: zipfile.ZipFile) -> dict[str, Any]:
    try:
        raw = json.loads(tools.read_small(archive, MANIFEST, limit=64 * 1024 * 1024).decode("utf-8"))
    except (KeyError, ValueError, *tools.DAMAGE) as exc:
        raise BackupError("backup_invalid", "the archive has no readable manifest") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("media", {}), dict):
        raise BackupError("backup_invalid", "the manifest has the wrong shape")
    return raw


def write_archive(target: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    """The archive as nextime made it before the module and as ``pack`` makes it now: the database through the backup
    API, the key, every attachment with its size and hash, the manifest last (an archive without it is recognisably
    incomplete). Returns the manifest written."""
    settings = get_settings()
    database = target.with_name(target.name + ".db")
    try:
        private.new_file(database)
        tools.copy_database(settings.database_path, database)
        connection = sqlite3.connect(database)
        try:
            calendars, events = _count(connection, CALENDARS_TABLE), _count(connection, EVENTS_TABLE)
        finally:
            connection.close()
        manifest = {**manifest, "calendars": calendars, "events": events, "files": 0, "bytes": 0, "media": {}}
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            archive.write(database, DATABASE_ENTRY)
            key = settings.data_dir / "secret.key"
            if key.is_file():
                archive.write(key, SECRET_ENTRY)
            for rel, full in media_files(media_root()):
                try:
                    size = full.stat().st_size
                    # Photos and PDFs are packed already; deflating them again only costs time.
                    archive.write(full, MEDIA_PREFIX + rel, compress_type=zipfile.ZIP_STORED)
                    manifest["media"][rel] = [size, tools.sha256_file(full)]
                except OSError as exc:
                    logger.warning("A file could not be backed up and was left out: %s", exc.strerror)
                    continue
                manifest["files"] += 1
                manifest["bytes"] += size
            archive.writestr(MANIFEST, json.dumps(manifest, ensure_ascii=False))
    finally:
        database.unlink(missing_ok=True)
        # The settings show what nextime takes; a new archive should be in it at once, not a minute later.
        storage.forget_footprint()
    logger.info("Backup written kind=%s events=%s files=%s bytes=%s", manifest.get("kind"), manifest["events"],
                manifest["files"], manifest["bytes"])
    return manifest


# --- What a restore changes -------------------------------------------------------------------------------------------


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


def _archived_contents(scratch: Path) -> dict[str, dict[str, _Row]]:
    connection = sqlite3.connect(f"file:{scratch.as_posix()}?mode=ro", uri=True)
    try:
        return _contents(connection)
    except sqlite3.DatabaseError:
        return {}
    finally:
        connection.close()


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


# --- The steps of a restore at the start --------------------------------------------------------------------------


def _end_restored_sessions(database: Path) -> None:
    """Sign-ins stored in the backup would come back to life, even ones ended since: everybody signs in anew."""
    connection = sqlite3.connect(database)
    try:
        if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='auth_sessions'").fetchone():
            connection.execute("DELETE FROM auth_sessions")
            connection.commit()
    finally:
        connection.close()


def new_sync_mark(database: Path) -> None:
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


def step(name: str) -> None:
    """A point between two steps of applying a restore. Nothing happens here; the tests stop the start at each one
    to show that a start that stops anywhere leaves no mixed state (TIME-1 EC-5)."""


def _database_files(target: Path) -> list[Path]:
    return [target, *(target.with_name(target.name + suffix) for suffix in ("-wal", "-shm", "-journal"))]


def _aside(path: Path) -> Path:
    return path.with_name(path.name + DATABASE_ASIDE)


def _attachments_aside() -> Path:
    root = media_root()
    return root.with_name(f"{root.name}.{ATTACHMENTS_ASIDE}")


def _legacy_pending(pending: Path) -> None:
    """A restore nextime 0.4.0 laid out and did not apply before this version started (an update in between): its
    folder holds the manifest as its marker. It is complete, so it gets the module's marker and is applied."""
    marker = pending / nexsicherung.restore.MARKER
    complete = (pending / MANIFEST).is_file() and (pending / "nextime.db").is_file()
    if pending.is_dir() and not marker.exists() and complete:
        try:
            manifest = json.loads((pending / MANIFEST).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        marker.write_text(json.dumps({"name": "", "created": manifest.get("created"),
                                      "version": manifest.get("version"), "at": tools.now_iso()}), encoding="utf-8")
        logger.info("A restore laid out by an older version is applied now")


def schedule_of(values: dict[str, Any]) -> Schedule:
    """The schedule from nextime's settings (``backup_schedule``, ``backup_time``, ``backup_keep``), with the defaults
    for what is missing or not allowed; the settings route shows the same."""
    every, at, keep = values.get("backup_schedule"), values.get("backup_time"), values.get("backup_keep")
    return Schedule(
        every=every if every in nexsicherung.EVERY else DEFAULT_EVERY,
        time=at if isinstance(at, str) and nexsicherung.model.TIME.match(at) else DEFAULT_TIME,
        keep=keep if isinstance(keep, int) and not isinstance(keep, bool)
        and nexsicherung.model.KEEP_MIN <= keep <= nexsicherung.model.KEEP_MAX else DEFAULT_KEEP,
    )


class NextimeHooks(nexsicherung.AppHooks):
    # --- What goes in

    def pack(self, target: Path, *, kind: str, note: str, created: str) -> None:
        from .. import __version__

        write_archive(target, {"version": __version__, "created": created, "kind": kind, "note": note})

    def describe(self, path: Path) -> Described:
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = _manifest(archive)
                if DATABASE_ENTRY not in archive.namelist():
                    raise BackupError("backup_invalid", "the archive holds no database")
        except zipfile.BadZipFile as exc:
            raise BackupError("backup_invalid", "not a ZIP archive") from exc
        return Described(
            created=str(manifest.get("created") or "") or None, kind=str(manifest.get("kind") or "manual"),
            note=str(manifest.get("note") or ""), version=str(manifest.get("version") or ""),
            facts={"events": int(manifest.get("events") or 0), "files": int(manifest.get("files") or 0)},
        )

    def check(self, path: Path, *, viewer: Any = None, archive_password: str | None = None) -> Checked:
        """The trial run: is the archive whole, and what would a restore change, in events, accounts and the
        attachments? Numbers only, never a title."""
        scratch = nexsicherung.archives.scratch("check")
        archived: dict[str, dict[str, _Row]] = {}
        try:
            with zipfile.ZipFile(path) as archive:
                manifest = _manifest(archive)
                media: dict[str, list[Any]] = manifest.get("media", {})
                members = {info.filename: info for info in archive.infolist()}
                try:
                    database_ok = DATABASE_ENTRY in members and tools.read_database(
                        archive, DATABASE_ENTRY, scratch, limit=DATABASE_MAX, tries=DATABASE_TRIES,
                        pause=DATABASE_PAUSE_SECONDS)
                except BackupError:
                    database_ok = False
                if database_ok:
                    archived = _archived_contents(scratch)
                else:
                    logger.warning("The database in the backup is damaged or could not be read name=%s", path.name)
                damaged: list[str] = []
                for rel, value in media.items():
                    member = members.get(MEDIA_PREFIX + rel)
                    if member is None or tools.safe_member(member.filename, MEDIA_PREFIX, MEDIA_NAME) != rel \
                            or member.file_size != value[0]:
                        damaged.append(rel)
                        continue
                    try:
                        if tools.sha256_member(archive, member, limit=MEMBER_MAX) != (value[0], value[1]):
                            damaged.append(rel)
                    except (BackupError, *tools.DAMAGE):
                        damaged.append(rel)
                damaged += [name for name, info in members.items() if not info.is_dir()
                            and name not in (MANIFEST, DATABASE_ENTRY, SECRET_ENTRY)
                            and tools.safe_member(name, MEDIA_PREFIX, MEDIA_NAME) not in media]
        except zipfile.BadZipFile as exc:
            raise BackupError("backup_invalid", "not a ZIP archive") from exc
        finally:
            try:
                scratch.unlink(missing_ok=True)
            except OSError:
                # Still held for a moment: a hidden name the module tidies away later.
                pass
        current = dict(media_files(media_root()))
        details = {
            "calendars": int(manifest.get("calendars") or 0),
            "would_add": sum(1 for rel in media if rel not in current),
            "would_remove": sum(1 for rel in current if rel not in media),
            "would_change": sum(1 for rel, full in current.items() if rel in media
                                and (full.stat().st_size != media[rel][0]
                                     or tools.sha256_file(full) != media[rel][1])),
            **_compare(archived, _live_contents()),
        }
        return Checked(database_ok=database_ok, files_ok=not damaged, damaged=sorted(damaged)[:20], details=details)

    # --- How it comes back, at the next start

    def unpack(self, path: Path, pending: Path, *, archive_password: str | None = None) -> None:
        with zipfile.ZipFile(path) as archive:
            manifest = _manifest(archive)
            tools.extract(archive, DATABASE_ENTRY, pending / "nextime.db", limit=DATABASE_MAX)
            if SECRET_ENTRY in archive.namelist():
                (pending / "secret.key").write_bytes(tools.read_small(archive, SECRET_ENTRY, limit=4096))
            (pending / ATTACHMENTS).mkdir()
            for rel in manifest.get("media", {}):
                if not MEDIA_NAME.fullmatch(rel):
                    raise BackupError("backup_damaged", "a file name leaves its folder")
                tools.extract(archive, MEDIA_PREFIX + rel, pending / ATTACHMENTS / rel, limit=MEMBER_MAX)
            # The manifest as nextime laid it out before the module: an older version going back finds it complete.
            (pending / MANIFEST).write_bytes(json.dumps(manifest, ensure_ascii=False).encode())

    def tidy(self) -> None:
        """At every start, before ``apply``: a restore an older version laid out gets the module's marker; and once
        nothing waits to be applied any more, what an earlier restore set aside goes (never while a restore waits: a
        start that stopped in the middle keeps the state from before until the restore is done)."""
        pending = nexsicherung.restore.pending_folder()
        _legacy_pending(pending)
        if pending.is_dir():
            return
        settings = get_settings()
        for path in (*map(_aside, _database_files(settings.database_path)), _aside(settings.data_dir / "secret.key")):
            path.unlink(missing_ok=True)
        root = media_root()
        if not root.parent.is_dir():
            return
        for leftover in root.parent.glob(f"{root.name}.replaced-*"):
            if leftover.is_dir() and not leftover.is_symlink() and not tools.remove_tree(leftover):
                logger.warning("A folder set aside by a restore could not be removed yet name=%s", leftover.name)

    def apply(self, pending: Path) -> None:
        """Step by step, every step repeatable (TIME-1 EC-5):

        1. The database: the restored one is copied beside the live one, its sessions ended and a new sync mark set;
           the live one (with its side files) is set aside once, then the restored one put in place in one rename.
        2. The server's key: the live one set aside once, the restored one copied.
        3. The attachments: the live folder is set aside once, the restored one moved in. The pending folder keeps its
           ``attachments`` until that move; without it, the restored attachments are in place already.
        4. Done: what was set aside goes.

        A step that fails with an error puts the state from before back (the module then starts with it)."""
        settings = get_settings()
        target = settings.database_path
        key = settings.data_dir / "secret.key"
        root = media_root()
        aside = _attachments_aside()
        try:
            step("database")
            fresh = target.with_name(target.name + ".restoring")
            shutil.copyfile(pending / "nextime.db", fresh)
            _end_restored_sessions(fresh)
            new_sync_mark(fresh)
            if not _aside(target).exists() and target.exists():
                for path in reversed(_database_files(target)):
                    if path.exists():
                        os.replace(path, _aside(path))
            # Left behind, SQLite would read the old database's WAL into the restored one.
            for path in _database_files(target)[1:]:
                path.unlink(missing_ok=True)
            os.replace(fresh, target)

            step("secret")
            if (pending / "secret.key").is_file():
                if key.exists() and not _aside(key).exists():
                    os.replace(key, _aside(key))
                shutil.copyfile(pending / "secret.key", key)
                private.tighten(key)

            step("aside")
            incoming = pending / ATTACHMENTS
            if incoming.is_dir():
                # The attachments folder belongs to nextime alone: it is swapped as a whole. A folder set aside by a
                # start that stopped here stays aside as it is.
                if root.exists():
                    if aside.exists():
                        tools.remove_tree(root)
                    else:
                        os.rename(root, aside)
                step("attachments")
                shutil.move(str(incoming), str(root))
            elif not root.exists():
                root.mkdir(parents=True)
        except Exception:
            logger.exception("Restoring the backup failed, the previous state is put back")
            self._put_back(target, key, root, aside)
            raise
        step("done")
        for path in (*map(_aside, _database_files(target)), _aside(key)):
            path.unlink(missing_ok=True)
        if aside.exists():
            tools.remove_tree(aside)
        logger.info("Backup restored")

    @staticmethod
    def _put_back(target: Path, key: Path, root: Path, aside: Path) -> None:
        if _aside(target).exists():
            for path in _database_files(target):
                path.unlink(missing_ok=True)
            for path in _database_files(target):
                if _aside(path).exists():
                    os.replace(_aside(path), path)
        target.with_name(target.name + ".restoring").unlink(missing_ok=True)
        if _aside(key).exists():
            os.replace(_aside(key), key)
        if aside.exists():
            if root.exists():
                tools.remove_tree(root)
            os.rename(aside, root)

    # --- The schedule, under nextime's own keys

    def load_schedule(self) -> Schedule:
        from ..db import SessionLocal

        with SessionLocal() as db:
            return schedule_of({key: settings_service.get(db, key)
                                for key in ("backup_schedule", "backup_time", "backup_keep")})

    def save_schedule(self, schedule: Schedule) -> None:
        from ..db import SessionLocal

        with SessionLocal() as db:
            settings_service.save(db, {"backup_schedule": schedule.every, "backup_time": schedule.time,
                                       "backup_keep": schedule.keep})


def configure() -> NextimeHooks:
    """The module set up for nextime: its folders, version, hooks and the zone of the schedule (the server's, as
    before). At the start, before ``apply_pending``; done on import too, so the tests find it configured."""
    from .. import __version__

    settings = get_settings()
    hooks = NextimeHooks()
    nexsicherung.configure(nexsicherung.AppConfig(
        app_name="nextime", data_dir=settings.data_dir, version=__version__, hooks=hooks,
        database_path=settings.database_path,
        parts={"attachments": (ATTACHMENTS,), "exports": ("exports",)},
        time_zone=lambda: app_config.server_zone().zone,
    ))
    return hooks
