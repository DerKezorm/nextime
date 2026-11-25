"""The sign-in brake behind a proxy and device cookies, an unchanged password,
mail without a mail server, a blocked account at the sign-in."""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app import deps
from app.config import get_settings
from app.db import SessionLocal
from app.main import app
from app.models import Account, Invite, utcnow
from app.security import DEVICE_COOKIE, MAX_FAILURES, brake, device_token, hash_password
from app.services import settings_service

from .conftest import PASSWORD, make_account, new_client

NEW_PASSWORD = "another long password"


def _browser(host: str = "172.18.0.2") -> TestClient:
    """A browser behind the proxy nobody named: every one of them comes from the proxy's address."""
    return TestClient(app, base_url="http://testserver", headers={"X-Nextime-Client": "tab-proxy00001"},
                      client=(host, 50000))


def _code(answer: object) -> str:
    return str(answer.json()["detail"]["code"])  # type: ignore[attr-defined]


def _row(name: str) -> Account:
    with SessionLocal() as db:
        row = db.query(Account).filter_by(name=name).one()
        db.expunge(row)
    return row


# --- the brake per sender always counts ---------------------------------------------------------------------------


def _spray(count: int = 32) -> list[int]:
    # The shared account module's brake: 30 free at the sender, the 31st counted starts the wait.
    """A stranger behind the same proxy tries one password against many names, with made-up X-Forwarded-For."""
    stranger = _browser()
    return [stranger.post("/api/auth/login", json={"name": f"guess{n}", "password": "summer2026"},
                          headers={"X-Forwarded-For": f"198.51.100.{n}"}).status_code for n in range(count)]


def _known(name: str, password: str = PASSWORD) -> TestClient:
    """A browser that signed in as ``name`` (and so holds its device cookie), signed out again."""
    brake.forget()
    browser = _browser()
    assert browser.post("/api/auth/login", json={"name": name, "password": password}).status_code == 200
    assert browser.cookies.get(DEVICE_COOKIE)
    browser.post("/api/auth/logout")
    return browser


def _sign_in(browser: TestClient, name: str, password: str = PASSWORD) -> None:
    brake.forget()
    assert browser.post("/api/auth/login", json={"name": name, "password": password}).status_code == 200


def _passes(browser: TestClient, name: str, password: str = PASSWORD) -> int:
    """After a stranger's guesses from the same address: in (200), or waiting like everybody (429)."""
    brake.forget()
    assert 429 in _spray()
    return browser.post("/api/auth/login", json={"name": name, "password": password}).status_code


def test_a5_a_made_up_forwarded_header_does_not_switch_off_the_brake_across_names(client: TestClient) -> None:
    """Without trusted proxies any X-Forwarded-For took the brake per sender away, and one password could be tried
    against any number of names. The header is ignored then, the connection counts."""
    sprayer = _browser("203.0.113.66")
    answers = [sprayer.post("/api/auth/login", json={"name": f"name{n}", "password": "summer2026"},
                            headers={"X-Forwarded-For": f"198.51.100.{n}"}).status_code for n in range(40)]
    assert 429 in answers and answers.index(429) <= 32


