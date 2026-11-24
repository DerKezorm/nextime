"""TIME-29: the nextime app for iPhone and iPad (``/api/app/1``, ``docs/vertrag-app1.md``).

A test client of the interface: pairing by QR code and by the sign-in page, the device's token, ``hello``, ``me``,
``changes`` with its cursor, ``wait``, the queue ``ops`` (exactly once, the later arrival wins, conflicts with both
versions), disconnecting, and the calendar account on the same device.
"""

from __future__ import annotations

import base64
import secrets
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.db import SessionLocal
from app.main import app
from app.models import RIGHT_BUSY, RIGHT_EDIT, RIGHT_READ, Account, AppPairing, Device, Event, utcnow
from app.routers import app1 as app1_router
from app.services import accounts, appdevices

from .calendar_helpers import code, make_calendar, make_event, share
from .conftest import PASSWORD, make_account, new_client
from .test_time20 import calendar_url, raw


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    appdevices.forget()
    yield
    appdevices.forget()


def app_client() -> TestClient:
    """The app: no cookie, no tab header, only its token."""
    return TestClient(app, base_url="https://testserver")


def pair(browser: TestClient, name: str = "Mika's iPhone", kind: str = "ios") -> dict[str, Any]:
    """QR code in the browser, picked up by the app: ``{token, account, device}``."""
    made = browser.post("/api/app-pairing", json={"address": "https://kalender.example.com"})
    assert made.status_code == 201, made.text
    query = parse_qs(urlsplit(made.json()["link"]).query)
    with app_client() as phone:
        got = phone.post(f"/api/app/1/pairing/{query['pairing'][0]}/key",
                         json={"secret": query["secret"][0], "device_name": name, "device_kind": kind})
    assert got.status_code == 200, got.text
    return got.json()


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def call(method: str, path: str, token: str, **kwargs: Any) -> Any:
    with app_client() as phone:
        return phone.request(method, f"/api/app/1{path}", headers=bearer(token), **kwargs)


