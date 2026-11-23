"""TIME-24 Export, import and moving house: each calendar as an ICS file and all as a ZIP with the attachments beside
them ("busy only" without title, place and notes); a file loaded into an own calendar with a preview, duplicates
skipped, all or nothing and once; a calendar from iCloud or Google moved into an own one without touching the source.
The other side of iCloud is nextime's own calendar account in a thread (``remote_helpers``)."""

from __future__ import annotations

import base64
import io
import sqlite3
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import (
    RIGHT_BUSY,
    RIGHT_READ,
    Account,
    Attachment,
    Calendar,
    CalendarSetting,
    Event,
    ImportFile,
    ReminderChoice,
    RemoteChange,
    Source,
)
from app.services import settings_service, subscriptions, transfer

from .calendar_helpers import code, make_calendar, make_event, share, window
from .conftest import make_account, new_client
from .remote_helpers import (
    SWITCHES,
    Remote,
    connected,
    dav_server,  # noqa: F401 - the fixture
    event_text,
    quiet_world,  # noqa: F401 - the fixture
    remote_account,
    round_now,
    use_icloud,
)
from .test_time9 import upload
from .test_time18 import invitation

START = "2026-11-01T00:00:00Z"
END = "2027-01-01T00:00:00Z"


@pytest.fixture
def remote(client: TestClient) -> Iterator[Remote]:
    yield from remote_account()


def ics(*events: str, extra: str = "") -> bytes:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\n" + extra + "".join(events)
            + "END:VCALENDAR\r\n").encode()


def vevent(uid: str | None, title: str, start: str = "20251014T100000", end: str = "20251014T110000",
           extra: str = "") -> str:
    when = (f"DTSTART;TZID=Europe/Berlin:{start}\r\nDTEND;TZID=Europe/Berlin:{end}\r\n" if "T" in start
            else f"DTSTART;VALUE=DATE:{start}\r\nDTEND;VALUE=DATE:{end}\r\n")
    head = f"UID:{uid}\r\n" if uid else ""
    return f"BEGIN:VEVENT\r\n{head}DTSTAMP:20251001T000000Z\r\n{when}SUMMARY:{title}\r\n{extra}END:VEVENT\r\n"


def take(client: TestClient, data: bytes, name: str = "familie-2025.ics") -> Any:
    return client.post("/api/imports", params={"name": name}, content=data,
                       headers={"Content-Type": "text/calendar"})


def taken(client: TestClient, data: bytes, name: str = "familie-2025.ics") -> dict[str, Any]:
    answer = take(client, data, name)
    assert answer.status_code == 201, answer.text
    return answer.json()


def apply(client: TestClient, import_id: str, calendar_id: int) -> Any:
    return client.post(f"/api/imports/{import_id}/apply", json={"calendar_id": calendar_id})


def finished(client: TestClient, import_id: str, calendar_id: int) -> dict[str, Any]:
    """Starts the import, waits for its job and gives its state."""
    started = apply(client, import_id, calendar_id)
    assert started.status_code in (200, 202), started.text
    transfer.wait_jobs()
    answer = client.get(f"/api/imports/{import_id}")
    assert answer.status_code == 200, answer.text
    return answer.json()


def applied(client: TestClient, import_id: str, calendar_id: int) -> dict[str, Any]:
    state = finished(client, import_id, calendar_id)
    assert state["state"] == "done", state
    return state["result"]


def move_state(client: TestClient, calendar_id: int, ref: str) -> dict[str, Any]:
    started = client.post(f"/api/calendars/{calendar_id}/move", json={"client_ref": ref})
    assert started.status_code in (200, 202), started.text
    transfer.wait_jobs()
    answer = client.get(f"/api/moves/{ref}")
    assert answer.status_code == 200, answer.text
    return answer.json()


def moved(client: TestClient, calendar_id: int, ref: str) -> dict[str, Any]:
    state = move_state(client, calendar_id, ref)
    assert state["state"] == "done", state
    return state["result"]


def _staging() -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(Calendar).where(Calendar.staging.is_(True))) or 0)


def titles_in(client: TestClient, calendar_id: int, start: str = "2025-01-01T00:00:00Z",
              end: str = "2026-01-01T00:00:00Z") -> list[str]:
    return sorted(row["title"] for row in window(client, start, end, calendars=str(calendar_id), hidden="true"))


def export(client: TestClient, calendar_id: int) -> str:
    answer = client.get(f"/api/calendars/{calendar_id}/export.ics")
    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("text/calendar")
    return answer.text


def imports_left() -> list[str]:
    folder = get_settings().data_dir / "imports"
    return sorted(path.name for path in folder.glob("*.ics")) if folder.is_dir() else []


# --- Export (AC-1, AC-7, EC-3, EC-4, EC-8) -----------------------------------------------------------------------------


