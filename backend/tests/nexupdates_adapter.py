"""nexupdates contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexupdates/`` run the module inside nextime: its configuration
(``services/updates.py``: the switch ``update_check`` in the settings, as before), its routes under ``/api/about`` and
its setup route ``POST /api/setup`` with the box "Nach neuen Versionen sehen". The database is the test session's
(``tests/conftest.py`` empties it before every test). Nothing reaches GitHub: the conftest of the contract tests puts a
stand-in on 127.0.0.1 in its place.

nextime has no bell (``has_bell = False``, U19). The operators' iPhones and iPads hear of a newer version through the
module's way (U24, ``has_devices = True``): ``devices_want`` → ``geraete.update_wanted``, ``on_devices`` →
``geraete.newer``. ``want_on_device`` pairs the operator's iPhone signed up for ``update`` and switches the kind;
``devices_told`` names the versions that reached it, opened with its key. Nothing reaches the relay: ``apppush.POST``
records.
"""

from __future__ import annotations

from typing import Any

from updates_contract import StandardAdapter


class Adapter(StandardAdapter):
    package = "app.vendor.nexupdates"
    prefix = "/api/about"
    also: tuple[str, ...] = ()
    has_bell = False
    #: The operators' iPhones and iPads (geraete, U24).
    has_devices = True

    def start(self) -> None:
        """nextime started as in production on the emptied test database. The operator and the member are made when
        first asked for (``client``): the setup route needs a server without accounts."""
        from fastapi.testclient import TestClient

        from app.config import get_settings
        from app.main import app
        from app.services import apppush

        self._main = TestClient(app, base_url="http://testserver")
        self._main.__enter__()
        self._clients: list[Any] = []
        self._accounts: dict[str, Any] = {}
        self._sent: list[dict[str, Any]] = []
        self._device: tuple[str, str, str] | None = None
        self._post, self._relay = apppush.POST, get_settings().push_relay

        def post(_url: str, body: dict[str, Any]) -> int:
            self._sent.append(body)
            return 200

        apppush.POST = post
        get_settings().push_relay = "https://push.example.com"
        self.restart()

    def stop(self) -> None:
        from app.config import get_settings
        from app.services import apppush, updates

        for client in self._clients:
            client.close()
        self._main.__exit__(None, None, None)
        apppush.POST = self._post
        get_settings().push_relay = self._relay
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

    def want_on_device(self, on: bool) -> None:
        """The operator's iPhone, signed up for ``update`` (paired the first time), with the kind on or off."""
        from app.db import SessionLocal
        from app.models import Account
        from app.services import appdevices, geraete
        from app.vendor.nexgeraete import push, seal

        account = self._account("operator")
        with SessionLocal() as db:
            row = db.get(Account, account.id)
            assert row is not None
            if self._device is None:
                private, public = seal.key_pair()
                ticket = f"operator-{account.id}"
                token, made = appdevices.make_device(db, row, "Boss's iPhone", "ios")
                found = appdevices.authenticate(db, token)
                push.register(geraete.Store(db), geraete.CONFIG, found.found, ticket, public, ["update"])
                self._device = (ticket, private, str(made.id))
            data = geraete.Store(db)
            device = data.device(self._device[2])
            assert device is not None
            data.set_kinds(device.id, {**device.kinds, "update": on})
            db.commit()

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

    def devices_told(self) -> list[str]:
        """The versions that reached the operator's iPhone, as tags (``v`` and the version it names)."""
        import json

        from app.services import geraete
        from app.vendor.nexgeraete import seal

        if self._device is None:
            return []
        ticket, private, _id = self._device
        found = []
        for body in self._sent:
            if body.get("ticket") != ticket:
                continue
            message = json.loads(seal.unseal(body["enc"], private, geraete.CONFIG.push_info))
            if message.get("type") == "update":
                found.append(f"v{message['version']}")
        return found