def all_changes(token: str, cursor: str | None = None, **params: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Every page from ``cursor`` on: the last page and every event of all pages."""
    events: list[dict[str, Any]] = []
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        page = call("GET", "/changes", token, params=query)
        assert page.status_code == 200, page.text
        body = page.json()
        events += body["events"]
        cursor = body["cursor"]
        if not body["more"]:
            body["all_removed"] = body["removed"]
            return body, events


def ops(token: str, *items: dict[str, Any]) -> list[dict[str, Any]]:
    answer = call("POST", "/ops", token, json={"ops": list(items)})
    assert answer.status_code == 200, answer.text
    return answer.json()["ops"]


def ref() -> str:
    return secrets.token_urlsafe(16)


# --- Pairing (AC-1, AC-5, EC-3) --------------------------------------------------------------------------------------


def test_TIME29_AC1_a_qr_code_pairs_one_device_and_the_dialog_learns_it(client: TestClient, account: Account) -> None:
    made = client.post("/api/app-pairing", json={"address": "https://kalender.example.com"}).json()
    assert made["link"].startswith("nextime://connect?server=https%3A%2F%2Fkalender.example.com&pairing=")
    assert made["qr_svg"].startswith("<svg") and made["address"] == "https://kalender.example.com"
    assert client.get(f"/api/app-pairing/{made['id']}").json()["state"] == "waiting"
    with app_client() as phone:
        got = phone.post(f"/api/app/1/pairing/{made['id']}/key",
                         json={"secret": made["secret"], "device_name": "Mika's iPhone", "device_kind": "ios"})
        again = phone.post(f"/api/app/1/pairing/{made['id']}/key",
                           json={"secret": made["secret"], "device_name": "Other", "device_kind": "ios"})
    assert got.status_code == 200 and got.json()["token"].startswith("ntd_")
    assert got.headers["cache-control"] == "no-store"
    assert got.json()["account"] == {"id": account.id, "name": "Tester", "login": "tester",
                                     "email": "tester@example.com", "avatar": "", "operator": True}
    assert again.status_code == 410 and code(again) == "pairing_gone"
    state = client.get(f"/api/app-pairing/{made['id']}").json()
    assert state["state"] == "used" and state["device"]["name"] == "Mika's iPhone"
    assert len(client.get("/api/app-devices").json()) == 1


def test_TIME29_AC1_a_code_runs_out_after_ten_minutes_and_a_new_one_replaces_it(client: TestClient,
                                                                                  account: Account) -> None:
    made = client.post("/api/app-pairing", json={}).json()
    with SessionLocal() as db:
        row = db.get(AppPairing, made["id"])
        assert row is not None
        assert timedelta(minutes=9, seconds=50) < row.expires_at - row.created_at <= timedelta(minutes=10)
        db.execute(update(AppPairing).where(AppPairing.id == made["id"])
                   .values(expires_at=utcnow() - timedelta(seconds=1)))
        db.commit()
    assert client.get(f"/api/app-pairing/{made['id']}").json()["state"] == "expired"
    with app_client() as phone:
        late = phone.post(f"/api/app/1/pairing/{made['id']}/key", json={"secret": made["secret"]})
    assert late.status_code == 410
    assert client.delete(f"/api/app-pairing/{made['id']}").status_code == 204
    fresh = client.post("/api/app-pairing", json={}).json()
    assert fresh["id"] != made["id"]


def test_TIME29_EC3_failures_count_at_the_sign_in_brake(client: TestClient, account: Account) -> None:
    made = client.post("/api/app-pairing", json={}).json()
    with app_client() as phone:
        answers = [phone.post(f"/api/app/1/pairing/{made['id']}/key", json={"secret": "wrong"}).status_code
                   for _ in range(8)]
        right = phone.post(f"/api/app/1/pairing/{made['id']}/key", json={"secret": made["secret"]})
    assert answers[0] == 410 and 429 in answers
    assert right.status_code == 429 and int(right.headers["retry-after"]) > 0


def test_TIME29_AC5_the_sign_in_page_hands_a_one_time_code_to_the_app(client: TestClient, account: Account) -> None:
    state = "s" * 22
    link = client.post("/api/app-connect", json={"state": state}).json()["link"]
    assert link.startswith("nextime://connected?code=") and link.endswith(f"&state={state}")
    one_time = parse_qs(urlsplit(link).query)["code"][0]
    with app_client() as phone:
        got = phone.post("/api/app/1/pairing/code", json={"code": one_time, "device_name": "iPad",
                                                          "device_kind": "ipados"})
        again = phone.post("/api/app/1/pairing/code", json={"code": one_time})
    assert got.status_code == 200 and got.json()["device"]["kind"] == "ipados"
    assert again.status_code == 410 and code(again) == "pairing_gone"
    assert client.post("/api/app-connect", json={"state": "no"}).status_code == 422


def test_TIME29_AC1_only_a_browser_makes_codes(client: TestClient, account: Account) -> None:
    device = pair(client)
    with app_client() as phone:
        refused = phone.post("/api/app-pairing", json={}, headers=bearer(device["token"]))
    assert refused.status_code == 401
    assert client.post("/api/app-pairing", json={}, headers=bearer(device["token"])).status_code == 403


# --- Versions (EC-2) ------------------------------------------------------------------------------------------------


def test_TIME29_EC2_hello_names_the_interface_and_health_names_nextime(client: TestClient) -> None:
    with app_client() as phone:
        hello = phone.get("/api/app/1/hello").json()
        health = phone.get("/api/health").json()
        newer = phone.get("/api/app/2/hello")
    assert hello["app"] == "nextime" and hello["api"] == 1 and hello["min_api"] == 1
    assert {"pairing", "changes", "ops", "conflicts", "calendar_account"} <= set(hello["features"])
    assert "password" in hello["sign_in"]
    assert health["app"] == "nextime"
    assert newer.status_code == 404


# --- Devices (AC-4, AC-7, EC-1, EC-5) -------------------------------------------------------------------------------


def test_TIME29_AC7_the_browser_lists_name_kind_last_contact_and_notifications(client: TestClient,
                                                                               account: Account) -> None:
    device = pair(client)
    assert call("GET", "/me", device["token"]).status_code == 200
    key = base64.b64encode(X25519PrivateKey.generate().public_key().public_bytes_raw()).decode()
    assert call("PUT", "/push", device["token"], json={"ticket": "1.ticket", "key": key}).status_code == 204
    listed = client.get("/api/app-devices").json()
    assert listed == [{"id": device["device"]["id"], "name": "Mika's iPhone", "kind": "ios",
                       "created_at": listed[0]["created_at"], "last_seen_at": listed[0]["last_seen_at"],
                       "push": True}]
    assert listed[0]["last_seen_at"] is not None
    assert call("PUT", "/push", device["token"], json={"ticket": "1.t", "key": "AAAA"}).status_code == 422
    assert call("DELETE", "/push", device["token"]).status_code == 204
    assert client.get("/api/app-devices").json()[0]["push"] is False


def test_TIME29_AC4_EC1_a_device_removed_in_the_browser_gets_nothing_and_takes_nothing(
        client: TestClient, account: Account) -> None:
    kept = pair(client, "iPad", "ipados")
    gone = pair(client)
    assert client.delete(f"/api/app-devices/{gone['device']['id']}").status_code == 204
    for answer in (call("GET", "/changes", gone["token"]),
                   call("POST", "/ops", gone["token"], json={"ops": [
                       {"ref": ref(), "op": "create", "event": {"title": "Offline", "start": "2026-11-26"
                                                                                             "T10:00"}}]})):
        assert answer.status_code == 401 and code(answer) == "device_disconnected"
    with SessionLocal() as db:
        assert db.scalar(select(Event).where(Event.title == "Offline")) is None
    assert call("GET", "/changes", kept["token"]).status_code == 200
    assert client.delete(f"/api/app-devices/{gone['device']['id']}").status_code == 404


def test_TIME29_EC5_a_blocked_account_hears_blocked_and_a_new_password_disconnects(client: TestClient,
                                                                                   account: Account) -> None:
    mika = make_account("mika")
    with new_client(mika) as browser:
        device = pair(browser)
        assert call("GET", "/me", device["token"]).json()["account"]["operator"] is False
        assert browser.put("/api/auth/password", json={"current": PASSWORD, "new": "a much newer password!"}
                           ).status_code == 204
    assert code(call("GET", "/me", device["token"])) == "device_disconnected"
    with new_client(mika) as browser:
        device = pair(browser)
    with SessionLocal() as db:
        row = db.get(Account, mika.id)
        assert row is not None
        accounts.block(db, row)
    blocked = call("GET", "/me", device["token"])
    assert blocked.status_code == 401 and code(blocked) == "account_blocked"
    with new_client(account) as operator_browser:
        assert operator_browser.get("/api/app-devices").status_code == 200
    with SessionLocal() as db:
        row = db.get(Account, mika.id)
        assert row is not None
        accounts.unblock(db, row)
    # Unblocked, the device of before signs in again; it never comes back by itself.
    assert code(call("GET", "/me", device["token"])) == "device_disconnected"


def test_TIME29_follow_up9_the_device_signs_itself_off(client: TestClient, account: Account) -> None:
    device = pair(client)
    assert call("DELETE", "/device", device["token"]).status_code == 204
    assert client.get("/api/app-devices").json() == []
    assert code(call("DELETE", "/device", device["token"])) == "device_disconnected"


def test_TIME29_AC7_another_own_device_comes_in_under_its_own_name(client: TestClient, account: Account) -> None:
    phone = pair(client)
    pad = call("POST", "/devices", phone["token"], json={"device_name": "iPad", "device_kind": "ipados"})
    assert pad.status_code == 201
    names = sorted(row["name"] for row in client.get("/api/app-devices").json())
    assert names == ["Mika's iPhone", "iPad"]
    listed = call("GET", "/devices", pad.json()["token"]).json()
    assert [row["this"] for row in listed] == [False, True]


# --- me (follow-up 8) -----------------------------------------------------------------------------------------------


def test_TIME29_follow_up8_me_names_the_role_the_display_and_the_limits(client: TestClient, account: Account) -> None:
    client.put("/api/me/display", json={"month_style": "tint", "week_start": 0})
    device = pair(client)
    me = call("GET", "/me", device["token"]).json()
    assert me["account"]["operator"] is True and me["device"]["name"] == "Mika's iPhone"
    assert me["display"]["month_style"] == "tint" and me["display"]["week_start"] == 0
    assert me["limits"]["ops"] == 50 and me["limits"]["title_chars"] == 200
    assert me["calendar_account"] is None and len(me["server_id"]) == 16


# --- What changed (AC-6, AC-8, EC-4) ----------------------------------------------------------------------------------


def test_TIME29_AC6_after_a_pause_only_what_changed_comes(client: TestClient, account: Account) -> None:
    first = make_event(client, "Training")
    second = make_event(client, "Zahnarzt", start="2026-11-27T09:00", end="2026-11-27T09:45")
    device = pair(client)
    full, everything = all_changes(device["token"])
    assert full["full"] is True and {row["id"] for row in everything} == {first["id"], second["id"]}
    shown = next(row for row in everything if row["id"] == second["id"])
    assert shown["ical"].startswith("BEGIN:VCALENDAR") and "SUMMARY:Zahnarzt" in shown["ical"]
    assert shown["title"] == "Zahnarzt" and shown["time_zone"] == "Europe/Berlin" and shown["etag"]
    quiet, none = all_changes(device["token"], full["cursor"])
    assert quiet["full"] is False and none == [] and quiet["removed"] == []
    client.patch(f"/api/events/{first['id']}", json={"title": "Training (Halle 2)"})
    third = make_event(client, "Elternabend", start="2026-11-30T19:00", end="2026-11-30T20:00")
    client.delete(f"/api/events/{second['id']}")
    later, changed = all_changes(device["token"], quiet["cursor"])
    assert {row["id"] for row in changed} == {first["id"], third["id"]}
    assert later["removed"] == [{"kind": "event", "calendar_id": shown["calendar_id"], "href": shown["href"]}]


def test_TIME29_EC4_a_cursor_of_another_database_gets_everything(client: TestClient, account: Account) -> None:
    made = make_event(client)
    device = pair(client)
    body, _events = all_changes(device["token"])
    foreign = "deadbeef." + body["cursor"].split(".", 1)[1]
    again, events = all_changes(device["token"], foreign)
    assert again["full"] is True and [row["id"] for row in events] == [made["id"]]


def test_TIME29_AC6_pages_of_at_most_the_page_size_go_on_with_the_cursor(client: TestClient, account: Account,
                                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services import appsync

    for day in range(1, 6):
        make_event(client, f"Termin {day}", start=f"2026-12-0{day}T10:00", end=f"2026-12-0{day}T11:00")
    device = pair(client)
    monkeypatch.setattr(appsync, "PAGE", 2)
    original = appsync.changes
    monkeypatch.setattr(appsync, "changes", lambda db, account_id, cursor, only=None, page=2: original(
        db, account_id, cursor, only, page))
    first = call("GET", "/changes", device["token"]).json()
    assert first["more"] is True and len(first["events"]) == 2 and first["full"] is True
    _last, events = all_changes(device["token"])
    assert len({row["id"] for row in events}) == 5


def test_TIME29_AC8_the_app_sees_what_the_account_sees_with_the_right(client: TestClient, account: Account) -> None:
    mika = make_account("mika")
    family = make_calendar(client, "Familie")
    busy = make_calendar(client, "Arbeit")
    private = make_calendar(client, "Privat")
    share(family, mika.id, RIGHT_READ)
    share(busy, mika.id, RIGHT_BUSY)
    make_event(client, "Elternabend", calendar_id=family, location="Schule")
    make_event(client, "Kundentermin", calendar_id=busy, location="Büro")
    make_event(client, "Frei", calendar_id=busy, busy=False)
    make_event(client, "Geheim", calendar_id=private)
    with new_client(mika) as browser:
        device = pair(browser)
    body, events = all_changes(device["token"])
    titles = {row["title"] for row in events}
    assert "Elternabend" in titles and "Geheim" not in titles and "Frei" not in titles
    blocked = next(row for row in events if row["calendar_id"] == busy)
    assert blocked["busy_only"] is True and blocked["title"] == "" and blocked["location"] == ""
    assert "Kundentermin" not in blocked["ical"] and "Büro" not in blocked["ical"]
    rights = {row["id"]: (row["right"], row["writable"]) for row in body["calendars"]}
    assert rights[family] == ("read", False) and rights[busy] == ("busy", False) and private not in rights
    owner = next(row for row in body["calendars"] if row["id"] == family)["owner"]
    assert owner == {"id": account.id, "name": "Tester"}


def test_TIME29_AC8_a_share_ended_drops_the_calendar_from_the_list(client: TestClient, account: Account) -> None:
    from .calendar_helpers import unshare

    mika = make_account("mika")
    family = make_calendar(client, "Familie")
    share(family, mika.id, RIGHT_EDIT)
    make_event(client, "Elternabend", calendar_id=family)
    with new_client(mika) as browser:
        device = pair(browser)
    body, _events = all_changes(device["token"])
    assert family in {row["id"] for row in body["calendars"]}
    unshare(family, mika.id)
    later, _changed = all_changes(device["token"], body["cursor"])
    assert family not in {row["id"] for row in later["calendars"]}
    assert later["cursor"] != body["cursor"]


def test_TIME29_AC6_wait_answers_at_once_when_something_changed(client: TestClient, account: Account,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    device = pair(client)
    body, _events = all_changes(device["token"])
    monkeypatch.setattr(app1_router, "WAIT_SECONDS", 0.3)
    monkeypatch.setattr(app1_router, "WAIT_RECHECK", 0.1)
    same = call("GET", "/wait", device["token"], params={"cursor": body["cursor"]}).json()
    assert same == {"cursor": body["cursor"]}
    make_event(client, "Neu")
    newer = call("GET", "/wait", device["token"], params={"cursor": body["cursor"]}).json()
    assert newer["cursor"] != body["cursor"]
    client.put("/api/me/display", json={"week_numbers": False})
    _later, _none = all_changes(device["token"], newer["cursor"])
    shown = call("GET", "/changes", device["token"], params={"cursor": newer["cursor"]}).json()
    assert shown["account"]["display"]["week_numbers"] is False


# --- Writing (AC-2, EC-6, EC-7) --------------------------------------------------------------------------------------


def test_TIME29_AC2_offline_made_events_arrive_without_loss(client: TestClient, account: Account) -> None:
    device = pair(client)
    home = make_calendar(client, "Zuhause")
    series = make_event(client, "Training", start="2026-12-01T18:00", end="2026-12-01T19:00",
                        rule={"freq": "weekly", "weekdays": ["TU"]})
    answers = ops(device["token"],
                  {"ref": ref(), "op": "create", "event": {"calendar_id": home, "title": "Zahnarzt",
                                                           "start": "2026-11-26T09:00", "end": "2026-11-26T09:45",
                                                           "time_zone": "Europe/Berlin"}},
                  {"ref": ref(), "op": "change", "id": series["id"], "etag": series["etag"], "scope": "this",
                   "occurrence": "20261208T170000Z", "event": {"title": "Training fällt aus"}},
                  {"ref": ref(), "op": "delete", "id": series["id"], "scope": "this",
                   "occurrence": "20261215T170000Z"})
    assert [row["state"] for row in answers] == ["done", "done", "done"], answers
    assert answers[0]["event"]["title"] == "Zahnarzt" and answers[0]["event"]["calendar_id"] == home
    assert answers[2]["trash"]["kind"] == "part"
    with SessionLocal() as db:
        text = db.get(Event, series["id"]).ical
    assert "RECURRENCE-ID" in text and "Training fällt aus" in text and "EXDATE" in text


def test_TIME29_EC6_the_same_change_twice_is_taken_once(client: TestClient, account: Account) -> None:
    device = pair(client)
    op = {"ref": ref(), "op": "create", "event": {"title": "Einmal", "start": "2026-11-26T09:00"}}
    first = ops(device["token"], op)
    second = ops(device["token"], op)
    assert first == second and first[0]["state"] == "done"
    with SessionLocal() as db:
        assert len(db.scalars(select(Event).where(Event.title == "Einmal")).all()) == 1
    made = first[0]["event"]
    change = {"ref": ref(), "op": "change", "id": made["id"], "etag": made["etag"], "event": {"title": "Zweimal"}}
    ops(device["token"], change)
    client.patch(f"/api/events/{made['id']}", json={"title": "Im Web"})
    assert ops(device["token"], change)[0]["state"] == "done"
    with SessionLocal() as db:
        assert db.get(Event, made["id"]).title == "Im Web"


def test_TIME29_EC7_a_calendar_now_read_only_or_gone_refuses_only_that_change(client: TestClient,
                                                                              account: Account) -> None:
    mika = make_account("mika")
    family = make_calendar(client, "Familie")
    share(family, mika.id, RIGHT_EDIT)
    made = make_event(client, "Elternabend", calendar_id=family)
    with new_client(mika) as browser:
        device = pair(browser)
    share(family, mika.id, RIGHT_READ)
    answers = ops(device["token"],
                  {"ref": ref(), "op": "change", "id": made["id"], "etag": made["etag"],
                   "event": {"title": "Elternabend (verlegt)"}},
                  {"ref": ref(), "op": "create", "event": {"title": "Eigener", "start": "2026-11-26T09:00"}},
                  {"ref": ref(), "op": "nonsense"},
                  {"ref": "short", "op": "create"})
    assert answers[0]["state"] == "refused" and answers[0]["code"] == "calendar_read_only"
    assert answers[1]["state"] == "done"
    assert answers[2]["code"] == "op_unknown" and answers[3]["code"] == "invalid_input"
    with SessionLocal() as db:
        assert db.get(Event, made["id"]).title == "Elternabend"
    client.delete(f"/api/calendars/{family}")
    gone = ops(device["token"], {"ref": ref(), "op": "create", "event": {
        "calendar_id": family, "title": "Zu spät", "start": "2026-11-26T09:00"}})
    assert gone[0]["state"] == "refused" and gone[0]["code"] in ("calendar_gone", "not_found")


def test_TIME29_AC8_the_limits_of_the_web_hold(client: TestClient, account: Account) -> None:
    device = pair(client)
    answers = ops(device["token"],
                  {"ref": ref(), "op": "create", "event": {"title": "x" * 201, "start": "2026-11-26T09:00"}},
                  {"ref": ref(), "op": "create", "event": {"title": "Rückwärts", "start": "2026-11-26T09:00",
                                                           "end": "2026-11-26T08:00", "time_zone": "Europe/Berlin"}},
                  {"ref": ref(), "op": "create", "event": {"title": "Ohne Zone", "start": "2026-11-26T09:00",
                                                           "time_zone": "Mars/Base"}})
    assert [row["code"] for row in answers] == ["title_too_long", "end_before_start", "invalid_time_zone"]
    too_many = call("POST", "/ops", device["token"], json={"ops": [{"ref": ref(), "op": "hide"}] * 51})
    assert too_many.status_code == 422 and code(too_many) == "too_many_ops"


def test_TIME29_AC2_the_later_arrival_wins_and_the_other_account_gets_both_versions(
        client: TestClient, account: Account) -> None:
    mika = make_account("mika")
    family = make_calendar(client, "Familie")
    share(family, mika.id, RIGHT_EDIT)
    made = make_event(client, "Elternabend", calendar_id=family)
    with new_client(mika) as browser:
        device = pair(browser)
    # Mika changes it offline first; Tester changes it online later; Mika's phone syncs after that.
    client.patch(f"/api/events/{made['id']}", json={"title": "Elternabend 19 Uhr", "etag": made["etag"]})
    answer = ops(device["token"], {"ref": ref(), "op": "change", "id": made["id"], "etag": made["etag"],
                                   "event": {"title": "Elternabend 20 Uhr"}})[0]
    assert answer["state"] == "done" and answer["event"]["title"] == "Elternabend 20 Uhr"
    shown = client.get(f"/api/events/{made['id']}").json()
    assert shown["title"] == "Elternabend 20 Uhr" and shown["conflict"]
    assert client.post(f"/api/events/{made['id']}/conflict", json={"restore": False}).status_code == 200
    # Now the other way round: Mika's phone saves first, Tester saves later on the version before it.
    before = client.get(f"/api/events/{made['id']}").json()["etag"]
    ops(device["token"], {"ref": ref(), "op": "change", "id": made["id"], "etag": before,
                          "event": {"title": "Elternabend 20:30 Uhr"}})
    client.patch(f"/api/events/{made['id']}", json={"title": "Elternabend 18 Uhr", "etag": before})
    body, _events = all_changes(device["token"])
    conflict = body["conflicts"]
    assert len(conflict) == 1 and conflict[0]["event_id"] == made["id"]
    assert "Elternabend 20:30 Uhr" in conflict[0]["mine"] and conflict[0]["by"] == {"id": account.id, "name": "Tester"}
    back = ops(device["token"], {"ref": ref(), "op": "resolve", "id": made["id"], "restore": True})[0]
    assert back["state"] == "done" and back["event"]["title"] == "Elternabend 20:30 Uhr"
    assert all_changes(device["token"])[0]["conflicts"] == []


def test_TIME29_AC2_EC13_the_device_clock_does_not_decide(client: TestClient, account: Account) -> None:
    """The device's change arrives later and counts, though the device says nothing of when it was made."""
    device = pair(client)
    made = make_event(client, "Zahnarzt")
    client.patch(f"/api/events/{made['id']}", json={"title": "Zahnarzt (Web)", "etag": made["etag"]})
    answer = ops(device["token"], {"ref": ref(), "op": "change", "id": made["id"], "etag": made["etag"],
                                   "updated_at": "2020-01-01T00:00:00Z", "event": {"title": "Zahnarzt (iPhone)"}})
    assert answer[0]["event"]["title"] == "Zahnarzt (iPhone)"
    body, _events = all_changes(device["token"])
    assert body["conflicts"][0]["by"] == {"id": account.id, "name": "Tester"}


def test_TIME29_AC2_a_change_after_a_delete_brings_the_event_back(client: TestClient, account: Account) -> None:
    device = pair(client)
    made = make_event(client, "Zahnarzt")
    client.delete(f"/api/events/{made['id']}")
    answer = ops(device["token"], {"ref": ref(), "op": "change", "id": made["id"], "etag": made["etag"],
                                   "event": {"title": "Zahnarzt (verlegt)"}})[0]
    assert answer["state"] == "done" and answer["event"]["title"] == "Zahnarzt (verlegt)"
    gone = ops(device["token"], {"ref": ref(), "op": "delete", "id": made["id"]},
               {"ref": ref(), "op": "delete", "id": made["id"]})
    assert [row["state"] for row in gone] == ["done", "done"]


def test_TIME29_AC14_hiding_a_calendar_from_the_app(client: TestClient, account: Account) -> None:
    device = pair(client)
    family = make_calendar(client, "Familie")
    assert ops(device["token"], {"ref": ref(), "op": "hide", "calendar_id": family, "hidden": True})[0]["state"] == \
        "done"
    body, _events = all_changes(device["token"])
    assert next(row for row in body["calendars"] if row["id"] == family)["hidden"] is True


# --- Calendar account on the same device (follow-up 4) --------------------------------------------------------------


def test_TIME29_follow_up4_the_calendar_account_of_the_app_device_gets_no_alarms(client: TestClient,
                                                                                  account: Account) -> None:
    device = pair(client)
    made = call("POST", "/calendar-account", device["token"], json={"device_name": "Mika's iPhone"})
    assert made.status_code == 201 and "/setup/" in made.json()["link"]
    setup = urlsplit(made.json()["link"]).path.rsplit("/", 1)[1]
    with new_client() as safari:
        password = safari.post(f"/api/device-setup/{setup}/credentials", json={}).json()["password"]
    family = make_calendar(client, "Familie")
    event = make_event(client, "Elternabend", calendar_id=family, reminders=[15])
    with SessionLocal() as db:
        href = db.get(Event, event["id"]).href
    path = calendar_url(account, family) + href
    quiet = raw("GET", path, account, password)
    assert quiet.status_code == 200 and "VALARM" not in quiet.text
    assert call("GET", "/me", device["token"]).json()["calendar_account"]["set_up"] is True
    # Apple Calendar writes it back without alarms: the reminder stays.
    put = raw("PUT", path, account, password, quiet.text, **{"If-Match": quiet.headers["etag"]})
    assert put.status_code == 204
    assert client.get(f"/api/events/{event['id']}").json()["reminders"]["minutes"] == [15]
    # "Remove, keep Apple Calendar": the link goes with the app device, Apple Calendar reminds again.
    call("DELETE", "/device", device["token"])
    assert "TRIGGER:-PT15M" in raw("GET", path, account, password).text


def test_TIME29_follow_up4_removing_it_from_apple_calendar_locks_the_device_out(client: TestClient,
                                                                                 account: Account) -> None:
    device = pair(client)
    call("POST", "/calendar-account", device["token"], json={})
    with SessionLocal() as db:
        assert db.scalar(select(Device).where(Device.app_device_id == device["device"]["id"])) is not None
    assert call("DELETE", "/calendar-account", device["token"]).status_code == 204
    with SessionLocal() as db:
        assert db.scalar(select(Device).where(Device.account_id == account.id)) is None
    assert call("GET", "/me", device["token"]).json()["calendar_account"] is None
