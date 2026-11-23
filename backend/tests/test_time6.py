"""TIME-6 Series: the rules nextime offers and their RRULE, short months and leap years, the fifth weekday, the
change of the clocks, and changing or deleting one occurrence, the following or all of them."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import RIGHT_EDIT, Account, Event
from app.services import ical, rules

from .calendar_helpers import code, make_calendar, make_event, share, window
from .conftest import make_account, new_client


def _ical(event_id: int) -> str:
    with SessionLocal() as db:
        row = db.get(Event, event_id)
        assert row is not None
        return row.ical


def _series(client: TestClient, rule: dict, title: str = "Training", start: str = "2026-11-26T18:30",
            end: str = "2026-11-26T20:00", **values) -> dict:
    return make_event(client, title, start, end, rule=rule, **values)


def _days(rows: list[dict], title: str | None = None) -> list[str]:
    return [row["start"][:10] for row in rows if title is None or row["title"] == title]


def _dates(rule: rules.Rule, first: date, until: str) -> list[str]:
    """The days of a whole-day series with this rule up to ``until``, rolled out as every view does."""
    calendar = ical.new_calendar()
    import icalendar

    component = icalendar.Event()
    component.add("UID", "x")
    component.add("DTSTART", first)
    component.add("RRULE", rules.to_rrule(rules.check(rule, first), first))
    calendar.add_component(component)
    end = datetime.fromisoformat(until).replace(tzinfo=UTC)
    return [occurrence.start.isoformat() for occurrence in ical.occurrences(
        calendar, datetime(first.year, first.month, first.day, tzinfo=UTC), end)]


# --- AC-1, EC-8: the rules ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(("rule", "expected"), [
    ({"freq": "daily", "interval": 2}, "FREQ=DAILY;INTERVAL=2"),
    ({"freq": "weekly", "weekdays": ["MO", "TH"]}, "FREQ=WEEKLY;BYDAY=MO,TH"),
    ({"freq": "weekly", "interval": 3, "weekdays": ["TH"], "end": "count", "count": 5},
     "FREQ=WEEKLY;INTERVAL=3;COUNT=5;BYDAY=TH"),
    ({"freq": "monthly", "monthly": "date"}, "FREQ=MONTHLY;BYMONTHDAY=26"),
    ({"freq": "monthly", "monthly": "nth"}, "FREQ=MONTHLY;BYDAY=4TH"),
    ({"freq": "monthly", "monthly": "last"}, "FREQ=MONTHLY;BYDAY=-1TH"),
    ({"freq": "yearly", "end": "until", "until": "2030-11-26"}, "FREQ=YEARLY;UNTIL=20301126T225959Z"),
])
def test_time6_ac1_every_kind_of_rule_with_every_n_and_its_end(client: TestClient, account: Account, rule: dict,
                                                               expected: str) -> None:
    made = _series(client, rule)
    recur = next(line for line in _ical(made["id"]).splitlines() if line.startswith("RRULE:"))
    assert sorted(recur[6:].split(";")) == sorted(expected.split(";"))
    back = made["rule"]
    assert back["freq"] == rule["freq"] and back["interval"] == rule.get("interval", 1)
    assert back["end"] == rule.get("end", "never")


def test_time6_ac1_a_weekly_series_rolls_out_on_its_days(client: TestClient, account: Account) -> None:
    _series(client, {"freq": "weekly", "weekdays": ["MO", "TH"], "end": "count", "count": 4})
    assert _days(window(client)) == ["2026-11-26", "2026-11-30", "2026-12-03", "2026-12-07"]


@pytest.mark.parametrize(("rule", "field", "error"), [
    ({"freq": "daily", "interval": 0}, "rule.interval", "rule_interval"),
    ({"freq": "weekly", "weekdays": []}, "rule.weekdays", "rule_weekdays"),
    ({"freq": "weekly", "weekdays": ["XX"]}, "rule.weekdays", "rule_weekdays"),
    ({"freq": "daily", "end": "until", "until": "2026-11-01"}, "rule.until", "rule_until"),
    ({"freq": "daily", "end": "until", "until": ""}, "rule.until", "rule_until"),
    ({"freq": "daily", "end": "count", "count": 0}, "rule.count", "rule_count"),
])
def test_time6_ec8_invalid_rules_are_refused_at_their_field(client: TestClient, account: Account, rule: dict,
                                                            field: str, error: str) -> None:
    answer = client.post("/api/events", json={"title": "x", "start": "2026-11-26T10:00",
                                              "time_zone": "Europe/Berlin", "rule": rule})
    assert answer.status_code == 422 and code(answer) == error
    assert answer.json()["detail"]["field"] == field
    assert window(client) == []


# --- EC-1, EC-4, EC-5: short months, the fifth weekday, leap years over four years ---------------------------------


def test_time6_ec1_monthly_on_the_31st_lands_on_the_last_day_of_shorter_months() -> None:
    days = _dates(rules.Rule("monthly"), date(2027, 1, 31), "2031-01-01")
    assert days[:5] == ["2027-01-31", "2027-02-28", "2027-03-31", "2027-04-30", "2027-05-31"]
    assert "2028-02-29" in days and len(days) == 48


@pytest.mark.parametrize("day", [29, 30])
def test_time6_ec1_monthly_on_the_29th_and_30th_too(day: int) -> None:
    days = _dates(rules.Rule("monthly"), date(2027, 1, day), "2031-01-01")
    assert days[1] == "2027-02-28" and days[2] == f"2027-03-{day}"
    assert ("2028-02-29" in days) is True and len(days) == 48
    assert all(int(text[8:]) <= day for text in days)


def test_time6_ec4_a_start_on_the_fifth_friday_becomes_the_last_friday() -> None:
    first = date(2027, 1, 29)  # the fifth Friday of January 2027
    assert rules.nth_of(first) == 5
    recur = rules.to_rrule(rules.Rule("monthly", monthly="nth"), first)
    assert recur.to_ical() == b"FREQ=MONTHLY;BYDAY=-1FR"
    days = _dates(rules.Rule("monthly", monthly="last"), first, "2031-01-01")
    assert days[:3] == ["2027-01-29", "2027-02-26", "2027-03-26"]
    assert len(days) == 48 and all(date.fromisoformat(text).weekday() == 4 for text in days)


def test_time6_ec5_yearly_on_the_29th_of_february_is_the_28th_otherwise() -> None:
    days = _dates(rules.Rule("yearly"), date(2028, 2, 29), "2033-01-01")
    assert days == ["2028-02-29", "2029-02-28", "2030-02-28", "2031-02-28", "2032-02-29"]


def test_time6_ec1_ec5_the_rules_read_back_as_the_interface_offers_them() -> None:
    for first, rule in ((date(2027, 1, 31), rules.Rule("monthly")), (date(2027, 1, 30), rules.Rule("monthly")),
                        (date(2028, 2, 29), rules.Rule("yearly")),
                        (date(2027, 1, 29), rules.Rule("monthly", monthly="last"))):
        back = rules.read(rules.to_rrule(rules.check(rule, first), first), first)
        assert back is not None and (back.freq, back.monthly) == (rule.freq, rule.monthly)


# --- EC-3, EC-7, EC-14: the clocks and other zones ---------------------------------------------------------------


def test_time6_ec3_ec7_nine_in_berlin_stays_nine_in_berlin_over_both_changes(client: TestClient,
                                                                           account: Account) -> None:
    _series(client, {"freq": "weekly", "weekdays": ["MO"]}, "Stand-up", "2027-03-22T09:00", "2027-03-22T09:15")
    spring = window(client, "2027-03-20T00:00:00Z", "2027-04-01T00:00:00Z")
    assert [row["start"] for row in spring] == ["2027-03-22T08:00:00Z", "2027-03-29T07:00:00Z"]
    autumn = window(client, "2027-10-20T00:00:00Z", "2027-11-02T00:00:00Z")
    assert [row["start"] for row in autumn] == ["2027-10-25T07:00:00Z", "2027-11-01T08:00:00Z"]
    # New York changes a week later in autumn: seen from there, the meeting moves for a week (EC-7). The instants
    # stay those of Berlin; the page converts them.
    assert all(row["time_zone"] == "Europe/Berlin" for row in spring + autumn)


def test_time6_ec14_a_yearly_whole_day_series_stays_on_its_date(client: TestClient, account: Account) -> None:
    _series(client, {"freq": "yearly"}, "Geburtstag Ben", "2026-11-30", None, zone="", all_day=True)
    rows = window(client, "2026-11-01T00:00:00Z", "2027-12-01T00:00:00Z")
    assert [(row["all_day"], row["start"], row["end"]) for row in rows] == [
        (True, "2026-11-30", "2026-11-30"), (True, "2027-11-30", "2027-11-30")]


# --- AC-2, EC-10, EC-11, EC-15, EC-16: changing and deleting -----------------------------------------------------


def _occurrence(client: TestClient, day: str, title: str = "Training") -> dict:
    return next(row for row in window(client) if row["start"].startswith(day) and row["title"] == title)


def test_time6_ac2_only_this_occurrence_changes_and_the_series_stays(client: TestClient, account: Account) -> None:
    made = _series(client, {"freq": "weekly", "weekdays": ["TH"], "end": "count", "count": 4})
    one = _occurrence(client, "2026-12-03")
    answer = client.patch(f"/api/events/{made['id']}", json={
        "scope": "this", "occurrence": one["occurrence"], "all_day": False, "start": "2026-12-03T19:00",
        "end": "2026-12-03T20:30", "time_zone": "Europe/Berlin", "title": "Training (später)"})
    assert answer.status_code == 200, answer.text
    rows = window(client)
    assert [(row["start"], row["title"]) for row in rows] == [
        ("2026-11-26T17:30:00Z", "Training"), ("2026-12-03T18:00:00Z", "Training (später)"),
        ("2026-12-10T17:30:00Z", "Training"), ("2026-12-17T17:30:00Z", "Training")]
    assert _ical(made["id"]).count("BEGIN:VEVENT") == 2


def test_time6_ac2_deleting_only_this_occurrence_leaves_the_rest(client: TestClient, account: Account) -> None:
    made = _series(client, {"freq": "weekly", "weekdays": ["TH"], "end": "count", "count": 3})
    one = _occurrence(client, "2026-12-03")
    assert client.delete(f"/api/events/{made['id']}", params={"scope": "this",
                                                               "occurrence": one["occurrence"]}).status_code == 200
    assert _days(window(client)) == ["2026-11-26", "2026-12-10"]
    assert "EXDATE" in _ical(made["id"])


def test_time6_ac2_all_occurrences_move_by_the_same_span(client: TestClient, account: Account) -> None:
    made = _series(client, {"freq": "weekly", "weekdays": ["MO", "TH"], "end": "count", "count": 4})
    one = _occurrence(client, "2026-11-30")
    client.patch(f"/api/events/{made['id']}", json={
        "scope": "all", "occurrence": one["occurrence"], "all_day": False, "start": "2026-11-30T19:00",
        "end": "2026-11-30T20:30", "time_zone": "Europe/Berlin"})
    rows = window(client)
    assert {row["start"][11:16] for row in rows} == {"18:00"}
    assert _days(rows) == ["2026-11-26", "2026-11-30", "2026-12-03", "2026-12-07"]


def test_time6_ac2_moving_all_a_day_later_moves_the_weekdays_too(client: TestClient, account: Account) -> None:
    made = _series(client, {"freq": "weekly", "weekdays": ["MO", "TH"], "end": "count", "count": 4},
                   start="2026-11-30T18:30", end="2026-11-30T20:00")
    client.patch(f"/api/events/{made['id']}", json={
        "scope": "all", "occurrence": "20261130T173000Z", "all_day": False, "start": "2026-12-01T18:30",
        "end": "2026-12-01T20:00", "time_zone": "Europe/Berlin"})
    assert _days(window(client)) == ["2026-12-01", "2026-12-04", "2026-12-08", "2026-12-11"]
    assert client.get(f"/api/events/{made['id']}").json()["rule"]["weekdays"] == ["TU", "FR"]


def test_time6_ec11_this_and_following_splits_the_series_in_one_go(client: TestClient, account: Account) -> None:
    made = _series(client, {"freq": "weekly", "weekdays": ["TH"], "end": "count", "count": 5})
    third = _occurrence(client, "2026-12-10")
    answer = client.patch(f"/api/events/{made['id']}", json={
        "scope": "following", "occurrence": third["occurrence"], "title": "Training neu"})
    assert answer.status_code == 200, answer.text
    rows = window(client)
    assert [(row["start"][:10], row["title"]) for row in rows] == [
        ("2026-11-26", "Training"), ("2026-12-03", "Training"), ("2026-12-10", "Training neu"),
        ("2026-12-17", "Training neu"), ("2026-12-24", "Training neu")]
    assert len({row["id"] for row in rows}) == 2
    assert answer.json()["rule"]["count"] == 3


def test_time6_ec11_this_and_following_from_the_first_is_all(client: TestClient, account: Account) -> None:
    made = _series(client, {"freq": "weekly", "weekdays": ["TH"], "end": "count", "count": 3})
    first = _occurrence(client, "2026-11-26")
    client.patch(f"/api/events/{made['id']}", json={"scope": "following", "occurrence": first["occurrence"],
                                                    "title": "Alle"})
    rows = window(client)
    assert {row["title"] for row in rows} == {"Alle"} and {row["id"] for row in rows} == {made["id"]}


def test_time6_ec11_deleting_this_and_following_ends_the_series_before(client: TestClient, account: Account) -> None:
    made = _series(client, {"freq": "weekly", "weekdays": ["TH"]})
    third = _occurrence(client, "2026-12-10")
    client.delete(f"/api/events/{made['id']}", params={"scope": "following", "occurrence": third["occurrence"]})
    assert _days(window(client, end="2027-03-01T00:00:00Z")) == ["2026-11-26", "2026-12-03"]


def test_time6_ec16_changing_all_keeps_changed_and_cancelled_occurrences(client: TestClient, account: Account) -> None:
    made = _series(client, {"freq": "weekly", "weekdays": ["TH"], "end": "count", "count": 4})
    second, third = _occurrence(client, "2026-12-03"), _occurrence(client, "2026-12-10")
    client.patch(f"/api/events/{made['id']}", json={
        "scope": "this", "occurrence": second["occurrence"], "all_day": False, "start": "2026-12-04T10:00",
        "end": "2026-12-04T11:00", "time_zone": "Europe/Berlin"})
    client.delete(f"/api/events/{made['id']}", params={"scope": "this", "occurrence": third["occurrence"]})
    client.patch(f"/api/events/{made['id']}", json={
        "scope": "all", "occurrence": "20261126T173000Z", "all_day": False, "start": "2026-11-26T19:00",
        "end": "2026-11-26T20:00", "time_zone": "Europe/Berlin", "title": "Neu"})
    rows = window(client)
    assert [(row["start"], row["title"]) for row in rows] == [
        ("2026-11-26T18:00:00Z", "Neu"), ("2026-12-04T09:00:00Z", "Training"), ("2026-12-17T18:00:00Z", "Neu")]


def test_time6_ec10_annas_change_to_all_and_my_change_to_one_both_hold(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    calendar = make_calendar(client, "Familie")
    share(calendar, anna.id, RIGHT_EDIT)
    made = _series(client, {"freq": "weekly", "weekdays": ["TH"], "end": "count", "count": 3}, calendar_id=calendar)
    base = made["etag"]
    second = _occurrence(client, "2026-12-03")
    with new_client(anna) as browser:
        assert browser.patch(f"/api/events/{made['id']}", json={"title": "Training Halle 2", "etag": base}) \
            .status_code == 200
    mine = client.patch(f"/api/events/{made['id']}", json={
        "scope": "this", "occurrence": second["occurrence"], "etag": base, "all_day": False,
        "start": "2026-12-03T19:30", "end": "2026-12-03T21:00", "time_zone": "Europe/Berlin"})
    assert mine.status_code == 200 and mine.json()["conflict"] is None
    rows = window(client)
    assert [(row["start"], row["title"]) for row in rows] == [
        ("2026-11-26T17:30:00Z", "Training Halle 2"), ("2026-12-03T18:30:00Z", "Training Halle 2"),
        ("2026-12-10T17:30:00Z", "Training Halle 2")]


def test_time6_ec15_a_failing_split_leaves_the_series_whole(client: TestClient, account: Account,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    made = _series(client, {"freq": "weekly", "weekdays": ["TH"], "end": "count", "count": 4})
    before = _ical(made["id"])
    third = _occurrence(client, "2026-12-10")
    from app.services import events

    real = events._store
    calls = {"n": 0}

    def breaking(db, row, parsed, by):  # the connection breaks after the old part was written
        calls["n"] += 1
        real(db, row, parsed, by)
        if calls["n"] == 2:
            raise RuntimeError("connection lost")

    monkeypatch.setattr(events, "_store", breaking)
    with TestClient(client.app, raise_server_exceptions=False, headers=dict(client.headers)) as other:
        other.cookies.update(client.cookies)
        answer = other.patch(f"/api/events/{made['id']}", json={"scope": "following",
                                                                "occurrence": third["occurrence"], "title": "X"})
    assert answer.status_code == 500
    assert _ical(made["id"]) == before
    with SessionLocal() as db:
        assert db.query(Event).count() == 1


def test_time6_ec9_a_rule_nextime_does_not_offer_stays_until_a_new_one_is_chosen(client: TestClient,
                                                                               account: Account) -> None:
    made = _series(client, {"freq": "weekly", "weekdays": ["TH"]})
    with SessionLocal() as db:
        row = db.get(Event, made["id"])
        assert row is not None
        text = row.ical.replace("RRULE:FREQ=WEEKLY;BYDAY=TH", "RRULE:FREQ=MONTHLY;BYDAY=2TU,4TU")
        from app.services import events

        events.write(db, row, text, ical.parse(text), account.id)
        db.commit()
    detail = client.get(f"/api/events/{made['id']}").json()
    assert detail["rule"] is None and detail["rrule"] == "FREQ=MONTHLY;BYDAY=2TU,4TU"
    client.patch(f"/api/events/{made['id']}", json={"title": "Stammtisch"})
    assert "RRULE:FREQ=MONTHLY;BYDAY=2TU,4TU" in _ical(made["id"])
    client.patch(f"/api/events/{made['id']}", json={"rule": {"freq": "monthly", "monthly": "date"}})
    assert client.get(f"/api/events/{made['id']}").json()["rule"]["freq"] == "monthly"


# --- EC-2, EC-12: endless and long series stay quick and finite -------------------------------------------------


def test_time6_ec2_a_series_without_end_is_found_finitely(client: TestClient, account: Account) -> None:
    made = _series(client, {"freq": "daily"})
    with SessionLocal() as db:
        row = db.get(Event, made["id"])
        assert row is not None and row.ends_at is None and row.recurring
    assert len(window(client, "2026-11-01T00:00:00Z", "2027-12-01T00:00:00Z")) == 370
    assert len(window(client, "2090-01-01T00:00:00Z", "2090-01-08T00:00:00Z")) == 7
    too_wide = client.get("/api/events", params={"start": "2026-01-01T00:00:00Z", "end": "2030-01-01T00:00:00Z"})
    assert too_wide.status_code == 422 and code(too_wide) == "invalid_range"


@pytest.mark.timing
def test_time6_ec12_a_daily_series_over_twenty_years_stays_quick(client: TestClient, account: Account) -> None:
    _series(client, {"freq": "daily", "end": "until", "until": "2046-11-26"})
    began = time.perf_counter()
    rows = window(client, "2045-01-01T00:00:00Z", "2045-12-31T00:00:00Z")
    assert len(rows) == 364
    assert time.perf_counter() - began < 2
