"""TIME-1 Installation and operation: setup with the setup code, backups on a schedule in the server's zone, the
operator's limits and switches, the state of HTTPS and the internet, failures at the start and on a full disk."""

from __future__ import annotations

import errno
import json
import logging
import os
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import __version__, config
from app.config import Settings, StartupError, get_settings
from app.db import SessionLocal
from app.main import app
from app.models import OPERATOR, Account
from app.services import accounts, backup_hooks, backups, internet, settings_service, storage
from app.vendor.nexsicherung import Schedule, archives, plan, restore

from .conftest import PASSWORD, SETUP_CODE, make_account, new_client, sign_in

GOOD = "a long enough password"
BERLIN = ZoneInfo("Europe/Berlin")
#: The real maker of archives, before any test puts its own in its place.
ORIGINAL_CREATE = backups.create


def _code(answer: object) -> str:
    return str(answer.json()["detail"]["code"])  # type: ignore[attr-defined]


# --- AC-1, EC-4: the setup ------------------------------------------------------------------------------------------


def test_time1_ac1_a_fresh_server_asks_for_the_setup_and_the_first_account_becomes_operator(client: TestClient) -> None:
    state = client.get("/api/setup").json()
    assert state["needs_setup"] is True and state["code_required"] is True and state["signed_in"] is False
    made = client.post("/api/setup", json={"display_name": "Robin", "email": "robin@example.com", "password": GOOD,
                                           "password_confirm": GOOD, "code": SETUP_CODE})
    assert made.status_code == 200, made.text
    assert made.json()["role"] == OPERATOR
    assert client.get("/api/setup").json()["needs_setup"] is False


