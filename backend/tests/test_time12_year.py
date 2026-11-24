"""TIME-12 AC-16 to AC-22 on the server: the year as a heat map counts the events of each day for the calendars the
views show (the same rules as TIMEIOS-8 in the app), and comes quickly with 20 calendars and 20,000 events."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert

from app.db import SessionLocal
from app.models import ORIGIN_IMIP, RIGHT_BUSY, Account, Event
from app.services import ical, yearmap

from .calendar_helpers import code, make_calendar, make_event, named, share
from .conftest import make_account, new_client
from .test_time12 import _fill


def year(client: TestClient, number: int = 2026, **query: Any) -> dict[str, Any]:
    answer = client.get("/api/events/year", params={"year": number, "zone": "Europe/Berlin", **query})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_time12_ac17_counts_each_day_series_multi_day_and_over_midnight(client: TestClient, account: Account) -> None:
    make_event(client, "Zahnarzt", "2026-03-03T09:00", "2026-03-03T10:00")
    make_event(client, "Elternabend", "2026-03-03T19:00", "2026-03-03T20:00")
    # A weekly series on Mondays, four times, the second one deleted (EC-3 of TIMEIOS-8: it no longer counts).
    series = make_event(client, "Training", "2026-01-05T18:30", "2026-01-05T20:00",
                        rule={"freq": "weekly", "weekdays": ["MO"], "end": "count", "count": 4})
    gone = client.delete(f"/api/events/{series['id']}", params={"scope": "this", "occurrence": "20260112T173000Z"})
    assert gone.status_code in (200, 204), gone.text
    # A holiday over five days, an evening over midnight, a night across the turn of the year.
    make_event(client, "Urlaub", "2026-07-20", "2026-07-24", all_day=True)
    make_event(client, "Party", "2026-04-10T22:00", "2026-04-11T02:00")
    make_event(client, "Silvester", "2026-12-31T22:00", "2027-01-01T01:00")
    days = year(client)["days"]
    assert days["2026-03-03"] == 2
    assert [days.get(day) for day in ("2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26", "2026-02-02")] == [
        1, None, 1, 1, None]
    assert [days.get(f"2026-07-{day}") for day in range(19, 26)] == [None, 1, 1, 1, 1, 1, None]
    assert (days["2026-04-10"], days["2026-04-11"]) == (1, 1)
    assert days["2026-12-31"] == 1
    assert year(client, 2027)["days"] == {"2027-01-01": 1}


def test_time12_ac17_a_day_belongs_to_the_zone_shown(client: TestClient, account: Account) -> None:
    # 03:00 in Berlin is still the evening before in New York (TIMEIOS-8 EC-8).
    make_event(client, "Früh", "2026-03-10T03:00", "2026-03-10T04:00")
    assert year(client, zone="Europe/Berlin")["days"] == {"2026-03-10": 1}
    assert year(client, zone="America/New_York")["days"] == {"2026-03-09": 1}
    assert code(client.get("/api/events/year", params={"year": 2026, "zone": "Mars/Olympus"})) == "invalid_input"
    assert client.get("/api/events/year", params={"year": 99999}).status_code == 422


def test_time12_ec18_an_empty_year_is_simply_free(client: TestClient, account: Account) -> None:
    assert year(client, 2031, zone="Europe/Berlin") == {"year": 2031, "zone": "Europe/Berlin", "days": {}, "holidays": []}


def test_time12_ac18_background_hidden_collection_duplicates_and_only_this(client: TestClient,
                                                                          account: Account) -> None:
    personal = named(client, "Personal")["id"]
    arbeit = make_calendar(client, "Arbeit")
    geburtstage = make_calendar(client, "Geburtstage", background=True)
    geheim = make_calendar(client, "Geheim")
    make_event(client, "Planung", "2026-05-04T09:00", "2026-05-04T10:00", calendar_id=arbeit)
    make_event(client, "Oma", "2026-05-04", "2026-05-04", all_day=True, calendar_id=geburtstage)
    make_event(client, "Heimlich", "2026-05-04T12:00", "2026-05-04T13:00", calendar_id=geheim)
    # The same event in two of one's own calendars counts once (TIME-14 AC-3), even if the person keeps them apart.
    make_event(client, "Kino", "2026-05-04T20:00", "2026-05-04T22:00", calendar_id=personal)
    make_event(client, "Kino", "2026-05-04T20:00", "2026-05-04T22:00", calendar_id=arbeit)
    assert client.put("/api/me/display", json={"merge_duplicates": False}).status_code == 200
    assert client.patch(f"/api/calendars/{geheim}", json={"hidden": True}).status_code == 200
    # "All": no background, no hidden calendar, the duplicate once.
    assert year(client)["days"] == {"2026-05-04": 2}
    # A collection without "Arbeit".
    assert year(client, calendars=f"{personal},{geburtstage}")["days"] == {"2026-05-04": 1}
    # The hidden calendar shown alone counts, as every view shows it then.
    assert year(client, calendars=str(geheim), hidden="true")["days"] == {"2026-05-04": 1}
    # A background calendar never counts, even alone.
    assert year(client, calendars=str(geburtstage), hidden="true")["days"] == {}


def test_time12_ac20_days_of_a_highlighted_calendar_come_as_holidays_without_counting(client: TestClient,
                                                                                     account: Account) -> None:
    feiertage = make_calendar(client, "Feiertage", background=True)
    assert client.patch(f"/api/calendars/{feiertage}", json={"holiday": True}).status_code == 200
    make_event(client, "Tag der Arbeit", "2026-05-01", "2026-05-01", all_day=True, calendar_id=feiertage)
    make_event(client, "Grillen", "2026-05-01T15:00", "2026-05-01T18:00")
    ferien = make_calendar(client, "Ferien", background=True)
    make_event(client, "Pfingstferien", "2026-05-26", "2026-05-29", all_day=True, calendar_id=ferien)
    found = year(client)
    assert found["days"] == {"2026-05-01": 1}
    assert found["holidays"] == ["2026-05-01"]


def test_time12_ec24_busy_only_counts_and_tells_nothing_more(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        arbeit = make_calendar(browser, "Annas Arbeit")
        make_event(browser, "Geheimes Projekt", "2026-06-02T09:00", "2026-06-02T11:00", calendar_id=arbeit)
        make_event(browser, "Frei", "2026-06-02T13:00", "2026-06-02T14:00", calendar_id=arbeit, busy=False)
    share(arbeit, account.id, RIGHT_BUSY)
    answer = client.get("/api/events/year", params={"year": 2026})
    assert answer.json()["days"] == {"2026-06-02": 1}
    assert "Geheim" not in answer.text and "Frei" not in answer.text


def _invitation(calendar_id: int, uid: str, start: str, me: str) -> None:
    """An invitation entered from a mail (TIME-26), the member ``tester@example.com`` with ``me`` as its answer."""
    text = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//nextime//tests//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:20261101T000000Z\r\nDTSTART:{start}\r\nDTEND:{start[:9]}200000Z\r\n"
            f"SUMMARY:{uid}\r\nORGANIZER;CN=Chris:mailto:chris@example.com\r\n"
            f"ATTENDEE;CN=Tester;PARTSTAT={me};RSVP=TRUE:mailto:tester@example.com\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    begin = datetime.strptime(start, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    now = datetime.now(UTC)
    with SessionLocal() as db:
        db.execute(insert(Event), [{
            "calendar_id": calendar_id, "uid": uid, "href": f"{uid}.ics", "ical": text, "etag": ical.etag_of(text),
            "title": uid, "starts_at": begin, "ends_at": begin.replace(hour=20), "size": len(text),
            "created_at": now, "updated_at": now, "busy": True, "time_zone": "UTC", "location": "", "prev_etag": "",
            "prev_ical": "", "origin": ORIGIN_IMIP, "origin_address": "tester@example.com"}])
        db.commit()


def test_time12_ac18_open_invitations_count_declined_ones_do_not(client: TestClient, account: Account) -> None:
    personal = named(client, "Personal")["id"]
    _invitation(personal, "offen", "20260908T080000Z", "NEEDS-ACTION")
    _invitation(personal, "abgelehnt", "20260909T080000Z", "DECLINED")
    assert year(client)["days"] == {"2026-09-08": 1}


@pytest.mark.timing
def test_time12_ec20_a_year_of_twenty_calendars_and_20000_events_comes_without_a_noticeable_wait(
        client: TestClient, account: Account) -> None:
    ids = [named(client, "Personal")["id"]] + [make_calendar(client, f"K{number}") for number in range(19)]
    _fill(ids, 20_000)
    make_event(client, "Training", "2026-01-05T18:30", "2026-01-05T20:00",
               rule={"freq": "weekly", "weekdays": ["MO", "TH"]})
    for number in (2026, 2027):
        began = time.perf_counter()
        found = year(client, number)
        took = time.perf_counter() - began
        assert len(found["days"]) > 300
        assert took < 1.0, f"{number}: {took:.2f} s"


def test_time12_ec20_single_events_from_their_columns_count_as_every_view_does(client: TestClient, account: Account,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """The quick way (columns) and the way of every view (``occurrence_list``) give the same year."""
    personal = named(client, "Personal")["id"]
    arbeit = make_calendar(client, "Arbeit")
    feiertage = make_calendar(client, "Feiertage", background=True)
    assert client.patch(f"/api/calendars/{feiertage}", json={"holiday": True}).status_code == 200
    make_event(client, "Neujahr", "2026-01-01", "2026-01-01", all_day=True, calendar_id=feiertage)
    make_event(client, "Kino", "2026-05-04T20:00", "2026-05-04T22:00", calendar_id=personal)
    make_event(client, "kino ", "2026-05-04T20:00", "2026-05-04T22:00", calendar_id=arbeit)
    make_event(client, "Party", "2026-04-10T22:00", "2026-04-11T02:00")
    make_event(client, "Urlaub", "2026-12-28", "2027-01-03", all_day=True, calendar_id=arbeit)
    make_event(client, "Training", "2026-01-05T18:30", "2026-01-05T20:00",
               rule={"freq": "weekly", "weekdays": ["MO", "TH"], "end": "count", "count": 20})
    _invitation(personal, "offen", "20260908T080000Z", "NEEDS-ACTION")
    _invitation(personal, "abgelehnt", "20260909T080000Z", "DECLINED")
    anna = make_account("anna")
    with new_client(anna) as browser:
        annas = make_calendar(browser, "Annas Arbeit")
        make_event(browser, "Projekt", "2026-06-02T09:00", "2026-06-02T11:00", calendar_id=annas)
        make_event(browser, "Frei", "2026-06-02T13:00", "2026-06-02T14:00", calendar_id=annas, busy=False)
    share(annas, account.id, RIGHT_BUSY)
    quick = [year(client, 2026), year(client, 2026, zone="America/New_York"), year(client, 2027)]
    monkeypatch.setattr(yearmap, "FAST", False)
    assert [year(client, 2026), year(client, 2026, zone="America/New_York"), year(client, 2027)] == quick
    assert quick[0]["days"]["2026-05-04"] == 1 and quick[0]["holidays"] == ["2026-01-01"]
