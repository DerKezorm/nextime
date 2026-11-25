"""nexoidc contract tests: nextime' adapter (copied once from the module's adapter template, from then on
nextime' own file).

The contract tests in ``tests/nexoidc/`` are the same in every nex app and never edited here. They reach nextime
only through this class: its real routes, its database, its accounts, invitations and second factor. The test run's
own data directory and the cleaning between tests come from ``tests/conftest.py``. Passwords and seeds are made at
run time; none is written into this file.
"""

from __future__ import annotations

import secrets
import time
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

import httpx
from fastapi.testclient import TestClient
from oidc_contract import StandardAdapter
from sqlalchemy import select

from app.db import SessionLocal
from app.deps import normal_address
from app.main import app
from app.models import MEMBER, OPERATOR, SIGN_IN_OIDC, SIGN_IN_PASSWORD, Account
from app.routers.auth import PENDING_COOKIE
from app.security import Brake, brake, hash_password
from app.services import accounts, oidc_store, settings_service, totp

OPERATOR_NAME = "operator"
#: nextime has two roles: an invitation with the rights "editor" of the tests brings the role operator, "member" the
#: role member.
ROLE_OF = {"member": MEMBER, "editor": OPERATOR}


class Adapter(StandardAdapter):
    package = "app.vendor.nexoidc"
    api = "/api"
    single_account = False
    only_authentik = False
    has_second_factor = True
    #: nextime was born with the provider list: no older single provider, no way back.
    legacy_model = "none"
    #: No directory service to couple to.
    supports_coupling = False
    old_callback_paths: tuple[str, ...] = ()
    operator_name = OPERATOR_NAME
    #: The own account lives in the settings (tab "My account").
    account_page = "/account"

    def start(self) -> None:
        # tests/conftest.py emptied the database before this test.
        oidc_store.configure()
        self._operator: TestClient | None = None
        self._seeds: dict[str, str] = {}
        self.make_account(OPERATOR_NAME, role=OPERATOR)

    def stop(self) -> None:
        pass

    def browser(self) -> TestClient:
        # As the interface does: every request names its tab (changes are refused without it).
        return TestClient(app, base_url="http://testserver", follow_redirects=False,
                          headers={"X-Nextime-Client": "tab-" + secrets.token_hex(6)})

    def operator(self) -> TestClient:
        if self._operator is None:
            self._operator = self.browser()
            self.sign_in_with_password(self._operator, OPERATOR_NAME)
        return self._operator

    def make_account(self, name: str, *, password: bool = True, second_factor: bool = False,
                     role: str = "member") -> int:
        with SessionLocal() as db:
            row = Account(name=name, role=role, sign_in=SIGN_IN_PASSWORD if password else SIGN_IN_OIDC,
                          password_hash=hash_password(self.password_for(name)) if password else "")
            if second_factor:
                seed = totp.generate_seed()
                self._seeds[name] = seed
                row.totp_secret_enc = totp.seal_seed(seed)
            db.add(row)
            db.commit()
            return row.id

    def sign_in_with_password(self, client: Any, name: str) -> None:
        answer = client.post("/api/auth/login", json={"name": name, "password": self.password_for(name)})
        assert answer.status_code == 200, answer.text
        if answer.json().get("second_factor"):
            # A code of the step after the last one taken, so that no two sign-ins in one step collide.
            code = totp.code_at(self._seeds[name], time.time() + 30 * self._codes_used(name))
            done = client.post("/api/auth/login/totp", json={"code": code})
            assert done.status_code == 200, done.text

    def _codes_used(self, name: str) -> int:
        with SessionLocal() as db:
            row = accounts.by_name(db, name)
            assert row is not None
            return 1 if row.totp_last_step else 0

    def who(self, client: Any) -> str | None:
        answer = client.get("/api/auth/me")
        return answer.json()["name"] if answer.status_code == 200 else None

    def second_factor_asked(self, client: Any, response: httpx.Response) -> bool:
        waiting = client.cookies.get(PENDING_COOKIE)
        return (
            response.headers.get("location", "") == "/login?step=code"
            and waiting is not None
            and totp.get_pending(waiting) is not None
        )

    def make_invite(self, rights: str) -> str:
        with SessionLocal() as db:
            operator = accounts.by_name(db, OPERATOR_NAME)
            assert operator is not None
            _invite, token = accounts.create_invite(db, operator, days=7, role=ROLE_OF[rights])
        return token

    def rights_of(self, name: str) -> str | None:
        with SessionLocal() as db:
            row = accounts.by_name(db, name)
            if row is None:
                return None
            return "editor" if row.role == OPERATOR else "member"

    def account_names(self) -> list[str]:
        with SessionLocal() as db:
            return sorted(db.scalars(select(Account.name)))

    def store(self) -> AbstractContextManager[Any]:
        @contextmanager
        def opened() -> Iterator[oidc_store.SqlStore]:
            with SessionLocal() as db:
                yield oidc_store.SqlStore(db)

        return opened()

    def set_public_url(self, url: str) -> None:
        with SessionLocal() as db:
            settings_service.save(db, {"public_url": url})

    def read_public_url(self) -> str:
        # The setting as a restart would find it (the environment variable is none of the test's).
        with SessionLocal() as db:
            return str(settings_service.get(db, "public_url") or "")

    def save_public_url_route(self, url: str, *, confirm: bool = False) -> httpx.Response:
        # Card 1 of the interface: the settings route that saves the address with the other settings. It asks first
        # (409 ``public_url_change``) and saves nothing until the operator confirms.
        return self.operator().put("/api/settings", json={"public_url": url, "confirm_address": confirm})

    def delete_account(self, name: str) -> None:
        # The operator's route, as the accounts list calls it (the operator's own password again).
        with SessionLocal() as db:
            row = accounts.by_name(db, name)
            assert row is not None
            account_id = row.id
        answer = self.operator().request(
            "DELETE", f"/api/accounts/{account_id}", json={"current_password": self.password_for(OPERATOR_NAME)}
        )
        assert answer.status_code == 204, answer.text

    def trip_throttle(self, client: Any) -> None:
        # As many failed runs at the provider from this sender as the brake lets pass (every TestClient is the
        # sender "testclient"): the next start, return and linking wait.
        key = "oidc:" + normal_address("testclient")
        for _ in range(Brake.FREE):
            brake.failed(key)

    def operator_unlink(self, client: Any, account_id: int, provider_id: int, password: str) -> httpx.Response:
        # nextime' acts on another account carry the operator's password as ``current_password``.
        path = self.path("operator_unlink", account=account_id, provider=provider_id)
        return client.request("DELETE", path, json={"current_password": password})
