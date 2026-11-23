"""TIME-21 after the review of stage 5 ("TIME-21 Prüfbericht"): the push goes only to public addresses on port 443,
never through NAT64 (findings 2, 4); only 2xx counts as delivered, a refused message is given up, ``Retry-After`` is
heeded (finding 3); a bundle names a person only for what that person did (finding 5); the size limit of RFC 8291
(finding 6)."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any, ClassVar, Self

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import SessionLocal
from app.models import RIGHT_EDIT, RIGHT_READ, Account, Calendar, NotifyOutbox
from app.services import shares, webpush

from .calendar_helpers import make_event
from .conftest import make_account, new_client
from .test_time19 import from_source, put_share, source_calendar, vevent
from .test_time21 import Clock, PushService, clock, family, prefs, push, switch  # noqa: F401


class NoNetwork:
    """``httpx.Client`` in a test that must not connect: any use fails the test."""

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("no connection may be made")


class OneAnswer:
    """``httpx.Client`` that answers every post with one status (and headers), keeping what was asked."""

    asked: ClassVar[list[str]] = []
    status: ClassVar[int] = 201
    headers: ClassVar[dict[str, str]] = {}

    def __init__(self, *_args: Any, **kwargs: Any) -> None:
        assert kwargs.get("follow_redirects") is False and kwargs.get("trust_env") is False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, url: str, **_kwargs: Any) -> httpx.Response:
        OneAnswer.asked.append(url)
        return httpx.Response(OneAnswer.status, headers=OneAnswer.headers)


# --- Findings 2 and 4: where a push may go ---------------------------------------------------------------------------


@pytest.mark.parametrize("addresses", [["10.0.0.1"], ["::1"], ["169.254.169.254"], ["64:ff9b::7f00:1"],
                                       ["64:ff9b:1::a00:1"], ["::ffff:127.0.0.1"], ["192.168.1.20"], ["fc00::1"],
                                       ["8.8.8.8", "10.0.0.1"], []])
def test_time21_ec10_a_push_never_goes_to_the_own_network(monkeypatch: pytest.MonkeyPatch,
                                                          addresses: list[str]) -> None:
    monkeypatch.setattr(webpush, "resolve", lambda host: addresses)
    monkeypatch.setattr(webpush.httpx, "Client", NoNetwork)
    with pytest.raises(webpush.Failed) as refused:
        webpush._post("https://push.example.com/send/abc", b"x", {})
    assert refused.value.code == "refused"


def test_time21_ec10_a_push_goes_only_to_https_on_port_443(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webpush, "resolve", lambda host: ["8.8.8.8"])
    monkeypatch.setattr(webpush.httpx, "Client", NoNetwork)
    for address in ("https://push.example.com:8443/send/abc", "http://push.example.com/send/abc"):
        with pytest.raises(webpush.Failed):
            webpush._post(address, b"x", {})
        assert not webpush.endpoint_valid(address)
    assert webpush.endpoint_valid("https://push.example.com:443/send/abc")
    assert webpush.endpoint_valid("https://push.example.com/send/abc")


def test_time21_ec10_a_public_push_service_is_asked_without_redirects_and_its_wait_is_read(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webpush, "resolve", lambda host: ["8.8.8.8", "2001:4860:4860::8888"])
    monkeypatch.setattr(webpush.httpx, "Client", OneAnswer)
    OneAnswer.asked, OneAnswer.status, OneAnswer.headers = [], 429, {"Retry-After": "120"}
    assert webpush._post("https://push.example.com/send/abc", b"x", {}) == (429, 120.0)
    assert OneAnswer.asked == ["https://push.example.com/send/abc"]
    assert webpush.retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0
    assert webpush.retry_after("9999999") == webpush.LONGEST_WAIT
    assert webpush.retry_after("soon") is None


# --- Finding 3: what counts as delivered ------------------------------------------------------------------------------


def _one_waiting() -> NotifyOutbox:
    with SessionLocal() as db:
        row = db.scalar(select(NotifyOutbox))
        assert row is not None
        db.expunge(row)
        return row


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_time21_ec10_a_redirect_is_not_delivered_and_is_tried_again(client: TestClient, family: dict[str, Any],
                                                                    push: PushService, clock: Clock,
                                                                    status: int) -> None:
    push.browser(client)
    push.answer = status
    with new_client(family["anna"]) as anna:
        make_event(anna, "Elternabend", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
    assert clock.later(200) == (1, 0)
    row = _one_waiting()
    assert row.sent_at is None and row.failed_at is None and row.last_error == f"http_{status}"


@pytest.mark.parametrize("status", [400, 413])
def test_time21_ec10_a_message_the_service_refuses_is_given_up_at_once_and_logged(
        client: TestClient, family: dict[str, Any], push: PushService, clock: Clock, status: int,
        caplog: pytest.LogCaptureFixture) -> None:
    push.browser(client)
    push.answer = status
    with new_client(family["anna"]) as anna:
        make_event(anna, "Elternabend", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
    with caplog.at_level(logging.WARNING, logger="nextime.notify"):
        assert clock.later(200) == (1, 0)
    row = _one_waiting()
    assert row.failed_at is not None and row.attempts == 1 and row.payload == {}
    assert "refused by the push service" in caplog.text
    push.answer = 201
    assert clock.later(3600) == (0, 0)


def test_time21_ec10_retry_after_of_the_push_service_is_heeded(client: TestClient, family: dict[str, Any],
                                                               push: PushService, clock: Clock,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    push.browser(client)
    answers = iter([(429, 3600.0), (429, None), 201])
    monkeypatch.setattr(webpush, "POST", lambda url, body, headers: (
        push.post(url, body, headers) if (answer := next(answers)) == 201 else answer))
    with new_client(family["anna"]) as anna:
        make_event(anna, "Elternabend", "2026-12-03T18:00", "2026-12-03T19:00", calendar_id=family["calendar"])
    assert clock.later(200) == (1, 0)
    row = _one_waiting()
    assert row.next_try_at - clock.now == timedelta(seconds=3600)
    # The plan alone would try again after one minute: not before the hour the service asked for.
    assert clock.later(120) == (0, 0)
    assert clock.later(3600) == (0, 0)
    assert clock.later(5 * 60) == (0, 1)
    assert len(push.messages()) == 1


# --- Finding 5: a person only for what she did -------------------------------------------------------------------


def test_time21_ec3_a_bundle_names_a_person_only_for_her_own_changes(client: TestClient, account: Account,
                                                                     push: PushService, clock: Clock) -> None:
    prefs(account.id)
    anna = make_account("anna")
    prefs(anna.id)
    arbeit = source_calendar(account.id)
    put_share(client, arbeit, anna.id, RIGHT_READ)
    with new_client(anna) as browser:
        switch(browser, arbeit)
        push.browser(browser)
    ben = make_account("ben")
    prefs(ben.id)
    # Ben may write into the same calendar (a CalDAV calendar the owner may write into, shared to edit): one bundle
    # then holds a change of Ben's and one from the source.
    with SessionLocal() as db:
        shares.set_right(db, arbeit, ben.id, RIGHT_EDIT)
        row = db.get(Calendar, arbeit)
        assert row is not None
        row.source_id = None
        db.commit()
    with new_client(ben) as browser:
        make_event(browser, "Kundentermin", "2026-12-03T10:00", "2026-12-03T11:00", calendar_id=arbeit)
    from_source(arbeit, vevent("team", "20261203T083000Z", "20261203T093000Z", "Teamrunde"))
    clock.later(200)
    assert [message["body"] for message in push.messages()] == [
        "Ben hat „Kundentermin“ am Do, 3.12. um 10:00 eingetragen. „Teamrunde“ am Do, 3.12. um 9:30 eingetragen."]


# --- Finding 6: the size limit of RFC 8291 -------------------------------------------------------------------------


def test_time21_ec7_a_message_with_its_header_stays_within_4096_bytes() -> None:
    receiver = ec.generate_private_key(ec.SECP256R1())
    p256dh = webpush.b64url(webpush.public_bytes(receiver))
    auth = webpush.b64url(os.urandom(16))
    body = webpush.encrypt(b"x" * webpush.MAX_PLAINTEXT, p256dh, auth)
    assert webpush.MAX_PLAINTEXT == 3993 and len(body) == 4096
    with pytest.raises(ValueError):
        webpush.encrypt(b"x" * (webpush.MAX_PLAINTEXT + 1), p256dh, auth)
