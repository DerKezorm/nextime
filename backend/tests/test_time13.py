"""TIME-13 Collections on the server: "All" is every calendar shown, own collections by name with their calendars,
the same on every device, the checks of the name, a second click, calendars that go and come back, and the views
asking only for the calendars of the collection chosen on the device."""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import RIGHT_BUSY, RIGHT_READ, Account, Calendar
from app.services import live

from .calendar_helpers import code, make_calendar, make_event, named, share, unshare, window
from .conftest import make_account, new_client


def _collections(client: TestClient) -> list[dict]:
    answer = client.get("/api/collections")
    assert answer.status_code == 200, answer.text
    return answer.json()["collections"]


def _make(client: TestClient, name: str, ids: list[int], ref: str | None = None) -> dict:
    answer = client.post("/api/collections", json={"name": name, "calendar_ids": ids, "client_ref": ref})
    assert answer.status_code in (200, 201), answer.text
    made = answer.json()["created"]
    return next(row for row in answer.json()["collections"] if row["id"] == made)


# --- AC-1, AC-2, AC-3, AC-5, AC-10 ----------------------------------------------------------------------------------


def test_time13_ac1_a_new_account_has_no_stored_collection_and_sees_every_shown_calendar(client: TestClient,
                                                                                       account: Account) -> None:
    personal = named(client, "Personal")["id"]
    familie = make_calendar(client, "Familie")
    make_event(client, "Training", calendar_id=familie)
    make_event(client, "Zahnarzt", "2026-11-27T08:15", "2026-11-27T09:00", calendar_id=personal)
    assert _collections(client) == []
    assert sorted(row["title"] for row in window(client)) == ["Training", "Zahnarzt"]


