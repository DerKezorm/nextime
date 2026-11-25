"""The calendar account (TIME-20) with the shared account module: every CalDAV address hangs on a fixed name of the
account (``accounts.dav_name``), never on its username, so that renaming an account keeps every calendar program it
has; as the user a program may send the current username, a former one or the address; a new password, "sign out
everywhere" and the operator's acts leave the calendar programs connected (draft "Konten, Einrichtung und
Anmeldung", table "Entscheidungen", nextime: DAV, nextime: Passwort neu, nextime: Umbenennen).
"""

from __future__ import annotations

import base64

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.main import app
from app.models import OPERATOR, Account
from app.services import devices

from .conftest import PASSWORD, TAB, make_account, new_client, sign_in

IPHONE = "iOS/18.1 (22B83) dataaccessd/1.0"
GOOD = "A-long-enough-password"


def _device(account_id: int, name: str = "iPhone") -> str:
    with SessionLocal() as db:
        row = db.get(Account, account_id)
        assert row is not None
        _row, password, _token = devices.create(db, row, name)
    return password


def _dav(method: str, path: str, user: str, password: str, depth: str = "0"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    with TestClient(app, base_url="https://testserver") as plain:
        return plain.request(method, path, headers={"Authorization": f"Basic {token}", "User-Agent": IPHONE,
                                                    "Depth": depth})


def _row(account_id: int) -> Account:
    with SessionLocal() as db:
        row = db.get(Account, account_id)
        assert row is not None
        db.expunge(row)
        return row


def test_a_new_account_keeps_its_first_username_as_its_calendar_name(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    assert _row(anna.id).dav_name == "anna"
    with new_client(anna) as browser:
        renamed = browser.put("/api/me/profile", json={"name": "anna.berg", "password": PASSWORD})
        assert renamed.status_code == 200, renamed.text
    row = _row(anna.id)
    assert (row.name, row.dav_name) == ("anna.berg", "anna")


def test_a_renamed_account_keeps_every_address_and_both_names_sign_the_program_in(client: TestClient,
                                                                                 operator: Account) -> None:
    anna = make_account("anna")
    password = _device(anna.id)
    home = "/dav/calendars/anna/"
    before = _dav("PROPFIND", home, "anna", password, "1")
    assert before.status_code == 207, before.text
    with new_client(anna) as browser:
        assert browser.put("/api/me/profile", json={"name": "anna.berg", "password": PASSWORD}).status_code == 200
    for user in ("anna", "ANNA", "anna.berg", "anna@example.com"):
        root = _dav("PROPFIND", "/dav/", user, password)
        assert root.status_code == 207, (user, root.text)
        assert "/dav/principals/anna/" in root.text and "anna.berg" not in root.text
        assert _dav("PROPFIND", home, user, password, "1").text == before.text
    # The new name gives no second set of addresses.
    assert _dav("PROPFIND", "/dav/calendars/anna.berg/", "anna.berg", password, "1").status_code == 404
    # A device set up after the rename gets the new user to type and the same principal.
    with new_client(anna) as browser:
        listed = browser.get("/api/devices").json()
        assert listed["user"] == "anna.berg"
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        made, second, _token = devices.create(db, row, "Mac")
        profile = devices.mobileconfig(host="kalender.example.com", port=443, https=True,
                                       base="https://kalender.example.com", account=row, device=made,
                                       password=second, description="nextime").decode()
    assert "<string>anna.berg</string>" in profile
    assert "https://kalender.example.com/dav/principals/anna/" in profile


def test_another_account_never_gets_in_with_a_former_name(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    password = _device(anna.id)
    with new_client(anna) as browser:
        assert browser.put("/api/me/profile", json={"name": "anna.berg", "password": PASSWORD}).status_code == 200
    # The former name stays reserved for good: nobody else takes it.
    lena = make_account("lena")
    with new_client(lena) as browser:
        taken = browser.put("/api/me/profile", json={"name": "anna", "password": PASSWORD})
        assert taken.status_code == 409, taken.text
    # Lena's own device with Anna's names or address opens nothing.
    lenas = _device(lena.id)
    for user in ("anna", "anna.berg", "anna@example.com"):
        assert _dav("PROPFIND", "/dav/", user, lenas).status_code == 401
    # Anna's device with Lena's name neither.
    assert _dav("PROPFIND", "/dav/", "lena", password).status_code == 401


def test_the_operator_renames_an_account_and_its_calendar_program_goes_on(client: TestClient,
                                                                         operator: Account) -> None:
    anna = make_account("anna")
    password = _device(anna.id)
    renamed = client.put(f"/api/accounts/{anna.id}/name", json={"name": "anna-b", "current_password": PASSWORD})
    assert renamed.status_code == 200, renamed.text
    assert _dav("PROPFIND", "/dav/calendars/anna/", "anna", password, "1").status_code == 207
    assert _dav("PROPFIND", "/dav/calendars/anna/", "anna-b", password, "1").status_code == 207


def test_a_new_password_leaves_the_calendar_programs_and_lists_them(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    password = _device(anna.id, "iPhone von Anna")
    made = client.post(f"/api/accounts/{anna.id}/reset-link", json={"current_password": PASSWORD})
    assert made.status_code == 201, made.text
    token = made.json()["link"].rsplit("/", 1)[1]
    stranger = TestClient(app, base_url="http://testserver", headers=TAB)
    used = stranger.post(f"/api/reset/{token}", json={"password": GOOD, "password_confirm": GOOD})
    assert used.status_code in (200, 204), used.text
    assert _dav("PROPFIND", "/dav/", "anna", password).status_code == 207
    with new_client(_row(anna.id)) as browser:
        access = browser.get("/api/auth/access")
        assert access.status_code == 200, access.text
        assert any(entry["kind"] == "calendar" and entry["label"] == "iPhone von Anna"
                   for entry in access.json()["access"]), access.json()


def test_signing_out_everywhere_leaves_the_calendar_programs(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    password = _device(anna.id)
    with new_client(anna) as browser:
        assert browser.post("/api/auth/logout-all").status_code in (200, 204)
    assert client.post(f"/api/accounts/{anna.id}/sign-out").status_code == 204
    assert _dav("PROPFIND", "/dav/", "anna", password).status_code == 207


def test_an_account_of_the_setup_and_of_an_invitation_get_their_calendar_name(client: TestClient) -> None:
    from .conftest import SETUP_CODE

    made = client.post("/api/setup", json={"code": SETUP_CODE, "display_name": "Robin", "name": "robin",
                                           "password": GOOD, "password_confirm": GOOD})
    assert made.status_code == 200, made.text
    boss = _row(made.json()["id"])
    assert (boss.name, boss.dav_name, boss.role) == ("robin", "robin", OPERATOR)
    sign_in(client, boss)
    link = client.post("/api/invites", json={"display_name": "Kim"}).json()["link"]
    stranger = TestClient(app, base_url="http://testserver", headers=TAB)
    joined = stranger.post(f"/api/invite/{link.rsplit('/', 1)[1]}",
                           json={"display_name": "Kim", "name": "kim.k", "password": GOOD, "password_confirm": GOOD})
    assert joined.status_code == 200, joined.text
    kim = _row(joined.json()["id"])
    assert (kim.name, kim.dav_name) == ("kim.k", "kim.k")


def test_the_emergency_command_prints_a_link(client: TestClient, operator: Account, capsys) -> None:
    from app import emergency

    assert emergency.main(["reset-link", operator.name]) == 0
    printed = capsys.readouterr().out
    assert "/reset/" in printed and "Valid until" in printed
    assert emergency.main(["reset-link", "nobody-here"]) == 1
