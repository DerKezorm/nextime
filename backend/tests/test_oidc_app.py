"""nextime' own side of sign-in through providers, around the shared contract tests (``tests/nexoidc``): the
address a provider gives, the operator's account list and unlinking, the password switch, the attempt cookie of two
instances on one host, the log, invitations at a known link and the required second factor.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.db import SessionLocal
from app.models import MEMBER, OPERATOR, Account, Invite, OidcLink, OidcProvider
from app.services import accounts, oidc_store, settings_service

from . import oidc_helpers
from .conftest import PASSWORD, make_account, sign_in
from .oidc_helpers import (
    FakeProvider,
    add_provider,
    come_back,
    configure,
    error_in,
    fresh_browser,
    link,
    location,
    sign_in_via_oidc,
    start,
)

provider = oidc_helpers.provider

def _entries() -> list[OidcProvider]:
    with SessionLocal() as db:
        rows = list(db.scalars(select(OidcProvider).order_by(OidcProvider.id)))
        for row in rows:
            db.expunge(row)
        return rows


def _links(provider_id: int) -> list[tuple[str, str]]:
    with SessionLocal() as db:
        return sorted((link.subject, db.get(Account, link.account_id).name)  # type: ignore[union-attr]
                      for link in db.scalars(select(OidcLink).where(OidcLink.provider_id == provider_id)))


def _row(name: str) -> Account:
    with SessionLocal() as db:
        row = db.scalar(select(Account).where(Account.name == name))
        assert row is not None
        db.expunge(row)
        return row


# --- The address a provider gives -------------------------------------


def test_an_account_through_the_provider_only_follows_its_address_never_one_of_another_account(
    client: TestClient, operator: Account, provider: FakeProvider
) -> None:
    configure(client, provider)
    make_account("bea")
    with SessionLocal() as db:
        db.query(Account).filter_by(name="bea").one().email = "taken@example.com"
        db.commit()
    browser = fresh_browser()
    sign_in_via_oidc(browser, provider, sub="p-1", preferred_username="neo", email="neo@example.com")
    neo = _row("neo")
    assert (neo.sign_in, neo.email, neo.email_source) == ("oidc", "neo@example.com", "provider")
    # The provider says otherwise later: followed; an address another account has: never.
    sign_in_via_oidc(fresh_browser(), provider, sub="p-1", preferred_username="neo", email="neo2@example.com")
    assert _row("neo").email == "neo2@example.com"
    sign_in_via_oidc(fresh_browser(), provider, sub="p-1", preferred_username="neo", email="TAKEN@example.com")
    assert _row("neo").email == "neo2@example.com"


def test_an_account_with_a_password_is_offered_the_address_and_takes_it_with_its_password(
    client: TestClient, operator: Account, provider: FakeProvider
) -> None:
    configure(client, provider, auto_create=False)
    alex = make_account("alex", email="")
    member = TestClient(client.app, base_url="http://testserver", headers=client.headers, follow_redirects=False)
    sign_in(member, alex)
    back = link(member, provider, PASSWORD, sub="p-alex", email="alex@example.com")
    assert error_in(back) is None
    assert _row("alex").email == ""
    me = member.get("/api/auth/me").json()
    assert me["provider_email"] == "alex@example.com"
    # Taking it is a change of address (the shared module): it needs the own password.
    assert member.post("/api/oidc/me/address", json={"password": "wrong-password-1"}).status_code == 401
    assert member.post("/api/oidc/me/address", json={"password": PASSWORD}).json()["email"] == "alex@example.com"
    row = _row("alex")
    assert (row.email, row.provider_email) == ("alex@example.com", "")
    assert member.get("/api/auth/me").json()["provider_email"] == ""
    # The person took it: it is the account's own now and stays when the link goes.
    assert member.delete("/api/oidc/sso/link").status_code == 204
    assert _row("alex").email == "alex@example.com"


def test_dont_ask_again_keeps_the_offer_away_and_an_own_address_stays_when_the_link_goes(
    client: TestClient, operator: Account, provider: FakeProvider
) -> None:
    configure(client, provider, auto_create=False)
    alex = make_account("alex")
    with SessionLocal() as db:
        db.query(Account).filter_by(name="alex").one().email = "own@example.com"
        db.commit()
    member = TestClient(client.app, base_url="http://testserver", headers=client.headers, follow_redirects=False)
    sign_in(member, alex)
    link(member, provider, PASSWORD, sub="p-alex", email="alex@example.com")
    assert member.get("/api/auth/me").json()["provider_email"] == "alex@example.com"
    assert member.delete("/api/oidc/me/address").status_code == 204
    assert member.get("/api/auth/me").json()["provider_email"] == ""
    assert member.post("/api/oidc/me/address").status_code == 409
    assert member.delete("/api/oidc/sso/link").status_code == 204
    assert _row("alex").email == "own@example.com"


def test_an_address_from_a_provider_stays_while_another_link_holds(
    client: TestClient, operator: Account, provider: FakeProvider
) -> None:
    second = provider.network.add(FakeProvider("https://second.example.com"))  # type: ignore[attr-defined]
    configure(client, provider)
    add_provider(client, second, slug="second", label="Second")
    sign_in_via_oidc(fresh_browser(), provider, sub="p-1", preferred_username="neo", email="neo@example.com")
    with SessionLocal() as db:
        neo = db.query(Account).filter_by(name="neo").one()
        entry = db.query(OidcProvider).filter_by(slug="second").one()
        db.add(OidcLink(provider_id=entry.id, subject="s-1", account_id=neo.id, issuer=entry.issuer))
        db.commit()
        first = db.query(OidcProvider).filter_by(slug="sso").one().id
    assert client.delete(f"/api/oidc/admin/providers/{first}").status_code == 200
    assert _row("neo").email == "neo@example.com", "the second link still holds"
    with SessionLocal() as db:
        second_id = db.query(OidcProvider).filter_by(slug="second").one().id
    assert client.delete(f"/api/oidc/admin/providers/{second_id}").status_code == 200
    assert _row("neo").email == ""


# --- The operator ---------------------------------------------------------------------------------------------------


def test_the_account_list_marks_the_providers_and_the_operator_unlinks(
    client: TestClient, operator: Account, provider: FakeProvider
) -> None:
    entry = configure(client, provider, auto_create=False)
    alex = make_account("alex")
    member = TestClient(client.app, base_url="http://testserver", headers=client.headers, follow_redirects=False)
    sign_in(member, alex)
    link(member, provider, PASSWORD, sub="p-alex")
    listed = {row["name"]: row for row in client.get("/api/accounts").json()}
    assert listed["alex"]["providers"] == [{"id": entry["id"], "slug": "sso", "label": "Test SSO", "managed": ""}]
    assert listed["tester"]["providers"] == []
    refused = client.request("DELETE", f"/api/oidc/admin/accounts/{alex.id}/links/{entry['id']}",
                             json={"current_password": "not the password"})
    assert refused.status_code == 401
    done = client.request("DELETE", f"/api/oidc/admin/accounts/{alex.id}/links/{entry['id']}",
                          json={"current_password": PASSWORD})
    assert done.status_code == 204, done.text
    listed = {row["name"]: row for row in client.get("/api/accounts").json()}
    assert listed["alex"]["providers"] == []


def test_the_password_switch_needs_an_active_provider(client: TestClient, operator: Account,
                                                      provider: FakeProvider) -> None:
    refused = client.put("/api/settings", json={"password_login": False})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "provider_first"
    configure(client, provider, enabled=False)
    refused = client.put("/api/settings", json={"password_login": False})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "provider_first"
    entry = _entries()[0]
    body = {"label": entry.label, "issuer": entry.issuer, "client_id": entry.client_id, "enabled": True}
    assert client.put(f"/api/oidc/admin/providers/{entry.id}", json=body).status_code == 200
    assert client.put("/api/settings", json={"password_login": False}).status_code == 200
    # The operator always gets in with the password (the way back when the provider fails).
    methods = fresh_browser().get("/api/auth/methods").json()
    assert methods["password"] is False and methods["providers"] == [{"slug": "sso", "label": "Test SSO"}]


# --- Two instances on one host -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("suffix", ["_a", "_b"])
def test_two_instances_on_one_host_keep_their_attempts_apart(
    client: TestClient, operator: Account, provider: FakeProvider, monkeypatch: pytest.MonkeyPatch, suffix: str
) -> None:
    configure(client, provider)
    monkeypatch.setattr(get_settings(), "cookie_suffix", suffix)
    oidc_store.configure()
    try:
        browser = fresh_browser()
        target = location(browser.get("/api/oidc/sso/start", params={"next": "/n/1"}))
        assert browser.cookies.get(f"nextime_oidc{suffix}") and browser.cookies.get(f"nextime_oidc_next{suffix}")
        assert browser.cookies.get("nextime_oidc") is None
        # The other instance's attempt (no suffix) is not this one's.
        other = fresh_browser()
        other.cookies.set("nextime_oidc", browser.cookies.get(f"nextime_oidc{suffix}") or "", path="/api/oidc")
        params = provider.authorize(target)
        assert error_in(other.get("/api/oidc/sso/callback", params=params)) == "oidc_state_mismatch"
        assert location(browser.get("/api/oidc/sso/callback", params=params)) == "/n/1"
    finally:
        monkeypatch.setattr(get_settings(), "cookie_suffix", "")
        oidc_store.configure()


# --- The log ------------------------------------------------------------------------------------------------------


def test_no_secret_leaves_a_trace_in_the_log(client: TestClient, operator: Account, provider: FakeProvider,
                                             caplog: pytest.LogCaptureFixture) -> None:
    configure(client, provider)
    caplog.set_level(logging.DEBUG)
    browser = fresh_browser()
    target = location(browser.get("/api/oidc/sso/start"))
    params = provider.authorize(target)
    back = browser.get("/api/oidc/sso/callback", params=params)
    assert location(back) == "/"
    sent = oidc_helpers.fakes.query_of(target)
    log = caplog.text
    for secret in (provider.client_secret, params["code"], params["state"], sent["code_challenge"], sent["nonce"]):
        assert secret not in log
    assert "alex@example.com" not in log


def test_a_blocked_account_does_not_come_in_through_the_provider(client: TestClient, operator: Account,
                                                                 provider: FakeProvider) -> None:
    configure(client, provider)
    sign_in_via_oidc(fresh_browser(), provider, sub="p-1", preferred_username="neo")
    neo = _row("neo")
    assert client.post(f"/api/accounts/{neo.id}/block", json={"current_password": PASSWORD}).status_code == 204
    browser = fresh_browser()
    back = sign_in_via_oidc(browser, provider, sub="p-1", preferred_username="neo")
    assert error_in(back) == "account_blocked" and browser.get("/api/auth/me").status_code == 401


def test_a_start_without_a_page_forgets_the_page_of_an_earlier_start(client: TestClient, operator: Account,
                                                                     provider: FakeProvider) -> None:
    configure(client, provider)
    browser = fresh_browser()
    location(browser.get("/api/oidc/sso/start", params={"next": "/b/old"}))
    assert (browser.cookies.get("nextime_oidc_next") or "").strip('"') == "/b/old"
    target = location(browser.get("/api/oidc/sso/start"))
    assert browser.cookies.get("nextime_oidc_next") is None
    assert location(oidc_helpers.come_back(browser, provider, target)) == "/"


# --- An invitation at a known link ----------------------------------------------------------------------------------


def _invite(role: str) -> str:
    with SessionLocal() as db:
        boss = db.scalar(select(Account).where(Account.role == OPERATOR))
        assert boss is not None
        _invite_row, token = accounts.create_invite(db, boss, days=7, role=role)
        return token


@pytest.mark.parametrize(("before", "invited", "after"), [
    (MEMBER, MEMBER, MEMBER), (MEMBER, OPERATOR, OPERATOR), (OPERATOR, MEMBER, OPERATOR)])
def test_an_invitation_at_a_known_link_adds_its_role_to_the_account_and_never_takes_one_away(
    client: TestClient, operator: Account, provider: FakeProvider, before: str, invited: str, after: str
) -> None:
    configure(client, provider)
    sign_in_via_oidc(fresh_browser(), provider, sub="p-1", preferred_username="dora")
    with SessionLocal() as db:
        db.query(Account).filter_by(name="dora").one().role = before
        db.commit()
    token = _invite(invited)
    browser = fresh_browser()
    assert location(sign_in_via_oidc(browser, provider, sub="p-1", preferred_username="dora", invite=token)) == "/"
    assert browser.get("/api/auth/me").json()["name"] == "dora"
    assert _row("dora").role == after
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Invite)) == 0, "the invitation is used up"


def test_an_invitation_used_meanwhile_still_lets_a_known_link_in_without_the_role(
    client: TestClient, operator: Account, provider: FakeProvider
) -> None:
    configure(client, provider)
    sign_in_via_oidc(fresh_browser(), provider, sub="p-1", preferred_username="dora")
    token = _invite(OPERATOR)
    browser = fresh_browser()
    where = start(browser, invite=token)
    with SessionLocal() as db:
        db.execute(delete(Invite))
        db.commit()
    assert location(come_back(browser, provider, where)) == "/"
    assert browser.get("/api/auth/me").json()["name"] == "dora" and _row("dora").role == MEMBER


# --- "Second factor required": not forced by a sign-in through a provider that checks it -------


def _required_with_linked_password_account(
    client: TestClient, provider: FakeProvider, *, trusts: bool
) -> tuple[Account, dict]:
    made = oidc_helpers.configure(client, provider, auto_create=False, trusts_second_factor=trusts)
    anna = make_account("anna")
    with SessionLocal() as db:
        db.add(OidcLink(provider_id=made["id"], subject="anna-1", account_id=anna.id, issuer=provider.issuer))
        settings_service.save(db, {"two_factor_required": True})
    return anna, made


def _setup_asked(browser: TestClient) -> bool:
    me = browser.get("/api/auth/me")
    assert me.status_code == 200, me.text
    return bool(me.json()["second_factor_setup_required"])


def test_required_second_factor_does_not_force_a_sign_in_through_a_provider_that_checks_it_but_does_the_password(
    client: TestClient, operator: Account, provider: FakeProvider
) -> None:
    anna, _ = _required_with_linked_password_account(client, provider, trusts=True)
    through_provider = fresh_browser()
    assert location(sign_in_via_oidc(through_provider, provider, sub="anna-1", preferred_username="anna")) == "/"
    assert _setup_asked(through_provider) is False
    assert through_provider.get("/api/oidc/me").status_code == 200
    # The same account with its password: asked, as before.
    with_password = TestClient(client.app, base_url="http://testserver", headers=client.headers, follow_redirects=False)
    sign_in(with_password, anna)
    assert _setup_asked(with_password) is True
    blocked = with_password.get("/api/oidc/me")
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "second_factor_setup_required"
    # Both sessions side by side: one does not decide for the other.
    assert through_provider.get("/api/oidc/me").status_code == 200


def test_required_second_factor_still_forces_a_provider_that_does_not_check_it(
    client: TestClient, operator: Account, provider: FakeProvider
) -> None:
    _required_with_linked_password_account(client, provider, trusts=False)
    browser = fresh_browser()
    assert location(sign_in_via_oidc(browser, provider, sub="anna-1", preferred_username="anna")) == "/"
    assert _setup_asked(browser) is True
    assert browser.get("/api/oidc/me").status_code == 403
