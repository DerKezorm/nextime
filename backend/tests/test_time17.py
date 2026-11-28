"""TIME-17 Subscriptions and holidays: ICS links read only and fetched hourly, the holidays and school holidays of
Germany, Austria and Switzerland built in.

The links are served by a fake behind ``httpx.MockTransport``: nothing leaves the machine.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.db import SessionLocal
from app.models import RIGHT_READ, Account, Calendar, Source
from app.services import holidays, internet, logs, subscriptions, sync

from .calendar_helpers import calendars_of, code, make_calendar, named, share, window
from .conftest import make_account, new_client
from .remote_helpers import mirrored, quiet_world, source_row  # noqa: F401 - the fixture


@dataclass
class Feeds:
    """Links and what they answer: ``(status, body, headers)``; a callable answers itself."""

    answers: dict[str, object] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    login: tuple[str, str] | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if self.login is not None:
            expected = httpx.BasicAuth(*self.login)._auth_header
            if request.headers.get("authorization") != expected:
                return httpx.Response(401)
        answer = self.answers.get(url)
        if answer is None:
            return httpx.Response(404)
        if callable(answer):
            return answer(request)
        status, body, headers = answer
        if headers.get("ETag") and request.headers.get("if-none-match") == headers["ETag"]:
            return httpx.Response(304)
        return httpx.Response(status, content=body.encode() if isinstance(body, str) else body, headers=headers)


@pytest.fixture
def feeds(monkeypatch: pytest.MonkeyPatch) -> Iterator[Feeds]:
    found = Feeds()
    monkeypatch.setattr(subscriptions, "transport", httpx.MockTransport(found.handler))
    yield found


def _event(uid: str, title: str, day: str, extra: str = "") -> str:
    return (f"BEGIN:VEVENT\r\nUID:{uid}\r\nDTSTAMP:20261101T000000Z\r\nDTSTART;VALUE=DATE:{day}\r\n"
            f"SUMMARY:{title}\r\n{extra}END:VEVENT\r\n")


def feed(*events: str, name: str = "Abfallkalender Musterstadt") -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Abfall//DE\r\n"
            f"X-WR-CALNAME:{name}\r\n" + "".join(events) + "END:VCALENDAR\r\n")


ABFALL = "https://abfall.example.com/musterstadt.ics"
WASTE = feed(*[_event(f"m{n}@abfall", "Restmüll", f"2027{1 + n // 7:02d}{1 + (n % 7) * 4:02d}") for n in range(74)])


def _subscribe(client: TestClient, url: str = ABFALL, **values: object) -> dict:
    answer = client.post("/api/sources/subscriptions", json={"url": url, "name": "Müllabfuhr", **values})
    assert answer.status_code in (200, 201), answer.text
    return answer.json()


def _source_id(client: TestClient) -> int:
    return int(client.get("/api/sources").json()["sources"][0]["id"])


# --- Subscriptions (AC-1, AC-2, AC-5, EC-1 to EC-11, EC-15, EC-16) ---------------------------------------------------


def test_time17_ac1_the_preview_names_source_count_and_span_and_subscribing_makes_it_read_only(
        client: TestClient, account: Account, feeds: Feeds) -> None:
    feeds.answers[ABFALL] = (200, WASTE, {"Content-Type": "text/calendar"})
    preview = client.post("/api/sources/subscriptions/preview", json={"url": ABFALL}).json()
    assert (preview["name"], preview["count"], preview["first"][:7], preview["last"][:7]) == (
        "Abfallkalender Musterstadt", 74, "2027-01", "2027-11")
    # A preview makes nothing ("Cancel" leaves nothing behind).
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Source)) == 0
    made = _subscribe(client, background=True)
    calendar = next(row for row in calendars_of(client)["calendars"] if row["name"] == "Müllabfuhr")
    assert calendar["group"] == "subscriptions" and calendar["writable"] is False and calendar["background"] is True
    assert made["sources"][0]["kind"] == "subscription"
    assert len(window(client, "2027-01-01T00:00:00Z", "2027-12-31T00:00:00Z")) == 74
    first = window(client, "2027-01-01T00:00:00Z", "2027-01-31T00:00:00Z")[0]
    assert client.patch(f"/api/events/{first['id']}", json={"title": "x"}).status_code == 403


def test_time17_ac2_changes_come_once_an_hour(client: TestClient, account: Account, feeds: Feeds,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Gelbe Tonne", "20270105")), {"ETag": '"v1"'})
    _subscribe(client)
    source = _source_id(client)
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Gelbe Tonne", "20270106")), {"ETag": '"v2"'})
    now = sync.clock()
    monkeypatch.setattr(sync, "clock", lambda: now + timedelta(minutes=59))
    calls = len(feeds.calls)
    sync.run_due()
    assert len(feeds.calls) == calls
    monkeypatch.setattr(sync, "clock", lambda: now + timedelta(minutes=61))
    sync.run_due()
    assert len(feeds.calls) == calls + 1
    assert [row["start"] for row in window(client, "2027-01-01T00:00:00Z", "2027-01-31T00:00:00Z")] == ["2027-01-06"]
    assert source_row(client, source)["state"] == "ok"


def test_time17_ac5_the_row_names_the_link_without_secret_fetch_again_and_remove(
        client: TestClient, account: Account, feeds: Feeds) -> None:
    secret_url = "https://cal.example.com/private/a8F3kQ9zLmX2pT7v/basic.ics?token=s3cr3t-t0ken"
    feeds.answers[secret_url] = (200, feed(_event("a@x", "Training", "20270105")), {})
    _subscribe(client, secret_url)
    row = client.get("/api/sources").json()["sources"][0]
    assert row["label"] == "https://cal.example.com/private/a8F3…/basic.ics?…"
    calls = len(feeds.calls)
    assert client.post(f"/api/sources/{row['id']}/refresh").status_code == 200
    assert len(feeds.calls) == calls + 1
    assert client.delete(f"/api/sources/{row['id']}").status_code == 200
    assert window(client, "2027-01-01T00:00:00Z", "2027-01-31T00:00:00Z") == []
    assert all(row["group"] != "subscriptions" for row in calendars_of(client)["calendars"])


def test_time17_ec1_a_broken_link_keeps_the_last_events_and_says_since_when(
        client: TestClient, account: Account, feeds: Feeds) -> None:
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Gelbe Tonne", "20270105")), {})
    _subscribe(client)
    source = _source_id(client)
    stand = source_row(client, source)["synced_at"]
    feeds.answers[ABFALL] = (500, "", {})
    assert client.post(f"/api/sources/{source}/refresh").status_code == 200
    row = source_row(client, source)
    assert row["state"] == "unreachable" and row["synced_at"] == stand and row["since"]
    assert [item["title"] for item in window(client, "2027-01-01T00:00:00Z", "2027-01-31T00:00:00Z")] == [
        "Gelbe Tonne"]


def test_time17_ec2_a_huge_subscription_warns_in_the_preview(client: TestClient, account: Account, feeds: Feeds,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import sources

    monkeypatch.setattr(sources, "EVENTS_SMOOTH_UP_TO", 50)
    feeds.answers[ABFALL] = (200, WASTE, {})
    preview = client.post("/api/sources/subscriptions/preview", json={"url": ABFALL}).json()
    assert preview["over"] is True and preview["count"] == 74 and preview["events_smooth_up_to"] == 50


def test_time17_ec3_a_link_with_a_login_asks_for_it_and_refuses_a_wrong_one(
        client: TestClient, account: Account, feeds: Feeds) -> None:
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Probe", "20270105")), {})
    feeds.login = ("verein", "Geheim-2027")
    needs = client.post("/api/sources/subscriptions/preview", json={"url": ABFALL})
    assert needs.status_code == 422 and code(needs) == "subscription_login_needed"
    wrong = client.post("/api/sources/subscriptions/preview", json={"url": ABFALL, "username": "verein",
                                                                    "password": "falsch"})
    assert code(wrong) == "subscription_login_refused" and wrong.json()["detail"]["field"] == "password"
    _subscribe(client, username="verein", password="Geheim-2027")
    assert "Geheim-2027" not in client.get("/api/sources").text
    assert len(window(client, "2027-01-01T00:00:00Z", "2027-01-31T00:00:00Z")) == 1


@pytest.mark.parametrize("body", ["<!doctype html><html><body>Hallo</body></html>", b"\x89PNG\r\n\x1a\n....", ""])
def test_time17_ec4_a_web_page_a_picture_or_nothing_is_no_calendar(client: TestClient, account: Account,
                                                                   feeds: Feeds, body: object) -> None:
    feeds.answers[ABFALL] = (200, body, {})
    answer = client.post("/api/sources/subscriptions", json={"url": ABFALL, "name": "x"})
    assert answer.status_code == 422 and code(answer) == "subscription_not_a_calendar"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Source)) == 0


def test_time17_ec5_webcal_and_blanks_from_the_clipboard_are_taken(client: TestClient, account: Account,
                                                                   feeds: Feeds) -> None:
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Probe", "20270105")), {})
    _subscribe(client, "  webcal://abfall.example.com/musterstadt.ics\n")
    assert feeds.calls[-1] == ABFALL


def test_time17_ec6_the_same_link_twice_makes_no_second_subscription(client: TestClient, account: Account,
                                                                     feeds: Feeds) -> None:
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Probe", "20270105")), {})
    _subscribe(client)
    again = client.post("/api/sources/subscriptions", json={"url": "webcal://abfall.example.com/musterstadt.ics"})
    assert again.status_code == 409 and code(again) == "subscription_exists"
    assert client.post("/api/sources/subscriptions/preview", json={"url": ABFALL}).json()["exists"]


def test_time17_ec7_code_in_a_subscription_comes_as_text(client: TestClient, account: Account, feeds: Feeds) -> None:
    title = "<script>alert(1)</script>"
    feeds.answers[ABFALL] = (200, feed(_event("a@x", title, "20270105", "LOCATION:javascript:alert(2)\r\n")), {})
    _subscribe(client)
    row = window(client, "2027-01-01T00:00:00Z", "2027-01-31T00:00:00Z")[0]
    assert row["title"] == title and row["location"] == "javascript:alert(2)"


def test_time17_ec8_a_fetch_that_breaks_off_leaves_everything_as_it_was(client: TestClient, account: Account,
                                                                        feeds: Feeds) -> None:
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Eins", "20270105"), _event("b@x", "Zwei", "20270106")), {})
    _subscribe(client)
    source = _source_id(client)

    def broken(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection lost", request=request)

    feeds.answers[ABFALL] = broken
    client.post(f"/api/sources/{source}/refresh")
    feeds.answers[ABFALL] = (200, "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:c@x\r\nSUMM", {})
    client.post(f"/api/sources/{source}/refresh")
    assert sorted(row["title"] for row in window(client, "2027-01-01T00:00:00Z", "2027-01-31T00:00:00Z")) == [
        "Eins", "Zwei"]
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Eins", "20270105")), {})
    client.post(f"/api/sources/{source}/refresh")
    assert [row["title"] for row in window(client, "2027-01-01T00:00:00Z", "2027-01-31T00:00:00Z")] == ["Eins"]


def test_time17_ec9_two_clicks_make_one_subscription_and_one_fetch(client: TestClient, account: Account,
                                                                   feeds: Feeds) -> None:
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Probe", "20270105")), {})
    first = _subscribe(client, client_ref="ref-abo")
    second = client.post("/api/sources/subscriptions", json={"url": ABFALL, "name": "x", "client_ref": "ref-abo"})
    assert second.status_code == 200 and second.json()["created"] == first["created"]
    source = first["created"]
    lock = sync._lock(source)
    with lock:
        # A fetch runs: the second "Fetch again" makes none.
        assert sync.run_source(source, force=True) is False
    calls = len(feeds.calls)
    assert sync.run_source(source, force=True) is True and len(feeds.calls) == calls + 1


def test_time17_ec10_zones_whole_days_and_series_show_right(client: TestClient, account: Account,
                                                            feeds: Feeds) -> None:
    timed = ("BEGIN:VEVENT\r\nUID:t@x\r\nDTSTAMP:20261101T000000Z\r\nDTSTART;TZID=America/New_York:20270105T090000\r\n"
             "DTEND;TZID=America/New_York:20270105T100000\r\nRRULE:FREQ=WEEKLY;COUNT=3\r\nSUMMARY:Call\r\n"
             "END:VEVENT\r\n")
    feeds.answers[ABFALL] = (200, feed(timed, _event("d@x", "Ferientag", "20270108")), {})
    _subscribe(client)
    rows = window(client, "2027-01-01T00:00:00Z", "2027-01-31T00:00:00Z")
    assert [row["start"] for row in rows if row["title"] == "Call"] == [
        "2027-01-05T14:00:00Z", "2027-01-12T14:00:00Z", "2027-01-19T14:00:00Z"]
    assert [(row["all_day"], row["start"]) for row in rows if row["title"] == "Ferientag"] == [(True, "2027-01-08")]


def test_time17_ec11_a_secret_in_the_link_never_reaches_answers_or_log(client: TestClient, account: Account,
                                                                       feeds: Feeds) -> None:
    secret_url = "https://cal.example.com/ical/geheimnis-q8Zt-f3x/basic.ics"
    feeds.answers[secret_url] = (200, feed(_event("a@x", "Probe", "20270105")), {})
    _subscribe(client, secret_url)
    sync.run_due()
    for path in ("/api/sources", "/api/calendars"):
        assert "geheimnis-q8Zt-f3x" not in client.get(path).text
    with SessionLocal() as db:
        assert all("geheimnis" not in (row.label + row.secret_enc) for row in db.scalars(select(Source)))
    text = logs.log_file().read_text(encoding="utf-8") if logs.log_file().exists() else ""
    assert "geheimnis-q8Zt-f3x" not in text
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_time17_ec15_without_internet_subscriptions_pause_and_holidays_stay(
        client: TestClient, account: Account, feeds: Feeds, monkeypatch: pytest.MonkeyPatch) -> None:
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Probe", "20270105")), {})
    _subscribe(client)
    assert client.put("/api/sources/holidays", json={"country": "DE", "regions": ["NW"]}).status_code == 200
    monkeypatch.setattr(internet, "probe_override", lambda: False)
    internet.forget()
    calls = len(feeds.calls)
    later = sync.clock() + timedelta(hours=2)
    monkeypatch.setattr(sync, "clock", lambda: later)
    sync.run_due()
    assert len(feeds.calls) == calls
    rows = client.get("/api/sources").json()["sources"]
    assert {row["kind"]: row["state"] for row in rows} == {"subscription": "ok", "holidays": "ok"}
    assert any(row["title"] == "Neujahr" or row["title"] == "New Year's Day"
               for row in window(client, "2027-01-01T00:00:00Z", "2027-01-02T00:00:00Z"))


def test_time17_ec16_removed_subscriptions_and_holidays_leave_their_collections(
        client: TestClient, account: Account, feeds: Feeds) -> None:
    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Probe", "20270105")), {})
    made = _subscribe(client)
    client.put("/api/sources/holidays", json={"country": "DE", "regions": []})
    ids = [row["id"] for row in calendars_of(client)["calendars"] if row["group"] == "subscriptions"]
    assert client.post("/api/collections", json={"name": "Alles leise", "calendar_ids": ids}).status_code == 201
    for row in client.get("/api/sources").json()["sources"]:
        assert client.delete(f"/api/sources/{row['id']}").status_code == 200
    assert client.get("/api/collections").json()["collections"][0]["calendar_ids"] == []
    assert made["created"]


# --- Holidays (AC-3, AC-4, AC-6, AC-7, AC-8, EC-12, EC-13, EC-14) ----------------------------------------------------


GERMANY_2027 = {"2027-01-01": "Neujahr", "2027-03-26": "Karfreitag", "2027-03-29": "Ostermontag",
                "2027-05-01": "Erster Mai", "2027-05-06": "Christi Himmelfahrt", "2027-05-17": "Pfingstmontag",
                "2027-10-03": "Tag der Deutschen Einheit", "2027-12-25": "Erster Weihnachtstag",
                "2027-12-26": "Zweiter Weihnachtstag"}


def _german(client: TestClient) -> None:
    assert client.put("/api/me/language", json={"language": "de"}).status_code in (200, 204)


def _holiday_titles(client: TestClient) -> dict[str, str]:
    calendar_ids = {row["id"] for row in calendars_of(client)["calendars"] if row["name"].startswith(("Feiertage",
                                                                                                      "Holidays"))}
    return {row["start"]: row["title"] for row in window(client, "2027-01-01T00:00:00Z", "2028-01-01T00:00:00Z")
            if row["calendar_id"] in calendar_ids and row["start"].startswith("2027")}


def test_time17_ac3_holidays_of_a_state_and_its_school_holidays_come_as_background(
        client: TestClient, account: Account) -> None:
    _german(client)
    answer = client.put("/api/sources/holidays", json={"country": "DE", "regions": ["NW"], "school": True})
    assert answer.status_code == 200, answer.text
    shown = {row["name"]: (row["background"], row["writable"], row["group"]) for row in
             calendars_of(client)["calendars"] if row["group"] == "subscriptions"}
    assert shown == {"Feiertage Deutschland": (True, False, "subscriptions"),
                     "Schulferien Deutschland": (True, False, "subscriptions")}
    # As from the start only the public holidays stand out in the views ("Tage hervorheben"), school holidays and an
    # own calendar stay quiet (decided while testing).
    make_calendar(client, "Familie", background=True)
    marked = {row["name"]: row["holiday"] for row in calendars_of(client)["calendars"]}
    assert marked["Feiertage Deutschland"] is True and marked["Schulferien Deutschland"] is False
    assert marked["Familie"] is False
    assert not any(flag for name, flag in marked.items() if name not in ("Feiertage Deutschland",))
    # Switched off on purpose, the public holidays stay quiet; null goes back to as from the start.
    public = named(client, "Feiertage Deutschland")["id"]
    assert client.patch(f"/api/calendars/{public}", json={"holiday": False}).status_code == 200
    assert named(client, "Feiertage Deutschland")["holiday"] is False
    assert client.patch(f"/api/calendars/{public}", json={"holiday": None}).status_code == 200
    assert named(client, "Feiertage Deutschland")["holiday"] is True
    titles = _holiday_titles(client)
    for day, name in GERMANY_2027.items():
        assert titles[day] == name, day
    assert titles["2027-05-27"] == "Fronleichnam" and titles["2027-11-01"] == "Allerheiligen"
    school = [row for row in window(client, "2027-10-01T00:00:00Z", "2027-11-15T00:00:00Z")
              if row["title"].startswith("Herbstferien")]
    # One span, not a day each.
    assert len(school) == 1 and school[0]["all_day"] and school[0]["start"] < school[0]["end"]
    assert all(row["busy"] is False for row in window(client, "2027-05-27T00:00:00Z", "2027-05-28T00:00:00Z"))


@pytest.mark.parametrize("country", ["DE", "AT", "CH"])
def test_time17_ac3_every_region_of_a_country_has_its_holidays(client: TestClient, account: Account,
                                                               country: str) -> None:
    regions = next(item for item in client.get("/api/sources/holidays").json()["countries"]
                   if item["code"] == country)["regions"]
    assert len(regions) == {"DE": 16, "AT": 9, "CH": 26}[country]
    for region in regions:
        # A day in the middle of 2027: ``replace(year=2027)`` of a 29 February would have no day.
        middle = sync.clock().date().replace(year=2027, month=6, day=15)
        built = holidays.build(country, [region["code"]], holidays.PUBLIC, middle, "de")
        # Four years around today; even the canton with the fewest has more than four a year.
        assert len(built) >= 4 * 4, region


def test_time17_ac3_austria_and_zurich_against_the_official_list_of_2027(client: TestClient,
                                                                         account: Account) -> None:
    _german(client)
    client.put("/api/sources/holidays", json={"country": "AT", "regions": ["9"]})
    client.put("/api/sources/holidays", json={"country": "CH", "regions": ["ZH"]})
    days = {}
    for row in window(client, "2027-01-01T00:00:00Z", "2028-01-01T00:00:00Z"):
        days.setdefault(row["start"], set()).add(row["title"])
    for day in ("2027-01-06", "2027-05-27", "2027-08-15", "2027-10-26", "2027-12-08"):
        assert days.get(day), f"Austria {day}"
    assert "Nationalfeiertag" in days["2027-10-26"] and "Nationalfeiertag" in days["2027-08-01"]
    zurich = {"2027-01-01", "2027-03-26", "2027-03-29", "2027-05-01", "2027-05-06", "2027-05-17", "2027-08-01",
              "2027-12-25", "2027-12-26"}
    switzerland = next(row["id"] for row in calendars_of(client)["calendars"] if row["name"] == "Feiertage Schweiz")
    swiss = {row["start"] for row in window(client, "2027-01-01T00:00:00Z", "2028-01-01T00:00:00Z")
             if row["start"].startswith("2027") and switzerland in
             {row["calendar_id"], *(item["calendar_id"] for item in row.get("sources", []))}}
    assert swiss == zurich


def test_time17_ac4_ec12_several_states_once_where_both_hold_named_where_one_does(
        client: TestClient, account: Account) -> None:
    _german(client)
    client.put("/api/sources/holidays", json={"country": "DE", "regions": ["NW", "BY"]})
    titles = _holiday_titles(client)
    assert titles["2027-05-27"] == "Fronleichnam"
    assert titles["2027-01-06"] == "Heilige Drei Könige (Bayern)"
    assert titles["2027-01-01"] == "Neujahr"
    assert list(_holiday_titles(client).values()).count("Neujahr") == 1


def test_time17_ac6_ac8_a_row_per_country_and_change_opens_the_choice(client: TestClient, account: Account) -> None:
    client.put("/api/sources/holidays", json={"country": "DE", "regions": ["NW"], "school": True})
    client.put("/api/sources/holidays", json={"country": "AT", "regions": []})
    rows = client.get("/api/sources").json()["sources"]
    assert [(row["kind"], row["holidays"]) for row in rows] == [
        ("holidays", {"country": "DE", "regions": ["NW"], "school": True}),
        ("holidays", {"country": "AT", "regions": [], "school": False})]
    # "Change": the same country again changes its row; school holidays off takes their calendar.
    client.put("/api/sources/holidays", json={"country": "DE", "regions": ["NW", "BY"], "school": False})
    rows = client.get("/api/sources").json()["sources"]
    assert len(rows) == 2 and rows[0]["holidays"]["regions"] == ["NW", "BY"]
    assert [item["name"] for item in rows[0]["calendars"]] == ["Holidays Germany"]
    assert len(mirrored(rows[1]["id"])) == 1


def test_time17_ac7_names_in_the_language_of_the_app(client: TestClient, account: Account) -> None:
    client.put("/api/me/language", json={"language": "en"})
    client.put("/api/sources/holidays", json={"country": "DE", "regions": ["NW"]})
    assert _holiday_titles(client)["2027-10-03"] == "German Unity Day"
    client.put("/api/me/language", json={"language": "de"})
    assert _holiday_titles(client)["2027-10-03"] == "Tag der Deutschen Einheit"


def test_time17_ec13_switzerland_has_no_school_holidays_switch(client: TestClient, account: Account) -> None:
    countries = {item["code"]: item["school"] for item in client.get("/api/sources/holidays").json()["countries"]}
    assert countries == {"DE": True, "AT": True, "CH": False}
    client.put("/api/sources/holidays", json={"country": "CH", "regions": ["ZH"], "school": True})
    row = client.get("/api/sources").json()["sources"][0]
    assert row["holidays"]["school"] is False and len(row["calendars"]) == 1


def test_time17_ec14_a_year_without_published_school_holidays_shows_the_holidays_alone(
        client: TestClient, account: Account, monkeypatch: pytest.MonkeyPatch) -> None:
    later = sync.clock().replace(year=2031, month=6, day=15)  # a 29 February has no 2031
    monkeypatch.setattr(sync, "clock", lambda: later)
    client.put("/api/sources/holidays", json={"country": "DE", "regions": ["NW"], "school": True})
    rows = window(client, "2032-01-01T00:00:00Z", "2033-01-01T00:00:00Z")
    assert rows and all("ferien" not in row["title"].lower() for row in rows)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Calendar).where(Calendar.source_id.is_not(None))) == 2


def test_time17_the_calendar_account_hands_subscriptions_on_read_only(client: TestClient, account: Account,
                                                                      feeds: Feeds) -> None:
    from app.routers import dav

    feeds.answers[ABFALL] = (200, feed(_event("a@x", "Probe", "20270105")), {})
    _subscribe(client)
    assert "subscription" not in dav.PASSED_BY and "icloud" in dav.PASSED_BY
    assert client.get("/api/devices").json()["calendars"][-1]["name"] == "Müllabfuhr"


# --- After the review (2026-11-22) -----------------------------------------------------------------------------------


@pytest.mark.parametrize("link,resolved", [
    ("http://127.0.0.1:8080/cal.ics", None),
    ("http://[::1]/cal.ics", None),
    ("http://169.254.169.254/latest/meta-data", None),
    ("http://0.0.0.0/cal.ics", None),
    ("http://[fe80::1]/cal.ics", None),
    ("https://localhost/cal.ics", ["127.0.0.1"]),
    ("https://intern.example.com/cal.ics", ["192.168.1.20", "169.254.10.1"]),
], ids=["loopback", "ipv6-loopback", "metadata", "any", "ipv6-link-local", "name-to-loopback", "one-link-local"])
def test_time17_ec4_the_server_itself_and_link_local_addresses_are_never_fetched(
        client: TestClient, account: Account, feeds: Feeds, monkeypatch: pytest.MonkeyPatch, link: str,
        resolved: list[str] | None) -> None:
    if resolved is not None:
        monkeypatch.setattr(subscriptions, "resolve", lambda host: resolved)
    feeds.answers[link] = (200, feed(_event("a@x", "Intern", "20270105")), {})
    answer = client.post("/api/sources/subscriptions/preview", json={"url": link})
    assert answer.status_code == 422 and code(answer) == "subscription_address_blocked"
    assert feeds.calls == []


def test_time17_ec4_a_redirect_into_the_server_itself_is_refused_the_home_network_is_not(
        client: TestClient, account: Account, feeds: Feeds, monkeypatch: pytest.MonkeyPatch) -> None:
    feeds.answers[ABFALL] = (302, "", {"Location": "http://127.0.0.1:9000/geheim.ics"})
    feeds.answers["http://127.0.0.1:9000/geheim.ics"] = (200, feed(_event("a@x", "Geheim", "20270105")), {})
    answer = client.post("/api/sources/subscriptions/preview", json={"url": ABFALL})
    assert code(answer) == "subscription_address_blocked"
    assert feeds.calls == [ABFALL]
    # Nextcloud at home stays reachable.
    home = "http://nextcloud.fritz.box/remote.php/dav/public-calendars/x?export"
    monkeypatch.setattr(subscriptions, "resolve", lambda host: ["192.168.178.20"])
    feeds.answers[home] = (200, feed(_event("b@x", "Familie", "20270105")), {})
    assert client.post("/api/sources/subscriptions/preview", json={"url": home}).status_code == 200


def test_time17_ec8_a_server_that_trickles_ends_at_the_limit_of_one_fetch(
        client: TestClient, account: Account, feeds: Feeds, monkeypatch: pytest.MonkeyPatch) -> None:
    import time

    class Trickle(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(20):
                time.sleep(0.1)
                yield b"BEGIN:VEVENT\r\n"

    monkeypatch.setattr(subscriptions, "FETCH_SECONDS", 0.5)
    feeds.answers[ABFALL] = lambda request: httpx.Response(200, stream=Trickle())
    began = time.monotonic()
    answer = client.post("/api/sources/subscriptions/preview", json={"url": ABFALL})
    assert answer.status_code == 502 and code(answer) == "subscription_unreachable"
    assert time.monotonic() - began < 1.5


def test_time17_highlight_its_days_is_each_persons_own_for_a_subscription_and_a_shared_calendar(
        client: TestClient, account: Account, feeds: Feeds) -> None:
    feeds.answers[ABFALL] = (200, WASTE, {"Content-Type": "text/calendar"})
    _subscribe(client, background=True)
    waste = named(client, "Müllabfuhr")["id"]
    assert named(client, "Müllabfuhr")["holiday"] is False
    # Read only at its source, still the person's own choice; null takes it back.
    assert client.patch(f"/api/calendars/{waste}", json={"holiday": True}).status_code == 200
    assert named(client, "Müllabfuhr")["holiday"] is True
    assert client.patch(f"/api/calendars/{waste}", json={"holiday": None}).status_code == 200
    assert named(client, "Müllabfuhr")["holiday"] is False
    # A calendar shared to read: the one it is shared with chooses for itself, the owner's stays as it was.
    family = make_calendar(client, "Familie", background=True)
    other = make_account("reader")
    stranger = make_account("stranger")
    share(family, other.id, RIGHT_READ)
    with new_client(other) as reader, new_client(stranger) as outsider:
        assert reader.patch(f"/api/calendars/{family}", json={"holiday": True}).status_code == 200
        assert named(reader, "Familie")["holiday"] is True
        assert named(client, "Familie")["holiday"] is False
        # Nobody reaches the choice of another: a calendar it does not see is not there.
        assert outsider.patch(f"/api/calendars/{family}", json={"holiday": True}).status_code == 404
        assert outsider.patch(f"/api/calendars/{waste}", json={"holiday": True}).status_code == 404
        assert reader.patch(f"/api/calendars/{waste}", json={"holiday": True}).status_code == 404
    assert named(client, "Familie")["holiday"] is False and named(client, "Müllabfuhr")["holiday"] is False
