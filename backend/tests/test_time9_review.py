"""TIME-9 after the review of stage 6a: the storage of an account counted while a file comes, also without a declared
length; what held when an upload began asked again at its end; attachments of sources streamed through a work file,
at most the operator's limit and only from the source's own host; files for devices sent piece by piece, at the
address the device asked."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import RIGHT_EDIT, RIGHT_READ, Account, Attachment, CalendarShare, Event, Source
from app.services import attachments, remote_files, subscriptions, vault

from .calendar_helpers import code, make_calendar, make_event
from .conftest import MEDIA, make_account, new_client
from .test_time9 import MB, inline_media, pdf, shown, upload  # noqa: F401 - the fixture
from .test_time19 import from_source, put_share, source_calendar, vevent
from .test_time20 import calendar_url, device_for, raw


def work_files() -> list[str]:
    return [entry.name for entry in MEDIA.iterdir() if entry.name.startswith(attachments.WORK_PREFIX)]


def limit_storage(account: Account, size: int) -> None:
    with SessionLocal() as db:
        row = db.get(Account, account.id)
        assert row is not None
        row.storage_limit = size
        db.commit()


def test_time9_ec12_the_storage_of_the_account_is_counted_while_the_file_comes(client: TestClient,
                                                                               account: Account) -> None:
    made = make_event(client, "Probe")
    limit_storage(account, 2 * MB)
    assert upload(client, made["id"], "eins.bin", b"\1" * MB).status_code == 201
    declared = upload(client, made["id"], "zwei.bin", b"\2" * (2 * MB))
    assert declared.status_code == 507 and code(declared) == "storage_limit_reached"
    # Without a declared length (chunked): stopped while it comes, nothing kept, no work file.
    streamed = upload(client, made["id"], "drei.bin", (b"\3" * (256 * 1024) for _ in range(8)))
    assert streamed.status_code == 507 and code(streamed) == "storage_limit_reached"
    assert [item["name"] for item in shown(client, made["id"])["attachments"]] == ["eins.bin"]
    assert work_files() == []


def test_time9_ec12_the_real_size_is_counted_again_at_the_end(client: TestClient, account: Account,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    made = make_event(client, "Probe")
    limit_storage(account, 2 * MB)
    real = attachments.finish

    def finish(upload_: Any, work: Any) -> Any:
        # Another upload filled the storage meanwhile.
        limit_storage(account, MB)
        return real(upload_, work)

    monkeypatch.setattr(attachments, "finish", finish)
    late = upload(client, made["id"], "spaet.bin", b"\4" * (MB + 1))
    assert late.status_code == 507 and code(late) == "storage_limit_reached"
    assert shown(client, made["id"])["attachments"] == [] and work_files() == []


def test_time9_ac5_an_event_moved_to_icloud_while_its_file_comes_takes_nothing(client: TestClient, account: Account,
                                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    made = make_event(client, "Sitzung")
    arbeit = source_calendar(account.id, "Arbeit")
    real = attachments.finish

    def finish(upload_: Any, work: Any) -> Any:
        with SessionLocal() as db:
            db.get(Event, made["id"]).calendar_id = arbeit
            db.commit()
        return real(upload_, work)

    monkeypatch.setattr(attachments, "finish", finish)
    refused = upload(client, made["id"], "Protokoll.pdf", pdf(1))
    assert refused.status_code == 409 and code(refused) == "attachments_own_only"
    with SessionLocal() as db:
        assert db.scalar(select(Attachment)) is None
    assert work_files() == []


def test_time9_ac3_a_right_taken_away_while_the_file_comes_takes_nothing(client: TestClient, account: Account,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    ben = make_account("ben")
    familie = make_calendar(client, "Familie")
    assert put_share(client, familie, ben.id, RIGHT_EDIT).status_code == 200
    made = make_event(client, "Konzert", calendar_id=familie)
    real = attachments.finish

    def finish(upload_: Any, work: Any) -> Any:
        with SessionLocal() as db:
            db.scalar(select(CalendarShare).where(CalendarShare.account_id == ben.id)).right = RIGHT_READ
            db.commit()
        return real(upload_, work)

    monkeypatch.setattr(attachments, "finish", finish)
    with new_client(ben) as browser:
        refused = upload(browser, made["id"], "Ticket.pdf", pdf(1))
    assert refused.status_code in (403, 404), refused.text
    with SessionLocal() as db:
        assert db.scalar(select(Attachment)) is None


class Large:
    """A source whose file is larger than the operator's limit."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.sent = 0

    def chunks(self) -> Iterator[bytes]:
        for _number in range(32):
            self.sent += 1
            yield b"\0" * (64 * 1024)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200, content=self.chunks())


