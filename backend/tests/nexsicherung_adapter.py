"""nexsicherung contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexsicherung/`` run the module against nextime's real hooks
(``app/services/backup_hooks.py``), its routes (``/api/server/storage`` and the old ``/api/backups``) and its data. The
database and data folder are the ones of the test session (``tests/conftest.py`` empties the database, the attachments
and the backups before every test).
"""

from __future__ import annotations

import json
import os
import secrets
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from sicherung_contract import StandardAdapter

from app.vendor.nexsicherung import routes


class Adapter(StandardAdapter):
    package = "app.vendor.nexsicherung"
    prefix = "/api/server/storage"
    legacy_prefix = "/api/backups"
    legacy_header = "x-nextime-password"
    # E53: the names nextime's own backup routes had, exactly as ``routers/backups.py`` gives them to the routers.
    _former = routes.former_names(legacy_prefix)
    operation_ids: ClassVar[dict[str, str]] = _former["operation_ids"]
    summaries: ClassVar[dict[str, str]] = _former["summaries"]
    schema_names: ClassVar[dict[str, str]] = _former["schema_names"]
    # The old routes check ``{name}`` with the pattern and length of before (``former_names`` gives it).
    name_pattern = bool(_former.get("name_pattern"))
    # nextime publishes its description only with ``NEXTIME_API_DOCS`` (off in the tests): ``description`` hands
    # over its own.
    openapi_path = "/api/openapi.json"

    def description(self) -> dict[str, Any]:
        from app.main import app

        return app.openapi()

    def start(self) -> None:
        from fastapi.testclient import TestClient

        from app.main import app
        from app.models import MEMBER, OPERATOR
        from tests.conftest import TAB, make_account

        self._password = secrets.token_urlsafe(12)
        self._operator = make_account("olli", OPERATOR, password=self._password)
        self._member = make_account("mika", MEMBER, password=secrets.token_urlsafe(12))
        self._clients: list[Any] = []
        self._main = TestClient(app, base_url="http://testserver", headers=TAB)
        self._main.__enter__()

    def stop(self) -> None:
        for client in self._clients:
            client.close()
        self._main.__exit__(None, None, None)

    @property
    def password(self) -> str:
        return self._password

    def _client(self, account: Any) -> Any:
        from tests.conftest import new_client

        client = new_client(account)
        self._clients.append(client)
        return client

    def operator(self) -> Any:
        return self._client(self._operator)

    def member(self) -> Any:
        return self._client(self._member)

    def restart(self) -> None:
        """The next start: the database closed, the staged restore swapped in, the database opened and checked."""
        from app.db import engine, init_db
        from app.services import backups

        engine.dispose()
        backups.configure()
        backups.tidy_at_start()
        backups.apply_pending()
        init_db()

    def add_content(self, label: str) -> None:
        """An event of the operator in a calendar of their own, and an attachment (as an upload leaves it: the file in
        the attachments folder)."""
        from app.services import backups
        from tests.calendar_helpers import make_calendar, make_event

        root = backups.media_root()
        root.mkdir(parents=True, exist_ok=True)
        name = secrets.token_hex(8) + ".bin"
        (root / name).write_bytes(label.encode() * 50)
        client = self.operator()
        calendar = make_calendar(client, f"Kalender {secrets.token_hex(3)}")
        make_event(client, title=label, calendar_id=calendar)

    def content(self) -> Any:
        from app.db import SessionLocal
        from app.models import Event
        from app.services import backup_hooks, backups
        from app.vendor.nexsicherung import tools

        with SessionLocal() as db:
            titles = sorted(event.title for event in db.query(Event).filter(Event.deleted_at.is_(None)))
        files = sorted((rel, tools.sha256_file(full)) for rel, full in backup_hooks.media_files(backups.media_root()))
        return {"titles": titles, "files": files}

    def legacy_archive(self) -> tuple[Path, Any]:
        """A backup as nextime 0.4.0 made it (``services/backups.create`` before the module): the same members and
        manifest, version 0.4.0, the old name."""
        self.add_content("from the old version")
        held = self.content()
        from app.services import backup_hooks, backups

        folder = backups.folder()
        folder.mkdir(parents=True, exist_ok=True)
        moment = datetime(2026, 10, 1, 3, 0, 0, tzinfo=UTC)
        target = folder / f"nextime-{moment.strftime('%Y-%m-%d-%H%M%S')}.zip"
        backup_hooks.write_archive(target, {"version": "0.4.0", "created": moment.isoformat(timespec="seconds"),
                                            "kind": "scheduled", "note": ""})
        # 0.4.0 wrote the manifest as its dataclass: every field, in this order.
        with zipfile.ZipFile(target) as archive:
            manifest = json.loads(archive.read(backup_hooks.MANIFEST))
            members = [(info, archive.read(info)) for info in archive.infolist() if info.filename != backup_hooks.MANIFEST]
        old = {key: manifest[key] for key in ("version", "created", "kind", "note", "calendars", "events", "files", "bytes", "media")}
        os.remove(target)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for info, data in members:
                archive.writestr(info, data)
            archive.writestr(backup_hooks.MANIFEST, json.dumps(old, ensure_ascii=False))
        return target, held

    def settings_schedule(self) -> tuple[str, int]:
        found = self.operator().get("/api/settings").json()
        return found["backup_schedule"], found["backup_keep"]
