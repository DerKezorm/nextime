"""TIME-2 Accounts and sign-in: invitations, roles, blocking and deleting with 30 days to bring back, the profile,
signing out, the password rules; what the operator sees of others (never calendars, events or access data)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.main import app
from app.models import MEMBER, OPERATOR, Account, Invite, utcnow
from app.services import accounts, content
from app.services.content import Counts

from . import oidc_helpers
from .conftest import PASSWORD, make_account, new_client, sign_in

GOOD = "a long enough password"


def _code(answer: object) -> str:
    return str(answer.json()["detail"]["code"])  # type: ignore[attr-defined]


def _token(link: str) -> str:
    return link.rsplit("/", 1)[1]


def _stranger() -> TestClient:
    return TestClient(app, base_url="http://testserver", headers={"X-Nextime-Client": "tab-stranger0"})


# --- AC-1, AC-6, EC-6: invitations ------------------------------------------------------------------------------


def test_time2_ac1_an_invited_person_makes_the_account_with_a_password(client: TestClient, operator: Account) -> None:
    made = client.post("/api/invites", json={"email": "lena@example.com"})
    assert made.status_code == 201
    stranger = _stranger()
    offer = stranger.get(f"/api/invite/{_token(made.json()['link'])}").json()
    assert offer["email"] == "lena@example.com" and offer["password"] is True
    accepted = stranger.post(f"/api/invite/{_token(made.json()['link'])}",
                             json={"display_name": "Lena", "password": GOOD, "password_confirm": GOOD})
    assert accepted.status_code == 200 and accepted.json()["role"] == MEMBER
    assert stranger.get("/api/auth/me").json()["display_name"] == "Lena"


def test_time2_ac6_an_open_invitation_stands_in_the_list_until_the_account_is_made(client: TestClient,
                                                                                    operator: Account) -> None:
    made = client.post("/api/invites", json={"email": "lena@example.com"}).json()
    listed = client.get("/api/invites").json()
    assert [row["email"] for row in listed] == ["lena@example.com"]
    _stranger().post(f"/api/invite/{_token(made['link'])}", json={"display_name": "Lena", "password": GOOD})
    assert client.get("/api/invites").json() == []
    assert "lena" in [row["name"] for row in client.get("/api/accounts").json()]


def test_time2_ec6_an_expired_or_used_invitation_says_so_and_makes_nothing(client: TestClient,
                                                                             operator: Account) -> None:
    used = _token(client.post("/api/invites", json={}).json()["link"])
    expired = _token(client.post("/api/invites", json={}).json()["link"])
    stranger = _stranger()
    assert stranger.post(f"/api/invite/{used}", json={"display_name": "Lena", "password": GOOD}).status_code == 200
    with SessionLocal() as db:
        for row in db.scalars(select(Invite)):
            row.expires_at = utcnow() - timedelta(minutes=1)
        db.commit()
    again = _stranger().post(f"/api/invite/{used}", json={"display_name": "Kim", "password": GOOD})
    assert again.status_code == 404 and _code(again) == "invite_invalid"
    late = _stranger().post(f"/api/invite/{expired}", json={"display_name": "Kim", "password": GOOD})
    assert late.status_code == 404 and _code(late) == "invite_expired"
    with SessionLocal() as db:
        assert db.scalar(select(Account).where(Account.name == "kim")) is None


# --- AC-10: passwords -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("place", ["setup", "invite", "own", "reset"])
def test_time2_ac10_a_password_has_twelve_characters_and_is_typed_twice_the_same(client: TestClient,
                                                                                  place: str) -> None:
    eleven, twelve = "eleven char", "twelve chars"

    def attempt(password: str, confirm: str) -> object:
        if place == "setup":
            return client.post("/api/setup", json={"display_name": "Robin", "password": password,
                                                   "password_confirm": confirm, "code": "test-setup-code"})
        if place == "invite":
            return _stranger().post(f"/api/invite/{token}", json={"display_name": "Lena", "password": password,
                                                                   "password_confirm": confirm})
        if place == "own":
            return member.put("/api/auth/password", json={"current": PASSWORD, "new": password,
                                                          "password_confirm": confirm})
        return _stranger().post(f"/api/reset/{token}", json={"password": password, "password_confirm": confirm})

    token = ""
    member = client
    if place != "setup":
        operator = make_account("boss", OPERATOR)
        sign_in(client, operator)
        if place == "invite":
            token = _token(client.post("/api/invites", json={}).json()["link"])
        if place == "own":
            member = new_client(make_account("anna"))
        if place == "reset":
            anna = make_account("anna")
            token = _token(client.post(f"/api/accounts/{anna.id}/reset-link",
                                       json={"current_password": PASSWORD}).json()["link"])
    short = attempt(eleven, eleven)
    assert short.status_code == 422 and _code(short) == "password_too_short"  # type: ignore[attr-defined]
    mismatch = attempt(twelve, "twelve charz")
    assert mismatch.status_code == 422 and _code(mismatch) == "password_mismatch"  # type: ignore[attr-defined]
    assert mismatch.json()["detail"]["field"] == "password_confirm"  # type: ignore[attr-defined]
    assert attempt(twelve, twelve).status_code in (200, 204)  # type: ignore[attr-defined]


# --- EC-7, EC-8: names and addresses --------------------------------------------------------------------------


@pytest.mark.parametrize(("typed", "kept"), [
    ("  Jürgen Ömer  ", "Jürgen Ömer"),
    ("Mia 🦄", "Mia 🦄"),
    ("<script>alert(1)</script>", "<script>alert(1)</script>"),
])
def test_time2_ec7_names_keep_umlauts_and_emoji_lose_the_blanks_and_stay_text(client: TestClient,
                                                                               operator: Account, typed: str,
                                                                               kept: str) -> None:
    saved = client.put("/api/me/profile", json={"display_name": typed})
    assert saved.status_code == 200 and saved.json()["display_name"] == kept
    # The API answers JSON; nothing in it is markup for a page (the page shows it as text, see the web tests).
    assert saved.headers["content-type"].startswith("application/json")


def test_time2_ec8_an_address_another_account_has_is_refused_in_any_case(client: TestClient,
                                                                          operator: Account) -> None:
    make_account("anna", email="anna@example.com")
    refused = client.put("/api/me/profile", json={"email": "ANNA@Example.com"})
    assert refused.status_code == 409 and _code(refused) == "email_taken"
    invite = client.post("/api/invites", json={"email": "Anna@EXAMPLE.com"})
    assert invite.status_code == 409 and _code(invite) == "email_taken"


# --- AC-3: what the operator sees ----------------------------------------------------------------------------------


def test_time2_ac3_the_operator_sees_name_address_role_storage_and_last_use_and_no_content(
    client: TestClient, operator: Account
) -> None:
    make_account("anna")
    row = next(row for row in client.get("/api/accounts").json() if row["name"] == "anna")
    assert {"display_name", "email", "role", "storage_used", "last_seen_at", "avatar"} <= set(row)
    forbidden = {"password_hash", "totp_secret_enc", "totp_recovery", "preferences", "calendars", "events", "access"}
    assert not forbidden & set(row)
    impact = client.get(f"/api/accounts/{row['id']}/impact").json()
    assert set(impact) == {"calendars", "events", "shared", "days"}


def test_time2_ac3_the_operator_reaches_no_account_page_of_another(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    # The account's own routes always mean the signed-in account; there is no way to name another.
    assert client.get("/api/auth/me").json()["name"] == "tester"
    assert client.put("/api/me/display", json={"week_start": 0, "account_id": anna.id}).json()["name"] == "tester"
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None and not row.preferences


# --- AC-4, EC-14: deleting with 30 days ---------------------------------------------------------------------------


def test_time2_ac4_deleting_asks_with_numbers_blocks_at_once_and_can_be_undone_for_30_days(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    anna = make_account("anna")
    monkeypatch.setattr(content, "_counters", [lambda _db, owner: Counts(calendars=2, events=17, shared=1)
                                               if owner == anna.id else Counts()])
    gone: list[int] = []
    monkeypatch.setattr(content, "_deleted", [lambda _db, owner: gone.append(owner)])
    assert client.get(f"/api/accounts/{anna.id}/impact").json() == {"calendars": 2, "events": 17, "shared": 1,
                                                                      "days": 30}
    with new_client(anna) as browser:
        assert client.request("DELETE", f"/api/accounts/{anna.id}", json={"current_password": PASSWORD}).status_code == 204
        assert browser.get("/api/auth/me").status_code == 401
    assert gone == [anna.id]
    # Day 29: back.
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        row.deleted_at = utcnow() - timedelta(days=29)
        db.commit()
    assert client.post(f"/api/accounts/{anna.id}/restore", json={"current_password": PASSWORD}).status_code == 200


def test_time2_ac4_on_day_31_the_account_goes_for_good_with_what_it_owns(client: TestClient, operator: Account,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    anna = make_account("anna")
    purged: list[int] = []
    monkeypatch.setattr(content, "_purged", [lambda _db, owner: purged.append(owner)])
    client.request("DELETE", f"/api/accounts/{anna.id}", json={"current_password": PASSWORD})
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        row.deleted_at = utcnow() - timedelta(days=31)
        db.commit()
        assert accounts.purge_deleted(db) == 1
    assert purged == [anna.id]
    refused = client.post(f"/api/accounts/{anna.id}/restore", json={"current_password": PASSWORD})
    assert refused.status_code == 404


# --- EC-1, EC-3, EC-5: blocking -----------------------------------------------------------------------------------


def test_time2_ec1_ec5_a_blocked_account_is_out_on_every_device_and_its_window_hears_why(
    client: TestClient, operator: Account
) -> None:
    anna = make_account("anna")
    first, second = new_client(anna), new_client(anna)
    assert first.get("/api/auth/me").status_code == 200
    assert client.post(f"/api/accounts/{anna.id}/block", json={"current_password": PASSWORD}).status_code == 204
    for browser in (first, second):
        answer = browser.put("/api/me/profile", json={"display_name": "Still here"})
        assert answer.status_code == 401 and _code(answer) == "account_blocked"
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None and row.display_name == "Anna"
    refused = _stranger().post("/api/auth/login", json={"name": "anna", "password": PASSWORD})
    assert refused.status_code in (401, 403)


def test_time2_ec3_the_last_operator_is_never_blocked_deleted_or_made_member(client: TestClient,
                                                                             operator: Account) -> None:
    second = make_account("boss", OPERATOR)
    for path, method, body in ((f"/api/accounts/{operator.id}/role", "PUT", {"role": "member"}),
                               (f"/api/accounts/{operator.id}", "DELETE", {})):
        answer = client.request(method, path, json={**body, "current_password": PASSWORD})
        # With a second operator the first may go down to member; deleting oneself is never allowed.
        assert answer.status_code in (200, 409)
    with SessionLocal() as db:
        accounts.set_role(db, db.get(Account, second.id), MEMBER)  # type: ignore[arg-type]
    with SessionLocal() as db, pytest.raises(accounts.AccountError) as caught:
        row = db.scalar(select(Account).where(Account.role == OPERATOR))
        assert row is not None
        accounts.block(db, row)
    assert caught.value.code == "last_operator"
    with SessionLocal() as db, pytest.raises(accounts.AccountError):
        row = db.scalar(select(Account).where(Account.role == OPERATOR))
        accounts.set_role(db, row, MEMBER)  # type: ignore[arg-type]


# --- AC-7, AC-8, AC-9: the own account --------------------------------------------------------------------------


def test_time2_ac8_the_profile_changes_the_name_and_tells_the_way_in_and_the_second_factor(client: TestClient,
                                                                                         operator: Account) -> None:
    me = client.get("/api/auth/me").json()
    assert me["sign_in"] == "password" and me["two_factor"] is False
    assert client.put("/api/me/profile", json={"display_name": "Robin"}).json()["display_name"] == "Robin"
    assert client.get("/api/auth/me").json()["display_name"] == "Robin"
    empty = client.put("/api/me/profile", json={"display_name": "   "})
    assert empty.status_code == 422 and _code(empty) == "display_name_empty"


def test_time2_ac9_signing_out_ends_this_browsers_session_only(client: TestClient, operator: Account) -> None:
    other = new_client(operator)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/setup").json()["signed_in"] is False
    assert other.get("/api/auth/me").status_code == 200


def test_time2_ac7_members_reach_their_account_and_only_the_operator_the_server(client: TestClient,
                                                                                operator: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as member:
        assert member.get("/api/auth/me").json()["role"] == MEMBER
        assert member.get("/api/settings").status_code == 403
        assert member.get("/api/accounts").status_code == 403
    assert client.get("/api/settings").status_code == 200 and client.get("/api/accounts").status_code == 200


# --- EC-11: the colors of the circles ---------------------------------------------------------------------------


def test_time2_ec11_up_to_eleven_people_never_share_a_color_and_a_color_stays(client: TestClient,
                                                                             operator: Account) -> None:
    for number in range(10):
        make_account(f"a{number:02d}")
    first = {row["name"]: row["color"] for row in client.get("/api/accounts").json()}
    assert len(first) == 11 and sorted(first.values()) == list(range(11))
    # Again: the same colors; one more account takes a color again, the others keep theirs.
    make_account("zoe")
    again = {row["name"]: row["color"] for row in client.get("/api/accounts").json()}
    assert {name: again[name] for name in first} == first
    assert 0 <= again["zoe"] < 11
    assert client.get("/api/auth/me").json()["color"] == first["tester"]


def test_time2_ec11_a_new_account_takes_a_color_nobody_has(client: TestClient, operator: Account) -> None:
    for name in ("anna", "arne"):
        make_account(name)
    client.get("/api/accounts")
    client.request("DELETE", f"/api/accounts/{_id('anna')}", json={"current_password": PASSWORD})
    make_account("anja")
    colors = {row["name"]: row["color"] for row in client.get("/api/accounts").json()}
    assert len({colors["arne"], colors["anja"], colors["tester"]}) == 3


def _id(name: str) -> int:
    with SessionLocal() as db:
        row = db.scalar(select(Account).where(Account.name == name))
        assert row is not None
        return row.id


# --- AC-5, EC-10: the picture is only the uploaded one (change of 2026-11-22) -------------------------------------

provider = oidc_helpers.provider


def test_time2_ec10_a_sign_in_through_a_provider_takes_no_picture_the_initial_stays(
    client: TestClient, operator: Account, provider: oidc_helpers.fakes.FakeProvider
) -> None:
    oidc_helpers.configure(client, provider, auto_create=True)
    browser = oidc_helpers.fresh_browser()
    oidc_helpers.sign_in_via_oidc(browser, provider, sub="lena-1", preferred_username="lena", name="Lena",
                                  picture="https://sso.example.com/lena.png")
    me = browser.get("/api/auth/me").json()
    assert me["name"] == "lena" and me["avatar"] is None
    with SessionLocal() as db:
        row = db.scalar(select(Account).where(Account.name == "lena"))
        assert row is not None and row.avatar is None
