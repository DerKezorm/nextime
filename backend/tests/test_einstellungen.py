"""The settings pass (plan "Einstellungen", 25.11.2026) on nextime's own routes: the theme at the account (konten 1.1.0,
E5), "Address not confirmed yet" (E23), "Ready for the internet?" for the operator, no storage limit per account any
more (E24), the module's ways for Mail and for Storage and backups beside the old ones, the old summary of the backups
in its old shape, and the old address of "Erinnerungen" leading to "Benachrichtigungen" (in the interface, vitest)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.config import get_settings
from app.db import SessionLocal
from app.models import Account, NotifyOutbox, Source, WebPushSubscription
from app.services import backups, internet, notify, sync

from .conftest import PASSWORD, make_account, new_client
from .test_time21 import push  # noqa: F401 - the fixture


def _code(answer) -> str:
    detail = answer.json().get("detail", {})
    return detail.get("code", "") if isinstance(detail, dict) else ""


def test_the_theme_is_kept_at_the_account_and_a_browsers_value_is_taken_once(client: TestClient,
                                                                             operator: Account) -> None:
    assert client.get("/api/auth/me").json()["theme"] == ""
    # The first load after the update: the browser kept "light" before; the account had none, so it takes it.
    assert client.post("/api/me/theme/adopt", json={"theme": "light"}).json() == {"theme": "light"}
    # Once: another browser with "dark" does not change it.
    assert client.post("/api/me/theme/adopt", json={"theme": "dark"}).json() == {"theme": "light"}
    changed = client.put("/api/me/theme", json={"theme": "system"})
    assert changed.status_code == 200 and changed.json()["theme"] == "system"
    assert client.put("/api/me/theme", json={"theme": "pink"}).status_code == 422
    with SessionLocal() as db:
        assert db.get(Account, operator.id).theme == "system"


def test_a_link_to_prove_the_address_needs_a_mail_server(client: TestClient, operator: Account) -> None:
    answer = client.post("/api/me/email/prove")
    assert answer.status_code == 409 and _code(answer) == "mail_off"
    assert client.get("/api/auth/me").json()["mail_server"] is False


def test_ready_for_the_internet_is_the_operators_and_names_its_points(client: TestClient, operator: Account) -> None:
    found = client.get("/api/server/sign-in/readiness")
    assert found.status_code == 200, found.text
    keys = [point["key"] for point in found.json()["points"]]
    assert keys[:2] == ["address", "factorForAll"] and "brake" in keys and "proxy" in keys
    # Without a public address the address is open (red).
    assert found.json()["points"][0]["state"] == "bad"
    member = new_client(make_account("mia"))
    assert member.get("/api/server/sign-in/readiness").status_code == 403


def test_ready_for_the_internet_has_nextimes_own_point_whether_the_server_reaches_the_internet(
        client: TestClient, operator: Account, monkeypatch) -> None:
    def point() -> dict:
        internet.forget()
        found = client.get("/api/server/sign-in/readiness").json()["points"]
        assert found[-1]["key"] == "internet" and [each["key"] for each in found].count("internet") == 1
        return found[-1]

    internet.probe_override = lambda: True
    assert point() == {"key": "internet", "state": "ok", "title": "server.ready.internet.title",
                       "text": "server.ready.internet.ok", "values": {}}
    internet.probe_override = lambda: False
    found = point()
    assert (found["state"], found["text"]) == ("bad", "server.ready.internet.bad")
    # No address to ask: no check, and the syncs pause as well; the point names the variable.
    monkeypatch.setattr(get_settings(), "internet_probe_url", "")
    internet.probe_override = lambda: True
    found = point()
    assert (found["state"], found["text"]) == ("bad", "server.ready.internet.off")
    assert found["values"] == {"variable": "NEXTIME_INTERNET_PROBE_URL"}


def test_the_storage_limit_per_account_is_gone_and_its_old_way_says_so(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    answer = client.put(f"/api/accounts/{anna.id}/storage-limit", json={"limit": 1024, "current_password": PASSWORD})
    assert answer.status_code >= 400 and _code(answer) == "feature_off"
    with SessionLocal() as db:
        assert db.get(Account, anna.id).storage_limit is None
    listed = {row["name"]: row for row in client.get("/api/accounts").json()}
    assert listed["anna"]["storage_limit"] is None


def test_mail_and_storage_have_the_modules_ways_beside_the_old_ones(client: TestClient, operator: Account) -> None:
    assert client.get("/api/server/mail").status_code == 200
    assert client.get("/api/settings").json()["smtp_host"] == ""
    made = client.post("/api/backups", json={"note": "by hand"})
    assert made.status_code == 201, made.text
    listed = client.get("/api/server/storage/backups").json()
    assert [entry["name"] for entry in listed["backups"]] == [made.json()["name"]]
    assert listed["schedule"] == {"every": "daily", "time": "03:00", "keep": 14}
    assert client.get("/api/server/storage").json()["app"] == "nextime"
    member = new_client(make_account("mia"))
    for path in ("/api/server/mail", "/api/server/storage", "/api/server/storage/backups", "/api/backups"):
        assert member.get(path).status_code == 403, path


def test_the_old_summary_of_the_backups_keeps_its_shape(client: TestClient, operator: Account) -> None:
    backups.create(kind=backups.SCHEDULED)
    summary = client.get("/api/backups/summary").json()
    assert set(summary) == {"schedule", "time", "zone", "keep", "kept", "count", "last", "failure", "running"}
    assert summary["schedule"] == "daily" and summary["kept"] == 1 and summary["count"] == 1
    assert set(summary["last"]) == {"created", "size"}
    # Every week and every month are new choices (the module sicherung), on the old way too.
    assert client.put("/api/settings", json={"backup_schedule": "monthly"}).json()["backup_schedule"] == "monthly"


# --- Notifications › Which way: the probe and the list of browsers (plan "Einstellungen", question 5, E34) ----------


def test_the_probe_goes_to_this_browser_at_once_in_the_accounts_words(client: TestClient, operator: Account,
                                                                      push) -> None:
    assert client.post("/api/notify/webpush/probe").status_code == 409  # not signed up yet
    push.browser(client)
    assert client.post("/api/notify/webpush/probe").status_code == 204
    (message,) = push.messages()
    assert message["tag"] == "probe" and "nextime" in message["body"]
    push.answer = 500
    failed = client.post("/api/notify/webpush/probe")
    assert failed.status_code == 502 and _code(failed) == "webpush_failed"
    # A browser the push service forgot is gone from the list, and the probe says so.
    push.answer = 410
    assert client.post("/api/notify/webpush/probe").status_code == 409
    assert client.get("/api/reminders").json()["browsers"] == []


def test_a_browser_of_the_own_account_can_be_removed_another_ones_never(client: TestClient, operator: Account,
                                                                        push) -> None:
    push.browser(client)
    mine = client.get("/api/reminders").json()["browsers"][0]["id"]
    other = new_client(make_account("mia"))
    push.browser(other)
    theirs = other.get("/api/reminders").json()["browsers"][0]["id"]
    assert client.delete(f"/api/notify/webpush/{theirs}").status_code == 404
    assert [row["id"] for row in other.get("/api/reminders").json()["browsers"]] == [theirs]
    assert client.delete(f"/api/notify/webpush/{mine}").status_code == 204
    assert client.get("/api/reminders").json()["browsers"] == []
    assert client.delete(f"/api/notify/webpush/{mine}").status_code == 404


def test_an_account_out_gets_no_push_its_browser_stays_and_its_sources_rest_until_it_is_back(
        client: TestClient, operator: Account, push, monkeypatch) -> None:
    """konten 1.1.0 (E38): blocked or deleted, nothing goes out to the account and its sources (iCloud, Google,
    CalDAV, subscriptions) rest; its browser stays signed up; back, the sources are due at once and run again."""
    anna = make_account("anna")
    with new_client(anna) as browser_of_anna:
        push.browser(browser_of_anna)
        with SessionLocal() as db:
            source = Source(account_id=anna.id, kind="caldav", label="Nextcloud", identity="contract-caldav",
                            due_at=datetime.now(UTC) - timedelta(minutes=1))
            db.add(source)
            db.commit()
            source_id = source.id
        ran: list[int] = []
        monkeypatch.setattr(sync, "_round", lambda _db, found, **_kw: ran.append(found.id))

        def push_goes_out() -> bool:
            with SessionLocal() as db:
                target = db.scalar(select(WebPushSubscription).where(WebPushSubscription.account_id == anna.id))
                assert target is not None, "the browser stays signed up while the account is out"
                db.add(NotifyOutbox(account_id=anna.id, kind="change", target_id=target.id, calendar_id=None,
                                    payload={"title": "nextime", "text": "probe", "url": "/"},
                                    next_try_at=datetime.now(UTC) - timedelta(minutes=1)))
                db.commit()
            push.arrived.clear()
            notify.send_round()
            return bool(push.arrived)

        assert push_goes_out() and sync.run_due() == [source_id] and ran == [source_id]
        for out, back in (("block", "unblock"), ("delete", "restore")):
            ran.clear()
            confirm = {"current_password": PASSWORD}
            if out == "block":
                assert client.post(f"/api/accounts/{anna.id}/block", json=confirm).status_code == 204
            else:
                assert client.request("DELETE", f"/api/accounts/{anna.id}", json=confirm).status_code == 204
            assert not push_goes_out(), out
            with SessionLocal() as db:
                db.execute(update(Source).where(Source.id == source_id).values(due_at=datetime.now(UTC)
                                                                               - timedelta(minutes=1)))
                db.commit()
            assert sync.run_due() == [] and ran == [], out
            # As if its last round had just run: the next one far off; the return makes it due at once.
            with SessionLocal() as db:
                db.execute(update(Source).where(Source.id == source_id).values(due_at=datetime.now(UTC)
                                                                               + timedelta(hours=1)))
                db.commit()
            assert client.post(f"/api/accounts/{anna.id}/{back}", json=confirm).status_code in (200, 204), back
            with SessionLocal() as db:
                due = db.get(Source, source_id).due_at
                assert due is not None and due.replace(tzinfo=UTC) <= datetime.now(UTC) + timedelta(seconds=1), back
            assert sync.run_due() == [source_id] and ran == [source_id], back
            # The return ends the sessions from before (konten), and their sign-ups go with them; the browser signs
            # up again by itself at its next sign-in (``renewBrowser``).
            with new_client(anna) as again:
                push.browser(again)
                assert push_goes_out(), back


def test_a_tick_for_nextasks_of_an_account_that_is_out_waits_until_it_is_back(client: TestClient, operator: Account,
                                                                              monkeypatch) -> None:
    """E38: what the account checked off waits while it is out, and goes once it is back."""
    from app.models import TaskTick
    from app.services import nextasks

    anna = make_account("anna")
    sent: list[int] = []
    monkeypatch.setattr(nextasks, "send", lambda _db, row: sent.append(row.id))
    with SessionLocal() as db:
        tick = TaskTick(account_id=anna.id, task_id="t1", ref="r1", next_at=datetime.now(UTC) - timedelta(minutes=1))
        db.add(tick)
        db.commit()
        tick_id = tick.id
    confirm = {"current_password": PASSWORD}
    assert client.post(f"/api/accounts/{anna.id}/block", json=confirm).status_code == 204
    with SessionLocal() as db:
        assert nextasks.run_waiting(db) == 0 and sent == []
    assert client.post(f"/api/accounts/{anna.id}/unblock", json=confirm).status_code == 204
    with SessionLocal() as db:
        assert nextasks.run_waiting(db) == 1 and sent == [tick_id]


def test_the_box_passkeys_offers_to_add_one_only_with_a_public_https_address_or_on_localhost(
        client: TestClient, operator: Account) -> None:
    """konten 1.1.0 (E49): ``passkeys_can_add`` in the account's answer, with the inputs of the passkeys' party."""
    from app.services import settings_service

    with SessionLocal() as db:
        settings_service.save(db, {"public_url": ""})
    assert client.get("/api/auth/me", headers={"origin": "http://nextime.example.com"}).json()["passkeys_can_add"] \
        is False
    assert client.get("/api/auth/me", headers={"origin": "http://localhost:5173"}).json()["passkeys_can_add"] is True
    with SessionLocal() as db:
        settings_service.save(db, {"public_url": "https://time.example.com"})
    assert client.get("/api/auth/me", headers={"origin": "https://time.example.com"}).json()["passkeys_can_add"] \
        is True
