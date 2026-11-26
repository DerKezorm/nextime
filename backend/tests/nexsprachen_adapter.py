"""nexsprachen contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexsprachen/`` run the module inside nextime: its start (``services/locales``
configures the module with the folder of before), its routes under ``/api/locales`` and its check of operators.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, ClassVar

from sprachen_contract import StandardAdapter

OPERATOR_NAME = "tester"


class Adapter(StandardAdapter):
    """nextime: the files in ``NEXTIME_LOCALES_DIR`` (the test session's own folder, ``<data>/locales`` in
    production), German and English built in, readable without signing in (the sign-in page), the routes under
    ``/api/locales``, refusals in nextime's form (``{"detail": {"code", "message", ...}}``), the template made on the
    page (``LanguagesCard template={en}``), not by the server."""

    package = "app.vendor.nexsprachen"
    prefix = "/api/locales"
    also: tuple[str, ...] = ()
    built_in: ClassVar[dict[str, str]] = {"de": "Deutsch", "en": "English"}
    public_read = True
    has_template = False

    def start(self) -> None:
        """nextime started as in production (its lifespan), with an empty folder of own languages."""
        from fastapi.testclient import TestClient

        from app.main import app
        from app.services import locales
        from tests.conftest import TAB

        self._clients: list[Any] = []
        self._empty(locales.locales_dir())
        locales.configure()
        locales.forget()
        self._main = TestClient(app, base_url="http://testserver", headers=TAB)
        self._main.__enter__()

    def stop(self) -> None:
        from app.services import locales

        for client in self._clients:
            client.close()
        self._main.__exit__(None, None, None)
        self._empty(locales.locales_dir())
        locales.configure()
        locales.forget()

    def client(self, role: str) -> Any:
        from app.models import MEMBER, OPERATOR
        from tests.conftest import make_account, new_client

        if role == "anonymous":
            client = new_client()
        else:
            name = OPERATOR_NAME if role == "operator" else "member"
            client = new_client(self._account(name, OPERATOR if role == "operator" else MEMBER, make_account))
        self._clients.append(client)
        return client

    @staticmethod
    def _account(name: str, role: str, make: Any) -> Any:
        from app.db import SessionLocal
        from app.models import Account

        with SessionLocal() as db:
            row = db.query(Account).filter_by(name=name).one_or_none()
            if row is not None:
                db.expunge(row)
                return row
        return make(name, role)

    @staticmethod
    def _empty(folder: Path) -> None:
        """The test session's own folder only (``NEXTIME_LOCALES_DIR``), never another."""
        assert Path(os.environ["NEXTIME_LOCALES_DIR"]) == folder, "never empty a folder that is not the test's own"
        folder.mkdir(parents=True, exist_ok=True)
        for entry in folder.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
