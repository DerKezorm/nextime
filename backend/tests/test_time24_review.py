"""TIME-24 after the review ("TIME-24 Prüfbericht"): two events under one UID, Google's secret address with
invitations, the limit of events per file and jobs that never hold the database long, the reference of a move per
calendar, names in the ZIP, Windows-1252 files, the limits of TIME-5, what the preview keeps, the ZIP streamed, and the
gaps in the tests (attachments of "busy only", UIDs without DTSTAMP, the single file without embedded files, the target
checked again at the end, a connected Google calendar, a full disk while moving)."""

from __future__ import annotations

import base64
import io
import sqlite3
import threading
import time
import zipfile
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import RIGHT_BUSY, RIGHT_READ, Account, Calendar, Event, EventChange, ImportFile, RemoteChange, Source
from app.services import subscriptions, transfer

from .calendar_helpers import make_calendar, make_event, share, window
from .conftest import make_account, new_client
from .remote_helpers import (
    SWITCHES,
    FakeGoogle,
    Remote,
    dav_server,  # noqa: F401 - the fixture
    event_text,
    quiet_world,  # noqa: F401 - the fixture
    remote_account,
    round_now,
)
from .test_time9 import upload
from .test_time16 import _connected, _google_calendar
from .test_time16 import fake as fake  # noqa: PLC0414 - the fixture
from .test_time24 import _staging, applied, finished, ics, move_state, moved, taken, titles_in, vevent


@pytest.fixture
def remote(client: TestClient) -> Iterator[Remote]:
    yield from remote_account()


def _subscribe_google(client: TestClient, monkeypatch: pytest.MonkeyPatch, bodies: dict[str, bytes]) -> dict[str, int]:
    """Google calendars through their secret addresses: the copy of each in nextime, by name."""
    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(
        lambda request: httpx.Response(200, content=bodies[str(request.url)])))
    for url in bodies:
        answer = client.post("/api/sources/subscriptions", json={"url": url, "google": True})
        assert answer.status_code == 201, answer.text
    return {row["name"]: row["id"] for row in client.get("/api/calendars").json()["calendars"]
            if row["group"] == "subscriptions"}


# --- Finding 1: two events under one UID ------------------------------------------------------------------------------


def test_time24_ec4_ec6_two_events_under_one_uid_both_arrive_and_nothing_twice_the_second_time(
        client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    data = ics(vevent("same@outlook", "Erster", "20251014T100000", "20251014T110000"),
               vevent("same@outlook", "Zweiter", "20251015T100000", "20251015T110000"))
    preview = taken(client, data)
    assert preview["events"] == 2
    result = applied(client, preview["id"], familie)
    assert (result["made"], result["existing"]) == (2, 0)
    assert titles_in(client, familie) == ["Erster", "Zweiter"]
    with SessionLocal() as db:
        uids = sorted(db.scalars(select(Event.uid).where(Event.calendar_id == familie)))
        assert uids[1] == "same@outlook" and uids[0].startswith("import-")
    again = taken(client, data)
    checked = client.get(f"/api/imports/{again['id']}", params={"calendar_id": familie}).json()
    assert (checked["existing"], checked["new"]) == (2, 0)
    assert applied(client, again["id"], familie)["made"] == 0


# --- Finding 2: Google's secret address with invitations --------------------------------------------------------------


def _invitation(uid: str, title: str, me: str, address: str, day: str) -> str:
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261101T000000Z\r\nDTSTART:{day}T090000Z\r\n"
            f"DTEND:{day}T100000Z\r\nSUMMARY:{title}\r\nORGANIZER;CN=Chris:mailto:chris@example.com\r\n"
            "ATTENDEE;PARTSTAT=ACCEPTED:mailto:chris@example.com\r\n"
            f"ATTENDEE;PARTSTAT={me}:mailto:{address}\r\nEND:VEVENT\r\n")


