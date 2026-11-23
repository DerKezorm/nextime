"""TIME-13, the column beside the month while one calendar is shown alone (decided while testing, 2026-11-23): every event
of that calendar, coming first then past, a series once with its next occurrence and its rule; by the rights of the
views: a calendar the account does not see is not there, "busy only" without title and place and without its free
events, nothing from the trash."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.models import RIGHT_BUSY, Account

from .calendar_helpers import make_calendar, make_event, share
from .conftest import make_account, new_client

NOW = "2026-11-26T15:00:00+00:00"


def listing(client: TestClient, calendar_id: int, **query: object) -> dict:
    answer = client.get(f"/api/search/calendar/{calendar_id}", params={"now": NOW, "zone": "Europe/Berlin", **query})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_time13_calendar_list_column_coming_then_past_a_series_once_with_its_rule(client: TestClient,
                                                                                 account: Account) -> None:
    geburtstage = make_calendar(client, "Geburtstage")
    make_event(client, "Oma", start="1954-12-09", end="1954-12-09", all_day=True, calendar_id=geburtstage,
               rule={"freq": "yearly"})
    make_event(client, "Ben", start="2026-11-30", end="2026-11-30", all_day=True, calendar_id=geburtstage)
    make_event(client, "Vorbei", start="2026-10-01T10:00", end="2026-10-01T11:00", calendar_id=geburtstage,
               location="Lindenstraße 4")
    gone = make_event(client, "Weg", start="2026-12-01T10:00", end="2026-12-01T11:00", calendar_id=geburtstage)
    assert client.delete(f"/api/events/{gone['id']}").status_code == 200
    make_event(client, "Anderswo", start="2026-12-02T10:00", end=None)
    found = listing(client, geburtstage)
    assert [item["title"] for item in found["items"]] == ["Ben", "Oma", "Vorbei"]
    assert found["future_total"] == 2 and found["total"] == 3
    oma = found["items"][1]
    assert oma["recurring"] is True and oma["start"] == "2026-12-09" and oma["rrule"].startswith("FREQ=YEARLY")
    assert oma["first"] == "1954-12-09"
    assert found["items"][2]["location"] == "Lindenstraße 4" and found["items"][2]["coming"] is False
    # A page at a time.
    assert [item["title"] for item in listing(client, geburtstage, offset=2, limit=5)["items"]] == ["Vorbei"]


def test_time13_calendar_list_column_by_the_rights_of_the_views(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        privat = make_calendar(browser, "Annas")
        make_event(browser, "Geheim", start="2026-12-03T10:00", end=None, calendar_id=privat, location="Praxis")
        make_event(browser, "Frei", start="2026-12-04T10:00", end=None, calendar_id=privat, busy=False)
        nicht = make_calendar(browser, "Nicht geteilt")
    share(privat, account.id, RIGHT_BUSY)
    found = listing(client, privat)
    assert len(found["items"]) == 1
    item = found["items"][0]
    assert item["title"] == "" and item["location"] == "" and item["busy_only"] is True
    assert "Geheim" not in str(found) and "Praxis" not in str(found)
    assert client.get(f"/api/search/calendar/{nicht}", params={"now": NOW}).status_code == 404


# --- The column for "Alles" and a collection, in Month and Week (asked for while testing, 2026-11-23) --------------------


def upcoming(client: TestClient, **query: object) -> dict:
    answer = client.get("/api/search/upcoming", params={"now": NOW, "zone": "Europe/Berlin", **query})
    assert answer.status_code == 200, answer.text
    return answer.json()


def test_time13_calendar_list_column_all_shown_calendars_never_a_hidden_one(client: TestClient,
                                                                            account: Account) -> None:
    familie = make_calendar(client, "Familie")
    arbeit = make_calendar(client, "Arbeit")
    versteckt = make_calendar(client, "Versteckt")
    make_event(client, "Elternabend", start="2026-12-01T19:00", end="2026-12-01T20:00", calendar_id=familie)
    make_event(client, "Planung", start="2026-11-30T09:00", end="2026-11-30T10:00", calendar_id=arbeit)
    make_event(client, "Heimlich", start="2026-11-29T09:00", end="2026-11-29T10:00", calendar_id=versteckt)
    assert client.patch(f"/api/calendars/{versteckt}", json={"hidden": True}).status_code == 200
    found = upcoming(client)
    assert [(item["title"], item["calendar_id"]) for item in found["items"]] == [
        ("Planung", arbeit), ("Elternabend", familie)]
    assert found["total"] == 2 and "Heimlich" not in str(found)
    # A collection: only its calendars; a hidden one in it still never.
    only = upcoming(client, calendars=f"{familie},{versteckt}")
    assert [item["title"] for item in only["items"]] == ["Elternabend"]


def test_time13_calendar_list_column_all_by_the_rights_busy_without_title_twins_once(client: TestClient,
                                                                                    account: Account) -> None:
    eigen = make_calendar(client, "Eigen")
    zweit = make_calendar(client, "Zweit")
    make_event(client, "Zahnarzt", start="2026-12-02T08:00", end="2026-12-02T09:00", calendar_id=eigen)
    make_event(client, "Zahnarzt", start="2026-12-02T08:00", end="2026-12-02T09:00", calendar_id=zweit)
    anna = make_account("anna")
    with new_client(anna) as browser:
        privat = make_calendar(browser, "Annas")
        make_event(browser, "Geheim", start="2026-12-03T10:00", end=None, calendar_id=privat, location="Praxis")
        make_event(browser, "Frei", start="2026-12-04T10:00", end=None, calendar_id=privat, busy=False)
        fremd = make_calendar(browser, "Nicht geteilt")
        make_event(browser, "Fremd", start="2026-12-05T10:00", end=None, calendar_id=fremd)
    share(privat, account.id, RIGHT_BUSY)
    found = upcoming(client)
    assert [item["title"] for item in found["items"]] == ["Zahnarzt", ""]
    busy = found["items"][1]
    assert busy["busy_only"] is True and busy["location"] == "" and busy["calendar_id"] == privat
    for text in ("Geheim", "Praxis", "Frei", "Fremd"):
        assert text not in str(found)
    # A calendar the account does not see, named on purpose: nothing of it.
    assert upcoming(client, calendars=str(fremd))["items"] == []


@pytest.mark.timing
def test_time13_calendar_list_column_twenty_calendars_twenty_thousand_events_without_a_wait(
        client: TestClient, account: Account) -> None:
    from .test_time22 import _many

    calendars = [make_calendar(client, f"Kalender {number}") for number in range(20)]
    _many(account, calendars)
    for number in range(30):
        make_event(client, f"Serie {number}", start="2018-01-01T07:00", end="2018-01-01T07:30",
                   calendar_id=calendars[0], rule={"freq": "daily"})
    took = []
    for _round in range(3):
        began = time.perf_counter()
        found = upcoming(client)
        took.append(time.perf_counter() - began)
        assert found["total"] == 20_030 and len(found["items"]) == 50
    assert max(took) < 1.0, took


def test_time13_calendar_list_column_since_from_the_first_day_in_the_series_own_zone(client: TestClient,
                                                                                     account: Account) -> None:
    """Review of the additions (2026-11-23): a series from 1 January 00:30 in Berlin began in that year, not the
    year before (its moment in UTC is 31 December)."""
    neujahr = make_calendar(client, "Neujahr")
    make_event(client, "Anstoßen", start="2020-01-01T00:30", end="2020-01-01T01:00", calendar_id=neujahr,
               rule={"freq": "yearly"})
    found = upcoming(client, calendars=str(neujahr))
    assert found["items"][0]["first"] == "2020-01-01"
