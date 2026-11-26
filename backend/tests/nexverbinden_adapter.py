"""The adapter of the contract tests of the module verbinden: nextime's (copied once from the module's template, now
nextime's file).

The app under test is nextime itself (its ASGI app, the test session's database, which ``tests/conftest.py`` empties
before every test); the partner is the module's reference playing nexmail, which reads and writes nextime's
calendars and answers invitations (catalog: ``calendars.read``, ``events.write``, ``invitations.answer``). The
calendars are the areas. Stage 1 of the plan: a purpose stands for the right of a key, so every purpose opens
``/api/v1/calendars``.
"""

from __future__ import annotations

import secrets
import sqlite3
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from verbinden_contract import StandardAdapter


class Adapter(StandardAdapter):
    package = "app.vendor.nexverbinden"
    partner = "nexmail"
    base = "https://nextime.example.com"
    older_link = False

    def start(self, folder: Path, *, older: bool = False) -> None:
        from app.main import app
        from app.models import MEMBER, OPERATOR
        from app.services.verbinden_store import Store
        from tests.calendar_helpers import make_calendar, make_event
        from tests.conftest import TAB, make_account

        self._app = app
        self._main = TestClient(app, base_url=self.base, headers=TAB)
        self._main.__enter__()
        self._clients: list[TestClient] = []
        from verbinden_partner import ROLES

        # The same names as the partner's accounts (the tests compare the name each side shows of the other).
        self._accounts = {role: make_account(name, OPERATOR if role == "operator" else MEMBER)
                          for role, (_number, name) in ROLES.items()}
        owner = self.client("owner")
        # Two calendars of the owner, each with an event (the test of the areas chosen needs two).
        for name in ("Familie", "Arbeit"):
            make_event(owner, f"Termin {name}", calendar_id=make_calendar(owner, name))
        from app.db import SessionLocal

        self._db = SessionLocal()
        self.store = Store(self._db)
        if older:
            self.older_key = self._older_key()
            self.older_key_path = "/api/v1/me"

    def _older_key(self) -> str:
        """An API key of the owner as nextime 0.4.0 made it (``apikeys.make``, unchanged)."""
        from app.db import SessionLocal
        from app.models import Account
        from app.services import apikeys

        with SessionLocal() as db:
            row = db.get(Account, self._accounts["owner"].id)
            assert row is not None
            _made, token = apikeys.make(db, row, name="Skript", level="read", mcp=False, calendar_ids=None,
                                        days=apikeys.DEFAULT_DAYS, client_ref=secrets.token_hex(8))
        assert token is not None
        return token

    def stop(self) -> None:
        for client in self._clients:
            client.close()
        self._db.close()
        self._main.__exit__(None, None, None)

    def asgi(self) -> Any:
        return self._app

    def client(self, role: str | None) -> TestClient:
        from tests.conftest import TAB, sign_in

        # A browser of nextime sends its tab; another app (``None``) does not.
        client = TestClient(self._app, base_url=self.base, headers=TAB if role is not None else {})
        if role is not None:
            sign_in(client, self._accounts[role])
        self._clients.append(client)
        return client

    def account_id(self, role: str) -> Any:
        return self._accounts[role].id

    def config(self) -> Any:
        from app.services.verbinden_store import CONFIG

        return CONFIG

    def block(self, role: str, blocked: bool = True) -> None:
        from app.db import SessionLocal
        from app.models import Account
        from app.services import accounts

        with SessionLocal() as db:
            row = db.get(Account, self._accounts[role].id)
            assert row is not None
            (accounts.block if blocked else accounts.unblock)(db, row)

    def probe(self, purpose: str) -> str | None:
        return "/api/v1/calendars"

    def areas_seen(self, purpose: str, answer: Any) -> set[str]:
        return {str(calendar["id"]) for calendar in answer["calendars"]}

    def everything(self) -> str:
        from app.config import get_settings

        with sqlite3.connect(str(get_settings().database_path)) as connection:
            return "\n".join(connection.iterdump())

    def own_tables(self) -> dict[str, Any]:
        from app.config import get_settings

        with sqlite3.connect(str(get_settings().database_path)) as connection:
            return {name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()  # noqa: S608
                    for name in ("api_keys", "nextasks_connections", "nexpaper_connections")}
