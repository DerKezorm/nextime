"""nexgeraete contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexgeraete/`` run the module inside nextime: its start, its sessions and
operators, its ``Store`` over ``app_devices``, ``app_pairings``, ``app_codes`` (``services/geraete.py``), its own device
route ``GET /api/app/1/me``. The database is the one of the test session (``tests/conftest.py`` empties it before
every test). Nothing goes into the network: ``apppush.POST`` (what ``geraete.send`` and the nudges post with) is the
contract's ``Relay``; the module's clock is ``geraete.now``, which ``travel`` moves on.

The browser routes of the module are mounted under ``/api/geraete``: the old routes under ``/api`` keep their answers
of before (``routers/app_pairing.py``).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from geraete_contract import LegacyDevice, Made, Relay, StandardAdapter

ROLES = ("owner", "other", "operator")


class Adapter(StandardAdapter):
    package = "app.vendor.nexgeraete"
    app_prefix = "/api/app/1"
    browser_prefix = "/api/geraete"

    def start(self) -> None:
        from app.config import get_settings
        from app.db import SessionLocal
        from app.models import MEMBER, OPERATOR, Account
        from app.security import brake, hash_password
        from app.services import apppush, geraete

        brake.forget()
        self.relay = Relay()
        self._post, self._relay_url = apppush.POST, get_settings().push_relay
        apppush.POST = self.relay
        get_settings().push_relay = "https://push.example.com"
        self._offset = geraete.OFFSET
        self._paired: list[tuple[Any, str]] = []
        self._on_paired = geraete.on_paired

        def recorded(holder: Any, device: Any) -> None:
            self._paired.append((holder.id, device.name))
            self._on_paired(holder, device)

        geraete.on_paired = recorded
        self.ids: dict[str, int] = {}
        with SessionLocal() as db:
            for role in ROLES:
                row = Account(name=role, display_name=role.capitalize(), email=f"{role}@example.com",
                              email_confirmed=True, role=OPERATOR if role == "operator" else MEMBER,
                              password_hash=hash_password(secrets.token_urlsafe(16)))
                db.add(row)
                db.commit()
                self.ids[role] = row.id
        self.clients: dict[str, TestClient] = {}

    def stop(self) -> None:
        from app.config import get_settings
        from app.services import apppush, geraete

        for found in self.clients.values():
            found.close()
        apppush.POST = self._post
        get_settings().push_relay = self._relay_url
        geraete.OFFSET = self._offset
        geraete.on_paired = self._on_paired

    def restart(self) -> None:
        from app.db import engine
        from app.security import brake

        for found in self.clients.values():
            found.close()
        self.clients = {}
        engine.dispose()
        brake.forget()

    def config(self) -> Any:
        from app.services import geraete

        return geraete.CONFIG

    def client(self, role: str) -> TestClient:
        from app.db import SessionLocal
        from app.main import app
        from app.models import Account
        from app.security import SESSION_COOKIE, start_session

        if role not in self.clients:
            made = TestClient(app, base_url="https://kalender.example.com",
                              headers={} if role == "anonymous" else {"X-Nextime-Client": "tab-geraete"})
            if role != "anonymous":
                with SessionLocal() as db:
                    row = db.get(Account, self.ids[role])
                    assert row is not None
                    made.cookies.set(SESSION_COOKIE, start_session(db, row, "127.0.0.1", "contract"))
            self.clients[role] = made
        return self.clients[role]

    def account_id(self, role: str) -> Any:
        return self.ids[role]

    def legacy_devices(self) -> list[LegacyDevice]:
        """What 0.4.0 wrote (``services/appdevices.py`` before the module): a QR code or the sign-in page, a device
        brought in over iCloud, one with the silent nudge, one of the TestFlight app that shows notifications
        (``alerts``)."""
        return [
            LegacyDevice("QR code"),
            LegacyDevice("brought in over iCloud"),
            LegacyDevice("with push", push=True),
            LegacyDevice("with push and alerts", push=True),
        ]

    def make_legacy(self, spec: LegacyDevice, *, account: str = "owner") -> Made:
        """As 0.4.0's ``make_device`` and ``remember_push`` wrote them: no model, no way, no kinds."""
        from app.db import SessionLocal
        from app.models import AppDevice
        from app.services import geraete
        from app.vendor.nexgeraete import seal

        token = "ntd_" + secrets.token_urlsafe(32)
        made = Made(token)
        public = ""
        if spec.push:
            made.private_key, public = seal.key_pair()
            made.ticket = f"legacy-{secrets.token_urlsafe(6)}"
        when = geraete.now() - timedelta(days=3)
        # 0.4.0 kept the device that brought one in over iCloud as ``via_id``, nothing more.
        via = 999_999 if "iCloud" in spec.label else None
        with SessionLocal() as db:
            db.add(AppDevice(account_id=self.ids[account], name=spec.label, kind="ios",
                             token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(), via_id=via,
                             created_at=when, last_used_at=when, push_ticket=made.ticket or "", push_key=public,
                             push_alerts="alerts" in spec.label))
            db.commit()
        return made

    def probe(self, token: str | None, headers: dict[str, str] | None = None) -> Any:
        sent = dict(headers or {})
        if token is not None:
            sent["Authorization"] = f"Bearer {token}"
        return self.device_client().get(f"{self.app_prefix}/me", headers=sent)

    def database_text(self) -> bytes:
        from pathlib import Path

        from app.config import get_settings

        path = get_settings().database_path
        return b"".join(Path(str(path) + rest).read_bytes() for rest in ("", "-wal")
                        if Path(str(path) + rest).is_file())

    def _manage(self, action: str, account: str) -> None:
        from app.db import SessionLocal
        from app.services.konten_store import KontenStore
        from app.vendor.nexkonten import manage

        with SessionLocal() as db:
            getattr(manage, action)(KontenStore(db), self.ids[account], actor_id=self.ids["operator"],
                                    actor_confirmed=True)

    def block(self, account: str = "owner") -> None:
        self._manage("block", account)

    def _fresh_session(self, account: str) -> None:
        """Unblocking and bringing back end the browser sessions of before (``KontenStore.end_sessions``): the
        person signs in anew, the devices rested and work again."""
        found = self.clients.pop(account, None)
        if found is not None:
            found.close()

    def unblock(self, account: str = "owner") -> None:
        self._manage("unblock", account)
        self._fresh_session(account)

    def remove(self, account: str = "owner") -> None:
        self._manage("remove", account)

    def restore(self, account: str = "owner") -> None:
        self._manage("restore", account)
        self._fresh_session(account)

    def purge(self, account: str = "owner") -> None:
        from app.db import SessionLocal
        from app.models import Account, utcnow
        from app.services.konten_store import KontenStore
        from app.vendor.nexkonten import manage

        self.remove(account)
        with SessionLocal() as db:
            row = db.get(Account, self.ids[account])
            assert row is not None
            row.deleted_at = utcnow() - timedelta(days=manage.DELETE_DAYS + 1)
            db.commit()
        with SessionLocal() as db:
            assert manage.purge_due(KontenStore(db)) == [self.ids[account]]

    def owe_factor(self, account: str = "owner") -> None:
        from app.db import SessionLocal
        from app.services import konten_store, settings_service, totp

        with SessionLocal() as db:
            settings_service.save(db, {konten_store.SWITCH_FACTOR_REQUIRED: True})
        for role in ROLES:
            if role != account:
                with SessionLocal() as db:
                    from app.models import Account

                    row = db.get(Account, self.ids[role])
                    assert row is not None
                    row.totp_secret_enc = totp.seal_seed(totp.generate_seed())
                    db.commit()

    def switch(self, name: str, on: bool) -> None:
        from app.db import SessionLocal
        from app.services import geraete, settings_service

        with SessionLocal() as db:
            settings_service.save(db, {geraete.SWITCHES[name]: on})

    def travel(self, delta: timedelta) -> None:
        from app.services import geraete

        geraete.OFFSET += delta

    def tell(self, account: str, kind: str) -> None:
        """nextime's own kinds: the silent nudge (``changed``, as ``apppush.flush`` sends it) and a notification of
        changes in a shared calendar (``changes``)."""
        from app.db import SessionLocal
        from app.services import apppush, geraete
        from app.vendor.nexgeraete import push

        owner = self.ids[account]
        if kind == geraete.KIND_CHANGED:
            message: dict[str, Any] = {"type": "changed", "account": owner, "server": apppush._server_id()}
            quiet = True
        else:
            message = {"type": kind, "title": "Familie", "text": "Kim changed something", "account": owner}
            quiet = False
        with SessionLocal() as db:
            push.tell(geraete.Store(db), geraete.CONFIG, owner, kind, message, collapse=apppush.collapse(owner),
                      quiet=quiet, send=geraete.send)

    def announce(self, version: str) -> None:
        """The way ``updates`` tells the devices (``on_devices`` → ``geraete.newer``, U24), with the running version
        (``running``)."""
        from app.services import updates  # noqa: F401  (configures the module)
        from app.vendor import nexupdates

        told = nexupdates.current().on_devices
        assert told is not None
        told(version)

    def update_wanted(self) -> bool:
        """What ``updates`` asks before its daily look (``devices_want`` → ``geraete.update_wanted``, G56)."""
        from app.services import updates  # noqa: F401  (configures the module)
        from app.vendor import nexupdates

        wanted = nexupdates.current().devices_want
        assert wanted is not None
        return wanted()

    def running(self) -> str:
        from app import __version__

        return __version__

    def sweep(self) -> int:
        from app.db import SessionLocal
        from app.services import geraete
        from app.vendor.nexgeraete import devices

        with SessionLocal() as db:
            return devices.sweep(geraete.Store(db), geraete.CONFIG, geraete.now())

    def paired_mails(self) -> list[tuple[Any, str]]:
        return list(self._paired)
