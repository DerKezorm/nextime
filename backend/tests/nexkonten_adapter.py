"""nexkonten contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexkonten/`` run the module's flows against nextime's real ``Store``
(``app.services.konten_store``) over nextime's real tables, and sign in through nextime's own route
``POST /api/auth/login`` with its field names (``name``, ``password``).

The database is the one of the test session (``tests/conftest.py`` empties it before every test and runs ``init_db``).
``legacy()`` loads the database that 0.4.0 itself built (``tests/bestand``).

⚠️ **What nextime does not have is said, not faked:** nextime's invitations are used by one person each
(``reusable_invites``), and a second factor is required only by the operator's switch for everybody
(``two_factor_required``, the contract's ``switch``), never of one account alone (``required_second_factor``): the
contract passes over those parts. An invitation that fixes a username is skipped with its reason.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from konten_contract import Legacy, StandardAdapter, png

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "bestand"))


class Adapter(StandardAdapter):
    package = "app.vendor.nexkonten"
    #: 10 wrong passwords, then 15 minutes (``security.MAX_FAILURES``, ``LOCK_MINUTES``).
    lock_after: int | None = 10
    #: What nextime has (since 0.3.2 the contract passes over the rest instead of skipping it).
    blocking: bool = True
    required_second_factor: bool = False
    reusable_invites: bool = False

    def start(self) -> None:
        from app.security import brake
        from app.services import konten_store, totp

        konten_store.configure()
        konten_store.PICTURE_IN_THREAD = False
        brake.forget()
        totp.forget()

    def stop(self) -> None:
        from app.db import engine
        from app.services import konten_store

        konten_store.PICTURE_IN_THREAD = True
        engine.dispose()

    @contextmanager
    def store(self) -> Iterator[Any]:
        from app.db import SessionLocal
        from app.services.konten_store import KontenStore

        with SessionLocal() as db:
            yield KontenStore(db)

    def seed(
        self,
        username: str,
        *,
        display_name: str = "",
        email: str = "",
        email_confirmed: bool = True,
        email_proven: bool = False,
        email_source: str = "",
        password: str | None = None,
        operator: bool = False,
        blocked: bool = False,
        picture: bool = False,
        second_factor: str = "none",
    ) -> Any:
        from app.db import SessionLocal
        from app.models import MEMBER, OPERATOR, SIGN_IN_OIDC, SIGN_IN_PASSWORD, Account, utcnow
        from app.security import hash_password
        from app.services import totp

        with SessionLocal() as db:
            row = Account(
                name=username,
                display_name=display_name,
                email=email,
                email_confirmed=email_confirmed,
                email_proven=email_proven,
                email_source=email_source,
                role=OPERATOR if operator else MEMBER,
                sign_in=SIGN_IN_PASSWORD if password else SIGN_IN_OIDC,
                password_hash=hash_password(password) if password else "",
                blocked_at=utcnow() if blocked else None,
                avatar=png() if picture else None,
                avatar_at=utcnow() if picture else None,
                totp_secret_enc=totp.seal_seed(totp.generate_seed()) if second_factor == "active" else "",
            )
            db.add(row)
            db.commit()
            return row.id

    def _operator_id(self) -> int:
        from sqlalchemy import select

        from app.db import SessionLocal
        from app.models import OPERATOR, Account

        with SessionLocal() as db:
            found = db.scalar(select(Account.id).where(Account.role == OPERATOR).order_by(Account.id).limit(1))
        return found if found is not None else self.seed("first-operator", operator=True, password="x" * 16)

    def make_invite(
        self,
        *,
        email: str = "",
        display_name: str = "",
        username: str = "",
        expired: bool = False,
        reusable: bool = False,
    ) -> str:
        """As ``POST /api/invites`` makes it (``accounts.create_invite``)."""
        if username:
            pytest.skip("nextime's invitations never fixed a username")
        from app.db import SessionLocal
        from app.models import Account, utcnow
        from app.services import accounts

        with SessionLocal() as db:
            by = db.get(Account, self._operator_id())
            assert by is not None
            invite, token = accounts.create_invite(db, by, days=7, email=email)
            invite.display_name = display_name
            if expired:
                invite.expires_at = utcnow() - timedelta(minutes=5)
            db.commit()
        return token

    def open_session(self, account_id: Any, kind: str = "browser") -> Any:
        """``browser`` a browser session, ``waiting`` a sign-in waiting for its code (``totp.start_pending``),
        ``device`` a paired iPhone (``app_devices``), ``program`` an API key, ``calendar`` a calendar program with
        its device password (``devices``), ``known_browser`` the mark in the device cookie."""
        from app.db import SessionLocal
        from app.models import Account
        from app.security import server_key, start_session
        from app.services import apikeys, appdevices, devices, totp
        from app.services.konten_store import KontenStore
        from app.vendor.nexkonten import sessions

        with SessionLocal() as db:
            row = db.get(Account, account_id)
            assert row is not None
            if kind == "browser":
                return ("browser", start_session(db, row, "127.0.0.1", "contract"))
            if kind == "waiting":
                return ("waiting", totp.start_pending(row.id))
            if kind == "device":
                _token, device_row = appdevices.make_device(db, row, "Probe", "ios")
                return ("app", device_row.id)
            if kind == "program":
                key_row, _token = apikeys.make(db, row, name="Probe", level="read", mcp=False, calendar_ids=None,
                                              days=None)
                db.commit()
                return ("key", key_row.id)
            if kind == "calendar":
                calendar_row, _password, _setup = devices.create(db, row, "Probe")
                db.commit()
                return ("calendar", calendar_row.id)
            if kind == "known_browser":
                store = KontenStore(db)
                mark = sessions.mark(server_key(), store.device_key(row.id))
                db.commit()
                return ("known", row.id, sessions.add_mark("", mark))
        raise ValueError(kind)

    def session_alive(self, handle: Any) -> bool:
        if handle is None:
            return False
        from app.db import SessionLocal
        from app.models import Account, ApiKey, AppDevice, Device
        from app.security import server_key, session_account
        from app.services import totp
        from app.vendor.nexkonten import sessions

        with SessionLocal() as db:
            if handle[0] == "browser":
                return session_account(db, handle[1]) is not None
            if handle[0] == "waiting":
                return totp.get_pending(handle[1]) is not None
            if handle[0] == "app":
                return db.get(AppDevice, handle[1]) is not None
            if handle[0] == "key":
                return db.get(ApiKey, handle[1]) is not None
            if handle[0] == "calendar":
                return db.get(Device, handle[1]) is not None
            if handle[0] == "known":
                row = db.get(Account, handle[1])
                return bool(row and row.device_key) and sessions.is_known(handle[2], server_key(), row.device_key)
        raise ValueError(handle)

    def switch(self, name: str, on: bool) -> None:
        from app.db import SessionLocal
        from app.services import konten_store, settings_service

        key = {"factor_required": konten_store.SWITCH_FACTOR_REQUIRED, "passkeys_allowed": konten_store.SWITCH_PASSKEYS}
        with SessionLocal() as db:
            settings_service.save(db, {key[name]: on})

    def delete_account(self, account_id: Any) -> None:
        """nextime deletes to a bin first (30 days), as the operator's list does."""
        from app.db import SessionLocal
        from app.models import Account
        from app.services import accounts

        with SessionLocal() as db:
            row = db.get(Account, account_id)
            assert row is not None
            accounts.delete_account(db, row)

    def raw_text(self) -> str:
        from sqlalchemy import text

        from app.db import engine

        parts: list[str] = []
        with engine.connect() as connection:
            for table in ("accounts", "invites", "password_resets", "email_changes", "former_names", "auth_sessions"):
                parts += [repr(tuple(row)) for row in connection.execute(text(f"SELECT * FROM {table}"))]  # noqa: S608
        return "\n".join(parts)

    def legacy(self) -> Legacy:
        """The database 0.4.0 built (``tests/bestand``), then nextime's start (``init_db``)."""
        from bauen import dump_datei, manifest_datei, totp_geheimnis

        from app.config import get_settings
        from app.db import engine, init_db

        engine.dispose()
        target = get_settings().database_path
        for rest in ("", "-wal", "-shm"):
            Path(str(target) + rest).unlink(missing_ok=True)
        connection = sqlite3.connect(str(target))
        try:
            connection.executescript(gzip.decompress(dump_datei().read_bytes()).decode("utf-8"))
            connection.execute("UPDATE auth_sessions SET expires_at = datetime('now', '+30 days')")
            connection.execute("UPDATE invites SET expires_at = datetime('now', '+30 days')")
            connection.execute("UPDATE password_resets SET expires_at = datetime('now', '+30 days')")
            connection.commit()
        finally:
            connection.close()
        init_db()

        manifest = json.loads(manifest_datei().read_text(encoding="utf-8"))
        self.manifest = manifest
        count: dict[str, int] = {}
        for entry in manifest["konten"]:
            if entry["email"]:
                count[entry["email"].lower()] = count.get(entry["email"].lower(), 0) + 1
        logins: list[tuple[str, str, bool]] = []
        for entry in manifest["konten"]:
            name, secret, address = entry["name"], entry.get("passwort") or "", entry["email"]
            if not secret:
                logins.append((name, "", False))
                continue
            open_ = not entry.get("gesperrt") and not entry.get("geloescht")
            logins += [(name, secret, open_), (name.upper(), secret, open_)]
            if address:
                logins.append((address.upper(), secret, open_ and count[address.lower()] == 1))
        subjects = [entry["anbieter"]["sub"] for entry in manifest["konten"] if entry.get("anbieter")]
        return Legacy(
            logins=logins,
            invites=[manifest["offene_einladung"]["schluessel"]],
            fixed_names={},
            provider_logins=[(sub, True) for sub in subjects] + [("bestand-nobody", False)],
            factors=[
                (entry["name"], totp_geheimnis(entry["zweiter_faktor"]), list(entry["wiederherstellungscodes"]))
                for entry in manifest["konten"]
                if entry.get("zweiter_faktor")
            ],
        )

    @contextmanager
    def oidc_store(self) -> Iterator[Any]:
        from app.db import SessionLocal
        from app.services.oidc_store import SqlStore

        with SessionLocal() as db:
            yield SqlStore(db)

    def provider_sign_in(self, subject: str) -> Any:
        """Through nextime's OIDC routes and the fake provider of ``tests/nexoidc``, with the provider the database of
        0.4.0 holds (``tests/bestand``)."""
        from urllib.parse import urlencode, urlsplit

        from fastapi.testclient import TestClient

        sys.path.insert(0, str(HERE / "nexoidc"))
        import oidc_fakes as fakes
        from bauen import TAB, manifest_datei

        from app.main import app
        from app.security import brake
        from app.vendor import nexoidc
        from app.vendor.nexoidc import attempt, protocol

        manifest = getattr(self, "manifest", None) or json.loads(manifest_datei().read_text(encoding="utf-8"))
        data = manifest["anbieter"]
        person = next(
            (entry["anbieter"] for entry in manifest["konten"] if entry.get("anbieter", {}).get("sub") == subject),
            {"sub": subject, "preferred_username": "nobody", "name": "", "email": ""},
        )
        network = fakes.Network()
        nexoidc.use_transport(network.transport())
        protocol.clear_caches()
        attempt.forget_used_states()
        brake.forget()
        try:
            fake = network.add(fakes.FakeProvider(data["issuer"], client_id=data["client_id"],
                                                  client_secret=data["client_geheimnis"]))
            fake.person = dict(person)
            browser = TestClient(app, base_url=manifest["oeffentliche_adresse"], follow_redirects=False, headers=TAB)
            start = browser.get(f"/api/oidc/{data['kuerzel']}/start")
            target = start.headers.get("location", "")
            path = urlsplit(fakes.query_of(target)["redirect_uri"]).path
            back = browser.get(f"{path}?{urlencode(fake.authorize(target))}")
            if "error=" in back.headers.get("location", ""):
                return None
            me = browser.get("/api/auth/me")
            return me.json()["id"] if me.status_code == 200 else None
        finally:
            nexoidc.use_transport(None)
            protocol.clear_caches()
            attempt.forget_used_states()

    def sign_in(self, typed: str, password: str) -> Any:
        """Through ``POST /api/auth/login`` (field ``name``) up to the session or the code of the second factor. The
        brake is emptied first: it counts in memory and would answer 429 in a test that tries many."""
        from fastapi.testclient import TestClient

        from app.main import app
        from app.security import brake

        brake.forget()
        browser = TestClient(app, base_url="https://kalender.example.com",
                             headers={"X-Nextime-Client": "tab-contract"})
        answer = browser.post("/api/auth/login", json={"name": typed, "password": password})
        if answer.status_code != 200:
            return None
        package = __import__(self.package, fromlist=["login"])
        with self.store() as store:
            account = package.login.resolve_login(store, typed)
        return account.id if account is not None else None

    def sign_in_from(self, typed: str, password: str, sender: str) -> tuple[int, Any]:
        """Since 0.3.10: ``POST /api/auth/login`` from the address ``sender`` (nextime's brake counts it), the brake
        left as it is: ``(status, account id)``."""
        from fastapi.testclient import TestClient

        from app.main import app

        browser = TestClient(app, base_url="https://kalender.example.com", client=(sender, 50000),
                             headers={"X-Nextime-Client": "tab-contract"})
        answer = browser.post("/api/auth/login", json={"name": typed, "password": password})
        if answer.status_code != 200:
            return answer.status_code, None
        package = __import__(self.package, fromlist=["login"])
        with self.store() as store:
            account = package.login.resolve_login(store, typed)
        return 200, account.id if account is not None else None

    def listed_invites(self) -> list[dict[str, Any]]:
        """Since 0.3.10: ``GET /api/invites`` as the operator's list asks it (expired ones stay until withdrawn)."""
        from fastapi.testclient import TestClient

        from app.db import SessionLocal
        from app.main import app
        from app.models import Account
        from app.security import SESSION_COOKIE, start_session

        with SessionLocal() as db:
            row = db.get(Account, self._operator_id())
            assert row is not None
            token = start_session(db, row, "127.0.0.1", "contract")
        browser = TestClient(app, base_url="https://kalender.example.com", headers={"X-Nextime-Client": "tab-contract"})
        browser.cookies.set(SESSION_COOKIE, token)
        answer = browser.get("/api/invites")
        assert answer.status_code == 200, answer.text
        return [{"email": entry["email"], "expired": entry["expired"]} for entry in answer.json()]

    def forgot_through_route(self, typed: str, *, mail_fails: bool) -> tuple[list[str], int]:
        """Since 1.0.0: ``POST /api/auth/forgot`` with a mail server and the public address set, the work after the
        answer run at once: ``(addresses mailed at once, new letters in the outbox)``."""
        from fastapi.testclient import TestClient
        from sqlalchemy import func, select

        from app.db import SessionLocal
        from app.main import app
        from app.models_accounts import MailOutbox
        from app.routers import auth as auth_router
        from app.security import brake
        from app.services import settings_service
        from app.vendor.nexpost import testing

        with SessionLocal() as db:
            settings_service.save(db, {"smtp_host": "smtp.example.com", "smtp_from": "kalender@example.com",
                                       "public_url": "https://kalender.example.com"})
            before = int(db.scalar(select(func.count()).select_from(MailOutbox)) or 0)
        brake.forget()
        at_once, auth_router.FORGOT_AT_ONCE = auth_router.FORGOT_AT_ONCE, True
        try:
            with testing.catch_mail(fail=mail_fails) as caught:
                browser = TestClient(app, base_url="https://kalender.example.com",
                                     headers={"X-Nextime-Client": "tab-contract"})
                answer = browser.post("/api/auth/forgot", json={"name": typed})
                assert answer.status_code == 202, answer.text
        finally:
            auth_router.FORGOT_AT_ONCE = at_once
        with SessionLocal() as db:
            after = int(db.scalar(select(func.count()).select_from(MailOutbox)) or 0)
        return [letter.to for letter in caught.sent], after - before

    def picture_status(self, owner: Any, viewer: Any | None) -> int:
        """Since 1.0.0: ``GET /api/avatars/<owner>``, signed in as ``viewer`` (None: nobody)."""
        from fastapi.testclient import TestClient

        from app.db import SessionLocal
        from app.main import app
        from app.models import Account
        from app.security import SESSION_COOKIE, start_session

        browser = TestClient(app, base_url="https://kalender.example.com", headers={"X-Nextime-Client": "tab-contract"})
        if viewer is not None:
            with SessionLocal() as db:
                row = db.get(Account, viewer)
                assert row is not None
                browser.cookies.set(SESSION_COOKIE, start_session(db, row, "127.0.0.1", "contract"))
        return browser.get(f"/api/avatars/{owner}").status_code


    # --- konten 1.1.0 ------------------------------------------------------------------------------------------------

    def age_deletion(self, account_id: Any, days: int) -> None:
        """The account was deleted ``days`` days earlier than it was."""
        from app.db import SessionLocal
        from app.models import Account

        with SessionLocal() as db:
            row = db.get(Account, account_id)
            assert row is not None and row.deleted_at is not None
            row.deleted_at = row.deleted_at - timedelta(days=days)
            db.commit()

    def provider_active(self, on: bool) -> None:
        """One provider of the module oidc's table, enabled or not (nextime counts ``oidc_providers.enabled``)."""
        from sqlalchemy import delete

        from app.db import SessionLocal
        from app.models import OidcProvider

        with SessionLocal() as db:
            db.execute(delete(OidcProvider).where(OidcProvider.slug == "contract"))
            db.add(OidcProvider(slug="contract", label="Contract", issuer="https://auth.example.com/", client_id="c",
                                enabled=on))
            db.commit()

    def readiness(self, operator: Any) -> dict[str, Any]:
        """``GET /api/server/sign-in/readiness`` over https, signed in as the operator (a session of its own)."""
        from fastapi.testclient import TestClient

        from app.db import SessionLocal
        from app.main import app
        from app.models import Account
        from app.security import SESSION_COOKIE, start_session

        with SessionLocal() as db:
            row = db.get(Account, operator)
            assert row is not None
            token = start_session(db, row, "127.0.0.1", "contract")
        browser = TestClient(app, base_url="https://time.example.com", headers={"X-Nextime-Client": "tab-contract"})
        browser.cookies.set(SESSION_COOKIE, token)
        answer = browser.get("/api/server/sign-in/readiness")
        assert answer.status_code == 200, answer.text
        return dict(answer.json())