def test_time24_ac1_a_calendar_downloads_with_series_reminders_and_places(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    dentist = make_event(client, "Zahnarzt", "2026-11-27T09:00", "2026-11-27T09:30", calendar_id=familie,
                         location="Praxis Sommer", notes="Karte mitnehmen")
    assert client.put(f"/api/events/{dentist['id']}/reminders", json={"minutes": [60]}).status_code == 200
    series = make_event(client, "Training", "2026-11-26T18:30", "2026-11-26T20:00", calendar_id=familie,
                        rule={"freq": "weekly", "weekdays": ["TH"]})
    one = next(row for row in window(client) if row["title"] == "Training" and row["start"].startswith("2026-12-03"))
    client.patch(f"/api/events/{series['id']}", json={"scope": "this", "occurrence": one["occurrence"],
                                                       "title": "Training fällt aus"})
    answer = client.get(f"/api/calendars/{familie}/export.ics")
    assert answer.status_code == 200
    assert "filename*=UTF-8''Familie.ics" in answer.headers["content-disposition"]
    assert answer.headers["cache-control"] == "no-store"
    text = answer.text
    assert text.startswith("BEGIN:VCALENDAR\r\n") and text.endswith("END:VCALENDAR\r\n")
    assert "X-WR-CALNAME:Familie" in text
    for wanted in ("SUMMARY:Zahnarzt", "LOCATION:Praxis Sommer", "DESCRIPTION:Karte mitnehmen", "RRULE:FREQ=WEEKLY",
                   "RECURRENCE-ID", "SUMMARY:Training fällt aus", "BEGIN:VALARM", "TRIGGER:-PT1H"):
        assert wanted in text, wanted
    # One VTIMEZONE per zone, however many events name it.
    assert text.count("BEGIN:VTIMEZONE") == 1


def test_time24_ec3_shared_to_read_exports_all_busy_only_only_times(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    arbeit = make_calendar(client, "Arbeit")
    make_event(client, "Teamrunde", "2026-11-27T09:00", "2026-11-27T10:00", calendar_id=arbeit,
               location="Raum Nord", notes="Geheime Notiz")
    make_event(client, "Homeoffice", "2026-11-28T09:00", "2026-11-28T17:00", calendar_id=arbeit, busy=False)
    share(arbeit, anna.id, RIGHT_READ)
    with new_client(anna) as browser:
        text = export(browser, arbeit)
        assert "SUMMARY:Teamrunde" in text and "LOCATION:Raum Nord" in text and "Homeoffice" in text
    share(arbeit, anna.id, RIGHT_BUSY)
    with new_client(anna) as browser:
        text = export(browser, arbeit)
        for secret in ("Teamrunde", "Raum Nord", "Geheime Notiz", "Homeoffice"):
            assert secret not in text
        assert text.count("BEGIN:VEVENT") == 1 and "DTSTART" in text
        zipped = browser.get("/api/export.zip")
        assert zipped.status_code == 200
        with zipfile.ZipFile(io.BytesIO(zipped.content)) as archive:
            every = "".join(archive.read(name).decode() for name in archive.namelist() if name.endswith(".ics"))
        for secret in ("Teamrunde", "Raum Nord", "Geheime Notiz", "Homeoffice"):
            assert secret not in every


def test_time24_ac1_ec3_nobody_exports_a_calendar_they_do_not_see(client: TestClient, account: Account) -> None:
    arbeit = make_calendar(client, "Arbeit")
    ben = make_account("ben")
    with new_client(ben) as browser:
        assert browser.get(f"/api/calendars/{arbeit}/export.ics").status_code == 404
    with new_client() as stranger:
        assert stranger.get(f"/api/calendars/{arbeit}/export.ics").status_code == 401
        assert stranger.get("/api/export.zip").status_code == 401


def test_time24_ac1_ac7_ec8_the_zip_has_every_calendar_readable_names_and_the_attachments_beside(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    anna = make_account("anna")
    familie = make_calendar(client, "Familie")
    second = make_calendar(client, "Familie")
    make_calendar(client, "Müll / Grün: Abfuhr", background=True)
    event = make_event(client, "Zeugnis", "2026-11-27T09:00", "2026-11-27T10:00", calendar_id=familie)
    for _ in range(2):
        assert upload(client, event["id"], "zeugnis.txt", b"Notenblatt").status_code == 201
    make_event(client, "Zweiter", "2026-11-27T11:00", "2026-11-27T12:00", calendar_id=second)
    with new_client(anna) as browser:
        theirs = make_calendar(browser, "Annas Kalender")
        make_event(browser, "Annas Termin", "2026-11-27T13:00", "2026-11-27T14:00", calendar_id=theirs)
    share(theirs, account.id, RIGHT_READ)
    secret = "https://example.com/verein.ics"
    body = ics(vevent("abo-1", "Vereinsfest", "20261128", "20261129"), extra="X-WR-CALNAME:Verein\r\n")
    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(lambda request: httpx.Response(200, content=body)))
    assert client.post("/api/sources/subscriptions", json={"url": secret}).status_code == 201
    listed = client.get("/api/calendars").json()["calendars"]
    answer = client.get("/api/export.zip")
    assert answer.status_code == 200 and answer.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(answer.content)) as archive:
        names = archive.namelist()
        assert len([name for name in names if name.endswith(".ics")]) == len(listed)
        assert "Familie.ics" in names and "Familie (2).ics" in names
        assert "Müll - Grün- Abfuhr.ics" in names
        assert "Annas Kalender.ics" in names and "Verein.ics" in names
        assert "Annas Termin" in archive.read("Annas Kalender.ics").decode()
        assert "Vereinsfest" in archive.read("Verein.ics").decode()
        # The attachments beside the file of their calendar, none overwriting the other.
        assert sorted(name for name in names if name.startswith("Familie/")) == ["Familie/zeugnis (2).txt",
                                                                                 "Familie/zeugnis.txt"]
        assert archive.read("Familie/zeugnis.txt") == b"Notenblatt"
    # The single file carries no attachments.
    assert "ATTACH" not in export(client, familie)
    # Nothing stays behind on the server.
    assert not list((get_settings().data_dir / "imports").glob("export-*.zip"))


def test_time24_ec4_an_export_loaded_again_gives_the_same_events(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    make_event(client, "New York", "2025-03-03T09:00", "2025-03-03T10:00", calendar_id=familie, zone="America/New_York")
    make_event(client, "Ferien", "2025-07-28", "2025-08-08", calendar_id=familie, all_day=True)
    make_event(client, "Uralt", "1990-05-01T12:00", "1990-05-01T13:00", calendar_id=familie)
    series = make_event(client, "Chor", "2025-01-07T19:00", "2025-01-07T21:00", calendar_id=familie,
                        rule={"freq": "weekly", "weekdays": ["TU"], "end": "count", "count": 10})
    rows = window(client, "2025-01-01T00:00:00Z", "2025-04-01T00:00:00Z", calendars=str(familie))
    third = [row for row in rows if row["title"] == "Chor"][2]
    client.patch(f"/api/events/{series['id']}", json={"scope": "this", "occurrence": third["occurrence"],
                                                       "start": "2025-01-21T20:00", "end": "2025-01-21T22:00",
                                                       "time_zone": "Europe/Berlin", "all_day": False})
    client.delete(f"/api/events/{series['id']}", params={"scope": "this", "occurrence": rows[3]["occurrence"]
                                                          if rows[3]["title"] == "Chor" else third["occurrence"]})
    text = export(client, familie).encode()
    copy = make_calendar(client, "Kopie")
    loaded = taken(client, text, "Familie.ics")
    assert loaded["events"] == 4
    assert applied(client, loaded["id"], copy)["made"] == 4

    def shape(calendar_id: int, start: str, end: str) -> list[tuple]:
        return sorted((row["title"], row["start"], row["end"], row["all_day"], row["time_zone"])
                      for row in window(client, start, end, calendars=str(calendar_id)))

    for start, end in (("1990-01-01T00:00:00Z", "1990-12-31T00:00:00Z"), ("2025-01-01T00:00:00Z", "2025-12-31T00:00:00Z")):
        assert shape(familie, start, end) == shape(copy, start, end)


# --- Loading a file (AC-2, AC-5, EC-1, EC-2, EC-5 to EC-7, EC-9 to EC-11, EC-14, EC-16) -------------------------------


def test_time24_ac2_the_preview_names_count_period_and_what_is_there_and_skips_it(client: TestClient,
                                                                                   account: Account) -> None:
    familie = make_calendar(client, "Familie")
    data = ics(*(vevent(f"e{n}@example.com", f"Termin {n}", f"2025{month:02d}14T100000", f"2025{month:02d}14T110000")
                 for n, month in enumerate(range(1, 13))))
    first = taken(client, data)
    assert applied(client, first["id"], familie)["made"] == 12
    more = data.replace(b"END:VCALENDAR", vevent("neu-1", "Neu", "20251201T090000", "20251201T100000").encode()
                        + b"END:VCALENDAR")
    preview = taken(client, more)
    assert (preview["name"], preview["events"], preview["first"], preview["last"]) == (
        "familie-2025.ics", 13, "2025-01-14", "2025-12-14")
    checked = client.get(f"/api/imports/{preview['id']}", params={"calendar_id": familie}).json()
    assert (checked["existing"], checked["new"], checked["over_limit"]) == (12, 1, False)
    result = applied(client, preview["id"], familie)
    assert (result["made"], result["existing"]) == (1, 12)
    assert result["calendar"] == {"id": familie, "name": "Familie"}
    assert titles_in(client, familie).count("Termin 3") == 1 and "Neu" in titles_in(client, familie)


def test_time24_ac2_the_same_title_at_the_same_time_counts_as_there_though_the_uid_differs(client: TestClient,
                                                                                         account: Account) -> None:
    familie = make_calendar(client, "Familie")
    make_event(client, "Elternabend", "2025-10-14T19:00", "2025-10-14T20:30", calendar_id=familie)
    data = ics(vevent("other-uid", "  ELTERNABEND ", "20251014T190000", "20251014T203000"),
               vevent("later", "Elternabend", "20251015T190000", "20251015T203000"))
    preview = taken(client, data)
    checked = client.get(f"/api/imports/{preview['id']}", params={"calendar_id": familie}).json()
    assert (checked["existing"], checked["new"]) == (1, 1)
    result = applied(client, preview["id"], familie)
    assert (result["made"], result["existing"]) == (1, 1)
    assert titles_in(client, familie) == ["Elternabend", "Elternabend"]


def test_time24_ec1_a_broken_file_or_no_calendar_is_refused_and_nothing_stays(client: TestClient,
                                                                              account: Account) -> None:
    familie = make_calendar(client, "Familie")
    before = imports_left()
    for data, wanted in ((b"<!doctype html><html><body>Login</body></html>", "import_not_calendar"),
                         (b"", "import_not_calendar"),
                         (b"BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:x\r\nDTSTART:20251010T100000Z\r\n", "import_broken"),
                         (ics("BEGIN:VEVENT\r\nUID:y\r\nSUMMARY:Ohne Zeit\r\nEND:VEVENT\r\n"), "import_broken"),
                         (ics("BEGIN:VTODO\r\nUID:t\r\nSUMMARY:Nur Aufgabe\r\nEND:VTODO\r\n"), "import_no_events")):
        answer = take(client, data)
        assert answer.status_code == 422 and code(answer) == wanted, (data, answer.text)
    assert imports_left() == before
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ImportFile).where(ImportFile.account_id == account.id)) == 0
    # An event that cannot be read at all fails the whole file: nothing half.
    half = taken(client, ics(vevent("ok-1", "Gut"), vevent("bad-1", "Kaputt").replace(
        "DTSTART;TZID=Europe/Berlin:20251014T100000", "DTSTART;TZID=Europe/Berlin:2025-99-99")))
    state = finished(client, half["id"], familie)
    assert (state["state"], state["problem"]) == ("failed", "import_broken")
    assert titles_in(client, familie) == []
    assert _staging() == 0


