"""Mails in the language of whoever receives them: the receiver's account language,
else their browser's at the last sign-in; else whoever sends the mail off (their account, their page, their browser);
else the instance's (the first operator's has no language of its own); else English. ``de-AT`` counts as
``de``. An invitation goes to an address no account has yet, so it speaks the inviter's language. The texts are
compared word for word where a word is the proof."""

from __future__ import annotations

from email.message import EmailMessage

import pytest
from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Account
from app.services import mailer, settings_service

from .conftest import PASSWORD, make_account
from .oidc_helpers import FakeProvider, configure, fresh_browser, sign_in_via_oidc
from .oidc_helpers import provider as oidc_provider

provider = oidc_provider


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    mails: list[EmailMessage] = []
    monkeypatch.setattr(mailer, "_send", lambda _db, message: mails.append(message))
    with SessionLocal() as db:
        settings_service.save(db, {"smtp_host": "smtp.example.com", "smtp_from": "notes@example.com",
                                   "public_url": "https://notes.example.com"})
        db.commit()
    return mails


def _set(name: str, **values: str) -> None:
    with SessionLocal() as db:
        row = db.query(Account).filter_by(name=name).one()
        for key, value in values.items():
            setattr(row, key, value)
        db.commit()


def _get(name: str) -> Account:
    with SessionLocal() as db:
        row = db.query(Account).filter_by(name=name).one()
        db.expunge(row)
        return row


def _body(message: EmailMessage) -> str:
    return str(message.get_content())


def test_mail_language_takes_the_first_it_has_and_a_region_counts_as_its_language() -> None:
    assert mailer.mail_language("de-AT") == "de"
    assert mailer.mail_language("", None, "fr", "DE_ch", "en") == "de"
    assert mailer.mail_language("fr", "") == "en"
    assert mailer.mail_language() == "en"


def test_an_invitation_to_an_address_without_an_account_speaks_the_inviters_language(
        client: TestClient, operator: Account, sent: list[EmailMessage]) -> None:
    _set("tester", display_name="Robin Keller", language="de")
    answer = client.post("/api/invites", json={"email": "new@example.com", "send": True, "days": 7, "language": "en"})
    assert answer.status_code == 201, answer.text
    message = sent[-1]
    assert message["Subject"] == "Robin Keller lädt dich zu nextime ein"
    body = _body(message)
    assert body.startswith("Robin Keller lädt dich zu nextime ein. Mit nextime liegen deine Kalender auf "
                           "deinem eigenen Server.")
    assert answer.json()["link"] in body
    # The date in German order, as a German reader writes it.
    assert "Der Link gilt einmal, bis " in body
    day = body.split("bis ", 1)[1].split(".")[0:3]
    assert len(day[0]) == 2 and len(day[1]) == 2 and day[2].startswith("20")
    assert "Wenn du diese Mail nicht erwartet hast, ignoriere sie." in body
    assert "nexsuite" not in body


def test_an_english_invitation_keeps_its_wording_and_writes_the_date_the_international_way(
        client: TestClient, operator: Account, sent: list[EmailMessage]) -> None:
    answer = client.post("/api/invites", json={"email": "new@example.com", "send": True})
    assert answer.status_code == 201, answer.text
    message = sent[-1]
    assert message["Subject"] == "Tester invites you to nextime"
    body = _body(message)
    assert body.startswith("Tester invites you to nextime, to keep your calendars on your own server.")
    until = body.split("The link works once, until ", 1)[1].split(".")[0]
    year, month, day = until.split("-")
    assert len(year) == 4 and len(month) == 2 and len(day) == 2


def test_names_stay_on_one_line(client: TestClient, operator: Account, sent: list[EmailMessage]) -> None:
    """A display name with a line break would start a new header line in the subject."""
    _set("tester", display_name="Robin\r\nBcc: x@example.com")
    answer = client.post("/api/invites", json={"email": "new@example.com", "send": True})
    assert answer.status_code == 201, answer.text
    message = sent[-1]
    assert message["Subject"] == "Robin Bcc: x@example.com invites you to nextime"
    assert "\n" not in str(message["Subject"]) and message["Bcc"] is None