def test_a5_a_browser_known_to_that_name_passes_a_strangers_guessing(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    bob = make_account("bob")
    own = _known("anna")
    assert _passes(own, "anna") == 200
    own.post("/api/auth/logout")
    # Without that browser, with a made-up cookie or with one given to another name: the sender waits.
    assert _passes(_browser(), "anna") == 429
    forged = _browser()
    forged.cookies.set(DEVICE_COOKIE, f"{anna.id}.abc." + "0f" * 16, path="/api/auth")
    assert _passes(forged, "anna") == 429
    other = _browser()
    with SessionLocal() as db:
        row = db.get(Account, bob.id)
        assert row is not None
        other.cookies.set(DEVICE_COOKIE, device_token(db, row), path="/api/auth")
    assert _passes(other, "anna") == 429
    # The count per name stays for the known browser too.
    brake.forget()
    tries = [own.post("/api/auth/login", json={"name": "anna", "password": "wrong guess"}).status_code
             for _ in range(12)]
    assert 429 in tries


def test_a5_the_operator_gets_in_from_its_browser_while_a_stranger_guesses(client: TestClient,
                                                                           operator: Account) -> None:
    """The emergency way (``/notzugang`` signs in through the same route) included."""
    own = _known("tester")
    assert _passes(own, "tester") == 200
    assert _passes(_browser(), "tester") == 429


def _spray_name(name: str, count: int = 12) -> list[int]:
    """A stranger behind the same proxy guesses the password of one name only."""
    stranger = _browser()
    return [stranger.post("/api/auth/login", json={"name": name, "password": f"guess {n}"}).status_code
            for n in range(count)]


@pytest.mark.parametrize("name", ["tester", "anna"])
def test_a5_a_stranger_guessing_that_very_name_does_not_keep_its_known_browser_out(client: TestClient,
                                                                                   operator: Account,
                                                                                   name: str) -> None:
    """Behind a proxy nobody named, everybody has one address: the count per address and name would hold the own
    browser too after five guesses at the operator's (or the emergency account's) name. For a browser with a device
    cookie of that name the count per name goes with the browser."""
    if name != "tester":
        make_account(name)
    own = _known(name)
    brake.forget()
    # konten 0.3.10: once the name's count waits, every guess is answered like a wrong password (401), never 429.
    assert set(_spray_name(name)) == {401}
    without = _browser().post("/api/auth/login", json={"name": name, "password": PASSWORD})
    assert without.status_code == 401, "without the cookie even the right password is held, answered as wrong"
    assert without.json()["detail"]["code"] == "wrong_credentials"
    assert own.post("/api/auth/login", json={"name": name, "password": PASSWORD}).status_code == 200


def test_a5_the_known_browser_is_still_slowed_by_its_own_wrong_passwords(client: TestClient,
                                                                         operator: Account) -> None:
    own = _known("tester")
    brake.forget()
    tries = [own.post("/api/auth/login", json={"name": "tester", "password": f"wrong {n}"}).status_code
             for n in range(8)]
    assert tries[:5] == [401] * 5 and 429 in tries
    # The count is its own: another name from the same browser is not held by it.
    make_account("anna")
    assert own.post("/api/auth/login", json={"name": "anna", "password": PASSWORD}).status_code == 200


def test_a5_a_device_cookie_with_an_endless_account_number_is_no_cookie(client: TestClient, operator: Account) -> None:
    browser = _browser("192.0.2.77")
    browser.cookies.set(DEVICE_COOKIE, "9" * 40 + ".abc." + "0f" * 16, path="/api/auth")
    assert browser.post("/api/auth/login", json={"name": "tester", "password": PASSWORD}).status_code == 200


def test_a5_the_operator_hears_of_a_proxy_nobody_named(client: TestClient, operator: Account,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deps, "_warned_unknown_proxy", False)
    assert client.get("/api/settings").json()["proxy_unknown"] is False
    assert client.get("/api/settings", headers={"X-Real-IP": "198.51.100.4"}).json()["proxy_unknown"] is True
    # Remembered for the next visit without the header, and gone once the proxy is named.
    assert client.get("/api/settings").json()["proxy_unknown"] is True
    monkeypatch.setattr(get_settings(), "trusted_proxies", "172.16.0.0/12")
    assert client.get("/api/settings", headers={"X-Forwarded-For": "198.51.100.4"}).json()["proxy_unknown"] is False
    with new_client(make_account("anna")) as member:
        assert member.get("/api/settings").status_code == 403


def test_a5_a_new_password_forgets_the_browsers_but_the_one_that_changed_it(client: TestClient,
                                                                             operator: Account) -> None:
    make_account("anna")
    old = _known("anna")
    changer = _browser()
    _sign_in(changer, "anna")
    assert changer.put("/api/auth/password", json={"current": PASSWORD, "new": NEW_PASSWORD}).status_code == 204
    changer.post("/api/auth/logout")
    assert _passes(old, "anna", NEW_PASSWORD) == 429
    assert _passes(changer, "anna", NEW_PASSWORD) == 200


def test_a5_a_device_cookie_from_before_a_new_password_does_not_open_the_lock(client: TestClient,
                                                                              operator: Account) -> None:
    make_account("anna")
    old = _known("anna")
    changer = _browser()
    _sign_in(changer, "anna")
    changer.put("/api/auth/password", json={"current": PASSWORD, "new": NEW_PASSWORD})
    with SessionLocal() as db:
        row = db.query(Account).filter_by(name="anna").one()
        row.failed_logins, row.locked_until = MAX_FAILURES, utcnow() + timedelta(minutes=10)
        db.commit()
    brake.forget()
    assert old.post("/api/auth/login", json={"name": "anna", "password": NEW_PASSWORD}).status_code == 401
    assert changer.post("/api/auth/login", json={"name": "anna", "password": NEW_PASSWORD}).status_code == 200


@pytest.mark.parametrize("way", ["block", "password", "sign-out", "totp-reset"])
def test_a5_what_the_operator_does_to_an_account_forgets_its_browsers(client: TestClient, operator: Account,
                                                                      way: str) -> None:
    anna = make_account("anna")
    browser = _known("anna")
    confirm = {"current_password": PASSWORD}
    if way == "block":
        for path in ("block", "unblock"):
            assert client.post(f"/api/accounts/{anna.id}/{path}", json=confirm).status_code == 204
    elif way == "password":
        made = client.post(f"/api/accounts/{anna.id}/reset-link", json=confirm)
        assert made.status_code == 201
        token = made.json()["link"].rsplit("/", 1)[1]
        assert client.post(f"/api/reset/{token}", json={"password": PASSWORD}).status_code == 204
    elif way == "sign-out":
        assert client.post(f"/api/accounts/{anna.id}/sign-out").status_code == 204
    else:
        with SessionLocal() as db:
            row = db.get(Account, anna.id)
            assert row is not None
            row.totp_secret_enc = "sealed seed"
            db.commit()
        assert client.post(f"/api/accounts/{anna.id}/totp/reset", json=confirm).status_code == 200
    assert _passes(browser, "anna") == 429, way


def test_a5_signing_out_everywhere_forgets_the_others_and_keeps_this_one(client: TestClient,
                                                                         operator: Account) -> None:
    make_account("anna")
    other, keeper = _known("anna"), _browser()
    _sign_in(keeper, "anna")
    assert keeper.post("/api/auth/logout-all").status_code == 204
    keeper.post("/api/auth/logout")
    assert _passes(other, "anna") == 429
    assert _passes(keeper, "anna") == 200


def test_a5_a_new_account_under_the_id_of_a_deleted_one_knows_none_of_its_browsers(client: TestClient,
                                                                                   operator: Account) -> None:
    gone = make_account("zoe")
    browser = _known("zoe")
    deleted = client.request("DELETE", f"/api/accounts/{gone.id}", json={"current_password": PASSWORD})
    assert deleted.status_code == 204
    # Gone for good after 30 days: only then is the id free again.
    with SessionLocal() as db:
        row = db.get(Account, gone.id)
        assert row is not None and row.deleted_at is not None
        row.deleted_at = utcnow() - timedelta(days=31)
        db.commit()
        from app.services import accounts

        assert accounts.purge_deleted(db) == 1
    after = make_account("zora")
    assert after.id == gone.id, "SQLite gives the id of the deleted last account again"
    assert _passes(browser, "zora") == 429


def test_a5_any_way_a_password_or_block_changes_forgets_the_browsers(client: TestClient, operator: Account) -> None:
    """Also where no session is ended on the way (a block from the directory service, a password set by a service)."""
    make_account("anna")
    for change in ("password", "block"):
        browser = _known("anna")
        with SessionLocal() as db:
            row = db.query(Account).filter_by(name="anna").one()
            if change == "password":
                row.password_hash = hash_password(PASSWORD)
            else:
                row.blocked_at = utcnow()
                db.commit()
                row.blocked_at = None
            db.commit()
        assert _passes(browser, "anna") == 429, change


def test_a5_an_account_from_before_the_device_key_gets_one_at_its_next_sign_in(client: TestClient,
                                                                               operator: Account) -> None:
    """Accounts from before 0.3 have an empty key: no browser is known until the next sign-in, which draws one."""
    make_account("anna")
    with SessionLocal() as db:
        row = db.query(Account).filter_by(name="anna").one()
        row.device_key = ""
        db.commit()
    browser = _known("anna")
    assert _row("anna").device_key
    assert _passes(browser, "anna") == 200


# --- control characters in mail addresses and names --------------------------------------------------------------


@pytest.mark.parametrize("address", ["anna@example.com\n", "anna@example.com\nBcc: x@example.com",
                                     "anna@example.com\r"])
def test_a13_the_mail_pattern_ends_at_the_end(address: str) -> None:
    """Every caller strips the address first; the pattern itself does not let a closing line break pass either."""
    from app.services import accounts

    assert accounts.EMAIL_PATTERN.match(address) is None
    assert accounts.EMAIL_PATTERN.match("anna@example.com") is not None


@pytest.mark.parametrize("address", ["anna\x00@example.com", "anna@exa\x1bmple.com", "anna\x7f@example.com",
                                     "anna\x85@example.com", "anna@exa\x9bmple.com"])
def test_a13_a_mail_address_with_a_control_character_is_refused(client: TestClient, operator: Account,
                                                                 address: str) -> None:
    # A new address asks for the password (the shared account module); the form of the address is checked then.
    answer = client.put("/api/me/profile", json={"email": address, "password": PASSWORD})
    assert answer.status_code == 422 and _code(answer) == "invalid_email"
    answer = client.post("/api/invites", json={"email": address})
    assert answer.status_code == 422 and _code(answer) == "invalid_email"


# --- the same password is no new one -----------------------------------------------------------------------------


def test_e25_the_current_password_is_no_new_one_and_ends_nothing(client: TestClient, operator: Account) -> None:
    with new_client(operator) as other:
        answer = client.put("/api/auth/password", json={"current": PASSWORD, "new": PASSWORD})
        assert answer.status_code == 422 and _code(answer) == "password_unchanged"
        assert other.get("/api/auth/me").status_code == 200, "the other session goes on"
        assert client.put("/api/auth/password", json={"current": PASSWORD, "new": NEW_PASSWORD}).status_code == 204
        assert other.get("/api/auth/me").status_code == 401


# --- mail without a mail server is a setting, not a failing server ----------------------------------------------


def test_g12_a_test_mail_without_a_mail_server_is_409_and_no_error_in_the_log(
    client: TestClient, operator: Account, caplog: pytest.LogCaptureFixture
) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://time.example.com"})
    with caplog.at_level(logging.INFO):
        tested = client.post("/api/settings/mail-test", json={"to": "anna@example.com"})
        operator_invite = client.post("/api/invites", json={"email": "anna@example.com", "send": True})
    for answer in (tested, operator_invite):
        assert answer.status_code == 409 and _code(answer) == "mail_off", answer.text
    assert not [record for record in caplog.records if record.levelno >= logging.ERROR]
    # No invitation stands without anybody holding its link.
    with SessionLocal() as db:
        assert db.query(Invite).count() == 0


# --- a blocked account hears so after its right password ---------------------------------------------------------


def test_a16_a_blocked_account_hears_so_only_after_its_right_password(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    assert client.post(f"/api/accounts/{anna.id}/block", json={"current_password": PASSWORD}).status_code == 204
    brake.forget()
    wrong = _browser("192.0.2.30").post("/api/auth/login", json={"name": "anna", "password": "wrong guess"})
    assert wrong.status_code == 401 and _code(wrong) == "wrong_credentials"
    assert _row("anna").failed_logins == 1, "a wrong password counts towards the lock"
    right = _browser("192.0.2.31").post("/api/auth/login", json={"name": "anna", "password": PASSWORD})
    assert right.status_code == 403 and _code(right) == "account_blocked"
    assert right.json()["detail"]["message"] == "This account is blocked. Ask the operator."
    assert not right.cookies.get("nextime_session")
    # Locked after guessing, it says nothing more, blocked or not.
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        row.locked_until = utcnow() + timedelta(minutes=10)
        db.commit()
    locked = _browser("192.0.2.32").post("/api/auth/login", json={"name": "anna", "password": PASSWORD})
    assert locked.status_code == 401 and _code(locked) == "wrong_credentials"


def test_a16_the_code_step_checks_the_block_too(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        row.totp_secret_enc = "sealed seed"
        db.commit()
    browser = _browser("192.0.2.40")
    assert browser.post("/api/auth/login", json={"name": "anna", "password": PASSWORD}).json() == {
        "second_factor": True}
    # Blocked between the password and the code.
    assert client.post(f"/api/accounts/{anna.id}/block", json={"current_password": PASSWORD}).status_code == 204
    code = "".join(secrets.choice("0123456789") for _ in range(6))
    answer = browser.post("/api/auth/login/totp", json={"code": code})
    assert answer.status_code == 403 and _code(answer) == "account_blocked"
    assert browser.get("/api/auth/me").status_code == 401