def test_time1_ac1_without_a_given_code_nextime_writes_one_to_the_log_at_every_start_until_set_up(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(get_settings(), "setup_token", "")
    from app.vendor.nexkonten import setup as konten_setup

    konten_setup.forget_generated_code()
    code = accounts.setup_code()
    with caplog.at_level(logging.WARNING, logger="nextime.auth"):
        with new_client():
            pass
        with new_client():
            pass
    assert caplog.text.count(code) == 2
    assert client.post("/api/setup", json={"display_name": "Robin", "password": GOOD, "code": code}).status_code == 200
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nextime.auth"), new_client():
        pass
    assert code not in caplog.text


def test_time1_ac1_the_code_from_the_environment_counts(client: TestClient) -> None:
    # The test run sets NEXTIME_SETUP_TOKEN (conftest): that code opens the setup, another does not.
    assert get_settings().setup_token == SETUP_CODE
    assert client.post("/api/setup", json={"display_name": "Robin", "password": GOOD, "code": "other"}).status_code == 403
    assert client.post("/api/setup", json={"display_name": "Robin", "password": GOOD, "code": SETUP_CODE}).status_code == 200


def test_time1_ec4_a_wrong_code_makes_nothing_and_guessing_is_braked(client: TestClient) -> None:
    answers = [client.post("/api/setup", json={"display_name": "x", "password": GOOD, "code": f"g{n}"}).status_code
               for n in range(7)]
    assert answers[:5] == [403] * 5 and answers[-1] == 429
    with SessionLocal() as db:
        assert accounts.count(db) == 0


def test_time1_ec4_two_setups_make_exactly_one_operator_and_after_it_the_setup_is_closed(client: TestClient) -> None:
    body = {"display_name": "Robin", "password": GOOD, "code": SETUP_CODE}
    first = client.post("/api/setup", json=body)
    second = new_client().post("/api/setup", json=body | {"display_name": "Kim"})
    assert first.status_code == 200 and second.status_code == 409
    with SessionLocal() as db:
        assert db.query(Account).filter_by(role=OPERATOR).count() == 1
    assert client.get("/api/setup").json()["needs_setup"] is False


def test_time1_ec4_before_the_setup_every_api_answer_says_sign_in_and_the_state_says_setup(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/setup").json()["needs_setup"] is True


# --- AC-6, EC-9, AC-9: HTTPS and the state -----------------------------------------------------------------------


def test_time1_ac6_the_setup_says_whether_nextime_is_reached_over_https(client: TestClient) -> None:
    assert client.get("/api/setup").json()["https"] is False
    over_https = TestClient(app, base_url="https://testserver", headers={"X-Nextime-Client": "tab-https00001"})
    assert over_https.get("/api/setup").json()["https"] is True


def test_time1_ec9_behind_a_trusted_proxy_that_ends_https_it_counts_as_https(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    internet.probe_override = lambda: True
    behind = TestClient(app, base_url="http://testserver", headers={"X-Nextime-Client": "tab-proxy00001"},
                        client=("172.18.0.2", 50000))
    sign_in(behind, operator)
    assert behind.get("/api/server/state").json()["https"] is False
    # Nobody named the proxy: its header changes nothing.
    assert behind.get("/api/server/state", headers={"X-Forwarded-Proto": "https"}).json()["https"] is False
    monkeypatch.setattr(get_settings(), "trusted_proxies", "172.18.0.0/16")
    assert behind.get("/api/server/state", headers={"X-Forwarded-Proto": "https"}).json()["https"] is True
    # The addresses nextime names are the ones from outside: an invitation link starts with https.
    link = behind.post("/api/invites", json={}, headers={"X-Forwarded-Proto": "https"}).json()["link"]
    assert link.startswith("https://testserver/invite/")


def test_time1_ac9_the_state_names_https_and_the_internet(client: TestClient, operator: Account) -> None:
    internet.probe_override = lambda: True
    state = client.get("/api/server/state").json()
    assert state["internet"] is True and state["https"] is False and state["version"] == __version__
    internet.forget()
    internet.probe_override = lambda: False
    assert client.get("/api/server/state").json()["internet"] is False


def test_time1_ac9_the_internet_question_carries_nothing_and_is_asked_seldom(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[int] = []
    internet.probe_override = lambda: asked.append(1) is None
    assert internet.reachable() is True
    assert internet.reachable() is True
    assert len(asked) == 1


# --- AC-7, EC-13: the version ---------------------------------------------------------------------------------------


def test_time1_ac7_a_newer_version_is_named_beside_the_running_one(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import updates

    monkeypatch.setattr(updates.check, "ask", lambda: "v9.9.9")
    internet.probe_override = lambda: True
    state = client.get("/api/server/state").json()
    assert state["version"] == __version__ and state["newer"] == "v9.9.9"


def test_time1_ec13_without_an_answer_there_is_no_hint_and_no_error(client: TestClient, operator: Account) -> None:
    # The update address of the tests refuses at once (port 9): no hint, the page works.
    internet.probe_override = lambda: False
    answer = client.get("/api/server/state")
    assert answer.status_code == 200 and answer.json()["newer"] is None


def test_time1_ac7_a_new_version_on_old_data_makes_a_backup_first(client: TestClient, operator: Account) -> None:
    with SessionLocal() as db:
        settings_service.save(db, {"running_version": "0.0.1"})
    with new_client():
        pass
    listed = backups.entries()
    assert any(entry.kind == backups.UPDATE and entry.note == f"before {__version__}" for entry in listed)
    with SessionLocal() as db:
        assert settings_service.get(db, "running_version") == __version__


# --- AC-2, AC-8, EC-15, EC-16, EC-3, EC-14: backups --------------------------------------------------------------


def test_time1_ac2_daily_at_three_in_the_servers_zone_and_fourteen_kept_from_the_start(client: TestClient,
                                                                                     operator: Account) -> None:
    settings = client.get("/api/settings").json()
    assert settings["backup_schedule"] == "daily" and settings["backup_time"] == "03:00"
    assert settings["backup_keep"] == 14


def test_time1_ec3_the_backup_runs_at_three_in_the_servers_zone_whatever_utc_says() -> None:
    daily = Schedule("daily", "03:00", 14)

    def due(now: datetime, zone: ZoneInfo) -> bool:
        # The copy of the night before was made at three in that zone.
        last = datetime(2026, 1, 14, 3, 0, tzinfo=zone)
        return plan.due(daily, last, now, zone)

    # 01:59 UTC is 02:59 in Berlin in winter: not yet; 02:00 UTC is 03:00 there: due.
    assert due(datetime(2026, 1, 15, 1, 59, tzinfo=UTC), BERLIN) is False
    assert due(datetime(2026, 1, 15, 2, 0, tzinfo=UTC), BERLIN) is True
    # In UTC itself the same moment is not three yet.
    assert due(datetime(2026, 1, 15, 2, 0, tzinfo=UTC), ZoneInfo("UTC")) is False


def test_time1_ac2_the_first_scheduled_backup_comes_at_once() -> None:
    """The shared module sicherung (plan "Einstellungen", E22): a server without any scheduled copy makes the first one
    at the next look, not only at the next three o'clock."""
    assert plan.due(Schedule("daily", "03:00", 14), None, datetime(2026, 1, 15, 1, 0, tzinfo=UTC), BERLIN) is True


def _run_over(start: datetime, hours: int, zone: ZoneInfo, monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """Runs the schedule every 15 minutes from ``start`` for ``hours``; the clock of the backups is moved along."""
    made: list[datetime] = []
    monkeypatch.setattr(config, "server_zone", lambda: config.ServerZone(zone, str(zone)))
    # The copy of the evening before (the first scheduled one comes at once, E22): the schedule goes on from there.
    moment = start - timedelta(minutes=1)
    real_create = ORIGINAL_CREATE

    def create(**kwargs):
        path = real_create(**kwargs)
        # The archive says when it was made: the moved clock, not the real one.
        with zipfile.ZipFile(path) as archive:
            manifest = json.loads(archive.read(backups.MANIFEST))
        manifest["created"] = moment.isoformat(timespec="seconds")
        rewritten = path.with_name(f"nextime-{moment.astimezone(UTC):%Y-%m-%d-%H%M%S}.zip")
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(rewritten, "w") as target:
            for info in source.infolist():
                data = json.dumps(manifest).encode() if info.filename == backups.MANIFEST else source.read(info)
                target.writestr(info, data)
        path.unlink()
        made.append(moment)
        return rewritten

    monkeypatch.setattr(archives, "create", create)
    create(kind=backups.SCHEDULED)
    made.clear()
    moment = start
    while moment < start + timedelta(hours=hours):
        backups.run_job(now=moment)
        moment += timedelta(minutes=15)
    return made


def test_time1_ac2_the_schedule_makes_one_backup_a_day_at_its_time(client: TestClient, operator: Account,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    made = _run_over(datetime(2026, 11, 22, 0, 0, tzinfo=BERLIN), 48, BERLIN, monkeypatch)
    assert [moment.astimezone(BERLIN).strftime("%d %H:%M") for moment in made] == ["22 03:00", "23 03:00"]


def test_time1_ec16_the_nights_the_clocks_change_make_exactly_one_backup(client: TestClient, operator: Account,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    client.put("/api/settings", json={"backup_time": "02:30"})
    # 29 March 2026: 02:00 becomes 03:00, there is no 02:30. 25 October 2026: 02:30 comes twice.
    spring = _run_over(datetime(2026, 3, 28, 23, 0, tzinfo=UTC), 24, BERLIN, monkeypatch)
    autumn = _run_over(datetime(2026, 10, 24, 22, 0, tzinfo=UTC), 26, BERLIN, monkeypatch)
    assert [moment.astimezone(BERLIN).date().isoformat() for moment in spring] == ["2026-03-29"]
    assert [moment.astimezone(BERLIN).date().isoformat() for moment in autumn] == ["2026-10-25"]


def test_time1_ac2_the_fifteenth_backup_pushes_out_the_oldest(client: TestClient, operator: Account) -> None:
    for _ in range(15):
        backups.create(kind=backups.SCHEDULED)
    removed = backups.prune(14)
    assert removed == 1
    assert len([entry for entry in backups.entries() if entry.kind == backups.SCHEDULED]) == 14


def test_time1_ac2_time_and_number_can_be_changed(client: TestClient, operator: Account) -> None:
    changed = client.put("/api/settings", json={"backup_time": "04:15", "backup_keep": 7})
    assert changed.status_code == 200
    assert changed.json()["backup_time"] == "04:15" and changed.json()["backup_keep"] == 7
    assert backup_hooks.NextimeHooks().load_schedule() == Schedule("daily", "04:15", 7)


def test_time1_ac8_the_box_says_rhythm_zone_last_backup_with_size_and_how_many_are_kept(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.routers.backups.server_zone", lambda: config.ServerZone(BERLIN, "Europe/Berlin"))
    assert client.get("/api/backups/summary").json()["last"] is None
    backups.create(kind=backups.SCHEDULED)
    summary = client.get("/api/backups/summary").json()
    assert summary["schedule"] == "daily" and summary["time"] == "03:00" and summary["zone"] == "Europe/Berlin"
    assert summary["keep"] == 14 and summary["kept"] == 1
    assert summary["last"]["size"] > 0 and summary["failure"] is None and summary["running"] is False


def test_time1_ac8_restoring_checks_first_and_says_what_comes_changes_and_goes(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = backups.create().name
    make_account("anna")
    brief = client.post(f"/api/backups/{name}/check").json()
    assert brief["usable"] is True
    # Anna came after the backup: a restore takes her away; nothing comes back.
    assert brief["accounts_remove"] == 1 and brief["accounts_add"] == 0
    assert {"events_add", "events_change", "events_remove"} <= set(brief)
    monkeypatch.setattr(restore, "restart_soon", lambda: None)
    # Only after the confirmation with the password.
    assert client.post(f"/api/backups/{name}/restore", json={"password": "wrong one"}).status_code == 401
    assert not backups.pending_folder().exists()
    assert client.post(f"/api/backups/{name}/restore", json={"password": PASSWORD}).status_code == 202
    assert backups.apply_pending() is True
    with SessionLocal() as db:
        assert db.query(Account).filter_by(name="anna").one_or_none() is None


def test_time1_ec15_a_second_click_while_a_backup_runs_makes_none(client: TestClient, operator: Account) -> None:
    backups.lock.acquire()
    try:
        refused = client.post("/api/backups", json={})
        assert refused.status_code == 409 and _code(refused) == "backup_running"
        assert client.get("/api/backups/summary").json()["running"] is True
    finally:
        backups.lock.release()
    assert client.post("/api/backups", json={}).status_code == 201
    assert len(backups.entries()) == 1


def test_time1_ec5_a_half_backup_is_no_backup_and_a_half_restore_is_thrown_away(client: TestClient,
                                                                                operator: Account) -> None:
    folder = backups.folder()
    folder.mkdir(parents=True, exist_ok=True)
    # Stopped while writing: the hidden part file, no manifest.
    (folder / ".nextime-2026-11-22-030000.zip.part").write_bytes(b"PK half")
    with zipfile.ZipFile(folder / "nextime-2026-11-22-030001.zip", "w") as archive:
        archive.writestr(backups.DATABASE_ENTRY, b"half")
    assert client.get("/api/backups").json() == []
    assert [entry.name for entry in backups.entries() if entry.readable] == []
    # Stopped while laying out a restore: the pending folder without its manifest.
    pending = backups.pending_folder()
    pending.mkdir(parents=True)
    (pending / "nextime.db").write_bytes(b"half")
    assert backups.apply_pending() is False
    assert not pending.exists()
    with SessionLocal() as db:
        assert db.query(Account).filter_by(name="tester").one() is not None


class _Stopped(BaseException):
    """The container stopped here: no ``except Exception`` runs, as when the process is killed."""


@pytest.mark.parametrize("stop_at", ["database", "secret", "aside", "attachments", "done"])
def test_time1_ec5_a_restore_stopped_at_any_step_finishes_at_the_next_start_without_losing_attachments(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch, stop_at: str
) -> None:
    media = backups.media_root()
    media.mkdir(parents=True, exist_ok=True)
    (media / "RESTORED1234").write_bytes(b"in the backup")
    name = backups.create().name
    # After the backup: another attachment and another account, both gone once the backup is back.
    (media / "LATER5678").write_bytes(b"made later")
    make_account("anna")
    backups.stage_restore(name)

    def stop(step: str) -> None:
        if step == stop_at:
            raise _Stopped(step)

    monkeypatch.setattr(backup_hooks, "step", stop)
    with pytest.raises(_Stopped):
        backups.apply_pending()
    # Nothing of the attachments is lost in between: the old ones aside or in place, the restored ones pending or in
    # place.
    names = {path.name for folder in media.parent.glob("attachments*") for path in folder.iterdir()}
    names |= {path.name for path in (backups.pending_folder() / backups.ATTACHMENTS).glob("*")}
    assert "RESTORED1234" in names
    # The next start finishes it.
    monkeypatch.setattr(backup_hooks, "step", lambda _step: None)
    assert backups.apply_pending() is True
    assert sorted(path.name for path in media.iterdir()) == ["RESTORED1234"]
    assert (media / "RESTORED1234").read_bytes() == b"in the backup"
    assert not backups.pending_folder().exists()
    assert list(media.parent.glob("attachments.replaced-*")) == []
    with SessionLocal() as db:
        assert db.query(Account).filter_by(name="anna").one_or_none() is None
        assert db.query(Account).filter_by(name="tester").one() is not None
    # A start after that finds nothing to do and keeps the attachments.
    assert backups.apply_pending() is False
    assert sorted(path.name for path in media.iterdir()) == ["RESTORED1234"]


def test_time1_ec6_an_older_backup_is_taken_and_a_newer_one_refused(client: TestClient, operator: Account) -> None:
    name = backups.create().name
    path = backups.path_of(name)

    def with_version(version: str) -> str:
        target = path.with_name(f"nextime-2020-01-0{1 if version < __version__ else 2}-000000.zip")
        with zipfile.ZipFile(path) as source, zipfile.ZipFile(target, "w") as archive:
            for info in source.infolist():
                data = source.read(info)
                if info.filename == backups.MANIFEST:
                    manifest = json.loads(data)
                    manifest["version"] = version
                    data = json.dumps(manifest).encode()
                archive.writestr(info, data)
        return target.name

    older = with_version("0.0.1")
    assert client.post(f"/api/backups/{older}/check").json()["usable"] is True
    newer = with_version("99.0.0")
    refused = client.post(f"/api/backups/{newer}/check")
    assert refused.status_code == 400 and _code(refused) == "backup_too_new"


def test_time1_ac2_a_backup_moves_to_another_server(client: TestClient, operator: Account,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    name = backups.create().name
    data = backups.path_of(name).read_bytes()
    backups.remove(name)
    # Brought in from elsewhere: it joins the list as uploaded and is restored like any other.
    import base64

    header = {"X-Nextime-Password": base64.b64encode(PASSWORD.encode()).decode()}
    uploaded = client.post("/api/backups/upload", content=data, headers=header)
    assert uploaded.status_code == 201, uploaded.text
    monkeypatch.setattr(restore, "restart_soon", lambda: None)
    restored = client.post(f"/api/backups/{uploaded.json()['name']}/restore", json={"password": PASSWORD})
    assert restored.status_code == 202
    assert backups.apply_pending() is True


def test_time1_ec7_a_failed_backup_says_why_keeps_the_older_ones_and_tells_the_log(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    older = backups.create(kind=backups.SCHEDULED).name

    def full(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(backup_hooks.NextimeHooks, "pack", full)
    with caplog.at_level(logging.ERROR, logger="nextime.backups"):
        refused = client.post("/api/backups", json={})
    # The old way keeps its old code; the module's own way says it in its words.
    assert refused.status_code == 507 and _code(refused) == "disk_full"
    assert "A backup could not be written" in caplog.text and "No space left on device" in caplog.text
    again = client.post("/api/server/storage/backups", json={})
    assert again.status_code == 507 and _code(again) == "backup_disk_full"
    summary = client.get("/api/backups/summary").json()
    assert summary["failure"]["reason"] == "disk_full"
    assert [entry.name for entry in backups.entries()] == [older]
    monkeypatch.undo()
    assert client.post("/api/backups", json={}).status_code == 201
    assert client.get("/api/backups/summary").json()["failure"] is None


def test_time1_ec14_the_servers_clock_decides_and_the_log_warns_when_it_is_far_off(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    browser_now = (datetime.now(UTC) + timedelta(hours=2)).timestamp() * 1000
    with caplog.at_level(logging.WARNING, logger="nextime.clock"):
        answer = client.post("/api/health/clock", json={"now": int(browser_now)})
    assert answer.status_code == 200 and answer.json()["drift"] < -3600
    assert "clock differs" in caplog.text
    # The schedule asks the server's clock: an hour before three it is not due, whatever a browser says.
    last = datetime(2026, 1, 14, 3, 0, tzinfo=BERLIN)
    assert plan.due(Schedule("daily", "03:00", 14), last, datetime(2026, 1, 15, 1, 0, tzinfo=UTC), BERLIN) is False


# --- AC-4, AC-5, AC-10, EC-11, EC-12: limits and switches -----------------------------------------------------------


def test_time1_ac4_attachments_25_mb_and_trash_30_days_from_the_start_and_changed_at_once(
    client: TestClient, operator: Account
) -> None:
    settings = client.get("/api/settings").json()
    assert settings["upload_max_mb"] == 25 and settings["trash_days"] == 30
    assert client.put("/api/settings", json={"upload_max_mb": 50, "trash_days": 7}).status_code == 200
    with SessionLocal() as db:
        assert settings_service.upload_max_mb(db) == 50 and settings_service.trash_days(db) == 7


@pytest.mark.parametrize("value", [0, -3, "", "zwölf", 1.5, None])
def test_time1_ec11_a_wrong_limit_is_said_at_the_field_and_the_old_value_stays(client: TestClient, operator: Account,
                                                                               value: object) -> None:
    for field in ("upload_max_mb", "trash_days", "backup_keep"):
        before = client.get("/api/settings").json()[field]
        if value is None:
            # Left out is not a change: the value stays.
            assert client.put("/api/settings", json={field: None}).json()[field] == before
            continue
        refused = client.put("/api/settings", json={field: value})
        assert refused.status_code == 422, (field, value)
        assert field in refused.json()["detail"].get("fields", []), refused.text
        assert client.get("/api/settings").json()[field] == before


@pytest.mark.parametrize("value", ["", "25:00", "3 Uhr", "03:60"])
def test_time1_ec11_a_wrong_backup_time_is_refused(client: TestClient, operator: Account, value: str) -> None:
    refused = client.put("/api/settings", json={"backup_time": value})
    assert refused.status_code == 422 and refused.json()["detail"]["field"] == "backup_time"
    assert client.get("/api/settings").json()["backup_time"] == "03:00"


def test_time1_ec12_a_lower_limit_counts_for_what_comes_after(client: TestClient, operator: Account) -> None:
    # The limit is read at every upload; nothing stored is touched when it is lowered.
    with SessionLocal() as db:
        settings_service.save(db, {"upload_max_mb": 100})
    client.put("/api/settings", json={"upload_max_mb": 5})
    with SessionLocal() as db:
        assert settings_service.upload_max_mb(db) == 5
        assert settings_service.get(db, "trash_days") == 30


def test_time1_ac5_the_switches_from_the_start(client: TestClient, operator: Account) -> None:
    settings = client.get("/api/settings").json()
    assert settings["share_links"] is True and settings["api_enabled"] is True and settings["mcp_enabled"] is True
    assert settings["address_suggestions"] is True
    # Google: off while it is not set up, on as soon as it is.
    assert settings["google_enabled"] is False and settings["google_configured"] is False
    with SessionLocal() as db:
        settings_service.save(db, {"google_client_id": "client-of-the-test"})
    now = client.get("/api/settings").json()
    assert now["google_enabled"] is True and now["google_configured"] is True


def test_time1_ac5_each_switch_counts_at_once_on_its_own(client: TestClient, operator: Account) -> None:
    for key in ("share_links", "api_enabled", "mcp_enabled", "address_suggestions"):
        off = client.put("/api/settings", json={key: False}).json()
        assert off[key] is False
        others = [name for name in ("share_links", "api_enabled", "mcp_enabled", "address_suggestions") if name != key]
        assert all(off[name] is True for name in others)
        with SessionLocal() as db:
            assert settings_service.get(db, key) is False
        assert client.put("/api/settings", json={key: True}).json()[key] is True


def test_time1_ac5_the_google_switch_needs_google_set_up(client: TestClient, operator: Account) -> None:
    refused = client.put("/api/settings", json={"google_enabled": False})
    assert refused.status_code == 409 and _code(refused) == "google_not_configured"
    with SessionLocal() as db:
        settings_service.save(db, {"google_client_id": "client-of-the-test"})
    assert client.put("/api/settings", json={"google_enabled": False}).json()["google_enabled"] is False
    with SessionLocal() as db:
        assert settings_service.google_on(db) is False


def test_time1_ac10_address_suggestions_have_their_own_switch_on_from_the_start(client: TestClient,
                                                                                  operator: Account) -> None:
    assert client.get("/api/settings").json()["address_suggestions"] is True
    assert client.put("/api/settings", json={"address_suggestions": False}).json()["address_suggestions"] is False


# --- EC-10: only the operator -------------------------------------------------------------------------------------


def test_time1_ec10_a_member_reaches_no_part_of_server(client: TestClient, operator: Account) -> None:
    anna = make_account("anna")
    with new_client(anna) as member:
        for method, path in (("GET", "/api/settings"), ("PUT", "/api/settings"), ("GET", "/api/server/state"),
                             ("GET", "/api/backups"), ("GET", "/api/backups/summary"), ("POST", "/api/backups"),
                             ("GET", "/api/accounts"), ("GET", "/api/logs")):
            answer = member.request(method, path, json={} if method != "GET" else None)
            assert answer.status_code == 403, (method, path, answer.status_code)
            assert _code(answer) == "operator_only"


# --- EC-1, EC-8: the disk and the start -------------------------------------------------------------------------


def test_time1_ec1_a_full_disk_refuses_saving_with_a_clear_code_and_reading_goes_on(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    def full(*_args: object, **_kwargs: object) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(settings_service, "save", full)
    browser = TestClient(app, base_url="http://testserver", headers={"X-Nextime-Client": "tab-full00001"},
                         raise_server_exceptions=False)
    sign_in(browser, operator)
    refused = browser.put("/api/settings", json={"trash_days": 9})
    assert refused.status_code == 507 and _code(refused) == "storage_full"
    monkeypatch.undo()
    assert client.get("/api/settings").json()["trash_days"] == 30


def test_time1_ec8_a_missing_or_unwritable_data_folder_stops_the_start_with_a_clear_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    with caplog.at_level(logging.CRITICAL, logger="nextime.storage"), pytest.raises(StartupError):
        storage.check_data_dir(tmp_path / "not-mounted", named=True)
    assert "does not exist" in caplog.text and "NEXTIME_DATA_DIR" in caplog.text
    caplog.clear()
    real_open = open

    def refuse(path: object, *args: object, **kwargs: object):
        if ".write-check-" in str(path):
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_open(path, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr("builtins.open", refuse)
    with caplog.at_level(logging.CRITICAL, logger="nextime.storage"), pytest.raises(StartupError):
        storage.check_data_dir(tmp_path, named=True)
    assert "may not write" in caplog.text


@pytest.mark.parametrize(("variable", "value", "said"), [
    ("NEXTIME_SESSION_DAYS", "zero", "a whole number"),
    ("NEXTIME_PUBLIC_URL", "time.example.com", "http:// or https://"),
    ("NEXTIME_TRUSTED_PROXIES", "the proxy", "networks"),
])
def test_time1_ec8_a_wrong_variable_stops_the_start_and_names_the_allowed_values(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, variable: str, value: str, said: str
) -> None:
    monkeypatch.setenv(variable, value)
    get_settings.cache_clear()
    try:
        with caplog.at_level(logging.CRITICAL, logger="nextime"), pytest.raises(StartupError):
            get_settings()
    finally:
        monkeypatch.delenv(variable)
        get_settings.cache_clear()
    assert variable in caplog.text and said in caplog.text
    assert isinstance(get_settings(), Settings)


# --- AC-3: the log --------------------------------------------------------------------------------------------------


def test_time1_ac3_a_failure_reaches_the_log_without_passwords(client: TestClient, operator: Account,
                                                               caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO):
        client.put("/api/settings", json={"smtp_host": "mail.example.com", "smtp_password": "the mail password"})
        client.post("/api/auth/login", json={"name": "tester", "password": "a wrong password here"})
    assert "the mail password" not in caplog.text and "a wrong password here" not in caplog.text
    assert "Settings changed" in caplog.text
    assert os.environ.get("NEXTIME_SECRET_KEY", "") not in caplog.text


# --- AC-11: the switch "Look for new versions" -------------------------------------------------------------------


def test_time1_ac11_looking_for_new_versions_is_on_from_the_start_and_asks_github(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import updates

    assert updates.API_URL == "https://api.github.com/repos/DerKezorm/nextime/releases/latest"
    assert client.get("/api/settings").json()["update_check"] is True
    monkeypatch.setattr(updates.check, "ask", lambda: "v9.9.9")
    internet.probe_override = lambda: True
    state = client.get("/api/server/state").json()
    assert state["update_check"] is True and state["newer"] == "v9.9.9"


def test_time1_ac11_switched_off_nothing_is_asked_and_no_hint_shows(
    client: TestClient, operator: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import updates

    asked: list[int] = []
    monkeypatch.setattr(updates.check, "ask", lambda: asked.append(1) or "v9.9.9")
    internet.probe_override = lambda: True
    assert client.put("/api/settings", json={"update_check": False}).json()["update_check"] is False
    state = client.get("/api/server/state").json()
    assert state["update_check"] is False and state["newer"] is None
    assert asked == []
