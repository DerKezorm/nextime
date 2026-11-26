"""nexmcp contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexmcp/`` run the module inside nextime: its store over its tables
(``services/mcp_store.py``), its seven tools (``services/mcp.py``), its routes (``/api/mcp``, ``/api/oauth``,
``/api/connections/mcp``) and the keys of the module schluessel under ``/api/connections``. The database is the test
session's (``tests/conftest.py`` empties it before every test).

What nextime 0.4.0 had: API keys ``nt_api_`` with the tick "also for AI tools" (the only way into ``/api/mcp``), MCP on
from the start without anybody switching it on, no OAuth. ``start_older`` writes the database that way (the tables of
the module gone, nothing stored about MCP) and starts nextime's real ``init_db`` on it.
"""

from __future__ import annotations

import secrets
from typing import Any

from mcp_contract import LegacyKey, LegacyProgram, StandardAdapter

#: The tables nextime 0.4.0 did not have.
NEW_TABLES = ("mcp_keys", "mcp_clients", "mcp_codes", "mcp_spent", "mcp_tool_rights", "mcp_requests")


class Adapter(StandardAdapter):
    package = "app.vendor.nexmcp"
    keys_prefix = "/api/connections"
    prefix = "/api/connections/mcp"
    has_ai = False
    older_had_mcp_on = True

    def start(self) -> None:
        """nextime started as in production on the emptied test database: owner and other with a calendar and an
        event each, an operator."""
        from fastapi.testclient import TestClient

        from app.main import app
        from app.models import MEMBER, OPERATOR
        from tests.calendar_helpers import make_calendar, make_event
        from tests.conftest import TAB, make_account, new_client

        self._main = TestClient(app, base_url="http://testserver", headers=TAB)
        self._main.__enter__()
        self._clients: list[Any] = []
        self._accounts = {
            "owner": make_account("owner", MEMBER),
            "other": make_account("other", MEMBER),
            "operator": make_account("boss", OPERATOR),
        }
        self._calendars: dict[str, int] = {}
        for name in ("owner", "other"):
            client = new_client(self._accounts[name])
            self._clients.append(client)
            self._calendars[name] = make_calendar(client, f"Kalender {name}")
            make_event(client, f"Termin {name}", calendar_id=self._calendars[name])

    def start_older(self) -> None:
        """The database as 0.4.0 left it: without the module's tables and with nothing stored about MCP (it was on
        from the start); then nextime's start (``init_db``) on it."""
        from sqlalchemy import text

        from app.db import engine, init_db
        from app.models import Base

        self.start()
        with engine.begin() as connection:
            for name in NEW_TABLES:
                connection.execute(text(f'DROP TABLE IF EXISTS "{name}"'))
            connection.execute(text("DELETE FROM settings WHERE key LIKE 'mcp%'"))
        self._older = True
        init_db()
        # The tables are back as the new version makes them (the test session's other tests rely on them).
        assert all(name in Base.metadata.tables for name in NEW_TABLES)

    def stop(self) -> None:
        for client in self._clients:
            client.close()
        self._main.__exit__(None, None, None)

    def client(self, role: str) -> Any:
        from tests.conftest import new_client

        client = new_client() if role == "anonymous" else new_client(self._accounts[role])
        self._clients.append(client)
        return client

    def config(self) -> Any:
        from app.services.mcp import CONFIG

        return CONFIG

    def keys_config(self) -> Any:
        from app.services.schluessel_store import CONFIG

        return CONFIG

    def read_call(self) -> tuple[str, dict[str, Any]]:
        return "list_events", {"start": "2026-11-26", "end": "2026-11-27", "calendar_ids": [self._calendars["owner"]]}

    def change_call(self) -> tuple[str, dict[str, Any]]:
        return "create_event", {"title": "Zahnarzt", "start": "2026-11-27T09:00", "end": "2026-11-27T10:00",
                                "calendar_id": self._calendars["owner"]}

    def foreign_call(self) -> tuple[str, dict[str, Any]]:
        return "list_events", {"start": "2026-11-26", "end": "2026-11-27", "calendar_ids": [self._calendars["other"]]}

    def legacy_keys(self) -> list[LegacyKey]:
        """The only form that opened ``/api/mcp`` in 0.4.0: an API key with the tick "also for AI tools", read and
        write."""
        return [LegacyKey("API key with the tick for MCP (read)", "read"),
                LegacyKey("API key with the tick for MCP (write)", "write")]

    def make_legacy(self, spec: LegacyKey) -> str:
        """As 0.4.0 made them: its own ``apikeys.make`` (the service is unchanged)."""
        from app.db import SessionLocal
        from app.models import Account
        from app.services import apikeys

        with SessionLocal() as db:
            row = db.get(Account, self._accounts["owner"].id)
            assert row is not None
            _made, token = apikeys.make(db, row, name=spec.label, level=spec.form, mcp=True, calendar_ids=None,
                                        days=apikeys.DEFAULT_DAYS, client_ref=secrets.token_hex(8))
        assert token is not None
        return token

    def legacy_program(self) -> LegacyProgram | None:
        # nextime 0.4.0 had no OAuth.
        return None


__all__ = ["Adapter", "LegacyKey", "LegacyProgram"]