def test_time24_ec6_tasks_and_other_entries_are_skipped_and_counted(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    data = ics(vevent("e1", "Elternabend"), "BEGIN:VTODO\r\nUID:t1\r\nSUMMARY:Einkaufen\r\nEND:VTODO\r\n",
               "BEGIN:VJOURNAL\r\nUID:j1\r\nSUMMARY:Tagebuch\r\nEND:VJOURNAL\r\n")
    preview = taken(client, data)
    assert (preview["events"], preview["skipped"]) == (1, 2)
    assert applied(client, preview["id"], familie)["made"] == 1
    assert titles_in(client, familie) == ["Elternabend"]


def test_time24_ec5_markup_and_scripts_stay_text(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    title = "<script>alert(1)</script><b>Fest</b>"
    data = ics(vevent("x1", title, extra="LOCATION:<img src=x onerror=alert(2)>\r\n"
                      "DESCRIPTION:javascript:alert(3)\r\n"))
    applied(client, taken(client, data)["id"], familie)
    row = window(client, "2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z")[0]
    assert row["title"] == title and row["location"] == "<img src=x onerror=alert(2)>"
    assert client.get(f"/api/events/{row['id']}").json()["notes"] == "javascript:alert(3)"


def test_time24_ec7_embedded_files_become_attachments_and_too_large_ones_are_named(client: TestClient,
                                                                                   account: Account) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"upload_max_mb": 1})
    familie = make_calendar(client, "Familie")
    small = base64.b64encode(b"Hallo Welt").decode()
    large = base64.b64encode(b"x" * (1024 * 1024 + 10)).decode()
    data = ics(vevent("a1", "Mit Datei", extra=f"ATTACH;VALUE=BINARY;ENCODING=BASE64;FILENAME=gruss.txt:{small}\r\n"),
               vevent("a2", "Mit großer Datei", extra=f"ATTACH;VALUE=BINARY;ENCODING=BASE64;FMTTYPE=text/plain:{large}\r\n"
                      "X-OTHER-APP:bleibt\r\n"))
    preview = taken(client, data)
    assert preview["too_large"] == 1
    result = applied(client, preview["id"], familie)
    assert result["made"] == 2
    assert result["failed"] == [{"title": "Mit großer Datei", "reason": "attachment_too_large"}]
    with SessionLocal() as db:
        files = list(db.scalars(select(Attachment)))
        assert [(row.name, row.size) for row in files] == [("gruss.txt", 10)]
        big = db.scalar(select(Event).where(Event.uid == "a2"))
        assert big is not None and "ATTACH" not in big.ical and "X-OTHER-APP:bleibt" in big.ical


