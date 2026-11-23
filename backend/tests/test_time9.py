"""TIME-9 Anhänge: files at events of nextime's own calendars, the limit from the operator's setting, what a file is
from its content, previews for pictures and PDFs, the rights of the calendar, the trash, backups, the calendar account,
and the attachments of iCloud and CalDAV events through nextime (the source played by ``httpx.MockTransport``). With
the negative side: no file without the right to change the event, nothing for guests, "busy only" or a stranger who
knows the address, nothing of a file runs in nextime's page, and a broken upload leaves nothing behind."""

from __future__ import annotations

import io
import os
import secrets
import zipfile
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from app.db import SessionLocal
from app.models import RIGHT_BUSY, RIGHT_EDIT, RIGHT_READ, Account, Attachment, Event, Source, utcnow
from app.services import attachments, backups, events, media_worker, remote_files, subscriptions, vault

from .calendar_helpers import code, make_calendar, make_event
from .conftest import MEDIA, make_account, new_client
from .test_time19 import from_source, guest_client, link_on, put_share, source_calendar, token_of, vevent
from .test_time20 import calendar_url, device_for, raw

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def inline_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """Previews are drawn in this process: no worker process per test on a small machine (one test below uses the
    worker itself)."""
    monkeypatch.setattr(media_worker, "INLINE", True)


def new_id() -> str:
    return secrets.token_urlsafe(16)


def upload(client: TestClient, event_id: int | None, name: str, data: bytes | Iterator[bytes], *,
           attachment_id: str | None = None, ref: str | None = None) -> Any:
    params: dict[str, Any] = {"id": attachment_id or new_id(), "name": name}
    if ref is not None:
        params["ref"] = ref
    else:
        params["event_id"] = event_id
    return client.post("/api/attachments", params=params, content=data,
                       headers={"Content-Type": "application/octet-stream"})


def pdf(pages: int = 2) -> bytes:
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(200, 300)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def picture(kind: str = "JPEG", exif: bool = False) -> bytes:
    image = Image.new("RGB", (64, 48), (90, 120, 160))
    out = io.BytesIO()
    if exif:
        data = Image.Exif()
        data[0x010F] = "Phone Maker"
        image.save(out, kind, exif=data.tobytes())
    else:
        image.save(out, kind)
    return out.getvalue()


