"""The operator's settings, invitation mail through a fake SMTP server, and what a backup keeps of the sign-in."""

from __future__ import annotations

import smtplib
import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, ClassVar, Self

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.models import Account, AuthSession, Setting
from app.security import decrypt_secret
from app.services import backup_hooks, backups, mailer, settings_service
from app.vendor.nexsicherung import restore, tools

from .conftest import PASSWORD, make_account, sign_in


class FakeSmtp:
    sent: ClassVar[list[EmailMessage]] = []
    logins: ClassVar[list[tuple[str, str]]] = []
    tls: ClassVar[list[bool]] = []
    fail = False

    def __init__(self, host: str, port: int, timeout: float = 0, context: Any = None) -> None:
        self.host, self.port = host, port
        if FakeSmtp.fail:
            raise smtplib.SMTPConnectError(421, b"mail.example.com says no to secret@example.com")

    def quit(self) -> None:
        pass

    def close(self) -> None:
        pass

    def __enter__(self) -> Self:
        if FakeSmtp.fail:
            raise smtplib.SMTPConnectError(421, b"mail.example.com says no to secret@example.com")
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def starttls(self, context: Any = None) -> None:
        FakeSmtp.tls.append(True)

    def login(self, user: str, password: str) -> None:
        FakeSmtp.logins.append((user, password))

    def send_message(self, message: EmailMessage) -> None:
        FakeSmtp.sent.append(message)


@pytest.fixture
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSmtp]:
    FakeSmtp.sent, FakeSmtp.logins, FakeSmtp.tls, FakeSmtp.fail = [], [], [], False
    monkeypatch.setattr(smtplib, "SMTP", FakeSmtp)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSmtp)
    return FakeSmtp


MAIL = {
    "smtp_host": "mail.example.com", "smtp_port": 587, "smtp_security": "starttls", "smtp_user": "time",
    "smtp_password": "mail password", "smtp_from": "notes@example.com",
}


def test_settings_are_the_operators_and_the_password_is_never_shown(client: TestClient, operator: Account) -> None:
    before = client.get("/api/settings").json()
    assert before["share_links"] is True and before["password_login"] is True
    assert before["backup_schedule"] == "daily" and before["smtp_password_set"] is False
    saved = client.put("/api/settings", json=MAIL).json()
    assert saved["smtp_password_set"] is True and "mail password" not in str(saved)
    with SessionLocal() as db:
        stored = db.get(Setting, "smtp_password_enc")
        assert stored is not None and "mail password" not in str(stored.value)
        assert decrypt_secret(str(stored.value)) == "mail password"
    # Left out keeps the password, empty removes it.
    assert client.put("/api/settings", json={"smtp_port": 465}).json()["smtp_password_set"] is True
    assert client.put("/api/settings", json={"smtp_password": ""}).json()["smtp_password_set"] is False
    member = TestClient(client.app, headers={"X-Nextime-Client": "tab-member00"})
    sign_in(member, make_account("member"))
    assert member.get("/api/settings").status_code == 403
    assert member.put("/api/settings", json={"shares_allowed": True}).status_code == 403


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("https://Time.example.com/", "https://time.example.com"),
        ("http://10.0.0.5:8470", "http://10.0.0.5:8470"),
        ("", ""),
    ],
)
def test_the_public_address(client: TestClient, operator: Account, value: str, expected: str) -> None:
    assert client.put("/api/settings", json={"public_url": value}).json()["public_url"] == expected


@pytest.mark.parametrize(
    "value", ["time.example.com", "javascript:alert(1)", "https://a@b.example.com", "https://x.example.com/path"]
)
def test_a_public_address_that_is_not_one_is_refused(client: TestClient, operator: Account, value: str) -> None:
    refused = client.put("/api/settings", json={"public_url": value})
    assert refused.status_code == 422 and refused.json()["detail"]["code"] == "invalid_url"


def test_invitation_links_use_the_public_address(client: TestClient, operator: Account) -> None:
    client.put("/api/settings", json={"public_url": "https://time.example.com"})
    assert client.post("/api/invites", json={}).json()["link"].startswith("https://time.example.com/invite/")


def test_an_invitation_goes_out_by_mail(client: TestClient, operator: Account, smtp: type[FakeSmtp]) -> None:
    # Mail links to the public address, never to one the request names (review before 1.0.0).
    client.put("/api/settings", json=MAIL | {"public_url": "https://time.example.com"})
    made = client.post("/api/invites", json={"email": "dora@example.com", "send": True}).json()
    assert made["sent"] is True
    [message] = smtp.sent
    # The shared post module names the sender: the app's name when the operator gave none.
    assert message["To"] == "dora@example.com" and message["From"] == "nextime <notes@example.com>"
    assert made["link"] in message.get_body(("plain",)).get_content()
    assert smtp.logins == [("time", "mail password")] and smtp.tls == [True]


def test_mail_carries_a_date_header(client: TestClient, operator: Account, smtp: type[FakeSmtp]) -> None:
    # Without Date iCloud, Outlook and Gmail drop the mail silently, and smtplib does not add it.
    client.put("/api/settings", json=MAIL)
    before = datetime.now(UTC) - timedelta(seconds=5)
    assert client.post("/api/settings/mail-test", json={"to": "dora@example.com"}).status_code == 204
    headers = smtp.sent[0].get_all("Date")
    assert headers is not None and len(headers) == 1
    # In UTC ("-0000", which Python reads without a zone).
    sent_at = parsedate_to_datetime(headers[0])
    sent_at = sent_at if sent_at.tzinfo else sent_at.replace(tzinfo=UTC)
    assert before <= sent_at <= datetime.now(UTC) + timedelta(seconds=5)