def test_time24_ec2_21000_events_warn_before_and_the_limit_counts_the_account(client: TestClient,
                                                                              account: Account) -> None:
    familie = make_calendar(client, "Familie")
    data = ics(*(vevent(f"m{n}", f"Viele {n}", f"2025{n % 12 + 1:02d}{n % 28 + 1:02d}T100000",
                        f"2025{n % 12 + 1:02d}{n % 28 + 1:02d}T110000") for n in range(21_000)))
    preview = taken(client, data)
    checked = client.get(f"/api/imports/{preview['id']}", params={"calendar_id": familie}).json()
    assert (checked["events"], checked["new"], checked["limit"], checked["over_limit"]) == (21_000, 21_000, 20_000, True)
    small = taken(client, ics(vevent("s1", "Wenige")))
    assert client.get(f"/api/imports/{small['id']}", params={"calendar_id": familie}).json()["over_limit"] is False


def test_time24_ec10_two_clicks_make_it_once_and_give_the_same_result(client: TestClient, account: Account,
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    preview = taken(client, ics(*(vevent(f"d{n}", f"Doppelt {n}") for n in range(30))))
    # The job waits after its first batch until the test lets it go: the second and third click come while it runs.
    go = threading.Event()
    real = transfer._write_batch

    def held(*args: Any) -> None:
        real(*args)
        go.wait(30)

    monkeypatch.setattr(transfer, "_write_batch", held)
    monkeypatch.setattr(transfer, "BATCH", 10)
    first = apply(client, preview["id"], familie)
    assert first.status_code == 202 and first.json()["state"] == "running"
    with new_client(account) as browser:
        second = apply(browser, preview["id"], familie)
    third = apply(client, preview["id"], familie)
    assert [second.status_code, third.status_code] == [202, 202]
    assert second.json()["state"] == third.json()["state"] == "running"
    # Nothing shows while it runs: the events wait in the staging calendar.
    assert titles_in(client, familie) == []
    go.set()
    transfer.wait_jobs()
    done = apply(client, preview["id"], familie)
    assert done.status_code == 200 and done.json()["state"] == "done" and done.json()["result"]["made"] == 30
    assert len(titles_in(client, familie)) == 30
    assert _staging() == 0


def test_time24_ec10_the_file_gone_meanwhile_is_no_500(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    preview = taken(client, ics(vevent("g1", "Weg")))
    applied(client, preview["id"], familie)
    # The file went with the import (or the cleanup); a later click gets the state, not an error.
    assert not (get_settings().data_dir / "imports" / f"{preview['id']}.ics").exists()
    again = apply(client, preview["id"], familie)
    assert again.status_code == 200 and again.json()["result"]["made"] == 1


def test_time24_ec11_the_same_file_twice_makes_nothing_twice_also_without_uids(client: TestClient,
                                                                               account: Account) -> None:
    familie = make_calendar(client, "Familie")
    data = ics(vevent(None, "Ohne Kennung"), vevent(None, "Auch ohne", "20251015T100000", "20251015T110000"),
               vevent("mit-1", "Mit Kennung"))
    assert applied(client, taken(client, data)["id"], familie)["made"] == 3
    again = taken(client, data)
    assert client.get(f"/api/imports/{again['id']}", params={"calendar_id": familie}).json()["existing"] == 3
    result = applied(client, again["id"], familie)
    assert (result["made"], result["existing"]) == (0, 3)
    assert titles_in(client, familie) == ["Auch ohne", "Mit Kennung", "Ohne Kennung"]


def test_time24_ec9_cut_off_in_the_middle_makes_nothing_and_starts_again(client: TestClient, account: Account,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    attached = "ATTACH;VALUE=BINARY;ENCODING=BASE64;FILENAME=a.txt:" + base64.b64encode(b"Datei").decode() + "\r\n"
    preview = taken(client, ics(*(vevent(f"c{n}", f"Abbruch {n}", extra=attached) for n in range(10))))
    real = transfer._write_batch
    calls: list[int] = []

    def stop(*args: Any) -> None:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("the container stops")
        real(*args)

    monkeypatch.setattr(transfer, "_write_batch", stop)
    monkeypatch.setattr(transfer, "BATCH", 4)
    state = finished(client, preview["id"], familie)
    assert (state["state"], state["problem"]) == ("failed", "failed")
    # The first batch was written and went again with its staging calendar and its files.
    assert titles_in(client, familie) == []
    assert _staging() == 0
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Attachment)) == 0
    monkeypatch.setattr(transfer, "_write_batch", real)
    assert applied(client, preview["id"], familie)["made"] == 10


def test_time24_ec9_a_job_cut_off_by_a_stop_is_rolled_back_at_the_start(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    preview = taken(client, ics(*(vevent(f"s{n}", f"Stopp {n}") for n in range(6))))
    # As a stop leaves it: the row "running", a staging calendar with a first batch, no job alive.
    staging = transfer._new_staging(account.id, "Familie", "slate")
    with SessionLocal() as db:
        db.add(Event(calendar_id=staging, uid="s0", href="s0.ics", ical="", etag="", starts_at=transfer.utcnow()))
        row = db.get(ImportFile, preview["id"])
        row.state, row.staging_id, row.calendar_id = "running", staging, familie
        db.commit()
    assert apply(client, preview["id"], familie).json()["state"] == "running"
    assert transfer.recover() >= 1
    assert _staging() == 0
    with SessionLocal() as db:
        found = db.get(ImportFile, preview["id"])
        assert (found.state, found.problem) == ("failed", "interrupted")
        assert db.scalar(select(func.count()).select_from(Event).where(Event.calendar_id == staging)) == 0
    assert applied(client, preview["id"], familie)["made"] == 6


def test_time24_ec14_a_full_disk_leaves_nothing_half(client: TestClient, account: Account,
                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    familie = make_calendar(client, "Familie")
    preview = taken(client, ics(vevent("f1", "Voll"), vevent("f2", "Auch voll")))

    def full(*_args: Any) -> None:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(transfer, "_write_batch", full)
    state = finished(client, preview["id"], familie)
    assert (state["state"], state["problem"]) == ("failed", "storage_full")
    assert titles_in(client, familie) == []
    assert _staging() == 0


def test_time24_ec16_only_own_calendars_take_a_file_and_only_its_owner_sees_it(client: TestClient,
                                                                               account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        theirs = make_calendar(browser, "Annas")
    share(theirs, account.id, "edit")
    preview = taken(client, ics(vevent("o1", "Fremd")))
    refused = apply(client, preview["id"], theirs)
    assert refused.status_code == 403 and code(refused) == "import_target_not_own"
    with new_client(anna) as browser:
        assert browser.get(f"/api/imports/{preview['id']}").status_code == 404
        assert apply(browser, preview["id"], theirs).status_code == 404
    other = make_account("ben")
    with new_client(other) as browser:
        stranger = make_calendar(browser, "Bens")
    assert apply(client, preview["id"], stranger).status_code == 404


def test_time24_the_file_has_a_limit_and_stops_while_it_streams(client: TestClient, account: Account,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    before = imports_left()
    monkeypatch.setattr(transfer, "IMPORT_MAX_BYTES", 1000)
    answer = take(client, ics(*(vevent(f"l{n}", "Lang") for n in range(20))))
    assert answer.status_code == 413 and code(answer) == "import_too_large"
    assert imports_left() == before


def test_time24_ec4_alarms_of_a_file_become_the_members_own_reminders(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    data = ics(vevent("r1", "Mit Erinnerung", extra="BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT30M\r\n"
                                                    "DESCRIPTION:x\r\nEND:VALARM\r\n"))
    applied(client, taken(client, data)["id"], familie)
    with SessionLocal() as db:
        row = db.scalar(select(Event).where(Event.uid == "r1"))
        assert row is not None and "VALARM" not in row.ical
        choice = db.get(ReminderChoice, (row.id, account.id))
        assert choice is not None and choice.minutes == [30]


def test_time24_old_imports_go_after_a_day(client: TestClient, account: Account) -> None:
    from datetime import timedelta

    preview = taken(client, ics(vevent("w1", "Wartet")))
    assert f"{preview['id']}.ics" in imports_left()
    assert transfer.sweep(transfer.utcnow() + timedelta(hours=23)) == 0
    assert f"{preview['id']}.ics" in imports_left()
    assert transfer.sweep(transfer.utcnow() + timedelta(hours=25)) >= 1
    assert f"{preview['id']}.ics" not in imports_left()
    with SessionLocal() as db:
        assert db.get(ImportFile, preview["id"]) is None


# --- Moving house (AC-3, AC-4, AC-6, AC-8, EC-9 to EC-13) --------------------------------------------------------------


def _icloud(client: TestClient, monkeypatch: pytest.MonkeyPatch, dav_server: str, remote: Remote,
            *texts: str, schedules: bool = False) -> tuple[int, int, int]:
    """iCloud with a calendar "Persönlich" holding ``texts``: (source, the calendar there, its copy in nextime)."""
    use_icloud(monkeypatch, dav_server)
    SWITCHES.schedules = schedules
    there = remote.calendar("Persönlich", color="orange")
    for number, text in enumerate(texts):
        remote.put(there, f"e{number}.ics", text)
    source = connected(client, dav_server, remote, ["Persönlich"], kind="icloud")
    round_now(source)
    copy = next(row["id"] for row in client.get("/api/calendars").json()["calendars"]
                if row["name"] == "Persönlich" and row["group"] == "icloud")
    return source, there, copy


def _remote_state(remote: Remote, calendar_id: int) -> dict[str, str]:
    with SessionLocal() as db:
        return {row.href: row.etag for row in db.scalars(select(Event).where(Event.calendar_id == calendar_id,
                                                                             Event.deleted_at.is_(None)))}


def test_time24_ac3_ac4_ac6_an_icloud_calendar_moves_and_icloud_stays_unchanged(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch, dav_server: str,
        remote: Remote) -> None:
    alarm = "BEGIN:VALARM\r\nACTION:DISPLAY\r\nTRIGGER:-PT15M\r\nDESCRIPTION:x\r\nEND:VALARM\r\n"
    _source, there, copy = _icloud(
        client, monkeypatch, dav_server, remote,
        event_text("p1", "Zahnarzt", extra=f"LOCATION:Praxis\r\nX-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC\r\n{alarm}"),
        event_text("p2", "Joggen", "20261124T070000", "20261124T080000", extra="RRULE:FREQ=WEEKLY;BYDAY=TU\r\n"),
        event_text("p3", "Ferien", "20261221", "20261231"))
    before = _remote_state(remote, there)
    SWITCHES.seen.clear()
    assert [row["id"] for row in client.get("/api/moves").json()["calendars"]] == [copy]
    preview = client.get(f"/api/calendars/{copy}/move").json()
    assert (preview["calendar"]["name"], preview["kind"], preview["events"], preview["moved_to"]) == (
        "Persönlich", "icloud", 3, None)
    result = moved(client, copy, "move-1")
    assert (result["made"], result["invitations"], result["failed"], result["kind"]) == (3, 0, [], "icloud")
    new = result["calendar"]["id"]
    own = next(row for row in client.get("/api/calendars").json()["calendars"] if row["id"] == new)
    assert (own["name"], own["color"], own["group"], own["right"]) == ("Persönlich", "orange", "own", "owner")
    titles = {row["title"] for row in window(client, calendars=str(new))}
    assert titles == {"Ferien", "Joggen", "Zahnarzt"}
    with SessionLocal() as db:
        copied = db.scalar(select(Event).where(Event.calendar_id == new, Event.uid == "p1"))
        assert copied is not None and "X-APPLE-TRAVEL-ADVISORY-BEHAVIOR:AUTOMATIC" in copied.ical
        assert "VALARM" not in copied.ical
        assert db.get(ReminderChoice, (copied.id, account.id)).minutes == [15]
        # Nothing waits to go to iCloud, and iCloud got nothing but reading.
        assert not db.scalar(select(func.count()).select_from(RemoteChange))
        assert db.get(CalendarSetting, (account.id, copy)).hidden is True
        assert db.get(Calendar, copy).source_id is not None
    assert [method for method, _path in SWITCHES.seen if method in ("PUT", "DELETE", "POST", "PROPPATCH")] == []
    assert _remote_state(remote, there) == before
    # The old one comes back like any calendar (AC-6).
    assert client.patch(f"/api/calendars/{copy}", json={"hidden": False}).status_code == 200
    assert {row["title"] for row in window(client, calendars=str(copy))} == {"Ferien", "Joggen", "Zahnarzt"}


def test_time24_ac8_only_accepted_invitations_move_as_ordinary_events(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch, dav_server: str,
        remote: Remote) -> None:
    _source, _there, copy = _icloud(
        client, monkeypatch, dav_server, remote,
        invitation("i1", "Zugesagt", me="ACCEPTED"), invitation("i2", "Offen"),
        invitation("i3", "Vielleicht", me="TENTATIVE"), invitation("i4", "Abgesagt", me="DECLINED"),
        event_text("e1", "Eigener"), schedules=True)
    preview = client.get(f"/api/calendars/{copy}/move").json()
    assert (preview["events"], preview["invitations"]) == (2, 3)
    result = moved(client, copy, "inv")
    assert (result["made"], result["invitations"]) == (2, 3)
    with SessionLocal() as db:
        accepted = db.scalar(select(Event).where(Event.calendar_id == result["calendar"]["id"], Event.uid == "i1"))
        assert accepted is not None and "ATTENDEE" not in accepted.ical and "ORGANIZER" not in accepted.ical
    rows = window(client, calendars=str(result["calendar"]["id"]))
    assert sorted(row["title"] for row in rows) == ["Eigener", "Zugesagt"]
    assert all(row.get("invitation") is None for row in rows)


def test_time24_ec10_ec11_one_click_once_and_moving_again_skips_what_is_there(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch, dav_server: str,
        remote: Remote) -> None:
    _source, there, copy = _icloud(client, monkeypatch, dav_server, remote, event_text("a", "Eins"),
                                   event_text("b", "Zwei", "20261127T100000", "20261127T110000"))
    first = moved(client, copy, "same")
    again = moved(client, copy, "same")
    assert first == again
    remote.put(there, "e9.ics", event_text("c", "Drei", "20261128T100000", "20261128T110000"))
    round_now(_source)
    assert client.get(f"/api/calendars/{copy}/move").json()["moved_to"] == first["calendar"]
    later = moved(client, copy, "later")
    assert (later["made"], later["existing"], later["again"]) == (1, 2, True)
    assert later["calendar"] == first["calendar"]
    with SessionLocal() as db:
        own = db.scalar(select(func.count()).select_from(Calendar).where(Calendar.owner_id == account.id,
                                                                          Calendar.source_id.is_(None),
                                                                          Calendar.name == "Persönlich"))
        assert own == 1
    assert sorted(row["title"] for row in window(client, calendars=str(first["calendar"]["id"]))) == [
        "Drei", "Eins", "Zwei"]


def test_time24_ec12_a_shared_calendar_keeps_its_shares_the_new_one_is_not_shared(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch, dav_server: str,
        remote: Remote) -> None:
    anna = make_account("anna")
    _source, _there, copy = _icloud(client, monkeypatch, dav_server, remote, event_text("s", "Geteilt"))
    share(copy, anna.id, RIGHT_READ)
    result = moved(client, copy, "shared")
    assert result["shared"] is True
    with new_client(anna) as browser:
        ids = {row["id"] for row in browser.get("/api/calendars").json()["calendars"]}
        assert copy in ids and result["calendar"]["id"] not in ids
        # The member it is shared with does not move it.
        refused = browser.post(f"/api/calendars/{copy}/move", json={"client_ref": "theirs"})
        assert refused.status_code == 403 and code(refused) == "move_not_possible"


def test_time24_ec9_a_source_not_answering_or_expired_does_not_start_and_a_cut_off_move_leaves_nothing(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch, dav_server: str,
        remote: Remote) -> None:
    source, _there, copy = _icloud(client, monkeypatch, dav_server, remote, event_text("x", "Halb"))
    with SessionLocal() as db:
        db.get(Source, source).state = "unreachable"
        db.commit()
    answer = client.post(f"/api/calendars/{copy}/move", json={"client_ref": "down"})
    assert answer.status_code == 409 and code(answer) == "move_source_unreachable"
    with SessionLocal() as db:
        db.get(Source, source).state = "expired"
        db.commit()
    answer = client.post(f"/api/calendars/{copy}/move", json={"client_ref": "expired"})
    assert answer.status_code == 409 and code(answer) == "move_source_expired"
    with SessionLocal() as db:
        db.get(Source, source).state = "ok"
        db.commit()

    def stop(*_args: Any) -> None:
        raise RuntimeError("the container stops")

    real = transfer._write_batch
    monkeypatch.setattr(transfer, "_write_batch", stop)
    state = move_state(client, copy, "cut")
    assert (state["state"], state["problem"]) == ("failed", "failed")
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Calendar).where(
            Calendar.owner_id == account.id, Calendar.source_id.is_(None), Calendar.name == "Persönlich")) == 0
        assert db.get(CalendarSetting, (account.id, copy)).hidden is False
    assert _staging() == 0
    monkeypatch.setattr(transfer, "_write_batch", real)
    # The same reference starts it again.
    assert moved(client, copy, "cut")["made"] == 1


def test_time24_ec13_google_s_secret_address_moves_other_subscriptions_and_own_calendars_do_not(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = "https://calendar.google.com/calendar/ical/abc%40group.calendar.google.com/private-0a1b2c/basic.ics"
    body = ics(vevent("g1-at-google", "Basar", "20261128", "20261129"), extra="X-WR-CALNAME:Elternbeirat\r\n")
    plain = "https://example.com/verein.ics"
    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(lambda request: httpx.Response(200, content=body)))
    assert client.post("/api/sources/subscriptions", json={"url": secret, "google": True}).status_code == 201
    assert client.post("/api/sources/subscriptions", json={"url": plain}).status_code == 201
    listed = [row for row in client.get("/api/calendars").json()["calendars"] if row["group"] == "subscriptions"]
    google_copy = next(row["id"] for row in listed if row["name"] == "Elternbeirat")
    assert client.get("/api/moves").json()["calendars"] == [
        {"id": google_copy, "name": "Elternbeirat", "color": next(row["color"] for row in listed if row["id"] == google_copy),
         "kind": "google"}]
    preview = client.get(f"/api/calendars/{google_copy}/move")
    assert preview.status_code == 200 and preview.json()["kind"] == "google"
    result = moved(client, google_copy, "g")
    assert result["made"] == 1 and result["kind"] == "google"
    others = [row["id"] for row in listed if row["id"] != google_copy]
    for calendar_id in others + [make_calendar(client, "Eigen")]:
        refused = client.post(f"/api/calendars/{calendar_id}/move", json={"client_ref": f"no-{calendar_id}"})
        assert refused.status_code == 403 and code(refused) == "move_not_possible"


def test_time24_ec7_attachments_move_along_and_too_large_ones_are_named(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch, dav_server: str,
        remote: Remote) -> None:
    from app.services import remote_files

    with SessionLocal() as db:
        settings_service.save(db, {"upload_max_mb": 1})
    small = base64.b64encode(b"Rechnung").decode()
    fetched: list[str] = []

    def fetch(_db: Any, _calendar: Any, url: str, max_bytes: int, into: Path) -> int:
        fetched.append(url)
        into.write_bytes(b"Plan aus iCloud")
        return 15

    monkeypatch.setattr(remote_files, "allowed", lambda _source, _url: True)
    monkeypatch.setattr(remote_files, "fetch", fetch)
    _source, _there, copy = _icloud(
        client, monkeypatch, dav_server, remote,
        event_text("f1", "Eingebettet", extra=f"ATTACH;VALUE=BINARY;ENCODING=BASE64;FILENAME=rechnung.txt:{small}\r\n"),
        event_text("f2", "Auf dem Server", extra="ATTACH;FILENAME=plan.txt:https://p01.icloud.com/f/plan.txt\r\n"),
        event_text("f3", "Zu groß", extra="ATTACH;SIZE=30000000;FILENAME=film.mov:https://p01.icloud.com/f/film.mov\r\n"))
    result = moved(client, copy, "files")
    assert result["made"] == 3
    assert result["failed"] == [{"title": "Zu groß", "reason": "attachment_too_large"}]
    assert fetched == ["https://p01.icloud.com/f/plan.txt"]
    with SessionLocal() as db:
        names = sorted(row.name for row in db.scalars(select(Attachment)))
        assert names == ["plan.txt", "rechnung.txt"]
        big = db.scalar(select(Event).where(Event.calendar_id == result["calendar"]["id"], Event.uid == "f3"))
        # Not fetched: its link stays with the event.
        assert big is not None and "film.mov" in big.ical


def test_time24_only_the_member_who_connected_moves_and_never_an_unknown_calendar(client: TestClient,
                                                                                  account: Account) -> None:
    assert client.get("/api/calendars/999999/move").status_code == 404
    assert client.post("/api/calendars/999999/move", json={"client_ref": "x"}).status_code == 404
    with new_client() as stranger:
        assert stranger.post("/api/calendars/1/move", json={"client_ref": "x"}).status_code == 401
