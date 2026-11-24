"""Every test run gets its own empty data directory; nothing touches ``data/`` of the project."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

_DATA = tempfile.mkdtemp(prefix="nextime-tests-")
os.environ["NEXTIME_DATA_DIR"] = _DATA
os.environ["NEXTIME_DISABLE_BACKGROUND"] = "1"
os.environ["NEXTIME_FRONTEND_DIST"] = os.path.join(_DATA, "no-frontend")
# Set, not removed: a value in the environment wins over a .env file in the project, a removed one does not.
os.environ["NEXTIME_LOCALES_DIR"] = os.path.join(_DATA, "locales")
os.environ["NEXTIME_LOG_LEVEL"] = ""
os.environ["NEXTIME_API_DOCS"] = "false"
# Argon2 as cheap as it goes: the tests make many accounts. The strength itself is Argon2's business.
os.environ["NEXTIME_ARGON2_TIME"] = "1"
os.environ["NEXTIME_ARGON2_MEMORY_KIB"] = "1024"
os.environ["NEXTIME_ARGON2_PARALLELISM"] = "1"
# Never GitHub from a test: port 9 refuses at once.
os.environ["NEXTIME_UPDATE_URL"] = "http://127.0.0.1:9/releases/latest"
os.environ["NEXTIME_SECRET_KEY"] = "test-secret-key-for-the-test-run-only"
SETUP_CODE = "test-setup-code"
os.environ["NEXTIME_SETUP_TOKEN"] = SETUP_CODE
# Never the internet from a test: the state box asks this, port 9 refuses at once.
os.environ["NEXTIME_INTERNET_PROBE_URL"] = "http://127.0.0.1:9/"
# The calendar account over plain HTTP: the test server of the CalDAV tests speaks HTTP (TIME-20 EC-21 switches it
# off where it checks the refusal).
os.environ["NEXTIME_DAV_ALLOW_HTTP"] = "1"
# Never the address service or the map tiles from a test (TIME-8): the tests put a transport of their own.
os.environ["NEXTIME_PLACES_URL"] = "https://photon.example.com/api/"
os.environ["NEXTIME_TILES_URL"] = "https://tiles.example.com/{z}/{x}/{y}.png"
# The tests record what would go to the push relay (``apppush.POST``); nothing reaches the real one.
os.environ["NEXTIME_PUSH_RELAY"] = "https://push.example.com"

import shutil  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402

from app import db as db_module  # noqa: E402
from app import main as main_module  # noqa: E402
from app.db import SessionLocal, init_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import MEMBER, OPERATOR, Account, Base, Setting  # noqa: E402
from app.routers import health  # noqa: E402
from app.security import SESSION_COOKIE, brake, hash_password, start_session  # noqa: E402
from app.services import apibrake, internet, nexpaper, nextasks, places, reminders, storage, totp, updates  # noqa: E402

DATA_DIR = _DATA
#: The attachments folder (``services/backups.media_root``).
MEDIA = Path(_DATA) / "attachments"
TAB = {"X-Nextime-Client": "tab-tests000"}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """A test that measures time against a limit (``@pytest.mark.timing``) says nothing beside nine other workers
    using the machine: in a parallel run (pytest-xdist) it is skipped, ``pytest -m timing`` runs them on their own."""
    if not os.environ.get("PYTEST_XDIST_WORKER"):
        return
    skip = pytest.mark.skip(reason="timing: runs on its own after the parallel run (pytest -m timing)")
    for item in items:
        if "timing" in item.keywords:
            item.add_marker(skip)


def _schema_version() -> int:
    """SQLite's count of changes to the schema (``PRAGMA schema_version``): any table, column or index made, changed
    or dropped, and another database file in its place, show as another number."""
    with db_module.engine.connect() as connection:
        return int(connection.exec_driver_sql("PRAGMA schema_version").scalar() or 0)


def once_per_schema(function):
    """``function`` checks and makes the schema; it runs again only when the schema changed since it last ran. Every
    test starts the app anew (its own ``TestClient``), and the start checked every table and column each time: about
    a second per test, the larger part of a full run. A test that changes the schema, or puts another database in
    place (a restored backup), still gets the real check. Called with arguments (a session), it always runs."""
    seen: dict[str, int | None] = {"version": None}

    def wrapper(*args, **kwargs):
        if args or kwargs:
            return function(*args, **kwargs)
        if seen["version"] is not None and seen["version"] == _schema_version():
            return None
        result = function()
        seen["version"] = _schema_version()
        return result

    wrapper.__wrapped__ = function
    return wrapper


# The checks of the start (``main.lifespan``: tables, columns, indexes).
main_module.init_db = once_per_schema(main_module.init_db)


@pytest.fixture(scope="session", autouse=True)
def schema() -> None:
    init_db()


@pytest.fixture(autouse=True)
def clean_db(schema: None) -> Iterator[None]:
    with SessionLocal() as db:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(delete(table))
        db.execute(delete(Setting))
        db.commit()
    assert MEDIA.resolve().is_relative_to(Path(_DATA).resolve())
    shutil.rmtree(MEDIA, ignore_errors=True)
    MEDIA.mkdir(parents=True)
    # Backups of the test before are none of this one's.
    shutil.rmtree(Path(_DATA) / "backups", ignore_errors=True)
    brake.forget()
    totp.forget()
    updates.forget()
    internet.forget()
    internet.probe_override = None
    health.forget()
    storage.forget_footprint()
    reminders.forget()
    places.forget()
    apibrake.forget()
    nextasks.forget()
    nexpaper.forget()
    yield
    app.dependency_overrides.clear()


PASSWORD = "correct horse battery"


def make_account(name: str, role: str = MEMBER, password: str = PASSWORD, email: str | None = None) -> Account:
    """An account with a password, straight into the database; its address ``<name>@example.com`` unless given."""
    with SessionLocal() as db:
        row = Account(name=name, display_name=name.capitalize(),
                      email=f"{name}@example.com" if email is None else email, role=role,
                      password_hash=hash_password(password))
        db.add(row)
        db.commit()
        db.expunge(row)
    return row


def sign_in(client: TestClient, account: Account) -> None:
    """The client carries a session of ``account`` from now on (a real one, as after signing in)."""
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        token = start_session(db, row, "127.0.0.1", "tests")
    client.cookies.set(SESSION_COOKIE, token)


def new_client(account: Account | None = None) -> TestClient:
    """Another browser, signed in as ``account`` when one is given. Close it (``with``) when done."""
    client = TestClient(app, base_url="http://testserver", headers=TAB)
    if account is not None:
        sign_in(client, account)
    return client


def _operator(client: TestClient) -> Account:
    with SessionLocal() as db:
        row = db.query(Account).filter_by(name="tester").one_or_none()
        if row is not None:
            db.expunge(row)
    if row is None:
        row = make_account("tester", OPERATOR)
    sign_in(client, row)
    return row


@pytest.fixture
def client() -> Iterator[TestClient]:
    # As the interface does: every request names its tab (changes are refused without it).
    with TestClient(app, base_url="http://testserver", headers=TAB) as test_client:
        yield test_client


@pytest.fixture
def operator(client: TestClient) -> Account:
    """The signed-in operator ``tester``."""
    return _operator(client)


@pytest.fixture
def account(client: TestClient) -> Account:
    """The same as ``operator``, under the name the tests from nexlore use."""
    return _operator(client)
