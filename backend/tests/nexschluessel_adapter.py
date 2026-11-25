"""nexschluessel contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexschluessel/`` run the module inside nextime: its store over ``api_keys``
(``services/schluessel_store.py``), its routes under ``/api/connections``, and every key of 0.4.0 presented to nextime's
real ways (``GET /api/v1/me``, ``POST /api/mcp``). The database is the test session's (``tests/conftest.py`` empties it
before every test).

Fill in every place marked ``APP:``. The contract tests in ``backend/tests/nexschluessel/`` (same in every app, never
edit them) call only what is here and in ``StandardAdapter``. Worked examples: the module's
``tests-fuer-apps/reference/nexschluessel_adapter.py`` (an app like nexnotes) and ``tests/mail_like_adapter.py`` (an
app like nexmail). Run them with ``pytest tests/nexschluessel``. Keys are made at run time; none is written into this
file.

The heart of it is ``legacy_keys`` and ``make_legacy``: every form of key the app made before the module (each kind,
each prefix, the old forms that still work, the connectors of AI tools), written exactly the way the older version
wrote it. The tests present each to the app's real routes before and after the module touched it.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any

from schluessel_contract import LegacyKey, StandardAdapter


class Adapter(StandardAdapter):
    package = "app.vendor.nexschluessel"
    prefix = "/api/connections"

    def start(self) -> None:
        """nextime started as in production; "owner" and "other" with a calendar each, and an operator."""
        from fastapi.testclient import TestClient

        from app.main import app
        from app.models import MEMBER, OPERATOR
        from tests.calendar_helpers import make_calendar
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

    def stop(self) -> None:
        for client in self._clients:
            client.close()
        self._main.__exit__(None, None, None)

    def config(self) -> Any:
        from app.services.schluessel_store import CONFIG

        return CONFIG

    def client(self, role: str) -> Any:
        from tests.conftest import new_client

        client = new_client() if role == "anonymous" else new_client(self._accounts[role])
        self._clients.append(client)
        return client

    def legacy_keys(self) -> list[LegacyKey]:
        """Every form of key nextime 0.4.0 made (TIME-26): one kind, ``nt_api_``, read or write; the tick for MCP is an
        extra of the same key (``make_legacy`` sets it for the write key, which ``probe`` presents at ``/api/mcp``)."""
        return [
            LegacyKey("API key (read)", "api", "nt_api_", "read"),
            LegacyKey("API key (write, also for MCP)", "api", "nt_api_", "write"),
        ]

    def make_legacy(self, spec: LegacyKey, *, account: str = "owner", expired: bool = False) -> str:
        """As nextime 0.4.0 made them: its own ``apikeys.make`` (the service is unchanged), the end moved into the past
        for an expired one."""
        from app.db import SessionLocal
        from app.models import Account, ApiKey
        from app.services import apikeys

        with SessionLocal() as db:
            row = db.get(Account, self._accounts[account].id)
            assert row is not None
            made, token = apikeys.make(db, row, name=spec.label, level=spec.right, mcp=spec.right == "write",
                                       calendar_ids=None, days=apikeys.DEFAULT_DAYS,
                                       client_ref=secrets.token_hex(8))
            assert token is not None
            if expired:
                key = db.get(ApiKey, made.id)
                assert key is not None
                key.expires_at = apikeys.clock() - timedelta(hours=1)
                db.commit()
        return token

    def probe(self, kind: str, secret: str) -> int:
        client = self.client("anonymous")
        headers = {"Authorization": f"Bearer {secret}"}
        if kind == "mcp":
            return client.post(
                "/api/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"}
            ).status_code
        return client.get("/api/v1/me", headers=headers).status_code

    def owe_factor(self) -> None:
        """The operator requires a second factor; the owner signs in with a password and has none."""
        from app.db import SessionLocal
        from app.services import settings_service

        with SessionLocal() as db:
            settings_service.save(db, {"two_factor_required": True})

    def foreign_area(self) -> Any:
        return self._calendars["other"]