def archive() -> bytes:
    """Always the same bytes: a fixed time in the archive (with the clock, two calls a second apart differed)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as made:
        made.writestr(zipfile.ZipInfo("ticket.txt", date_time=(2026, 11, 20, 10, 0, 0)), "Reihe 4")
    return out.getvalue()


def shown(client: TestClient, event_id: int) -> dict[str, Any]:
    answer = client.get(f"/api/events/{event_id}")
    assert answer.status_code == 200, answer.text
    return answer.json()


# --- AC-1, EC-4, EC-7, EC-8: attaching ----------------------------------------------------------------------------


def test_time9_ac1_pdf_pictures_and_other_files_with_name_size_and_previews(client: TestClient,
                                                                            account: Account) -> None:
    made = make_event(client, "Konzert")
    assert shown(client, made["id"])["attachments"] == [] and made["attachments_allowed"] is True
    assert made["attachment_max_mb"] == 25
    items = {name: upload(client, made["id"], name, data) for name, data in (
        ("Einladung.pdf", pdf(3)), ("Saalplan.jpg", picture("JPEG", exif=True)), ("Platz.png", picture("PNG")),
        ("Tickets.zip", archive()))}
    assert all(answer.status_code == 201 for answer in items.values()), [a.text for a in items.values()]
    listed = {item["name"]: item for item in shown(client, made["id"])["attachments"]}
    assert set(listed) == {"Einladung.pdf", "Saalplan.jpg", "Platz.png", "Tickets.zip"}
    assert listed["Einladung.pdf"]["kind"] == "pdf" and listed["Einladung.pdf"]["pages"] == 3
    assert listed["Einladung.pdf"]["thumb"] is True and listed["Einladung.pdf"]["inline"] is True
    assert listed["Saalplan.jpg"]["kind"] == "image" and listed["Saalplan.jpg"]["thumb"] is True
    assert listed["Tickets.zip"]["kind"] == "file" and listed["Tickets.zip"]["inline"] is False
    assert listed["Tickets.zip"]["size"] == len(archive())
    zipped = client.get(f"/api/attachments/{listed['Tickets.zip']['id']}/file")
    assert zipped.status_code == 200 and zipped.content == archive()
    assert zipped.headers["content-disposition"].startswith("attachment;")
    preview = client.get(f"/api/attachments/{listed['Einladung.pdf']['id']}/thumb")
    assert preview.status_code == 200 and preview.headers["content-type"] == "image/webp"
    # A photo loses where and with what it was taken.
    photo = client.get(f"/api/attachments/{listed['Saalplan.jpg']['id']}/file").content
    assert b"Phone Maker" not in photo and Image.open(io.BytesIO(photo)).size == (64, 48)


def test_time9_ec7_the_same_name_twice_keeps_both(client: TestClient, account: Account) -> None:
    made = make_event(client, "Konzert")
    assert upload(client, made["id"], "Ticket.pdf", pdf(1)).status_code == 201
    assert upload(client, made["id"], "Ticket.pdf", pdf(2)).status_code == 201
    listed = shown(client, made["id"])["attachments"]
    assert [item["name"] for item in listed] == ["Ticket.pdf", "Ticket.pdf"] and {item["pages"] for item in listed} == {1, 2}


def test_time9_ec8_a_long_name_with_umlauts_and_emoji_is_kept_whole_for_the_download(client: TestClient,
                                                                                    account: Account) -> None:
    name = "Übersicht 🎟️ " + "Ä" * 190 + ".pdf"
    made = make_event(client, "Konzert")
    item = upload(client, made["id"], f"C:\\fakepath\\{name}", pdf(1)).json()
    assert item["name"] == name
    answer = client.get(f"/api/attachments/{item['id']}/file", params={"download": 1})
    assert "filename*=UTF-8''%C3%9Cbersicht" in answer.headers["content-disposition"]
    assert answer.headers["content-disposition"].startswith("attachment;")


def test_time9_ec4_an_event_without_attachments_has_none(client: TestClient, account: Account) -> None:
    made = make_event(client, "Training")
    assert made["attachments"] == [] and made["attachments_allowed"] is True and made["remote_attachments"] == []


def test_time9_ac1_the_same_upload_twice_is_one_attachment(client: TestClient, account: Account) -> None:
    made = make_event(client, "Konzert")
    same = new_id()
    assert upload(client, made["id"], "a.pdf", pdf(1), attachment_id=same).status_code == 201
    assert upload(client, made["id"], "a.pdf", pdf(1), attachment_id=same).status_code == 200
    assert len(shown(client, made["id"])["attachments"]) == 1
    # Somebody else's id is no door.
    other = make_event(client, "Anderes")
    assert upload(client, other["id"], "a.pdf", pdf(1), attachment_id=same).status_code == 404


# --- AC-2, EC-5, EC-15: the limit ------------------------------------------------------------------------------


def test_time9_ac2_ec15_up_to_the_limit_and_not_a_byte_more_a_lowered_limit_keeps_what_is_there(
        client: TestClient, account: Account) -> None:
    made = make_event(client, "Probe")
    exactly = upload(client, made["id"], "genau.bin", b"\0" * (25 * MB))
    assert exactly.status_code == 201, exactly.text
    over = upload(client, made["id"], "Probe.mov", b"\0" * (25 * MB + 1))
    assert over.status_code == 413 and code(over) == "attachment_too_large"
    assert {key: over.json()["detail"][key] for key in ("name", "size", "max_mb")} == {
        "name": "Probe.mov", "size": 25 * MB + 1, "max_mb": 25}
    # Without a declared length (chunked) it stops while the file comes.
    streamed = upload(client, made["id"], "gross.bin", (b"\0" * MB for _ in range(26)))
    assert streamed.status_code == 413 and code(streamed) == "attachment_too_large"
    assert client.put("/api/settings", json={"upload_max_mb": 10}).status_code == 200
    assert shown(client, made["id"])["attachment_max_mb"] == 10
    lowered = upload(client, made["id"], "elf.bin", b"\0" * (10 * MB + 1))
    assert lowered.status_code == 413 and lowered.json()["detail"]["max_mb"] == 10
    assert [item["name"] for item in shown(client, made["id"])["attachments"]] == ["genau.bin"]
    # No work file stays behind.
    assert not [entry for entry in MEDIA.iterdir() if entry.name.startswith(attachments.WORK_PREFIX)]


def test_time9_ec5_of_ten_files_only_the_too_large_one_is_refused(client: TestClient, account: Account) -> None:
    made = make_event(client, "Ausflug")
    answers = [upload(client, made["id"], f"Foto {n}.png", picture("PNG")) for n in range(9)]
    answers.append(upload(client, made["id"], "Video.mov", b"\0" * (26 * MB)))
    assert [answer.status_code for answer in answers] == [201] * 9 + [413]
    assert len(shown(client, made["id"])["attachments"]) == 9


# --- AC-3, AC-8, AC-9, EC-1, EC-13: rights ---------------------------------------------------------------------


def test_time9_ac3_ec13_the_rights_of_the_calendar_and_nothing_for_anybody_else(client: TestClient,
                                                                               account: Account) -> None:
    anna, ben, carla = make_account("anna"), make_account("ben"), make_account("carla")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_EDIT)
    put_share(client, familie, ben.id, RIGHT_READ)
    made = make_event(client, "Konzert", calendar_id=familie)
    item = upload(client, made["id"], "Ticket.pdf", pdf(1)).json()
    url = f"/api/attachments/{item['id']}/file"
    with new_client(anna) as browser:
        assert [entry["name"] for entry in shown(browser, made["id"])["attachments"]] == ["Ticket.pdf"]
        assert browser.get(url).status_code == 200
        added = upload(browser, made["id"], "Annas.pdf", pdf(1))
        assert added.status_code == 201
    with new_client(ben) as browser:
        seen = shown(browser, made["id"])
        assert len(seen["attachments"]) == 2 and seen["attachments_allowed"] is False
        assert browser.get(url).status_code == 200
        refused = upload(browser, made["id"], "Bens.pdf", pdf(1))
        assert refused.status_code == 403
        assert browser.delete(f"/api/attachments/{item['id']}").status_code == 403
    # Somebody without the calendar who knows the address gets nothing; the same after the share ended.
    with new_client(carla) as browser:
        for suffix in ("file", "thumb", "view"):
            assert browser.get(f"/api/attachments/{item['id']}/{suffix}").status_code == 404
        assert browser.delete(f"/api/attachments/{item['id']}").status_code == 404
        assert upload(browser, made["id"], "x.pdf", pdf(1)).status_code == 404
    assert client.delete(f"/api/calendars/{familie}/sharing/{ben.id}").status_code in (200, 204)
    with new_client(ben) as browser:
        assert browser.get(url).status_code == 404
    with new_client() as nobody:
        assert nobody.get(url).status_code == 401


def test_time9_ec13_busy_only_shows_no_attachments(client: TestClient, account: Account,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    anna = make_account("anna")
    arbeit = source_calendar(account.id, "Arbeit")
    from_source(arbeit, vevent("sitzung", "20261126T150000Z", "20261126T160000Z", "Sitzung",
                               extra="ATTACH;FILENAME=Protokoll.pdf;FMTTYPE=application/pdf:"
                                     "https://p01-caldav.icloud.com/attachments/1"))
    put_share(client, arbeit, anna.id, RIGHT_BUSY)
    with SessionLocal() as db:
        event_id = db.scalar(select(Event.id).where(Event.calendar_id == arbeit))
    assert [entry["name"] for entry in shown(client, event_id)["remote_attachments"]] == ["Protokoll.pdf"]
    with new_client(anna) as browser:
        hidden = shown(browser, event_id)
        assert hidden["attachments"] == [] and hidden["remote_attachments"] == []
        assert browser.get(f"/api/events/{event_id}/remote-attachments/0").status_code == 404


def test_time9_ac8_ec1_guests_get_no_attachments_on_the_page_or_in_the_subscription(client: TestClient,
                                                                                 account: Account) -> None:
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Konzert", "2026-11-27T19:00", "2026-11-27T21:00", calendar_id=familie)
    item = upload(client, made["id"], "Geheimes Ticket.pdf", pdf(1)).json()
    link = link_on(client, familie)
    with guest_client() as guest:
        page = guest.get(f"/api/guest/{token_of(link)}")
        feed = guest.get(f"/abo/{token_of(link)}.ics")
        file = guest.get(f"/api/attachments/{item['id']}/file")
    assert page.status_code == 200 and "Geheimes Ticket" not in page.text and item["id"] not in page.text
    assert feed.status_code == 200 and "ATTACH" not in feed.text and "Geheimes" not in feed.text
    assert file.status_code == 401


def test_time9_ac9_removing_asks_nobody_but_is_for_good_and_not_in_the_trash(client: TestClient,
                                                                              account: Account) -> None:
    made = make_event(client, "Konzert")
    item = upload(client, made["id"], "Ticket.pdf", pdf(1)).json()
    assert (MEDIA / item["id"]).is_file()
    assert client.delete(f"/api/attachments/{item['id']}").status_code == 204
    assert shown(client, made["id"])["attachments"] == []
    with SessionLocal() as db:
        assert db.get(Attachment, item["id"]) is None
    assert not (MEDIA / item["id"]).exists() and not (MEDIA / f"{item['id']}.thumb").exists()
    assert client.get(f"/api/attachments/{item['id']}/file").status_code == 404


# --- AC-10: a new event ----------------------------------------------------------------------------------------


def test_time9_ac10_an_upload_for_an_event_being_made_lands_at_the_saved_event(client: TestClient,
                                                                                account: Account) -> None:
    ref = "new-event-ref-1"
    early = upload(client, None, "Vorher.pdf", pdf(1), ref=ref)
    assert early.status_code == 201
    made = client.post("/api/events", json={"title": "Konzert", "start": "2026-11-27T19:00",
                                            "time_zone": "Europe/Berlin", "client_ref": ref}).json()
    # The upload still running when "Save" came: it lands at the saved event.
    late = upload(client, None, "Danach.pdf", pdf(1), ref=ref)
    assert late.status_code == 201
    assert sorted(item["name"] for item in shown(client, made["id"])["attachments"]) == ["Danach.pdf", "Vorher.pdf"]
    # A pending upload is its uploader's alone, and goes after a day when no event came.
    anna = make_account("anna")
    lost = upload(client, None, "Nie.pdf", pdf(1), ref="never-saved").json()
    with new_client(anna) as browser:
        assert browser.get(f"/api/attachments/{lost['id']}/file").status_code == 404
        assert browser.delete(f"/api/attachments/{lost['id']}").status_code == 404
    with SessionLocal() as db:
        db.get(Attachment, lost["id"]).created_at = utcnow() - timedelta(days=2)
        db.commit()
    attachments.sweep()
    with SessionLocal() as db:
        assert db.get(Attachment, lost["id"]) is None


def test_time9_ac10_discarding_a_new_event_takes_its_uploads_away(client: TestClient, account: Account) -> None:
    pending = upload(client, None, "Weg.pdf", pdf(1), ref="discarded").json()
    assert client.delete(f"/api/attachments/{pending['id']}").status_code == 204
    assert not (MEDIA / pending["id"]).exists()


# --- AC-5, EC-14: events of a source -------------------------------------------------------------------------


class Source_:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.down = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("down", request=request)
        return httpx.Response(200, content=b"%PDF-1.4 protocol")


def test_time9_ac5_ec14_a_source_event_shows_its_attachments_takes_none_and_says_when_one_cannot_be_had(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    arbeit = source_calendar(account.id, "Arbeit")
    with SessionLocal() as db:
        calendar_source = db.scalar(select(Source))
        calendar_source.username = "anna@example.com"
        calendar_source.secret_enc = vault.seal({"password": "app-specific"}, f"source:{calendar_source.id}")
        db.commit()
    lines = ("ATTACH;FILENAME=Protokoll.pdf;FMTTYPE=application/pdf;SIZE=17:https://p01-caldav.icloud.com/a/1",
             "ATTACH;FILENAME=Elsewhere.pdf:https://evil.example.com/a/2",
             "ATTACH;FILENAME=Plan.docx:https://drive.google.com/open?id=abc",
             "ATTACH;FMTTYPE=text/plain;ENCODING=BASE64;VALUE=BINARY;X-FILENAME=Notiz.txt:SGFsbG8=")
    from_source(arbeit, vevent("sitzung", "20261126T150000Z", "20261126T160000Z", "Sitzung",
                               extra="\n".join(lines)))
    with SessionLocal() as db:
        event_id = db.scalar(select(Event.id).where(Event.calendar_id == arbeit))
    seen = shown(client, event_id)
    assert seen["attachments_allowed"] is False and seen["attachments"] == []
    assert [(item["name"], bool(item["link"])) for item in seen["remote_attachments"]] == [
        ("Protokoll.pdf", True), ("Elsewhere.pdf", True), ("Plan.docx", True), ("Notiz.txt", False)]
    refused = upload(client, event_id, "Neu.pdf", pdf(1))
    assert refused.status_code == 409 and code(refused) == "attachments_own_only"
    source = Source_()
    monkeypatch.setattr(remote_files, "transport", httpx.MockTransport(source))
    monkeypatch.setattr(subscriptions, "resolve", lambda host: ["17.248.1.1"])
    fetched = client.get(f"/api/events/{event_id}/remote-attachments/0")
    assert fetched.status_code == 200 and fetched.content == b"%PDF-1.4 protocol"
    assert fetched.headers["content-disposition"].startswith("attachment;")
    assert source.requests[0].headers["authorization"].startswith("Basic ")
    # Only from the source's own server; the embedded one from the event itself.
    elsewhere = client.get(f"/api/events/{event_id}/remote-attachments/1")
    assert elsewhere.status_code == 502 and code(elsewhere) == "remote_unavailable"
    assert len(source.requests) == 1
    assert client.get(f"/api/events/{event_id}/remote-attachments/3").content == b"Hallo"
    # The source does not answer: a sentence, no error page.
    source.down = True
    unreachable = client.get(f"/api/events/{event_id}/remote-attachments/0")
    assert unreachable.status_code == 502 and code(unreachable) == "remote_unavailable"
    # Never into the server's own machine.
    monkeypatch.setattr(subscriptions, "resolve", lambda host: ["127.0.0.1"])
    source.down = False
    assert client.get(f"/api/events/{event_id}/remote-attachments/0").status_code == 502
    assert len(source.requests) == 2


# --- EC-2, EC-3, EC-16: trash, for good, backups -----------------------------------------------------------------


def test_time9_ec2_ec3_the_trash_keeps_them_back_from_it_they_are_there_for_good_they_go(client: TestClient,
                                                                                       account: Account) -> None:
    made = make_event(client, "Konzert")
    item = upload(client, made["id"], "Ticket.pdf", pdf(1)).json()
    assert client.delete(f"/api/events/{made['id']}").status_code == 200
    assert client.get(f"/api/attachments/{item['id']}/file").status_code == 404
    with SessionLocal() as db:
        row = db.get(Event, made["id"])
        row.deleted_at = None
        events.record_change(db, row.calendar_id, row.href, row=row, remote=False)
        db.commit()
    assert client.get(f"/api/attachments/{item['id']}/file").status_code == 200
    assert [entry["id"] for entry in shown(client, made["id"])["attachments"]] == [item["id"]]
    # Deleted for good (the trash emptied, TIME-23): the row with the event, the files in the cleanup round.
    with SessionLocal() as db:
        db.delete(db.get(Event, made["id"]))
        db.commit()
        assert db.get(Attachment, item["id"]) is None
    old = (utcnow() - timedelta(hours=2)).timestamp()
    for entry in MEDIA.iterdir():
        os.utime(entry, (old, old))
    attachments.sweep()
    assert not any(entry.name.startswith(item["id"]) for entry in MEDIA.iterdir())


def test_time9_ec16_a_restored_backup_brings_the_attachments_back(client: TestClient, account: Account,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    made = make_event(client, "Konzert")
    item = upload(client, made["id"], "Ticket.pdf", pdf(2)).json()
    name = backups.create().name
    with zipfile.ZipFile(backups.path_of(name)) as made_archive:
        assert f"attachments/{item['id']}" in made_archive.namelist()
    assert client.delete(f"/api/attachments/{item['id']}").status_code == 204
    monkeypatch.setattr(backups, "restart_soon", lambda: None)
    assert client.post(f"/api/backups/{name}/restore", json={"password": "correct horse battery"}).status_code == 202
    assert backups.apply_pending() is True
    assert (MEDIA / item["id"]).is_file()
    with new_client(account) as browser:
        assert browser.get(f"/api/attachments/{item['id']}/file").status_code == 200


# --- EC-6: nothing runs ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "data"), [
    ("bild.svg", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
    ("seite.html", b"<html><script>alert(1)</script></html>"),
    ("<img src=x onerror=alert(1)>.pdf", b"<html><body onload=alert(1)>no pdf</body></html>"),
    ("programm.exe", b"MZ\x90\x00" + b"\0" * 200),
])
def test_time9_ec6_a_file_with_code_never_runs_in_nextime(client: TestClient, account: Account, name: str,
                                                          data: bytes) -> None:
    made = make_event(client, "Konzert")
    item = upload(client, made["id"], name, data).json()
    assert item["kind"] == "file" and item["inline"] is False and item["name"] == name
    answer = client.get(f"/api/attachments/{item['id']}/file")
    assert answer.headers["content-type"] == "application/octet-stream"
    assert answer.headers["content-disposition"].startswith("attachment;")
    assert answer.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in answer.headers["content-security-policy"]
    assert client.get(f"/api/attachments/{item['id']}/view").status_code == 404
    assert client.get(f"/api/attachments/{item['id']}/thumb").status_code == 404


def test_time9_ec6_a_pdf_is_shown_without_any_script_of_the_page(client: TestClient, account: Account) -> None:
    made = make_event(client, "Konzert")
    item = upload(client, made["id"], "Plan.pdf", pdf(1)).json()
    answer = client.get(f"/api/attachments/{item['id']}/file")
    assert answer.headers["content-type"] == "application/pdf"
    assert answer.headers["content-disposition"].startswith("inline;")
    assert "default-src 'none'" in answer.headers["content-security-policy"]


# --- EC-9: a photo every browser shows ------------------------------------------------------------------------


def test_time9_ec9_a_heic_photo_gets_a_view_every_browser_shows_and_stays_to_download(client: TestClient,
                                                                                     account: Account) -> None:
    pillow_heif = pytest.importorskip("pillow_heif")
    pillow_heif.register_heif_opener()
    out = io.BytesIO()
    try:
        Image.new("RGB", (80, 60), (200, 100, 50)).save(out, "HEIF")
    except Exception:  # noqa: BLE001 - this build cannot write HEIC; reading it is what nextime needs
        pytest.skip("pillow-heif here cannot write HEIC")
    made = make_event(client, "Ausflug")
    item = upload(client, made["id"], "IMG_0001.HEIC", out.getvalue()).json()
    assert item["kind"] == "image" and item["view"] is True
    view = client.get(f"/api/attachments/{item['id']}/view")
    assert view.status_code == 200 and view.headers["content-type"] == "image/webp"
    original = client.get(f"/api/attachments/{item['id']}/file", params={"download": 1})
    assert original.status_code == 200 and original.content[4:8] == b"ftyp"


# --- EC-10, EC-11, EC-12: at the same time, broken off, a full disk -------------------------------------------------


def test_time9_ec10_two_people_attaching_at_once_keep_everything(client: TestClient, account: Account) -> None:
    import threading

    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    put_share(client, familie, anna.id, RIGHT_EDIT)
    made = make_event(client, "Konzert", calendar_id=familie)
    answers: list[int] = []

    def send(account_: Account, name: str) -> None:
        with new_client(account_) as browser:
            answers.append(upload(browser, made["id"], name, pdf(1)).status_code)

    threads = [threading.Thread(target=send, args=(person, f"{person.name}-{n}.pdf")) for n in range(3)
               for person in (account, anna)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert answers == [201] * 6
    assert len(shown(client, made["id"])["attachments"]) == 6


def test_time9_ec11_a_broken_upload_or_a_restart_leaves_nothing_at_the_event(client: TestClient,
                                                                             account: Account) -> None:
    made = make_event(client, "Konzert")

    def broken() -> Iterator[bytes]:
        yield b"%PDF-1.4 " + b"x" * MB
        raise httpx.ReadError("the connection went")

    with pytest.raises(httpx.ReadError):
        upload(client, made["id"], "Halb.pdf", broken())
    assert shown(client, made["id"])["attachments"] == []
    # A restart in the middle leaves a work file: the cleanup round takes it away.
    (MEDIA / f"{attachments.WORK_PREFIX}leftover").write_bytes(b"half")
    old = (utcnow() - timedelta(days=2)).timestamp()
    os.utime(MEDIA / f"{attachments.WORK_PREFIX}leftover", (old, old))
    attachments.sweep()
    assert not [entry for entry in MEDIA.iterdir() if entry.name.startswith(attachments.WORK_PREFIX)]


def test_time9_ec12_a_disk_that_takes_nothing_ends_only_the_upload(client: TestClient, account: Account,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    made = make_event(client, "Konzert")

    def full(*_args: Any, **_kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(attachments.os, "replace", full)
    answer = upload(client, made["id"], "Tickets.zip", archive())
    assert answer.status_code == 507 and code(answer) == "storage_failed"
    after = shown(client, made["id"])
    assert after["attachments"] == [] and after["etag"] == made["etag"]
    with SessionLocal() as db:
        assert db.scalar(select(Attachment)) is None


# --- AC-4: the calendar account -------------------------------------------------------------------------------


def test_time9_ac4_the_calendar_account_hands_the_attachments_to_the_device(client: TestClient,
                                                                            account: Account) -> None:
    ben = make_account("ben")
    familie = make_calendar(client, "Familie")
    made = make_event(client, "Konzert", calendar_id=familie)
    item = upload(client, made["id"], "Ticket.pdf", pdf(1)).json()
    with SessionLocal() as db:
        name = db.get(Event, made["id"]).href
    password = device_for(account)
    path = calendar_url(account, familie) + name
    text = raw("GET", path, account, password).text
    unfolded = text.replace("\r\n ", "")
    assert f"ATTACH;MANAGED-ID={item['id']};FILENAME=\"Ticket.pdf\";FMTTYPE=application/pdf" in unfolded
    assert f"/dav/attachments/{item['id']}" in unfolded
    fetched = raw("GET", f"/dav/attachments/{item['id']}", account, password)
    assert fetched.status_code == 200 and fetched.content[:5] == b"%PDF-"
    # Somebody else's device gets nothing.
    assert raw("GET", f"/dav/attachments/{item['id']}", ben, device_for(ben)).status_code == 404
    # The device sends the event back with the line: it is not stored, the attachment stays.
    assert raw("PUT", path, account, password, text.replace("Konzert", "Konzert!")).status_code == 204
    with SessionLocal() as db:
        assert "ATTACH" not in db.get(Event, made["id"]).ical
    assert [entry["id"] for entry in shown(client, made["id"])["attachments"]] == [item["id"]]


def test_time9_ac5_moving_an_event_to_a_source_calendar_leaves_its_attachments_behind(client: TestClient,
                                                                                     account: Account) -> None:
    arbeit = source_calendar(account.id, "Arbeit")
    made = make_event(client, "Konzert")
    item = upload(client, made["id"], "Ticket.pdf", pdf(1)).json()
    moved = client.patch(f"/api/events/{made['id']}", json={"calendar_id": arbeit, "etag": made["etag"]})
    assert moved.status_code == 200, moved.text
    assert moved.json()["attachments"] == []
    with SessionLocal() as db:
        assert db.get(Attachment, item["id"]) is None


def test_time9_ac1_the_preview_is_drawn_in_a_worker_process(client: TestClient, account: Account,
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(media_worker, "INLINE", False)
    made = make_event(client, "Konzert")
    try:
        item = upload(client, made["id"], "Plan.pdf", pdf(4)).json()
    finally:
        media_worker.shutdown()
    assert item["kind"] == "pdf" and item["pages"] == 4 and item["thumb"] is True