def test_time13_ac2_a_collection_holds_calendars_of_every_source_and_one_calendar_may_be_in_several(
        client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        annas = make_calendar(browser, "Anna · Arbeit")
    share(annas, account.id, RIGHT_BUSY)
    familie = make_calendar(client, "Familie")
    feiertage = make_calendar(client, "Feiertage", background=True)
    arbeit = _make(client, "Arbeit", [annas, feiertage])
    family = _make(client, "Familie", [familie, annas, feiertage])
    assert arbeit["calendar_ids"] == sorted([annas, feiertage])
    assert family["calendar_ids"] == sorted([familie, annas, feiertage])
    assert [row["name"] for row in _collections(client)] == ["Arbeit", "Familie"]


def test_time13_ac3_the_views_ask_only_for_the_calendars_of_the_chosen_collection(client: TestClient,
                                                                                  account: Account) -> None:
    familie = make_calendar(client, "Familie")
    verein = make_calendar(client, "Verein")
    make_event(client, "Fußball Ben", calendar_id=familie)
    make_event(client, "Training", calendar_id=verein)
    assert [row["title"] for row in window(client, calendars=f"{familie}")] == ["Fußball Ben"]
    assert sorted(row["title"] for row in window(client, calendars=f"{familie},{verein}")) == ["Fußball Ben",
                                                                                               "Training"]
    assert window(client, calendars="") == []
    assert client.get("/api/events", params={"start": "2026-11-01T00:00:00Z", "end": "2026-12-01T00:00:00Z",
                                             "calendars": "eins"}).status_code == 422


def test_time13_ac5_a_new_calendar_lands_in_all_and_not_quietly_in_other_collections(client: TestClient,
                                                                                    account: Account) -> None:
    familie = make_calendar(client, "Familie")
    _make(client, "Familie", [familie])
    neu = make_calendar(client, "Neu")
    make_event(client, "Neu da", calendar_id=neu)
    assert _collections(client)[0]["calendar_ids"] == [familie]
    assert [row["title"] for row in window(client)] == ["Neu da"]


def test_time13_ac10_a_collection_made_on_one_device_is_the_same_on_another(client: TestClient,
                                                                            account: Account) -> None:
    familie = make_calendar(client, "Familie")
    _make(client, "Familie", [familie])
    with new_client(account) as phone:
        assert [(row["name"], row["calendar_ids"]) for row in _collections(phone)] == [("Familie", [familie])]


def test_time13_ec3_all_is_no_stored_collection_and_cannot_be_deleted(client: TestClient, account: Account) -> None:
    assert client.delete("/api/collections/0").status_code == 404
    assert client.patch("/api/collections/0", json={"name": "Alles"}).status_code == 404


# --- EC-1, EC-2, EC-4, EC-5, EC-16 ----------------------------------------------------------------------------------


def test_time13_ec1_a_deleted_calendar_or_an_ended_share_leaves_the_collection(client: TestClient,
                                                                               account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        annas = make_calendar(browser, "Garten")
    share(annas, account.id, RIGHT_READ)
    familie = make_calendar(client, "Familie")
    verein = make_calendar(client, "Verein")
    made = _make(client, "Familie", [familie, verein, annas])
    assert client.delete(f"/api/calendars/{verein}").status_code == 200
    unshare(annas, account.id)
    assert _collections(client)[0]["calendar_ids"] == [familie]
    # Shared again later, it is in no collection but "All" (the share ended took it out).
    share(annas, account.id, RIGHT_READ)
    assert _collections(client)[0]["calendar_ids"] == [familie]
    # An emptied collection stays, with no calendar in it.
    assert client.delete(f"/api/calendars/{familie}").status_code == 200
    assert _collections(client) == [{**made, "calendar_ids": [], "updated_at": _collections(client)[0]["updated_at"]}]


def test_time13_ec16_a_calendar_brought_back_from_the_trash_is_in_its_collections_again(client: TestClient,
                                                                                        account: Account) -> None:
    familie = make_calendar(client, "Familie")
    _make(client, "Arbeit", [familie])
    _make(client, "Familie", [familie])
    assert client.delete(f"/api/calendars/{familie}").status_code == 200
    # Saved while it lies in the trash: it keeps its place all the same.
    arbeit = _collections(client)[0]
    assert client.patch(f"/api/collections/{arbeit['id']}", json={"calendar_ids": []}).status_code == 200
    assert client.delete(f"/api/collections/{_collections(client)[1]['id']}").status_code == 200
    with SessionLocal() as db:
        row = db.get(Calendar, familie)
        assert row is not None
        row.deleted_at = None
        db.commit()
    assert [(row["name"], row["calendar_ids"]) for row in _collections(client)] == [("Arbeit", [familie])]


def test_time13_ec4_a_hidden_calendar_stays_in_the_collection_but_shows_nothing(client: TestClient,
                                                                               account: Account) -> None:
    familie = make_calendar(client, "Familie")
    make_event(client, "Schwimmen", calendar_id=familie)
    _make(client, "Familie", [familie])
    assert client.patch(f"/api/calendars/{familie}", json={"hidden": True}).status_code == 200
    assert _collections(client)[0]["calendar_ids"] == [familie]
    assert window(client, calendars=f"{familie}") == []
    assert client.patch(f"/api/calendars/{familie}", json={"hidden": False}).status_code == 200
    assert [row["title"] for row in window(client, calendars=f"{familie}")] == ["Schwimmen"]


async def test_time13_ec5_the_last_save_counts_and_the_other_device_hears_of_it(client: TestClient,
                                                                                account: Account) -> None:
    familie = make_calendar(client, "Familie")
    verein = make_calendar(client, "Verein")
    made = _make(client, "Familie", [familie])
    entry = live.subscribe(account.id)
    try:
        with new_client(account) as phone:
            await asyncio.to_thread(phone.patch, f"/api/collections/{made['id']}",
                                    json={"name": "Familie", "calendar_ids": [verein]})
            await asyncio.to_thread(client.patch, f"/api/collections/{made['id']}",
                                    json={"name": "Zuhause", "calendar_ids": [familie, verein]})
            assert await asyncio.wait_for(entry[1].get(), 2) == "changed"
            assert [(row["name"], row["calendar_ids"]) for row in _collections(phone)] == [
                ("Zuhause", sorted([familie, verein]))]
    finally:
        live.unsubscribe(account.id, entry)


def test_time13_ec2_a_deleted_collection_is_gone_everywhere(client: TestClient, account: Account) -> None:
    made = _make(client, "Familie", [])
    with new_client(account) as phone:
        assert phone.delete(f"/api/collections/{made['id']}").status_code == 200
    assert _collections(client) == []
    answer = client.patch(f"/api/collections/{made['id']}", json={"name": "Familie"})
    assert answer.status_code == 404 and code(answer) == "collection_gone"


def test_time13_ac8_deleting_a_collection_leaves_calendars_and_events(client: TestClient, account: Account) -> None:
    familie = make_calendar(client, "Familie")
    make_event(client, "Schwimmen", calendar_id=familie)
    made = _make(client, "Familie", [familie])
    assert client.delete(f"/api/collections/{made['id']}").status_code == 200
    assert named(client, "Familie")["id"] == familie
    assert [row["title"] for row in window(client)] == ["Schwimmen"]


# --- EC-6, EC-10, EC-14, EC-15 ----------------------------------------------------------------------------------------


def test_time13_ec6_umlauts_and_emoji_stay_blanks_at_the_edges_go_an_empty_name_is_refused(client: TestClient,
                                                                                          account: Account) -> None:
    assert _make(client, "  Größere Familie 🎄  ", [])["name"] == "Größere Familie 🎄"
    answer = client.post("/api/collections", json={"name": "   ", "calendar_ids": []})
    assert answer.status_code == 422 and code(answer) == "name_missing"
    assert answer.json()["detail"]["field"] == "name"


def test_time13_ec10_a_second_click_on_save_makes_one_collection(client: TestClient, account: Account) -> None:
    first = client.post("/api/collections", json={"name": "Arbeit", "calendar_ids": [], "client_ref": "ref-1"})
    second = client.post("/api/collections", json={"name": "Arbeit", "calendar_ids": [], "client_ref": "ref-1"})
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()["created"] == second.json()["created"]
    assert len(_collections(client)) == 1


def test_time13_ec14_a_name_with_code_is_kept_as_text(client: TestClient, account: Account) -> None:
    name = "<img src=x onerror=alert(1)>"
    assert _make(client, name, [])["name"] == name


def test_time13_ec15_at_most_60_characters_and_no_second_name_alike(client: TestClient, account: Account) -> None:
    assert _make(client, "x" * 60, [])["name"] == "x" * 60
    answer = client.post("/api/collections", json={"name": "y" * 61, "calendar_ids": []})
    assert answer.status_code == 422 and code(answer) == "name_too_long"
    familie = _make(client, "Familie", [])
    answer = client.post("/api/collections", json={"name": "familie", "calendar_ids": []})
    assert answer.status_code == 422 and code(answer) == "collection_name_taken"
    # Its own name again, other letters' case: fine.
    assert client.patch(f"/api/collections/{familie['id']}", json={"name": "FAMILIE"}).status_code == 200


def test_time13_ac2_a_calendar_the_account_does_not_see_is_refused(client: TestClient, account: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as browser:
        annas = make_calendar(browser, "Geheim")
    answer = client.post("/api/collections", json={"name": "Neugier", "calendar_ids": [annas]})
    assert answer.status_code == 422 and code(answer) == "calendar_unknown"
    with new_client(anna) as browser:
        assert browser.get("/api/collections").json()["collections"] == []
