"""TIME-11 "Nächste Termine" beside Today (asked for while testing, 2026-11-23): the list of the column, from tomorrow
on, only what still comes, without background calendars (holidays stay small in Today's head), a series once."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import Account

from .calendar_helpers import make_calendar, make_event

NOW = "2026-11-26T15:00:00+00:00"


def next_events(client: TestClient, **query: object) -> dict:
    answer = client.get("/api/search/upcoming", params={"now": NOW, "zone": "Europe/Berlin", "background": "false",
                                                        "from": "2026-11-27", **query})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_time11_next_events_from_tomorrow_only_coming_without_background(client: TestClient,
                                                                          account: Account) -> None:
    familie = make_calendar(client, "Familie")
    feiertage = make_calendar(client, "Feiertage", background=True)
    make_event(client, "Heute Abend", start="2026-11-26T19:00", end="2026-11-26T20:00", calendar_id=familie)
    make_event(client, "Vorbei", start="2026-11-20T19:00", end="2026-11-20T20:00", calendar_id=familie)
    make_event(client, "Werkstatt", start="2026-11-27T08:15", end="2026-11-27T09:00", calendar_id=familie)
    # Tomorrow begins at midnight in Berlin: 00:30 there is still the 27th.
    make_event(client, "Nachtzug", start="2026-11-27T00:30", end="2026-11-27T01:30", calendar_id=familie)
    make_event(client, "Oma", start="1954-12-09", end="1954-12-09", all_day=True, calendar_id=familie,
               rule={"freq": "yearly"})
    make_event(client, "1. Advent", start="2026-11-29", end="2026-11-29", all_day=True, calendar_id=feiertage)
    found = next_events(client)
    assert [item["title"] for item in found["items"]] == ["Nachtzug", "Werkstatt", "Oma"]
    assert found["total"] == 3 and found["future_total"] == 3
    oma = found["items"][2]
    assert oma["start"] == "2026-12-09" and oma["first"] == "1954-12-09"
    # The column of Month and Week keeps today's rest and the background.
    column = client.get("/api/search/upcoming", params={"now": NOW, "zone": "Europe/Berlin"}).json()
    titles = [item["title"] for item in column["items"]]
    assert "Heute Abend" in titles and "1. Advent" in titles


def test_time11_next_events_a_page_at_a_time(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    for day in range(1, 13):
        make_event(client, f"Termin {day:02d}", start=f"2026-12-{day:02d}T10:00", end=f"2026-12-{day:02d}T11:00",
                   calendar_id=familie)
    first = next_events(client, limit=10)
    assert len(first["items"]) == 10 and first["total"] == 12
    rest = next_events(client, limit=10, offset=10)
    assert [item["title"] for item in rest["items"]] == ["Termin 11", "Termin 12"]


def test_time11_next_events_a_series_with_one_still_to_come_today_stands_with_its_next_from_tomorrow(
        client: TestClient, account: Account) -> None:
    """Review of the additions (2026-11-23): a series with an occurrence later today is not left out."""
    familie = make_calendar(client, "Familie")
    make_event(client, "Tabletten", start="2026-11-20T20:00", end="2026-11-20T20:15", calendar_id=familie,
               rule={"freq": "daily"})
    make_event(client, "Büro", start="2026-11-23T08:00", end="2026-11-23T16:00", calendar_id=familie,
               rule={"freq": "weekly", "weekdays": ["MO", "TU", "WE", "TH", "FR"]})
    make_event(client, "Chor", start="2026-11-19T19:00", end="2026-11-19T21:00", calendar_id=familie,
               rule={"freq": "weekly", "weekdays": ["TH"]})
    found = next_events(client)
    starts = {item["title"]: item["start"] for item in found["items"]}
    assert starts == {"Büro": "2026-11-27T07:00:00Z", "Tabletten": "2026-11-27T19:00:00Z",
                      "Chor": "2026-12-03T18:00:00Z"}
    assert [item["title"] for item in found["items"]] == ["Büro", "Tabletten", "Chor"]
    assert all(item["recurring"] for item in found["items"])