def test_a_failing_mail_server_says_so_without_its_answer(
    client: TestClient, operator: Account, smtp: type[FakeSmtp], caplog: pytest.LogCaptureFixture
) -> None:
    client.put("/api/settings", json=MAIL)
    smtp.fail = True
    failed = client.post("/api/settings/mail-test", json={"to": "dora@example.com"})
    assert failed.status_code == 502 and failed.json()["detail"]["code"] == "mail_failed"
    # The server's answer can quote addresses; only its kind goes to the log.
    assert "secret@example.com" not in caplog.text and "code=mail_refused" in caplog.text
    smtp.fail = False
    assert client.post("/api/settings/mail-test", json={"to": "dora@example.com"}).status_code == 204
    assert client.post("/api/settings/mail-test", json={"to": "not an address"}).status_code == 422


def test_mail_off_without_a_server() -> None:
    """The test mail of the post module (post 1.1.0) without a mail server: ``mail_off``, as nextime said before."""
    from app.services.post_store import PostStore
    from app.vendor import nexpost

    with SessionLocal() as db:
        assert mailer.configured(db) is False
        with pytest.raises(nexpost.MailError) as caught:
            nexpost.outbox.test_mail(PostStore(db), "dora@example.com")
    assert caught.value.code == "mail_off" and mailer.old_code(caught.value.code) == "mail_off"


def test_a_backup_keeps_the_secret_key_and_a_restore_signs_everybody_out(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    key_file = get_settings().data_dir / "secret.key"
    key_file.write_text("the key of this server", encoding="utf-8")
    try:
        client.put("/api/settings", json=MAIL)
        name = backups.create().name
        key_file.write_text("a key made later", encoding="utf-8")
        monkeypatch.setattr(restore, "restart_soon", lambda: None)
        answer = client.post(f"/api/backups/{name}/restore", json={"password": PASSWORD})
        # The answer's text names the reason, should the restore ever be refused.
        assert answer.status_code == 202, answer.text
        with SessionLocal() as db:
            assert db.scalar(select(AuthSession)) is not None
        assert backups.apply_pending() is True
        assert key_file.read_text(encoding="utf-8") == "the key of this server"
        with SessionLocal() as db:
            assert db.scalar(select(AuthSession)) is None
            assert db.scalar(select(Account).where(Account.name == "tester")) is not None
            assert settings_service.get(db, "smtp_host") == "mail.example.com"
    finally:
        key_file.unlink(missing_ok=True)


def test_a_restore_is_not_refused_as_damaged_when_the_system_held_the_database_copy_for_a_moment(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check reads the archive's database from a copy beside the backups. A virus scanner or a busy disk may keep
    SQLite from opening that fresh copy for a moment ("disk I/O error"); that is no damage, and the restore must not be
    refused for it (400 backup_damaged). A database that really is broken still is."""
    name = backups.create().name
    real = sqlite3.connect
    held: list[str] = []

    def connect(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        if Path(str(database)).name.startswith(".check-") and len(held) < 2:
            held.append(str(database))
            raise sqlite3.OperationalError("disk I/O error")
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(backup_hooks, "DATABASE_PAUSE_SECONDS", 0.01)
    monkeypatch.setattr(restore, "restart_soon", lambda: None)
    answer = client.post(f"/api/backups/{name}/restore", json={"password": PASSWORD})
    assert answer.status_code == 202, answer.text
    assert len(held) == 2
    tools.remove_tree(backups.pending_folder())
    # Held for good: then it is refused, and says so.
    opened: list[str] = []

    def never(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        if Path(str(database)).name.startswith(".check-"):
            opened.append(str(database))
            raise sqlite3.OperationalError("disk I/O error")
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", never)
    refused = client.post(f"/api/backups/{name}/restore", json={"password": PASSWORD})
    assert refused.status_code == 400 and refused.json()["detail"]["code"] == "backup_damaged"
    assert len(opened) == backup_hooks.DATABASE_TRIES
    # A database SQLite reads and finds broken is damaged at once, without trying again.
    broken = backups.folder() / "nextime-2000-01-01-000000.zip"
    with zipfile.ZipFile(backups.path_of(name)) as source, zipfile.ZipFile(broken, "w") as target:
        for info in source.infolist():
            data = source.read(info)
            if info.filename == backups.DATABASE_ENTRY:
                data = data[:100] + bytes([0xFF]) * (len(data) - 100)
            target.writestr(info, data)
    opened.clear()

    def counted(database: Any, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        if Path(str(database)).name.startswith(".check-"):
            opened.append(str(database))
        return real(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", counted)
    assert backups.check(broken.name).database_ok is False
    assert len(opened) == 1


@pytest.mark.parametrize(("value", "status"), [(1, 200), (100, 200), (365, 200), (0, 422), (366, 422)])
def test_backups_to_keep_go_up_to_365_as_the_interface_offers(client: TestClient, operator: Account, value: int,
                                                              status: int) -> None:
    """The interface offers 1 to 365 and the backup job reads up to 365; the setting must take the same (0.2.1 took
    only up to 100 and refused what the interface let through)."""
    answer = client.put("/api/settings", json={"backup_keep": value})
    assert answer.status_code == status, answer.text
    if status == 200:
        assert answer.json()["backup_keep"] == value
        assert backup_hooks.NextimeHooks().load_schedule().keep == value
