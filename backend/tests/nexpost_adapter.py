"""nexpost contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexpost/`` run the module against nextime's real store
(``app.services.post_store``) over the operator's settings and the table ``mail_outbox``. The database is the one of
the test session (``tests/conftest.py`` empties it before every test).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from post_contract import Legacy, StandardAdapter, password


class Adapter(StandardAdapter):
    package = "app.vendor.nexpost"

    def start(self) -> None:
        from app.services import post_store

        post_store.configure()

    def stop(self) -> None:
        from app.vendor import nexpost

        nexpost.use_smtp(None)

    @contextmanager
    def store(self) -> Iterator[Any]:
        from app.db import SessionLocal
        from app.services.post_store import PostStore

        with SessionLocal() as db:
            yield PostStore(db)

    def raw_text(self) -> str:
        from sqlalchemy import text

        from app.db import engine

        parts: list[str] = []
        with engine.connect() as connection:
            for table in ("settings", "mail_outbox"):
                parts += [repr(tuple(row)) for row in connection.execute(text(f"SELECT * FROM {table}"))]  # noqa: S608
        return "\n".join(parts)

    def legacy(self) -> Legacy:
        """The mail server as the settings page of 0.4.0 saved it (``PUT /api/settings``: the keys ``smtp_*``, the
        password sealed with the server's key); 0.4.0 had no outbox."""
        from app.db import SessionLocal
        from app.security import encrypt_secret
        from app.services import settings_service
        from app.vendor.nexpost.model import MailSettings

        secret = password()
        with SessionLocal() as db:
            settings_service.save(db, {
                "smtp_host": "smtp.example.com",
                "smtp_port": 465,
                "smtp_security": "tls",
                "smtp_user": "notes@example.com",
                "smtp_password_enc": encrypt_secret(secret),
                "smtp_from": "notes@example.com",
            })
        return Legacy(
            settings=MailSettings(
                host="smtp.example.com",
                port=465,
                security="tls",
                user="notes@example.com",
                password=secret,
                sender="notes@example.com",
                sender_name="",
            ),
            queued=0,
        )

    def new_sign_in_letter(self, where: str, sender: str) -> Any:
        """Since 1.0.0: an account with a confirmed address signs in through ``POST /api/auth/login`` from a browser
        it does not know (User-Agent ``where``, address ``sender``); the letter nextime writes for it."""
        from fastapi.testclient import TestClient

        from app.db import SessionLocal
        from app.main import app
        from app.models import OPERATOR, Account
        from app.security import brake, hash_password
        from app.services import settings_service
        from app.vendor.nexpost import testing

        secret = password()
        with SessionLocal() as db:
            settings_service.save(db, {"smtp_host": "smtp.example.com", "smtp_from": "kalender@example.com",
                                       "public_url": "https://kalender.example.com"})
            db.add(Account(name="alex", display_name="Alex", email="alex@example.com", role=OPERATOR,
                           password_hash=hash_password(secret)))
            db.commit()
        brake.forget()
        with testing.catch_mail() as caught:
            browser = TestClient(app, base_url="https://kalender.example.com", client=(sender, 50000),
                                 headers={"X-Nextime-Client": "tab-contract", "User-Agent": where})
            answer = browser.post("/api/auth/login", json={"name": "alex", "password": secret})
            assert answer.status_code == 200, answer.text
        letters = [letter for letter in caught.letters if letter.kind == "new_sign_in"]
        assert letters, "no letter about the new sign-in"
        return letters[-1]


    # --- post 1.1.0 --------------------------------------------------------------------------------------------------

    mail_prefix = "/api/server/mail"

    def account(self, address: str, *, language: str, name: str, confirmed: bool) -> Any:
        """A new account with this address, language and name, committed; its id as ``PostStore.recipient`` takes
        it."""
        import secrets

        from app.db import SessionLocal
        from app.models import Account

        with SessionLocal() as db:
            row = Account(name=f"p{secrets.token_hex(4)}", display_name=name, email=address, language=language,
                          email_confirmed=confirmed, password_hash="")
            db.add(row)
            db.commit()
            return row.id

    def client(self, role: str) -> Any:
        """nextime's own TestClient, signed in as nobody, a member or the operator (the routes are mounted in
        ``main``)."""
        from app.models import MEMBER, OPERATOR
        from tests.conftest import make_account, new_client

        if role == "anonymous":
            return new_client()
        import secrets

        name = f"{role[:4]}{secrets.token_hex(3)}"
        return new_client(make_account(name, OPERATOR if role == "operator" else MEMBER))