@pytest.fixture
def icloud_event(client: TestClient, account: Account) -> int:
    arbeit = source_calendar(account.id, "Arbeit")
    with SessionLocal() as db:
        calendar_source = db.scalar(select(Source))
        calendar_source.username = "anna@example.com"
        calendar_source.secret_enc = vault.seal({"password": "app-specific"}, f"source:{calendar_source.id}")
        db.commit()
    lines = ("ATTACH;FILENAME=Gross.pdf:https://p01-caldav.icloud.com/a/1",
             "ATTACH;FILENAME=Falle.pdf:https://evilicloud.com/a/2",
             "ATTACH;FILENAME=Auch.pdf:https://p01-caldav.icloud.com.evil.example/a/3")
    from_source(arbeit, vevent("sitzung", "20261126T150000Z", "20261126T160000Z", "Sitzung",
                               extra="\n".join(lines)))
    with SessionLocal() as db:
        return int(db.scalar(select(Event.id).where(Event.calendar_id == arbeit)))


def test_time9_ec14_an_attachment_of_a_source_over_the_limit_is_not_fetched_to_the_end(
        client: TestClient, account: Account, icloud_event: int, monkeypatch: pytest.MonkeyPatch) -> None:
    source = Large()
    monkeypatch.setattr(remote_files, "transport", httpx.MockTransport(source))
    monkeypatch.setattr(subscriptions, "resolve", lambda host: ["17.248.1.1"])
    assert client.put("/api/settings", json={"upload_max_mb": 1}).status_code == 200
    refused = client.get(f"/api/events/{icloud_event}/remote-attachments/0")
    assert refused.status_code == 502 and code(refused) == "remote_unavailable"
    assert source.sent < 32 and work_files() == []
    # Within the limit it comes whole, streamed through a work file that goes afterwards.
    assert client.put("/api/settings", json={"upload_max_mb": 5}).status_code == 200
    fetched = client.get(f"/api/events/{icloud_event}/remote-attachments/0")
    assert fetched.status_code == 200 and len(fetched.content) == 32 * 64 * 1024
    assert work_files() == []


def test_time9_ec14_a_host_that_only_ends_in_icloud_com_is_not_asked(client: TestClient, account: Account,
                                                                    icloud_event: int,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    source = Large()
    monkeypatch.setattr(remote_files, "transport", httpx.MockTransport(source))
    monkeypatch.setattr(subscriptions, "resolve", lambda host: ["17.248.1.1"])
    for index in (1, 2):
        refused = client.get(f"/api/events/{icloud_event}/remote-attachments/{index}")
        assert refused.status_code == 502 and code(refused) == "remote_unavailable"
    assert source.requests == []


def test_time9_ac4_a_device_gets_the_file_whole_and_its_size_first_at_the_address_it_asked(
        client: TestClient, account: Account) -> None:
    made = make_event(client, "Konzert")
    content = pdf(2)
    item = upload(client, made["id"], "Programm.pdf", content).json()
    password = device_for(account)
    with SessionLocal() as db:
        name = db.get(Event, made["id"]).href
    # No public address set: the lines carry the address the device asked.
    text = raw("GET", calendar_url(account, made["calendar_id"]) + name, account, password,
               Host="kalender.example.org").text.replace("\r\n ", "")
    assert f"https://kalender.example.org/dav/attachments/{item['id']}" in text
    head = raw("HEAD", f"/dav/attachments/{item['id']}", account, password)
    assert head.status_code == 200 and head.content == b""
    size = int(head.headers["content-length"])
    fetched = raw("GET", f"/dav/attachments/{item['id']}", account, password)
    assert fetched.status_code == 200 and len(fetched.content) == size and fetched.content[:5] == b"%PDF-"
