"""TIME-26 The API for programs and MCP: one kind of key with a right, its calendars, a run-out and "for MCP", shown
once and kept only as a hash; every route under ``/api/v1/`` by the rights of the account and the key ("busy only"
stays busy, other calendars are not there); writing through the services of the interface (limits, rights, the trash,
series, sources); the walls (Origin, the operator's switches, brakes, blocked accounts); MCP tools by the key's right.
The other side of iCloud is nextime's own calendar account in a thread (``remote_helpers``)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import RIGHT_BUSY, RIGHT_READ, Account, ApiKey, utcnow
from app.services import accounts, apibrake, apikeys, logs, settings_service

from .calendar_helpers import code, make_calendar, make_event, share
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
)

START = "2026-11-01T00:00:00Z"
END = "2027-01-01T00:00:00Z"


def make_key(client: TestClient, name: str = "Skript", **values: Any) -> dict[str, Any]:
    answer = client.post("/api/keys", json={"name": name, "level": "read", **values})
    assert answer.status_code == 201, answer.text
    return answer.json()


@pytest.fixture
def program() -> Iterator[TestClient]:
    """A program: no session cookie, no tab."""
    with TestClient(app, base_url="http://testserver") as found:
        yield found


def as_key(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def mcp(program: TestClient, token: str, method: str, params: dict[str, Any] | None = None) -> Any:
    answer = program.post("/api/mcp", headers=as_key(token),
                          json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert answer.status_code == 200, answer.text
    return answer.json()


def tool(program: TestClient, token: str, name: str, **args: Any) -> dict[str, Any]:
    return mcp(program, token, "tools/call", {"name": name, "arguments": args})["result"]


# --- Keys (AC-1, AC-4, AC-11, EC-1, EC-15, EC-16) -------------------------------------------------------------------


def test_time26_ac1_a_key_is_shown_once_and_kept_only_as_a_hash(client: TestClient, account: Account) -> None:
    made = make_key(client, "  n8n   Wetter ", mcp=True)
    token = made["token"]
    assert token.startswith("nt_api_") and len(token) > 40
    assert made["made"]["name"] == "n8n Wetter" and made["made"]["mcp"] is True
    listed = client.get("/api/keys").json()
    assert token not in json.dumps(listed)
    with SessionLocal() as db:
        row = db.scalar(select(ApiKey))
        assert row is not None and row.token_hash == apikeys.digest(token) and token not in row.token_hash
        assert row.prefix == token[:11]


def test_time26_ac4_the_list_names_right_mcp_calendars_run_out_and_last_use(client: TestClient, account: Account,
                                                                           program: TestClient) -> None:
    familie = make_calendar(client, "Familie")
    made = make_key(client, "Tablet", calendar_ids=[familie], days=30)
    make_key(client, "Planer", level="write", mcp=True, days=None)
    rows = {row["name"]: row for row in client.get("/api/keys").json()["keys"]}
    assert rows["Tablet"]["level"] == "read" and rows["Tablet"]["mcp"] is False
    assert rows["Tablet"]["calendars"] == [{"id": familie, "name": "Familie"}]
    assert rows["Tablet"]["expires_at"] is not None and rows["Tablet"]["last_used_at"] is None
    assert rows["Planer"]["calendars"] is None and rows["Planer"]["expires_at"] is None
    assert program.get("/api/v1/me", headers=as_key(made["token"])).status_code == 200
    rows = {row["name"]: row for row in client.get("/api/keys").json()["keys"]}
    assert rows["Tablet"]["last_used_at"] is not None
    # Revoked: gone at once.
    assert client.delete(f"/api/keys/{made['made']['id']}").status_code == 204
    assert program.get("/api/v1/me", headers=as_key(made["token"])).status_code == 401
    assert [row["name"] for row in client.get("/api/keys").json()["keys"]] == ["Planer"]


def test_time26_ac11_ninety_days_from_the_start_and_both_switches_on(client: TestClient, account: Account) -> None:
    listed = client.get("/api/keys").json()
    assert listed["expiry_days"] == [30, 90, 365, None] and listed["default_days"] == 90
    assert listed["api_allowed"] is True and listed["mcp_allowed"] is True
    made = make_key(client)["made"]
    with SessionLocal() as db:
        row = db.get(ApiKey, made["id"])
        assert row is not None and row.expires_at is not None
        assert timedelta(days=89) < row.expires_at - row.created_at <= timedelta(days=90)
    assert client.post("/api/keys", json={"name": "x", "level": "read", "days": 7}).status_code == 422


def test_time26_ec1_an_expired_key_is_turned_away_without_a_word_and_listed_as_expired(
        client: TestClient, account: Account, program: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    make_calendar(client, "Familie")
    made = make_key(client, days=30)
    later = utcnow() + timedelta(days=31)
    monkeypatch.setattr(apikeys, "clock", lambda: later)
    answer = program.get("/api/v1/calendars", headers=as_key(made["token"]))
    assert answer.status_code == 401 and code(answer) == "token_invalid"
    assert "Familie" not in answer.text
    row = client.get("/api/keys").json()["keys"][0]
    assert row["expired"] is True
    assert client.delete(f"/api/keys/{row['id']}").status_code == 204
    assert client.get("/api/keys").json()["keys"] == []


def test_time26_ec15_twenty_keys_at_most_names_cleaned_the_same_name_twice_fine(client: TestClient,
                                                                               account: Account) -> None:
    for _ in range(20):
        make_key(client, "Gleich")
    refused = client.post("/api/keys", json={"name": "Einer zu viel", "level": "read"})
    assert refused.status_code == 409 and code(refused) == "keys_too_many"
    assert refused.json()["detail"]["max"] == 20
    first = client.get("/api/keys").json()["keys"][0]
    assert client.delete(f"/api/keys/{first['id']}").status_code == 204
    empty = client.post("/api/keys", json={"name": "   ", "level": "read"})
    assert empty.status_code == 422 and code(empty) == "key_name_missing"
    assert len(client.get("/api/keys").json()["keys"]) == 19


def test_time26_ec16_two_clicks_on_anlegen_make_one_key(client: TestClient, account: Account) -> None:
    first = client.post("/api/keys", json={"name": "Doppelt", "level": "read", "client_ref": "ref-1234567"})
    second = client.post("/api/keys", json={"name": "Doppelt", "level": "read", "client_ref": "ref-1234567"})
    assert first.status_code == 201 and second.status_code == 200
    assert second.json()["token"] is None and second.json()["made"]["id"] == first.json()["made"]["id"]
    assert len(client.get("/api/keys").json()["keys"]) == 1


def test_time26_ec15_a_member_revokes_only_its_own_keys(client: TestClient, account: Account) -> None:
    made = make_key(client)
    anna = make_account("anna")
    with new_client(anna) as browser:
        assert browser.get("/api/keys").json()["keys"] == []
        assert browser.delete(f"/api/keys/{made['made']['id']}").status_code == 404
    assert len(client.get("/api/keys").json()["keys"]) == 1


# --- Reading and writing (AC-2, EC-3 to EC-6, EC-10, EC-11) --------------------------------------------------------


def test_time26_ac2_ec5_a_read_key_reads_a_write_key_makes_changes_and_deletes(client: TestClient, account: Account,
                                                                              program: TestClient) -> None:
    familie = make_calendar(client, "Familie")
    reader = make_key(client, "Leser")["token"]
    writer = make_key(client, "Schreiber", level="write")["token"]
    names = [row["name"] for row in program.get("/api/v1/calendars", headers=as_key(reader)).json()["calendars"]]
    assert "Familie" in names
    made = program.post("/api/v1/events", headers=as_key(writer), json={
        "title": "Zahnarzt", "start": "2026-11-27T15:00", "end": "2026-11-27T15:45", "time_zone": "Europe/Berlin",
        "calendar_id": familie, "location": "Praxis"})
    assert made.status_code == 201, made.text
    event = made.json()
    assert event["title"] == "Zahnarzt" and event["start"] == "2026-11-27T15:00" and event["calendar_id"] == familie
    found = program.get("/api/v1/events", headers=as_key(reader), params={"start": START, "end": END}).json()["events"]
    assert [entry["title"] for entry in found] == ["Zahnarzt"]
    assert program.get(f"/api/v1/events/{event['id']}", headers=as_key(reader)).json()["location"] == "Praxis"
    refused = program.post("/api/v1/events", headers=as_key(reader), json={"title": "Nein", "start": "2026-11-28"})
    assert refused.status_code == 403 and code(refused) == "token_reads_only"
    changed = program.patch(f"/api/v1/events/{event['id']}", headers={**as_key(writer), "If-Match": event["etag"]},
                            json={"title": "Zahnarzt Ben"})
    assert changed.status_code == 200 and changed.json()["title"] == "Zahnarzt Ben"
    assert program.delete(f"/api/v1/events/{event['id']}", headers=as_key(reader)).status_code == 403
    gone = program.delete(f"/api/v1/events/{event['id']}", headers=as_key(writer))
    assert gone.status_code == 200 and gone.json()["trash"]["kind"] == "event"


def test_time26_ec10_deleting_puts_it_into_the_trash_never_for_good(client: TestClient, account: Account,
                                                                   program: TestClient) -> None:
    event = make_event(client, "Training")
    writer = make_key(client, "Skript", level="write")["token"]
    assert program.delete(f"/api/v1/events/{event['id']}", headers=as_key(writer)).status_code == 200
    trash = client.get("/api/trash").json()["items"]
    assert [item["title"] for item in trash] == ["Training"]
    assert trash[0]["deleted_via"] == "Skript"
    assert client.post(f"/api/trash/events/{event['id']}/restore").status_code == 200


def test_time26_ec4_a_key_for_some_calendars_sees_nothing_else_not_even_later_ones(client: TestClient, account: Account,
                                                                                  program: TestClient) -> None:
    familie = make_calendar(client, "Familie")
    arbeit = make_calendar(client, "Arbeit")
    hidden = make_event(client, "Geheim", calendar_id=arbeit)
    limited = make_key(client, "Tablet", level="write", calendar_ids=[familie])["token"]
    everything = make_key(client, "Alles")["token"]
    later = make_calendar(client, "Später")
    names = [row["name"] for row in program.get("/api/v1/calendars", headers=as_key(limited)).json()["calendars"]]
    assert names == ["Familie"]
    assert "Später" in [row["name"] for row in program.get("/api/v1/calendars",
                                                           headers=as_key(everything)).json()["calendars"]]
    assert program.get(f"/api/v1/calendars/{arbeit}", headers=as_key(limited)).status_code == 404
    assert program.get(f"/api/v1/events/{hidden['id']}", headers=as_key(limited)).status_code == 404
    events = program.get("/api/v1/events", headers=as_key(limited), params={"start": START, "end": END}).json()
    assert events["events"] == []
    asked = program.get("/api/v1/events", headers=as_key(limited),
                        params={"start": START, "end": END, "calendars": str(arbeit)})
    assert asked.status_code == 404
    for target in (arbeit, later):
        refused = program.post("/api/v1/events", headers=as_key(limited),
                               json={"title": "Nein", "start": "2026-11-28", "all_day": True, "calendar_id": target})
        assert refused.status_code == 404, target
    assert program.patch(f"/api/v1/events/{hidden['id']}", headers=as_key(limited),
                         json={"title": "x"}).status_code == 404
    assert program.delete(f"/api/v1/events/{hidden['id']}", headers=as_key(limited)).status_code == 404
    # Without a calendar named, a new event goes into one the key reaches.
    made = program.post("/api/v1/events", headers=as_key(limited), json={"title": "Ja", "start": "2026-11-28",
                                                                         "all_day": True})
    assert made.status_code == 201 and made.json()["calendar_id"] == familie
    # A search finds nothing outside either.
    assert program.get("/api/v1/search", headers=as_key(limited), params={"q": "Geheim"}).json()["events"] == []


def test_time26_ec3_busy_only_gives_times_and_nothing_else(client: TestClient, account: Account,
                                                          program: TestClient) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        privat = make_calendar(browser, "Annas")
        secret = make_event(browser, "Geheim", calendar_id=privat, location="Praxis", notes="Notiz")
        make_event(browser, "Frei", start="2026-11-27T10:00", end="2026-11-27T11:00", calendar_id=privat, busy=False)
    share(privat, account.id, RIGHT_BUSY)
    token = make_key(client)["token"]
    found = program.get("/api/v1/events", headers=as_key(token), params={"start": START, "end": END}).json()["events"]
    assert len(found) == 1 and found[0]["busy_only"] is True
    assert set(found[0]) == {"calendar_id", "busy_only", "busy", "all_day", "start", "end", "time_zone"}
    one = program.get(f"/api/v1/events/{secret['id']}", headers=as_key(token))
    assert one.status_code == 200
    assert "Geheim" not in one.text and "Praxis" not in one.text and "Notiz" not in one.text
    assert program.get("/api/v1/search", headers=as_key(token), params={"q": "Geheim"}).json()["events"] == []


def test_time26_ec5_a_calendar_to_read_only_is_refused_and_unchanged(client: TestClient, account: Account,
                                                                    program: TestClient) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        verein = make_calendar(browser, "Verein")
        event = make_event(browser, "Training", calendar_id=verein)
    share(verein, account.id, RIGHT_READ)
    writer = make_key(client, level="write")["token"]
    refused = program.patch(f"/api/v1/events/{event['id']}", headers=as_key(writer), json={"title": "Anders"})
    assert refused.status_code == 403 and code(refused) == "calendar_read_only"
    refused = program.post("/api/v1/events", headers=as_key(writer),
                           json={"title": "Neu", "start": "2026-11-28", "all_day": True, "calendar_id": verein})
    assert refused.status_code == 403
    assert program.get(f"/api/v1/events/{event['id']}", headers=as_key(writer)).json()["title"] == "Training"


def test_time26_ec6_wrong_input_is_refused_as_in_the_interface_and_nothing_is_written(
        client: TestClient, account: Account, program: TestClient) -> None:
    writer = make_key(client, level="write")["token"]
    cases = [
        ({"title": "x", "start": "2026-11-28T10:00", "end": "2026-11-28T09:00", "time_zone": "Europe/Berlin"},
         "end_before_start"),
        ({"title": "x" * 201, "start": "2026-11-28T10:00", "time_zone": "Europe/Berlin"}, "title_too_long"),
        ({"title": "x", "start": "2026-02-31", "all_day": True}, "invalid_date"),
        ({"title": "x", "start": "2026-11-28T10:00", "time_zone": "Mars/Base"}, "invalid_time_zone"),
    ]
    for body, wanted in cases:
        refused = program.post("/api/v1/events", headers=as_key(writer), json=body)
        assert refused.status_code == 422 and code(refused) == wanted, body
    unknown = program.post("/api/v1/events", headers=as_key(writer),
                           json={"title": "x", "start": "2026-11-28", "all_day": True, "calendar_id": 999999})
    assert unknown.status_code == 404
    assert program.get("/api/v1/events", headers=as_key(writer),
                       params={"start": START, "end": END}).json()["events"] == []


def test_time26_ec11_zones_whole_days_and_series_go_through_unchanged(client: TestClient, account: Account,
                                                                      program: TestClient) -> None:
    writer = make_key(client, level="write")["token"]
    flight = program.post("/api/v1/events", headers=as_key(writer), json={
        "title": "Flug", "start": "2026-12-01T09:00", "end": "2026-12-01T17:00", "time_zone": "America/New_York"})
    assert flight.json()["time_zone"] == "America/New_York" and flight.json()["start"] == "2026-12-01T09:00"
    holiday = program.post("/api/v1/events", headers=as_key(writer),
                           json={"title": "Urlaub", "start": "2026-12-20", "end": "2026-12-24", "all_day": True})
    assert holiday.json()["all_day"] is True and holiday.json()["end"] == "2026-12-24"
    series = program.post("/api/v1/events", headers=as_key(writer), json={
        "title": "Training", "start": "2026-11-26T18:30", "end": "2026-11-26T20:00", "time_zone": "Europe/Berlin",
        "rule": {"freq": "weekly", "weekdays": ["TH"]}}).json()
    assert series["recurring"] is True
    found = program.get("/api/v1/events", headers=as_key(writer),
                        params={"start": "2026-11-26", "end": "2026-12-11"}).json()["events"]
    occurrences = [entry for entry in found if entry["title"] == "Training"]
    assert len(occurrences) == 3
    second = occurrences[1]["occurrence"]
    one = program.patch(f"/api/v1/events/{series['id']}", headers=as_key(writer),
                        json={"title": "Training fällt aus", "scope": "this", "occurrence": second})
    assert one.status_code == 200, one.text
    titles = [entry["title"] for entry in program.get("/api/v1/events", headers=as_key(writer), params={
        "start": "2026-11-26", "end": "2026-12-11"}).json()["events"] if entry.get("recurring")]
    assert titles == ["Training", "Training fällt aus", "Training"]
    gone = program.delete(f"/api/v1/events/{series['id']}", headers=as_key(writer),
                          params={"scope": "this", "occurrence": occurrences[2]["occurrence"]})
    assert gone.status_code == 200 and gone.json()["trash"]["kind"] == "part"
    asked_this_without = program.patch(f"/api/v1/events/{series['id']}", headers=as_key(writer),
                                       json={"title": "x", "scope": "this"})
    assert asked_this_without.status_code == 422


def test_time26_ac6_writing_into_icloud_goes_to_the_source_and_waits_while_it_is_away(
        dav_server: str, client: TestClient, account: Account, program: TestClient) -> None:
    for remote in remote_account():
        _write_into_icloud(dav_server, client, program, remote)


def _write_into_icloud(server: str, client: TestClient, program: TestClient, remote: Remote) -> None:
    arbeit = remote.calendar("Arbeit")
    remote.put(arbeit, "a.ics", event_text("a@x", "Kundentermin"))
    source = connected(client, server, remote, ["Arbeit"])
    round_now(source)
    writer = make_key(client, "n8n", level="write")["token"]
    found = program.get("/api/v1/events", headers=as_key(writer), params={"start": START, "end": END}).json()
    event = next(entry for entry in found["events"] if entry["title"] == "Kundentermin")
    assert program.patch(f"/api/v1/events/{event['id']}", headers=as_key(writer),
                         json={"title": "Kundentermin 15 Uhr"}).status_code == 200
    round_now(source)
    assert "SUMMARY:Kundentermin 15 Uhr" in remote.get(arbeit, "a.ics").text
    SWITCHES.down = True
    try:
        assert program.patch(f"/api/v1/events/{event['id']}", headers=as_key(writer),
                             json={"title": "Kundentermin 16 Uhr"}).status_code == 200
        round_now(source)
        waiting = program.get(f"/api/v1/events/{event['id']}", headers=as_key(writer)).json()
        assert waiting["waiting"] is True
    finally:
        SWITCHES.down = False
    round_now(source)
    assert "SUMMARY:Kundentermin 16 Uhr" in remote.get(arbeit, "a.ics").text
    assert program.get(f"/api/v1/events/{event['id']}", headers=as_key(writer)).json()["waiting"] is False


# --- Concurrency (EC-8, EC-9) and text (EC-7) -----------------------------------------------------------------------


def test_time26_ec8_the_same_client_ref_twice_at_once_makes_one_event(client: TestClient, account: Account,
                                                                      program: TestClient) -> None:
    writer = make_key(client, level="write")["token"]
    body = {"title": "Einmal", "start": "2026-11-28", "all_day": True, "client_ref": "skript-0815"}
    gate = threading.Barrier(2)
    answers: list[int] = []

    def send() -> None:
        with TestClient(app, base_url="http://testserver") as other:
            gate.wait()
            answers.append(other.post("/api/v1/events", headers=as_key(writer), json=body).status_code)

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(answers) == [200, 201]
    again = program.post("/api/v1/events", headers=as_key(writer), json=body)
    assert again.status_code == 200
    found = program.get("/api/v1/events", headers=as_key(writer), params={"start": START, "end": END}).json()
    assert [entry["title"] for entry in found["events"]] == ["Einmal"]


def test_time26_ec9_a_change_over_a_newer_one_counts_as_the_later_and_the_other_hears_of_it(
        client: TestClient, account: Account, program: TestClient) -> None:
    event = make_event(client, "Training")
    writer = make_key(client, "Planer", level="write")["token"]
    old = event["etag"]
    assert client.patch(f"/api/events/{event['id']}", json={"title": "Training 19 Uhr", "etag": old}).status_code == 200
    late = program.patch(f"/api/v1/events/{event['id']}", headers={**as_key(writer), "If-Match": f'"{old}"'},
                         json={"title": "Training 20 Uhr"})
    assert late.status_code == 200 and late.json()["title"] == "Training 20 Uhr"
    detail = client.get(f"/api/events/{event['id']}").json()
    assert detail["conflict"] is not None and detail["updated_via"] == "Planer"


def test_time26_ec7_html_and_script_stay_text(client: TestClient, account: Account, program: TestClient) -> None:
    writer = make_key(client, level="write")["token"]
    title = '<img src=x onerror="alert(1)"><script>alert(2)</script>'
    made = program.post("/api/v1/events", headers=as_key(writer), json={
        "title": title, "start": "2026-11-28", "all_day": True, "notes": "[x](javascript:alert(3))"})
    assert made.status_code == 201
    assert made.json()["title"] == title
    assert client.get(f"/api/events/{made.json()['id']}").json()["title"] == title
    assert made.headers["content-type"].startswith("application/json")


# --- The walls (AC-5, AC-9, AC-10, EC-2, EC-13, EC-14) --------------------------------------------------------------


def test_time26_ac5_switches_off_turn_keys_away_keep_them_and_on_again_they_work(
        client: TestClient, account: Account, program: TestClient) -> None:
    both = make_key(client, "Beides", mcp=True)["token"]
    with SessionLocal() as db:
        settings_service.save(db, {"api_enabled": False})
    refused = program.get("/api/v1/me", headers=as_key(both))
    assert refused.status_code == 403 and code(refused) == "api_off"
    assert program.post("/api/mcp", headers=as_key(both), json={"jsonrpc": "2.0", "id": 1,
                                                                "method": "ping"}).status_code == 200
    assert client.get("/api/keys").json()["api_allowed"] is False
    made = client.post("/api/keys", json={"name": "Neu", "level": "read"})
    assert made.status_code == 403 and code(made) == "api_off"
    with SessionLocal() as db:
        settings_service.save(db, {"api_enabled": True, "mcp_enabled": False})
    assert program.get("/api/v1/me", headers=as_key(both)).status_code == 200
    off = program.post("/api/mcp", headers=as_key(both), json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert off.status_code == 403 and code(off) == "mcp_off"
    assert client.post("/api/keys", json={"name": "M", "level": "read", "mcp": True}).status_code == 403
    with SessionLocal() as db:
        settings_service.save(db, {"mcp_enabled": True})
    assert mcp(program, both, "ping")["result"] == {}


def test_time26_ac5_the_operator_sees_how_many_keys_never_names_or_calendars(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    make_key(client, "Geheimer Name", calendar_ids=[familie], mcp=True)
    make_key(client, "Zweiter")
    overview = client.get("/api/admin/keys")
    assert overview.status_code == 200
    data = overview.json()
    assert data["keys"] == 2 and data["mcp"] == 1
    assert data["accounts"][0]["keys"] == 2 and data["accounts"][0]["mcp"] == 1
    assert "Geheimer Name" not in overview.text and "Familie" not in overview.text
    anna = make_account("anna")
    with new_client(anna) as browser:
        assert browser.get("/api/admin/keys").status_code == 403


def test_time26_ac9_me_names_account_right_mcp_calendars_run_out_and_version(client: TestClient, account: Account,
                                                                            program: TestClient) -> None:
    familie = make_calendar(client, "Familie")
    made = make_key(client, "Tablet", calendar_ids=[familie], mcp=True)
    me = program.get("/api/v1/me", headers=as_key(made["token"])).json()
    assert me["app"] == "nextime" and me["name"] == "tester" and me["level"] == "read" and me["mcp"] is True
    assert me["calendars"] == [{"id": familie, "name": "Familie"}]
    assert me["expires_at"] and me["version"] and "events" in me["features"]
    assert me["key"] == {"id": made["made"]["id"], "name": "Tablet"}


def test_time26_ac10_the_key_is_named_as_author_the_log_never_holds_it_nor_a_title(
        client: TestClient, account: Account, program: TestClient) -> None:
    made = make_key(client, "Kurzbefehl", level="write")
    token = made["token"]
    created = program.post("/api/v1/events", headers=as_key(token), json={
        "title": "Streng geheimer Titel", "start": "2026-11-28", "all_day": True, "location": "Geheimer Ort"}).json()
    detail = client.get(f"/api/events/{created['id']}").json()
    assert detail["updated_via"] == "Kurzbefehl"
    assert program.get(f"/api/v1/events/{created['id']}", headers=as_key(token)).json()["updated_via"] == "Kurzbefehl"
    # A change in the web is the person's again.
    assert client.patch(f"/api/events/{created['id']}", json={"notes": "x"}).status_code == 200
    assert client.get(f"/api/events/{created['id']}").json()["updated_via"] == ""
    text = logs.log_file().read_text(encoding="utf-8")
    assert f"event_id={created['id']}" in text and "Kurzbefehl" in text
    assert token not in text and "Streng geheimer Titel" not in text and "Geheimer Ort" not in text


def test_time26_ec2_too_many_requests_or_wrong_keys_are_braked(client: TestClient, account: Account,
                                                              program: TestClient,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    token = make_key(client)["token"]
    monkeypatch.setitem(apibrake.PER_MINUTE, "api", 3)
    for _ in range(3):
        assert program.get("/api/v1/me", headers=as_key(token)).status_code == 200
    braked = program.get("/api/v1/me", headers=as_key(token))
    assert braked.status_code == 429 and code(braked) == "slow_down" and braked.headers["Retry-After"] == "60"
    for _ in range(20):
        assert program.get("/api/v1/me", headers=as_key("nt_api_falsch")).status_code == 401
    stopped = program.get("/api/v1/me", headers=as_key("nt_api_falsch"))
    assert stopped.status_code == 429 and code(stopped) == "too_many_attempts"


def test_time26_ec13_a_blocked_account_has_no_working_key(client: TestClient, account: Account,
                                                          program: TestClient) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        token = make_key(browser, mcp=True)["token"]
    assert program.get("/api/v1/me", headers=as_key(token)).status_code == 200
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        accounts.block(db, row)
    assert program.get("/api/v1/me", headers=as_key(token)).status_code == 401
    assert program.post("/api/mcp", headers=as_key(token),
                        json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code == 401


def test_time26_ec14_a_request_from_a_web_page_is_refused_and_a_session_counts_for_nothing(
        client: TestClient, account: Account, program: TestClient) -> None:
    token = make_key(client, mcp=True)["token"]
    page = program.get("/api/v1/me", headers={**as_key(token), "Origin": "https://evil.example"})
    assert page.status_code == 403 and code(page) == "origin_refused"
    mcp_page = program.post("/api/mcp", headers={**as_key(token), "Origin": "https://evil.example"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert mcp_page.status_code == 403
    # The browser's session is no key.
    assert client.get("/api/v1/me").status_code == 401


# --- MCP (AC-3, EC-12) ----------------------------------------------------------------------------------------------


def test_time26_ac3_mcp_tools_by_the_keys_right(client: TestClient, account: Account, program: TestClient) -> None:
    make_event(client, "Training")
    reader = make_key(client, "Leser", mcp=True)["token"]
    writer = make_key(client, "Planer", level="write", mcp=True)["token"]
    plain = make_key(client, "Ohne MCP")["token"]
    hello = mcp(program, reader, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert hello["serverInfo"]["name"] == "nextime" and hello["protocolVersion"] == "2025-06-18"
    read_tools = {entry["name"] for entry in mcp(program, reader, "tools/list")["result"]["tools"]}
    assert read_tools == {"list_calendars", "list_events", "search_events", "find_free_time"}
    write_tools = {entry["name"] for entry in mcp(program, writer, "tools/list")["result"]["tools"]}
    assert write_tools == read_tools | {"create_event", "update_event", "delete_event"}
    assert program.post("/api/mcp", headers=as_key(plain), json={"jsonrpc": "2.0", "id": 1,
                                                                 "method": "ping"}).status_code == 401
    listed = tool(program, reader, "list_events", start="2026-11-23", end="2026-11-29")
    assert listed["isError"] is False and [entry["title"] for entry in listed["structuredContent"]["events"]] == [
        "Training"]
    found = tool(program, reader, "search_events", query="train")
    assert found["structuredContent"]["events"][0]["title"] == "Training"
    assert tool(program, reader, "list_calendars")["structuredContent"]["time_zone"]
    refused = tool(program, reader, "create_event", title="Nein", start="2026-11-27T15:00")
    assert refused["isError"] is True and "No tool" in refused["content"][0]["text"]
    made = tool(program, writer, "create_event", title="Zahnarzt", start="2026-11-27T15:00", end="2026-11-27T15:45")
    assert made["isError"] is False
    event = made["structuredContent"]
    assert event["start"] == "2026-11-27T15:00" and event["updated_via"] == "Planer"
    changed = tool(program, writer, "update_event", event_id=event["id"], title="Zahnarzt Ben")
    assert changed["structuredContent"]["title"] == "Zahnarzt Ben"
    gone = tool(program, writer, "delete_event", event_id=event["id"])
    assert gone["structuredContent"]["in_trash"] is True
    wrong = tool(program, writer, "create_event", title="x", start="morgen")
    assert wrong["isError"] is True
    unknown_arg = tool(program, writer, "list_events", start="2026-11-23", colour="red")
    assert unknown_arg["isError"] is True


def test_time26_ec12_free_times_count_only_busy_events_of_the_keys_calendars(client: TestClient, account: Account,
                                                                            program: TestClient) -> None:
    assert client.put("/api/me/display", json={"time_zone": "Europe/Berlin"}).status_code == 200
    familie = make_calendar(client, "Familie")
    arbeit = make_calendar(client, "Arbeit")
    make_event(client, "Belegt", start="2026-11-27T10:00", end="2026-11-27T12:00", calendar_id=familie)
    make_event(client, "Frei", start="2026-11-27T13:00", end="2026-11-27T15:00", calendar_id=familie, busy=False)
    make_event(client, "Woanders", start="2026-11-27T15:00", end="2026-11-27T16:00", calendar_id=arbeit)
    token = make_key(client, mcp=True, calendar_ids=[familie])["token"]
    found = tool(program, token, "find_free_time", start="2026-11-27", end="2026-11-27", day_start="08:00",
                 day_end="18:00", minutes=60)["structuredContent"]
    gaps = [(gap["local_start"][11:], gap["local_end"][11:]) for gap in found["free"]]
    assert gaps == [("08:00", "10:00"), ("12:00", "18:00")]
    shown = program.get("/api/v1/free", headers=as_key(token), params={
        "start": "2026-11-27", "end": "2026-11-28", "day_start": "08:00", "day_end": "18:00", "minutes": 180})
    assert [(gap["local_start"][11:], gap["local_end"][11:]) for gap in shown.json()["free"]] == [("12:00", "18:00")]


# --- The description (AC-8) ----------------------------------------------------------------------------------------


def test_time26_ac8_the_description_only_for_members_signed_in_only_routes_under_api_v1(
        client: TestClient, account: Account, program: TestClient) -> None:
    token = make_key(client)["token"]
    described = client.get("/api/v1/openapi.json")
    assert described.status_code == 200
    paths = described.json()["paths"]
    assert paths and all(path.startswith("/api/v1/") for path in paths)
    assert "/api/v1/events" in paths and "curl" in paths["/api/v1/events"]["post"]["description"]
    page = client.get("/api/v1/docs")
    assert page.status_code == 200
    assert "/api-docs/swagger-ui-bundle.js" in page.text and "http" not in page.text.split("<body>")[1].replace(
        "http-equiv", "")
    assert program.get("/api/v1/docs").status_code == 401
    assert program.get("/api/v1/openapi.json", headers=as_key(token)).status_code == 401


# --- After the review (2026-11-23) -------------------------------------------------------------------------------------


def test_time26_ec4_moving_an_event_into_a_calendar_outside_the_key_is_refused(client: TestClient, account: Account,
                                                                               program: TestClient) -> None:
    familie = make_calendar(client, "Familie")
    arbeit = make_calendar(client, "Arbeit")
    event = make_event(client, "Training", calendar_id=familie)
    key = make_key(client, "Tablet", level="write", mcp=True, calendar_ids=[familie])["token"]
    moved = program.patch(f"/api/v1/events/{event['id']}", headers=as_key(key), json={"calendar_id": arbeit})
    assert moved.status_code == 404
    over_mcp = tool(program, key, "update_event", event_id=event["id"], calendar_id=arbeit)
    assert over_mcp["isError"] is True
    assert client.get(f"/api/events/{event['id']}").json()["calendar_id"] == familie


def test_time26_ec4_a_key_for_all_leaves_hidden_calendars_out_a_named_one_reads_it(client: TestClient,
                                                                                    account: Account,
                                                                                    program: TestClient) -> None:
    """Decided after the review (2026-11-23): a key for "all" reads what the member shows; a hidden calendar only
    when the key or the question names it. The API lists it with ``hidden: true``; MCP, the search and the card leave
    it out."""
    familie = make_calendar(client, "Familie")
    tagebuch = make_calendar(client, "Tagebuch")
    make_event(client, "Arzttermin", start="2026-11-27T10:00", end="2026-11-27T11:00", calendar_id=tagebuch)
    make_event(client, "Training", start="2026-11-27T18:00", end="2026-11-27T19:00", calendar_id=familie)
    assert client.patch(f"/api/calendars/{tagebuch}", json={"hidden": True}).status_code == 200
    every = make_key(client, "Alles", mcp=True)["token"]
    named = make_key(client, "Tagebuch", calendar_ids=[tagebuch])["token"]
    # Listed with hidden: true (for nexmail, 2026-11-23), its events read only when a question names it.
    shown = {row["name"]: row["hidden"] for row in program.get("/api/v1/calendars", headers=as_key(every)).json()[
        "calendars"]}
    assert shown["Tagebuch"] is True and shown["Familie"] is False
    window = program.get("/api/v1/events", headers=as_key(every), params={"start": START, "end": END}).json()
    assert [row["title"] for row in window["events"]] == ["Training"]
    asked = program.get("/api/v1/events", headers=as_key(every),
                        params={"start": START, "end": END, "calendars": str(tagebuch)}).json()
    assert [row["title"] for row in asked["events"]] == ["Arzttermin"]
    assert program.get("/api/v1/search", headers=as_key(every), params={"q": "Arzt"}).json()["events"] == []
    listed = tool(program, every, "list_calendars")["structuredContent"]["calendars"]
    assert "Tagebuch" not in [row["name"] for row in listed]
    assert "Arzttermin" not in json.dumps(program.get("/api/v1/dashboard", headers=as_key(every)).json())
    assert "Arzttermin" in json.dumps(
        program.get("/api/v1/events", headers=as_key(named), params={"start": START, "end": END}).json())


def test_time26_ec11_times_without_a_zone_are_in_the_zone_of_the_event(client: TestClient, account: Account,
                                                                        program: TestClient) -> None:
    familie = make_calendar(client, "Familie")
    event = make_event(client, "Anruf", start="2026-11-27T09:00", end="2026-11-27T09:30", zone="America/New_York",
                       calendar_id=familie)
    key = make_key(client, "Skript", level="write")["token"]
    moved = program.patch(f"/api/v1/events/{event['id']}", headers=as_key(key),
                          json={"start": "2026-11-27T10:00", "end": "2026-11-27T10:30"})
    assert moved.status_code == 200, moved.text
    found = moved.json()
    assert found["time_zone"] == "America/New_York" and found["start"].startswith("2026-11-27T10:00")