def test_without_anybody_saying_a_language_the_first_operators_counts(
        client: TestClient, operator: Account, sent: list[EmailMessage]) -> None:
    """The inviter has no language and their page sends none: the instance speaks, which is its first operator."""
    make_account("lead", role="operator")
    _set("tester", language="de")
    with fresh_browser(client) as other:
        assert other.post("/api/auth/login", json={"name": "lead", "password": PASSWORD}).status_code == 200
        answer = other.post("/api/invites", json={"email": "new@example.com", "send": True})
    assert answer.status_code == 201, answer.text
    assert "lädt dich" in str(sent[-1]["Subject"])


def test_names_lose_every_control_character(client: TestClient, operator: Account, sent: list[EmailMessage]) -> None:
    """Not only line breaks: DEL, C1 controls such as NEL and the Unicode line and paragraph separators break a header
    or a line too, and none of them belongs in a name."""
    odd = "Robin" + chr(127) + "Keller" + chr(0x85) + "aus" + chr(0x2028) + "Team" + chr(0x2029) + "Nord" + chr(27) + "!" + chr(0x9b) + "2J"
    _set("tester", display_name=odd)
    answer = client.post("/api/invites", json={"email": "new@example.com", "send": True})
    assert answer.status_code == 201, answer.text
    message = sent[-1]
    assert message["Subject"] == "Robin Keller aus Team Nord ! 2J invites you to nextime"
    assert _body(message).startswith("Robin Keller aus Team Nord ! 2J invites you to nextime,")


def test_the_test_mail_finds_the_account_whatever_the_case_of_the_address(
        client: TestClient, operator: Account, sent: list[EmailMessage]) -> None:
    make_account("zoe")
    _set("zoe", email="zoe@example.com", language="de")
    answer = client.post("/api/settings/mail-test", json={"to": "Zoe@Example.COM", "language": "en"})
    assert answer.status_code == 204, answer.text
    assert sent[-1]["Subject"] == "nextime: Testmail"


@pytest.mark.parametrize(("owner", "operator_language", "page", "expect"), [
    ("de", "en", "en", "nextime: Testmail"),
    ("", "de", "en", "nextime: Testmail"),
    ("", "", "de", "nextime: Testmail"),
    ("en", "de", "de", "nextime test mail"),
    ("", "", "", "nextime test mail"),
])
def test_time3_ec10_a_mail_speaks_the_language_of_whoever_receives_it(
        client: TestClient, operator: Account, sent: list[EmailMessage], owner: str, operator_language: str,
        page: str, expect: str) -> None:
    make_account("zoe")
    _set("zoe", email="zoe@example.com", language=owner)
    _set("tester", language=operator_language)
    answer = client.post("/api/settings/mail-test", json={"to": "zoe@example.com", "language": page})
    assert answer.status_code == 204, answer.text
    assert sent[-1]["Subject"] == expect
    assert _body(sent[-1]).strip() == (
        "Der Mailserver in nextime funktioniert." if "Testmail" in expect else "The mail server in nextime works."
    )


def test_a_sign_in_notes_the_browsers_language(client: TestClient, operator: Account) -> None:
    make_account("zoe")
    with fresh_browser(client) as browser:
        answer = browser.post("/api/auth/login", json={"name": "zoe", "password": PASSWORD},
                              headers={"Accept-Language": "de-AT,de;q=0.9,en;q=0.8"})
        assert answer.status_code == 200, answer.text
    assert _get("zoe").browser_language == "de"
    with fresh_browser(client) as browser:
        browser.post("/api/auth/login", json={"name": "zoe", "password": PASSWORD}, headers={"Accept-Language": "*"})
    assert _get("zoe").browser_language == "de", "a header without a language keeps what was known"


def test_a_sign_in_through_oidc_notes_the_browsers_language(
        client: TestClient, operator: Account, provider: FakeProvider) -> None:
    configure(client, provider)
    browser = fresh_browser(client)
    browser.headers["Accept-Language"] = "de-CH"
    answer = sign_in_via_oidc(browser, provider)
    assert answer.status_code == 303, answer.text
    with SessionLocal() as db:
        row = db.query(Account).filter_by(email="alex@example.com").one()
        assert row.browser_language == "de"
