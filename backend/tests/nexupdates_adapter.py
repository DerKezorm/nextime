"""nexupdates contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexupdates/`` run the module inside nextime: its configuration
(``services/updates.py``: the switch ``update_check`` in the settings, as before), its routes under ``/api/about`` and
its setup route ``POST /api/setup`` with the box "Nach neuen Versionen sehen". The database is the test session's
(``tests/conftest.py`` empties it before every test). Nothing reaches GitHub: the conftest of the contract tests puts a
stand-in on 127.0.0.1 in its place.

nextime has no bell in the browser (U19). Since the shared module geraete 0.1.0 its ``on_newer`` tells the operators'
iPhones and iPads (G18, ``geraete.on_newer``): that is the bell here (``has_bell = True``). ``bell`` names the versions
``on_newer`` was told; ``bell_notices`` the notices that reached the operator's device (paired with the account, it
understands ``update``), opened with its key. Nothing reaches the relay: ``apppush.POST`` records.
"""

from __future__ import annotations

from typing import Any

from updates_contract import StandardAdapter


class Adapter(StandardAdapter):
    package = "app.vendor.nexupdates"
    prefix = "/api/about"
    also: tuple[str, ...] = ()
    #: The operators' devices (geraete G18).
    has_bell = True

    def start(self) -> None:
        """nextime started as in production on the emptied test database. The operator and the member are made when
        first asked for (``client``): the setup route needs a server without accounts."""
        from fastapi.testclient import TestClient

        from app.config import get_settings
        from app.main import app
        from app.services import apppush, geraete

        self._main = TestClient(app, base_url="http://testserver")
        self._main.__enter__()
        self._clients: list[Any] = []
        self._accounts: dict[str, Any] = {}
        self._told: list[str] = []
        self._sent: list[dict[str, Any]] = []
        self._devices: list[tuple[str, str, str]] = []
        self._post, self._relay = apppush.POST, get_settings().push_relay
        self._on_newer = geraete.on_newer

        def post(_url: str, body: dict[str, Any]) -> int:
            self._sent.append(body)
            return 200

        def on_newer(version: str, current: str | None = None) -> None:
            self._told.append(version)
            self._on_newer(version, current)

        apppush.POST = post
        get_settings().push_relay = "https://push.example.com"
        geraete.on_newer = on_newer
        self.restart()

    def stop(self) -> None:
        from app.config import get_settings
        from app.services import apppush, geraete, updates

        for client in self._clients:
            client.close()
        self._main.__exit__(None, None, None)
        apppush.POST = self._post
        get_settings().push_relay = self._relay
        geraete.on_newer = self._on_newer
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
            if role == "operator":
                self._pair(self._accounts[role])
        return self._accounts[role]

    def _pair(self, account: Any) -> None:
        """The operator's iPhone, signed up for ``update`` (G18)."""
        from app.db import SessionLocal
        from app.models import Account
        from app.services import appdevices, geraete
        from app.vendor.nexgeraete import push, seal

        private, public = seal.key_pair()
        ticket = f"operator-{account.id}"
        with SessionLocal() as db:
            row = db.get(Account, account.id)
            assert row is not None
            token, _device = appdevices.make_device(db, row, "Boss's iPhone", "ios")
            found = appdevices.authenticate(db, token)
            push.register(geraete.Store(db), geraete.CONFIG, found.found, ticket, public, ["update"])
            self._devices.append((ticket, private, geraete.language_of(row)))

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
        return list(self._told)

    def bell_notices(self) -> list[tuple[str, str, str]]:
        import json

        from app.services import geraete
        from app.vendor.nexgeraete import seal

        found = []
        for ticket, private, language in self._devices:
            for body in self._sent:
                if body.get("ticket") != ticket:
                    continue
                message = json.loads(seal.unseal(body["enc"], private, geraete.CONFIG.push_info))
                version = next(told for told in self._told if told.removeprefix("v") == message["version"])
                found.append((language, version, message["title"]))
        return found
