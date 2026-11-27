"""nexwasneu contract tests: nextime's adapter (copied once from the module's template, now nextime's file).

The contract tests in ``backend/tests/nexwasneu/`` run the module inside nextime: its configuration
(``services/whatsnew.py``), its routes under ``/api/whats-new``, the route of before ``POST /api/me/whats-new/seen``
and ``/api/auth/me`` with ``whats_new_seen``. The database is the one of the test session (``tests/conftest.py``
empties it before every test). The guard reads nextime's own texts in ``app/whatsnew/``.
"""

from __future__ import annotations

import itertools
from typing import Any

from wasneu_contract import StandardAdapter

ROLES = ("operator", "member")
_names = itertools.count()


class Adapter(StandardAdapter):
    package = "app.vendor.nexwasneu"
    root_package = "app"
    prefix = "/api/whats-new"
    old_seen: tuple[str, ...] = ("/api/me/whats-new/seen",)
    me = "/api/auth/me"
    me_field = "whats_new_seen"

    def start(self) -> None:
        from app.db import SessionLocal
        from app.models import MEMBER, OPERATOR, Account
        from app.security import hash_password
        from app.services import whatsnew

        whatsnew.configure()
        self.ids: dict[str, int] = {}
        self.clients: list[Any] = []
        with SessionLocal() as db:
            for role in ROLES:
                row = Account(name=f"wasneu-{role}", display_name=role.capitalize(), email=f"{role}@example.com",
                              role=OPERATOR if role == "operator" else MEMBER,
                              password_hash=hash_password("correct horse battery"))
                db.add(row)
                db.commit()
                self.ids[role] = row.id

    def stop(self) -> None:
        from app.services import whatsnew

        for found in self.clients:
            found.close()
        whatsnew.configure()

    def client(self, role: str) -> Any:
        from fastapi.testclient import TestClient

        from app.db import SessionLocal
        from app.main import app
        from app.models import Account
        from app.security import SESSION_COOKIE, start_session

        made = TestClient(app, base_url="https://kalender.example.com", headers={"X-Nextime-Client": "tab-wasneu0"})
        if role in self.ids:
            with SessionLocal() as db:
                row = db.get(Account, self.ids[role])
                assert row is not None
                made.cookies.set(SESSION_COOKIE, start_session(db, row, "127.0.0.1", "contract"))
        self.clients.append(made)
        return made

    def stored_seen(self, role: str) -> str:
        from app.db import SessionLocal
        from app.models import Account

        with SessionLocal() as db:
            row = db.get(Account, self.ids[role])
            assert row is not None
            return row.whats_new_seen or ""

    def set_seen(self, role: str, version: str) -> None:
        from app.db import SessionLocal
        from app.models import Account

        with SessionLocal() as db:
            row = db.get(Account, self.ids[role])
            assert row is not None
            row.whats_new_seen = version
            db.commit()

    def new_account_seen(self) -> str:
        """As the operator's list or an invitation makes one (``accounts.create_with_password``)."""
        from app.db import SessionLocal
        from app.services import accounts

        with SessionLocal() as db:
            row = accounts.create_with_password(db, "correct horse battery", display_name=f"Neu {next(_names)}")
            return row.whats_new_seen or ""
