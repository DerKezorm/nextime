"""nexupdates contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexupdates/`` run the module inside nextime: its configuration
(``services/updates.py``: the switch ``update_check`` in the settings, as before), its routes under ``/api/about`` and
its setup route ``POST /api/setup`` with the box "Nach neuen Versionen sehen". The database is the test session's
(``tests/conftest.py`` empties it before every test). Nothing reaches GitHub: the conftest of the contract tests puts a
stand-in on 127.0.0.1 in its place.

nextime has no bell (U19): ``bell`` and ``bell_notices`` answer what nextime tells, nothing. The contract tests that
expect a bell are skipped in nextime by ``tests/conftest.py`` (TODO updates 0.1.1, questions file).
"""

from __future__ import annotations

from typing import Any

from updates_contract import StandardAdapter


class Adapter(StandardAdapter):
    package = "app.vendor.nexupdates"
    prefix = "/api/about"
    also: tuple[str, ...] = ()

    def start(self) -> None:
        """nextime started as in production on the emptied test database. The operator and the member are made when
        first asked for (``client``): the setup route needs a server without accounts."""
        from fastapi.testclient import TestClient

        from app.main import app

        self._main = TestClient(app, base_url="http://testserver")
        self._main.__enter__()
        self._clients: list[Any] = []
        self._accounts: dict[str, Any] = {}
        self.restart()

    def stop(self) -> None:
        from app.services import updates

        for client in self._clients:
            client.close()
        self._main.__exit__(None, None, None)
        updates.configure()

    def restart(self) -> None:
        """The module's start again on the same data: nextime configures it at import (``services/updates.py``)."""
        from app.services import updates

        updates.configure()

    def _account(self, role: str) -> Any:
        from app.models import MEMBER, OPERATOR
        from tests.conftest import make_account

        if role not in self._accounts:
            self._accounts[role] = make_account(f"updates-{role}", OPERATOR if role == "operator" else MEMBER)
        return self._accounts[role]

    def client(self, role: str) -> Any:
        from tests.conftest import new_client

        client = new_client() if role == "anonymous" else new_client(self._account(role))
        self._clients.append(client)
        return client

    def stored_switch(self) -> Any:
        from app.db import SessionLocal
        from app.models import Setting

        with SessionLocal() as db:
            row = db.get(Setting, "update_check")
            return None if row is None else row.value

    def store_legacy_switch(self, on: bool) -> None:
        """As nextime 0.4.0 stored it: a JSON true or false under ``update_check`` (``PUT /api/settings``)."""
        from app.db import SessionLocal
        from app.services import settings_service

        with SessionLocal() as db:
            settings_service.save(db, {"update_check": on})

    def setup(self, on: bool) -> None:
        from tests.conftest import SETUP_CODE, new_client

        with new_client() as client:
            answer = client.post("/api/setup", json={
                "code": SETUP_CODE, "display_name": "Boss", "password": "correct horse battery staple",
                "language": "de", "update_check": on,
            })
        assert answer.status_code == 200, answer.text

    def bell(self) -> list[str]:
        # nextime has no bell (U19).
        return []

    def bell_notices(self) -> list[tuple[str, str, str]]:
        return []