def test_time24_ac8_ec13_google_s_secret_address_moves_accepted_invitations_only(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    mine = "familie.test@example.com"
    secret = "https://calendar.google.com/calendar/ical/familie.test%40example.com/private-0a1b2c/basic.ics"
    body = ics(_invitation("i1", "Zugesagt", "ACCEPTED", mine, "20261128"),
               _invitation("i2", "Offen", "NEEDS-ACTION", mine, "20261129"),
               _invitation("i3", "Vielleicht", "TENTATIVE", mine, "20261130"),
               _invitation("i4", "Abgesagt", "DECLINED", mine, "20261201"),
               vevent("e1", "Eigener", "20261202T100000", "20261202T110000"), extra="X-WR-CALNAME:Persönlich\r\n")
    copy = _subscribe_google(client, monkeypatch, {secret: body})["Persönlich"]
    preview = client.get(f"/api/calendars/{copy}/move").json()
    assert (preview["events"], preview["invitations"]) == (2, 3)
    result = moved(client, copy, "google-invitations")
    assert (result["made"], result["invitations"]) == (2, 3)
    with SessionLocal() as db:
        accepted = db.scalar(select(Event).where(Event.calendar_id == result["calendar"]["id"], Event.uid == "i1"))
        assert accepted is not None and "ATTENDEE" not in accepted.ical and "ORGANIZER" not in accepted.ical
    assert sorted(row["title"] for row in window(client, calendars=str(result["calendar"]["id"]))) == [
        "Eigener", "Zugesagt"]


def test_time24_ac8_ec13_a_group_calendar_takes_the_address_of_a_connected_google_account(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    mine = "familie.test@example.com"
    secret = "https://calendar.google.com/calendar/ical/abc%40group.calendar.google.com/private-0a1b2c/basic.ics"
    body = ics(_invitation("g1", "Zugesagt", "ACCEPTED", mine, "20261128"),
               _invitation("g2", "Abgesagt", "DECLINED", mine, "20261129"), extra="X-WR-CALNAME:Verein\r\n")
    copy = _subscribe_google(client, monkeypatch, {secret: body})["Verein"]
    # Without a connected Google account nextime knows no own address there: nothing counts as an invitation.
    assert client.get(f"/api/calendars/{copy}/move").json()["invitations"] == 0
    with SessionLocal() as db:
        db.add(Source(account_id=account.id, kind="google", identity="g-test", label="Google", username=mine,
                      state="ok"))
        db.commit()
    assert client.get(f"/api/calendars/{copy}/move").json()["invitations"] == 1
    assert moved(client, copy, "google-group")["invitations"] == 1


# --- Finding 3: at most 50,000 events, jobs in short transactions -----------------------------------------------------


def test_time24_ec2_more_than_50000_events_in_a_file_are_refused_with_the_limit(client: TestClient,
                                                                                 account: Account,
                                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transfer, "FILE_EVENTS_MAX", 50)
    answer = client.post("/api/imports", params={"name": "viel.ics"},
                         content=ics(*(vevent(f"v{n}", f"Viel {n}") for n in range(51))))
    assert answer.status_code == 422 and answer.json()["detail"]["code"] == "import_too_many"
    assert answer.json()["detail"]["max"] == 50
    assert transfer.FILE_EVENTS_MAX == 50
    monkeypatch.undo()
    assert transfer.FILE_EVENTS_MAX == 50_000


@pytest.mark.timing
def test_time24_ec2_ec9_a_large_file_goes_in_short_steps_and_others_write_meanwhile(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    data = ics(*(vevent(f"g{n}", f"Groß {n}", f"2025{n % 12 + 1:02d}{n % 28 + 1:02d}T100000",
                        f"2025{n % 12 + 1:02d}{n % 28 + 1:02d}T110000") for n in range(5000)))
    preview = taken(client, data)
    steps: list[float] = []
    others: list[float] = []
    real_batch, real_finish = transfer._write_batch, transfer._finish_import

    def timed_batch(*args: Any) -> None:
        began = time.perf_counter()
        real_batch(*args)
        steps.append(time.perf_counter() - began)
        # Between two steps somebody else writes: the database is free.
        began = time.perf_counter()
        with SessionLocal() as db:
            db.add(EventChange(calendar_id=familie, href="x.ics"))
            db.flush()
            db.rollback()
        others.append(time.perf_counter() - began)

    def timed_finish(*args: Any) -> None:
        began = time.perf_counter()
        real_finish(*args)
        steps.append(time.perf_counter() - began)

    monkeypatch.setattr(transfer, "_write_batch", timed_batch)
    monkeypatch.setattr(transfer, "_finish_import", timed_finish)
    result = applied(client, preview["id"], familie)
    assert result["made"] == 5000
    assert len(steps) == 11
    assert max(steps) < 2.0, steps
    assert max(others) < 0.5, others


# --- Finding 5: a reference belongs to one calendar -------------------------------------------------------------------


def test_time24_ec10_a_reference_of_one_calendar_does_not_move_another(client: TestClient, account: Account,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    one = "https://calendar.google.com/calendar/ical/eins%40example.com/private-1/basic.ics"
    two = "https://calendar.google.com/calendar/ical/zwei%40example.com/private-2/basic.ics"
    copies = _subscribe_google(client, monkeypatch, {
        one: ics(vevent("a1", "Eins", "20261128", "20261129"), extra="X-WR-CALNAME:Eins\r\n"),
        two: ics(vevent("b1", "Zwei", "20261128", "20261129"), extra="X-WR-CALNAME:Zwei\r\n")})
    assert moved(client, copies["Eins"], "ref-1")["from"]["name"] == "Eins"
    refused = client.post(f"/api/calendars/{copies['Zwei']}/move", json={"client_ref": "ref-1"})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "move_ref_taken"
    assert moved(client, copies["Zwei"], "ref-2")["from"]["name"] == "Zwei"


# --- Finding 6: names in the ZIP --------------------------------------------------------------------------------------


def test_time24_ec8_ac7_a_folder_never_has_the_name_of_a_file_and_endings_are_clean(client: TestClient,
                                                                                    account: Account) -> None:
    dotted = make_calendar(client, "Familie.ics")
    plain = make_calendar(client, "Familie")
    event = make_event(client, "Zeugnis", "2026-11-27T09:00", "2026-11-27T10:00", calendar_id=dotted)
    assert upload(client, event["id"], 'plan.p:d*f', b"Plan").status_code == 201
    make_event(client, "Zweiter", "2026-11-27T11:00", "2026-11-27T12:00", calendar_id=plain)
    with zipfile.ZipFile(io.BytesIO(client.get("/api/export.zip").content)) as archive:
        names = archive.namelist()
    folders = {name.split("/")[0] for name in names if "/" in name}
    files = {name for name in names if "/" not in name}
    assert not folders & files, names
    assert any(name.endswith("/plan.pdf") for name in names), names
    assert not any(char in name for name in names for char in ':*?"<>|')


# --- Finding 7: twins within one file --------------------------------------------------------------------------------


def test_time24_ac2_the_same_title_and_time_twice_in_a_file_counts_as_there_in_the_preview(client: TestClient,
                                                                                           account: Account) -> None:
    familie = make_calendar(client, "Familie")
    data = ics(vevent("u1", "Urlaub", "20251014T100000", "20251014T110000"),
               vevent("u2", "urlaub ", "20251014T100000", "20251014T110000"))
    preview = taken(client, data)
    checked = client.get(f"/api/imports/{preview['id']}", params={"calendar_id": familie}).json()
    assert (checked["events"], checked["existing"], checked["new"]) == (2, 1, 1)
    result = applied(client, preview["id"], familie)
    assert (result["made"], result["existing"]) == (1, 1)


# --- Finding 8: Windows-1252 -----------------------------------------------------------------------------------------


def test_time24_ec4_a_file_in_windows_1252_keeps_its_umlauts(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    data = ics(vevent("l1", "Grüße aus Köln", extra="LOCATION:Straße 5\r\n")).decode("utf-8").encode("cp1252")
    preview = taken(client, data)
    assert preview["encoding"] == "cp1252"
    applied(client, preview["id"], familie)
    row = window(client, "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z", calendars=str(familie))[0]
    assert (row["title"], row["location"]) == ("Grüße aus Köln", "Straße 5")
    utf = taken(client, ics(vevent("l2", "Grüße")))
    assert utf["encoding"] == "utf-8"


# --- Finding 9: the limits of TIME-5 ----------------------------------------------------------------------------------


def test_time24_ec1_long_titles_and_notes_are_cut_deep_or_huge_events_are_named(client: TestClient,
                                                                               account: Account) -> None:
    familie = make_calendar(client, "Familie")
    deep = "".join(f"BEGIN:X-PART{n}\r\n" for n in range(15)) + "".join(
        f"END:X-PART{n}\r\n" for n in reversed(range(15)))
    data = ics(vevent("t1", "T" * 300, extra="DESCRIPTION:" + "n" * 20_000 + "\r\n"),
               vevent("t2", "Tief", "20251015T100000", "20251015T110000", extra=deep),
               vevent("t3", "Riesig", "20251016T100000", "20251016T110000",
                      extra="X-HUGE:" + "x" * (transfer.OBJECT_MAX_CHARS + 10) + "\r\n"))
    result = applied(client, taken(client, data)["id"], familie)
    assert (result["made"], result["shortened"]) == (1, 1)
    assert sorted((entry["title"], entry["reason"]) for entry in result["failed"]) == [
        ("Riesig", "event_too_large"), ("Tief", "event_too_deep")]
    row = window(client, "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z", calendars=str(familie))[0]
    assert len(row["title"]) == 200
    assert len(client.get(f"/api/events/{row['id']}").json()["notes"]) == 10_000


# --- Finding 10: what the preview keeps, how many wait ---------------------------------------------------------------


def test_time24_the_preview_keeps_no_uids_and_at_most_five_files_wait(client: TestClient, account: Account) -> None:
    first = taken(client, ics(vevent("p1", "Eins")))
    with SessionLocal() as db:
        assert "uids" not in (db.get(ImportFile, first["id"]).summary or {})
    ids = [first["id"]] + [taken(client, ics(vevent(f"p{n}", f"Nr {n}")))["id"] for n in range(2, 8)]
    with SessionLocal() as db:
        left = set(db.scalars(select(ImportFile.id).where(ImportFile.account_id == account.id)))
    assert left == set(ids[-transfer.OPEN_IMPORTS:])


# --- Finding 11: the ZIP is streamed ---------------------------------------------------------------------------------


def test_time24_ac1_ac7_the_zip_goes_out_piece_by_piece_without_a_file_on_the_server(client: TestClient,
                                                                                    account: Account) -> None:
    familie = make_calendar(client, "Familie")
    event = make_event(client, "Großer Anhang", "2026-11-27T09:00", "2026-11-27T10:00", calendar_id=familie)
    assert upload(client, event["id"], "gross.bin", bytes(range(256)) * 4096).status_code == 201
    pieces = list(transfer.export_zip(account.id))
    assert len([piece for piece in pieces if piece]) > 2
    with zipfile.ZipFile(io.BytesIO(b"".join(pieces))) as archive:
        assert archive.read("Familie/gross.bin") == bytes(range(256)) * 4096
    from app.config import get_settings

    assert not list((get_settings().data_dir / "imports").glob("export-*"))


# --- Finding 12: the gaps in the tests --------------------------------------------------------------------------------


def test_time24_ec3_attachments_of_busy_only_stay_out_of_the_zip(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    arbeit = make_calendar(client, "Arbeit")
    event = make_event(client, "Vertrag", "2026-11-27T09:00", "2026-11-27T10:00", calendar_id=arbeit)
    assert upload(client, event["id"], "vertrag.txt", b"geheim").status_code == 201
    share(arbeit, anna.id, RIGHT_READ)
    with new_client(anna) as browser, zipfile.ZipFile(io.BytesIO(browser.get("/api/export.zip").content)) as archive:
        assert any(name.endswith("vertrag.txt") for name in archive.namelist())
    share(arbeit, anna.id, RIGHT_BUSY)
    with new_client(anna) as browser, zipfile.ZipFile(io.BytesIO(browser.get("/api/export.zip").content)) as archive:
        assert not any(name.endswith("vertrag.txt") for name in archive.namelist())
        assert all(b"geheim" not in archive.read(name) for name in archive.namelist())


def test_time24_ec11_a_uid_made_from_the_content_ignores_dtstamp(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    applied(client, taken(client, ics(vevent(None, "Ohne Kennung")))["id"], familie)
    # Renamed in nextime since: only the UID made from the content still knows it is the same event.
    row = window(client, "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z", calendars=str(familie), hidden="true")[0]
    assert client.patch(f"/api/events/{row['id']}", json={"title": "Umbenannt"}).status_code == 200
    later = ics(vevent(None, "Ohne Kennung")).replace(b"DTSTAMP:20251001T000000Z", b"DTSTAMP:20261123T080000Z")
    assert applied(client, taken(client, later)["id"], familie) == {
        "made": 0, "existing": 1, "shortened": 0, "failed": [], "calendar": {"id": familie, "name": "Familie"}}


def test_time24_ac7_the_single_file_carries_no_embedded_files_of_a_source(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "https://calendar.google.com/calendar/ical/x%40example.com/private-9/basic.ics"
    inline = "ATTACH;VALUE=BINARY;ENCODING=BASE64;FILENAME=a.txt:" + base64.b64encode(b"Inhalt").decode() + "\r\n"
    copy = _subscribe_google(client, monkeypatch, {secret: ics(vevent("e1", "Mit Datei", "20261128", "20261129",
                                                                      extra=inline), extra="X-WR-CALNAME:Q\r\n")})["Q"]
    with SessionLocal() as db:
        assert "VALUE=BINARY" in db.scalar(select(Event.ical).where(Event.calendar_id == copy))
    text = client.get(f"/api/calendars/{copy}/export.ics").text
    assert "SUMMARY:Mit Datei" in text and "ATTACH" not in text


def test_time24_ec9_a_calendar_deleted_while_the_job_runs_gets_nothing(client: TestClient, account: Account,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    preview = taken(client, ics(vevent("d1", "Weg"), vevent("d2", "Auch weg")))
    real = transfer._write_batch

    def then_delete(*args: Any) -> None:
        real(*args)
        assert client.delete(f"/api/calendars/{familie}").status_code in (200, 204)

    monkeypatch.setattr(transfer, "_write_batch", then_delete)
    state = finished(client, preview["id"], familie)
    assert (state["state"], state["problem"]) == ("failed", "not_found")
    assert _staging() == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Event).where(Event.calendar_id == familie)) == 0


def test_time24_ac3_a_connected_google_calendar_moves_and_google_hears_nothing(
        client: TestClient, account: Account, fake: FakeGoogle, remote: Remote) -> None:
    beirat = _google_calendar(remote, fake, "Elternbeirat")
    remote.put(beirat, "a.ics", event_text("a@google", "Elternabend"))
    source = _connected(client, fake)
    round_now(source)
    copy = next(row["id"] for row in client.get("/api/calendars").json()["calendars"] if row["name"] == "Elternbeirat")
    SWITCHES.seen.clear()
    before = len(fake.patches)
    result = moved(client, copy, "google-connected")
    assert (result["made"], result["kind"]) == (1, "google")
    assert [method for method, _path in SWITCHES.seen if method in ("PUT", "DELETE", "POST", "PROPPATCH")] == []
    assert len(fake.patches) == before
    with SessionLocal() as db:
        assert not db.scalar(select(func.count()).select_from(RemoteChange))


def test_time24_ec14_a_full_disk_while_moving_leaves_nothing_half(client: TestClient, account: Account,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "https://calendar.google.com/calendar/ical/voll%40example.com/private-3/basic.ics"
    copy = _subscribe_google(client, monkeypatch, {secret: ics(vevent("v1", "Voll", "20261128", "20261129"),
                                                               extra="X-WR-CALNAME:Voll\r\n")})["Voll"]

    def full(*_args: Any) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(transfer, "_write_batch", full)
    state = move_state(client, copy, "full")
    assert (state["state"], state["problem"]) == ("failed", "storage_full")
    assert _staging() == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Calendar).where(
            Calendar.owner_id == account.id, Calendar.source_id.is_(None), Calendar.name == "Voll")) == 0


def test_time24_ec10_a_file_windows_still_holds_open_is_no_500(client: TestClient, account: Account,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    preview = taken(client, ics(vevent("w1", "Gesperrt")))
    path = transfer._import_path(preview["id"])
    real = type(path).unlink

    def locked(self: Any, missing_ok: bool = False) -> None:
        # Windows: a file another request still reads cannot go yet.
        if self == path:
            raise PermissionError(32, "The process cannot access the file")
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(type(path), "unlink", locked)
    result = applied(client, preview["id"], familie)
    assert result["made"] == 1
    again = client.post(f"/api/imports/{preview['id']}/apply", json={"calendar_id": familie})
    assert again.status_code == 200 and again.json()["state"] == "done"
    monkeypatch.undo()
    # The cleanup round takes it later.
    assert transfer.sweep(transfer.utcnow() + transfer.timedelta(hours=transfer.IMPORT_KEEP_HOURS + 1)) == 1
    assert not path.exists()


def test_time24_ec11_an_event_made_in_the_calendar_while_the_file_runs_is_not_made_twice(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    preview = taken(client, ics(vevent("lauf-1", "Elternabend"), vevent("lauf-2", "Training", "20251015T180000",
                                                                         "20251015T190000")))
    go = threading.Event()
    real = transfer._write_batch

    def held(*args: Any) -> None:
        real(*args)
        go.wait(30)

    monkeypatch.setattr(transfer, "_write_batch", held)
    started = client.post(f"/api/imports/{preview['id']}/apply", json={"calendar_id": familie})
    assert started.status_code == 202
    # Meanwhile the same event comes into the calendar another way: what the calendar has wins.
    make_event(client, "Elternabend", "2025-10-14T10:00", "2025-10-14T11:00", calendar_id=familie)
    go.set()
    transfer.wait_jobs()
    result = client.get(f"/api/imports/{preview['id']}").json()["result"]
    assert (result["made"], result["existing"]) == (1, 1)
    assert titles_in(client, familie) == ["Elternabend", "Training"]


def test_time24_ec10_two_clicks_that_both_saw_ready_start_one_job(client: TestClient, account: Account,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    preview = taken(client, ics(vevent("r1", "Einmal")))
    go = threading.Event()
    started: list[str] = []
    real_start = transfer._start

    def counted(key: str, work: Any) -> None:
        started.append(key)
        real_start(key, lambda: (go.wait(30), work()))

    monkeypatch.setattr(transfer, "_start", counted)
    assert client.post(f"/api/imports/{preview['id']}/apply", json={"calendar_id": familie}).status_code == 202
    # A second click that read the file as "ready" just before the first one claimed it.
    real_find = transfer._find_import
    asked: list[int] = []

    def find(db: Any, account_: Account, import_id: str) -> Any:
        row = real_find(db, account_, import_id)
        asked.append(1)
        if len(asked) == 1:
            db.expunge(row)
            row.state = "ready"
        return row

    monkeypatch.setattr(transfer, "_find_import", find)
    with SessionLocal() as db:
        again = transfer.apply_import(db, db.get(Account, account.id), preview["id"], familie)
    assert again["state"] == "running"
    go.set()
    transfer.wait_jobs()
    assert started == [f"import:{preview['id']}"]
    assert titles_in(client, familie) == ["Einmal"]
