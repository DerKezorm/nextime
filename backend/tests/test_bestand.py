"""Test "Bestand": every way in that worked in 0.4.0 still works in the running version.

The database comes from 0.4.0 itself (``tests/bestand/bauen.py``, stored as ``bestand-0.4.0.sql.gz``, the accounts
and their test values in ``bestand-0.4.0.json``). Each test loads it in place of the test database and then starts
the app exactly like a container after an update (the lifespan: backup check, tables, columns, indexes). Then it signs
in the way each account could sign in before: name and password (any case), address and password, second factor and
recovery code, a provider, an open browser session, a paired iPhone, an API key, a calendar program over CalDAV (user
name or address, the addresses it keeps), an open invitation, the operator's reset link; and the blocked, the deleted
and the locked stay out as before.

⚠️ **Nothing here may be adapted to make a new version pass.** A red test means: an installation that runs 0.4.0
today loses a way in after the update. Change the code, not the test. The only thing that is allowed to change is
the list at the end (strict xfail until the module came): what the account module changes on purpose (draft
"Konten, Einrichtung und Anmeldung", rules 3 and 6 and "Passwort vergessen").

⚠️ **The fixture ages.** Sessions run out after 30 days, invitations and links after their days, a deleted account
goes after 30; loading pushes those dates into the future, and nothing else is touched before the start.
"""

from __future__ import annotations

import base64
import gzip
import json
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent / "nexoidc"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "bestand"))

import oidc_fakes as fakes
from bauen import TAB, dump_datei, manifest_datei, totp_geheimnis

from app.config import get_settings
from app.db import SessionLocal, engine
from app.main import app
from app.models import Account
from app.security import MAX_FAILURES, SESSION_COOKIE, brake
from app.services import settings_service, totp
from app.vendor import nexoidc
from app.vendor.nexoidc import attempt, protocol

MANIFEST = json.loads(manifest_datei().read_text(encoding="utf-8"))
KONTEN = MANIFEST["konten"]
ANBIETER = MANIFEST["anbieter"]
ADRESSE_DER_APP = MANIFEST["oeffentliche_adresse"]

MIT_PASSWORT = [k for k in KONTEN if k.get("passwort")]
#: Accounts that sign in with a password today: not blocked, not deleted.
OFFEN = [k for k in MIT_PASSWORT if not k.get("gesperrt") and not k.get("geloescht")]
MIT_FAKTOR = [k for k in KONTEN if k.get("zweiter_faktor")]
MIT_ANBIETER = [k for k in KONTEN if k.get("anbieter")]
_ZAHL: dict[str, int] = {}
for _k in KONTEN:
    if _k["email"]:
        _ZAHL[_k["email"].lower()] = _ZAHL.get(_k["email"].lower(), 0) + 1
#: Addresses only one account has: they sign in today and must after the change (rule 2 of the draft).
EINDEUTIGE_ADRESSE = [k for k in OFFEN if k["email"] and _ZAHL[k["email"].lower()] == 1]
DOPPELTE_ADRESSE = [k for k in OFFEN if k["email"] and _ZAHL[k["email"].lower()] > 1]

#: The tables whose rows the start must leave as they are (nothing renamed, nothing deleted).
ZAEHLEN = (
    "accounts",
    "auth_sessions",
    "invites",
    "password_resets",
    "api_keys",
    "app_devices",
    "devices",
    "calendars",
    "events",
    "oidc_providers",
    "oidc_links",
)


def _name(konto: dict) -> str:
    return konto["name"]


def _schema(datei: Path) -> tuple[dict[str, set[str]], dict[str, int]]:
    verbindung = sqlite3.connect(str(datei))
    try:
        tabellen = [
            z[0]
            for z in verbindung.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            )
        ]
        spalten = {t: {z[1] for z in verbindung.execute(f'PRAGMA table_info("{t}")')} for t in tabellen}
        # Table names from the constant above, never input.
        zahlen = {
            t: verbindung.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]  # noqa: S608
            for t in ZAEHLEN
            if t in spalten
        }
    finally:
        verbindung.close()
    return spalten, zahlen


@pytest.fixture
def bestand():
    """The 0.4.0 database in place of the test one, then the start of the running version.

    Gives back what the old database had (columns per table, rows per counted table), read before the start.
    """
    engine.dispose()
    ziel = get_settings().database_path
    for rest in ("", "-wal", "-shm"):
        Path(str(ziel) + rest).unlink(missing_ok=True)
    verbindung = sqlite3.connect(str(ziel))
    try:
        verbindung.executescript(gzip.decompress(dump_datei().read_bytes()).decode("utf-8"))
        verbindung.execute("UPDATE auth_sessions SET expires_at = datetime('now', '+30 days')")
        verbindung.execute("UPDATE invites SET expires_at = datetime('now', '+30 days')")
        verbindung.execute("UPDATE password_resets SET expires_at = datetime('now', '+30 days')")
        verbindung.execute("UPDATE accounts SET deleted_at = datetime('now') WHERE deleted_at IS NOT NULL")
        verbindung.commit()
    finally:
        verbindung.close()
    vorher = _schema(ziel)
    with TestClient(app, base_url=ADRESSE_DER_APP, headers=TAB):
        pass
    brake.forget()
    totp.forget()
    yield vorher


@pytest.fixture
def anbieter():
    netz = fakes.Network()
    nexoidc.use_transport(netz.transport())
    protocol.clear_caches()
    attempt.forget_used_states()
    fake = netz.add(
        fakes.FakeProvider(
            ANBIETER["issuer"], client_id=ANBIETER["client_id"], client_secret=ANBIETER["client_geheimnis"]
        )
    )
    yield fake
    nexoidc.use_transport(None)
    protocol.clear_caches()
    attempt.forget_used_states()


class Postfach:
    """The system mail that went into the outbox during the test (the shared post module sends it from there), or out
    at once (``nexpost.send.deliver``: "Forgot your password?" since konten 0.3.10), as a list of ``{"an", "text"}``."""

    def __init__(self) -> None:
        self.start = self._last()
        self.sofort: list[dict] = []

    @staticmethod
    def _last() -> int:
        from sqlalchemy import func, select

        from app.models_accounts import MailOutbox

        with SessionLocal() as db:
            return int(db.scalar(select(func.max(MailOutbox.id))) or 0)

    def _mails(self) -> list[dict]:
        from sqlalchemy import select

        from app.models_accounts import MailOutbox

        with SessionLocal() as db:
            rows = db.scalars(select(MailOutbox).where(MailOutbox.id > self.start).order_by(MailOutbox.id))
            liegen = [{"an": json.loads(row.letter)["to"], "text": json.loads(row.letter)["text"]} for row in rows]
        return liegen + self.sofort

    def __iter__(self):
        return iter(self._mails())

    def __getitem__(self, index: int) -> dict:
        return self._mails()[index]

    def __len__(self) -> int:
        return len(self._mails())

    def __eq__(self, other: object) -> bool:
        return self._mails() == other

    def clear(self) -> None:
        self.start = self._last()
        self.sofort.clear()


@pytest.fixture
def post(monkeypatch):
    """A mail server counts as set up; what is mailed is read from the outbox (the shared post module sends it from
    there). "Forgot your password?" does its work before the answer here (it runs after the answer otherwise)."""
    from app.routers import auth as auth_router
    from app.vendor.nexpost import send

    monkeypatch.setattr(auth_router, "FORGOT_AT_ONCE", True)
    with SessionLocal() as db:
        settings_service.save(db, {"smtp_host": "smtp.example.com", "smtp_from": "kalender@example.com"})
    postfach = Postfach()
    # What goes out at once is caught here instead of at the mail server of the fixture (there is none).
    monkeypatch.setattr(send, "deliver", lambda _settings, letter: postfach.sofort.append(
        {"an": letter.to, "text": letter.text}))
    return postfach


def browser() -> TestClient:
    # The fixture's public address is https: cookies are Secure there, as in production.
    return TestClient(app, base_url=ADRESSE_DER_APP, follow_redirects=False, headers=TAB)


def ich(klient: TestClient) -> dict | None:
    antwort = klient.get("/api/auth/me")
    return antwort.json() if antwort.status_code == 200 else None


def code_wieder_frei() -> None:
    """A TOTP code counts once per 30 seconds; a second sign-in in the same test needs the step freed again."""
    with engine.begin() as verbindung:
        verbindung.exec_driver_sql("UPDATE accounts SET totp_last_step = 0")


def anmelden(klient: TestClient, konto: dict, eingabe: str | None = None, passwort: str | None = None):
    """The sign-in form of 0.4.0: field ``name`` (address or name), then the code if the account has a second
    factor."""
    antwort = klient.post(
        "/api/auth/login", json={"name": eingabe or _name(konto), "password": passwort or konto["passwort"]}
    )
    assert antwort.status_code == 200, (_name(konto), eingabe, antwort.status_code, antwort.text)
    if antwort.json().get("second_factor"):
        code = totp.code_at(totp_geheimnis(konto["zweiter_faktor"]), time.time())
        zweiter = klient.post("/api/auth/login/totp", json={"code": code})
        assert zweiter.status_code == 200, (_name(konto), zweiter.text)
    else:
        assert antwort.json()["name"] == _name(konto), antwort.json()


def ueber_anbieter(klient: TestClient, fake, person: dict):
    fake.person = dict(person)
    start = klient.get(f"/api/oidc/{ANBIETER['kuerzel']}/start")
    assert start.status_code in (302, 303), start.text
    beim_anbieter = start.headers["location"]
    pfad = urlsplit(fakes.query_of(beim_anbieter)["redirect_uri"]).path
    return klient.get(f"{pfad}?{urlencode(fake.authorize(beim_anbieter))}")


def betreiber() -> tuple[TestClient, dict]:
    """The operator of the fixture, signed in (password and second factor)."""
    konto = next(k for k in OFFEN if k.get("operator"))
    klient = browser()
    anmelden(klient, konto)
    return klient, konto


# --- The start after the update -------------------------------------------------------------------------------------


def test_the_fixture_is_from_0_4_0():
    assert MANIFEST["fassung"] == "0.4.0"
    roh = gzip.decompress(dump_datei().read_bytes()).decode("utf-8")
    assert 'CREATE TABLE "accounts"' in roh or "CREATE TABLE accounts" in roh


def test_the_start_only_adds(bestand):
    """Rule 10 of the draft: new columns with a default, nothing renamed or deleted, no row lost."""
    spalten_vorher, zahlen_vorher = bestand
    spalten_nachher, zahlen_nachher = _schema(get_settings().database_path)
    for tabelle, spalten in spalten_vorher.items():
        assert tabelle in spalten_nachher, f"table {tabelle} is gone"
        fehlt = spalten - spalten_nachher[tabelle]
        assert not fehlt, f"{tabelle}: columns gone {sorted(fehlt)}"
    assert zahlen_nachher == zahlen_vorher


def test_every_account_is_still_there_with_its_names(bestand):
    with SessionLocal() as db:
        leute = {row.name: row for row in db.query(Account).all()}
    assert set(leute) == {_name(k) for k in KONTEN}
    for konto in KONTEN:
        person = leute[_name(konto)]
        assert person.email == konto["email"], _name(konto)
        assert (person.email_source or "") == konto.get("email_source", ""), _name(konto)
        assert bool(person.password_hash) == bool(konto.get("passwort")), _name(konto)
        assert bool(person.totp_secret_enc) == bool(konto.get("zweiter_faktor")), _name(konto)
        assert (person.role == "operator") == bool(konto.get("operator")), _name(konto)
        assert (person.blocked_at is not None) == bool(konto.get("gesperrt")), _name(konto)
        assert (person.deleted_at is not None) == bool(konto.get("geloescht")), _name(konto)


def test_every_name_of_0_4_0_fits_the_new_rules():
    """Not a promise of the draft (old names keep working whatever they are), a finding for the plan: 0.4.0 only ever
    made names out of a to z, digits, dot, hyphen, underscore. If this goes red, the fixture changed, not the code."""
    for konto in KONTEN:
        assert re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,63}", _name(konto)), _name(konto)


# --- Name or address, and the password ------------------------------------------------------------------------------


@pytest.mark.parametrize("konto", OFFEN, ids=_name)
def test_the_name_and_password_still_sign_in(bestand, konto):
    klient = browser()
    anmelden(klient, konto)
    assert ich(klient)["name"] == _name(konto)


@pytest.mark.parametrize("konto", OFFEN, ids=_name)
def test_the_name_in_capitals_still_signs_in(bestand, konto):
    """0.4.0 lowers what was typed; ``KIM`` is ``kim``."""
    klient = browser()
    anmelden(klient, konto, eingabe="  " + _name(konto).upper())
    assert ich(klient)["name"] == _name(konto)


@pytest.mark.parametrize("konto", EINDEUTIGE_ADRESSE, ids=_name)
def test_a_unique_address_still_signs_in_in_any_case(bestand, konto):
    """Rule 2: whoever signs in by address today keeps that way; ``sam`` took his provider's address
    (``email_source`` provider) and signs in by it too."""
    for eingabe in (konto["email"], konto["email"].upper()):
        brake.forget()
        code_wieder_frei()
        klient = browser()
        anmelden(klient, konto, eingabe=eingabe)
        assert ich(klient)["name"] == _name(konto)


@pytest.mark.parametrize("konto", OFFEN, ids=_name)
def test_a_wrong_password_is_still_refused(bestand, konto):
    antwort = browser().post("/api/auth/login", json={"name": _name(konto), "password": konto["passwort"] + "x"})
    assert antwort.status_code == 401
    assert antwort.json()["detail"]["code"] == "wrong_credentials"


@pytest.mark.parametrize("konto", MIT_FAKTOR, ids=_name)
def test_a_recovery_code_still_replaces_the_app(bestand, konto):
    klient = browser()
    antwort = klient.post("/api/auth/login", json={"name": _name(konto), "password": konto["passwort"]})
    assert antwort.status_code == 200 and antwort.json() == {"second_factor": True}, antwort.text
    zweiter = klient.post("/api/auth/login/totp", json={"code": konto["wiederherstellungscodes"][0]})
    assert zweiter.status_code == 200, zweiter.text
    assert ich(klient)["name"] == _name(konto)


def test_a_blocked_account_still_hears_it_only_after_its_password(bestand):
    konto = next(k for k in KONTEN if k.get("gesperrt"))
    for eingabe in (_name(konto), konto["email"]):
        brake.forget()
        richtig = browser().post("/api/auth/login", json={"name": eingabe, "password": konto["passwort"]})
        assert richtig.status_code == 403 and richtig.json()["detail"]["code"] == "account_blocked", richtig.text
        falsch = browser().post("/api/auth/login", json={"name": eingabe, "password": "Bestand-falsch-1"})
        assert falsch.status_code == 401 and falsch.json()["detail"]["code"] == "wrong_credentials"


def test_a_deleted_account_stays_out_and_comes_back_with_its_sign_in(bestand):
    konto = next(k for k in KONTEN if k.get("geloescht"))
    draussen = browser().post("/api/auth/login", json={"name": _name(konto), "password": konto["passwort"]})
    assert draussen.status_code == 403 and draussen.json()["detail"]["code"] == "account_blocked"
    chef, chef_konto = betreiber()
    with SessionLocal() as db:
        konto_id = db.query(Account).filter(Account.name == _name(konto)).one().id
    zurueck = chef.post(f"/api/accounts/{konto_id}/restore", json={"current_password": chef_konto["passwort"]})
    assert zurueck.status_code == 200, zurueck.text
    brake.forget()
    for eingabe in (_name(konto), konto["email"]):
        klient = browser()
        anmelden(klient, konto, eingabe=eingabe)
        assert ich(klient)["name"] == _name(konto)


def test_password_sign_in_switched_off_still_keeps_members_out_and_lets_the_operator_in(bestand):
    with SessionLocal() as db:
        settings_service.save(db, {"password_login": False})
    for konto in OFFEN:
        brake.forget()
        code_wieder_frei()
        klient = browser()
        if konto.get("operator"):
            anmelden(klient, konto)
            assert ich(klient)["name"] == _name(konto)
        else:
            antwort = klient.post("/api/auth/login", json={"name": _name(konto), "password": konto["passwort"]})
            assert antwort.status_code == 403 and antwort.json()["detail"]["code"] == "password_login_off", (
                _name(konto),
                antwort.text,
            )


def test_wrong_passwords_lock_the_account_whatever_was_typed(bestand):
    """The lock counts at the account, not at the text: half the tries by name, half by address, and the account is
    locked for its right password as well (the draft's "Anmeldebremse zählt am Konto")."""
    konto = next(k for k in EINDEUTIGE_ADRESSE if not k.get("operator") and not k.get("zweiter_faktor"))
    for nummer in range(MAX_FAILURES):
        brake.forget()
        eingabe = _name(konto) if nummer % 2 else konto["email"]
        antwort = browser().post("/api/auth/login", json={"name": eingabe, "password": "Bestand-falsch-1"})
        assert antwort.status_code == 401, antwort.text
    brake.forget()
    antwort = browser().post("/api/auth/login", json={"name": _name(konto), "password": konto["passwort"]})
    assert antwort.status_code == 401, antwort.text


# --- Providers ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("konto", MIT_ANBIETER, ids=_name)
def test_the_provider_still_signs_in(bestand, anbieter, konto):
    klient = browser()
    zurueck = ueber_anbieter(klient, anbieter, konto["anbieter"])
    assert zurueck.status_code in (302, 303), zurueck.text
    assert "error=" not in zurueck.headers["location"], zurueck.headers["location"]
    selbst = ich(klient)
    assert selbst["name"] == _name(konto)
    assert (selbst["role"] == "operator") == bool(konto.get("operator"))


@pytest.mark.parametrize("konto", [k for k in MIT_ANBIETER if not k.get("passwort")], ids=_name)
def test_an_account_without_password_still_has_none(bestand, konto):
    """A provider-only account must not get a password sign-in by the update (no empty or guessed password)."""
    for eingabe in (_name(konto), konto["email"]):
        for versuch in ("-", " ", _name(konto)):
            brake.forget()
            antwort = browser().post("/api/auth/login", json={"name": eingabe, "password": versuch})
            assert antwort.status_code == 401, antwort.text


# --- What is already signed in --------------------------------------------------------------------------------------


def test_an_open_browser_session_stays_open(bestand):
    konto = next(k for k in KONTEN if k.get("sitzung"))
    klient = browser()
    klient.cookies.set(SESSION_COOKIE, konto["sitzung"])
    assert (ich(klient) or {}).get("name") == _name(konto)


def test_a_paired_iphone_stays_paired(bestand):
    konto = next(k for k in KONTEN if k.get("geraet"))
    antwort = browser().get("/api/app/1/me", headers={"Authorization": f"Bearer {konto['geraet']}"})
    assert antwort.status_code == 200, antwort.text
    # What nextime-ios shows as "Angemeldet als": ``account.login``.
    assert antwort.json()["account"]["login"] == _name(konto)


def test_an_api_key_keeps_working(bestand):
    konto = next(k for k in KONTEN if k.get("api_token"))
    antwort = browser().get("/api/v1/me", headers={"Authorization": f"Bearer {konto['api_token']}"})
    assert antwort.status_code == 200, antwort.text
    assert antwort.json()["name"] == _name(konto)


# --- The calendar account (CalDAV) ----------------------------------------------------------------------------------
#
# A phone keeps what it was told at setup: the user (the name, or the address typed by hand), the device password, and
# every address it got back (principal, calendar home, calendars, events). All of them must still answer.

MIT_CALDAV = next(k for k in KONTEN if k.get("caldav"))


def dav(methode: str, pfad: str, benutzer: str, passwort: str, **kopf: str):
    zugang = base64.b64encode(f"{benutzer}:{passwort}".encode()).decode()
    with TestClient(app, base_url=ADRESSE_DER_APP) as klient:
        return klient.request(methode, pfad, headers={"Authorization": f"Basic {zugang}", **kopf})


def hrefs(antwort) -> set[str]:
    return set(re.findall(r"<(?:\w+:)?href>([^<]+)</(?:\w+:)?href>", antwort.text))


@pytest.mark.parametrize("benutzer", ["name", "NAME", "address"])
def test_a_calendar_program_still_syncs_at_the_addresses_it_keeps(bestand, benutzer):
    """Rule 6: the paths with the name in them answer as before, for the user as set up (name from the profile, or
    the address typed by hand; any case)."""
    konto, zugang = MIT_CALDAV, MIT_CALDAV["caldav"]
    name = _name(konto)
    wer_ich_bin = {"name": name, "NAME": name.upper(), "address": konto["email"]}[benutzer]
    passwort = zugang["passwort_adresse"] if benutzer == "address" else zugang["passwort_name"]
    wurzel = dav("PROPFIND", "/dav/", wer_ich_bin, passwort, Depth="0")
    assert wurzel.status_code == 207, wurzel.text
    assert f"/dav/principals/{name}/" in hrefs(wurzel)
    prinzipal = dav("PROPFIND", f"/dav/principals/{name}/", wer_ich_bin, passwort, Depth="0")
    assert prinzipal.status_code == 207, prinzipal.text
    assert f"/dav/calendars/{name}/" in hrefs(prinzipal)
    heim = dav("PROPFIND", f"/dav/calendars/{name}/", wer_ich_bin, passwort, Depth="1")
    assert heim.status_code == 207, heim.text
    assert zugang["kalender"] in hrefs(heim)
    termin = dav("GET", zugang["termin"], wer_ich_bin, passwort)
    assert termin.status_code == 200, termin.text
    assert "SUMMARY:Bestand Termin" in termin.text


def test_a_calendar_program_with_a_wrong_password_or_another_user_stays_out(bestand):
    konto, zugang = MIT_CALDAV, MIT_CALDAV["caldav"]
    assert dav("PROPFIND", "/dav/", _name(konto), zugang["passwort_name"] + "x", Depth="0").status_code == 401
    assert dav("PROPFIND", "/dav/", "kim", zugang["passwort_name"], Depth="0").status_code == 401


# --- Open links -----------------------------------------------------------------------------------------------------


def test_the_open_invitation_is_still_accepted_with_the_form_of_0_4_0(bestand):
    """Rule 7: an invitation of 0.4.0 stays valid, and the page of 0.4.0 (display name, address, password twice, no
    username) still makes an account; it signs in by the invitation's address."""
    offen = MANIFEST["offene_einladung"]
    klient = browser()
    vorschau = klient.get(f"/api/invite/{offen['schluessel']}")
    assert vorschau.status_code == 200, vorschau.text
    assert vorschau.json()["email"] == offen["email"]
    passwort = "Bestand-gast-26"
    annahme = klient.post(
        f"/api/invite/{offen['schluessel']}",
        json={"display_name": "Gast Beispiel", "email": "", "password": passwort, "password_confirm": passwort},
    )
    assert annahme.status_code == 200, annahme.text
    selbst = ich(klient)
    assert selbst["email"] == offen["email"] and selbst["role"] == offen["role"]
    brake.forget()
    zweiter = browser()
    anmelden(zweiter, {"name": selbst["name"], "passwort": passwort}, eingabe=offen["email"])


def test_the_open_reset_link_of_the_operator_still_sets_a_password(bestand):
    """A link the operator made in 0.4.0 (30 days) still works once, and the account hears about it."""
    offen = MANIFEST["offener_ruecksetzlink"]
    konto = next(k for k in KONTEN if _name(k) == offen["name"])
    alt = browser()
    alt.cookies.set(SESSION_COOKIE, konto["sitzung"])
    seite = browser().get(f"/api/reset/{offen['schluessel']}")
    assert seite.status_code == 200, seite.text
    neu = "Bestand-neu-2026"
    gesetzt = browser().post(f"/api/reset/{offen['schluessel']}", json={"password": neu, "password_confirm": neu})
    assert gesetzt.status_code == 204, gesetzt.text
    assert ich(alt) is None, "the sessions of before end with a new password"
    klient = browser()
    anmelden(klient, konto, passwort=neu)
    assert ich(klient)["password_reset_notice"] is True
    nochmal = browser().post(f"/api/reset/{offen['schluessel']}", json={"password": neu, "password_confirm": neu})
    assert nochmal.status_code == 404


# --- After the change (account module): what the draft changes on purpose ------------------------------------------
#
# These were strict xfail until the shared account module was wired in (rule 3, "Passwort vergessen", rule 6); since
# then they hold.


def test_after_the_change_a_shared_address_signs_in_neither(bestand):
    """Rule 3. Today 0.4.0 looks the address up with ``limit(1)`` and lets the first of the two in (``kim``); after
    the change neither signs in by it, both still by name."""
    for konto in DOPPELTE_ADRESSE:
        brake.forget()
        antwort = browser().post("/api/auth/login", json={"name": konto["email"], "password": konto["passwort"]})
        assert antwort.status_code == 401, (_name(konto), antwort.text)


@pytest.mark.parametrize("konto", EINDEUTIGE_ADRESSE, ids=_name)
def test_after_the_change_forgot_password_mails_a_link(bestand, post, konto):
    """"Passwort vergessen" by mail, with name or unique address; the link holds one hour. Route of the plan:
    ``POST /api/auth/forgot`` with the field ``name`` (as the sign-in form)."""
    for eingabe in (_name(konto), konto["email"].upper()):
        post.clear()
        brake.forget()
        antwort = browser().post("/api/auth/forgot", json={"name": eingabe})
        assert antwort.status_code == 202, antwort.text
        assert [m["an"].lower() for m in post] == [konto["email"].lower()]
        assert re.search(re.escape(ADRESSE_DER_APP) + r"/reset/[A-Za-z0-9_-]{20,}", post[0]["text"])


def test_forgot_password_never_mails_a_shared_address_or_none(bestand, post):
    """True today (there is no such route) and after the change: a shared address and an account without one get no
    link by mail; the answer gives nothing away either way."""
    for eingabe in [k["email"] for k in DOPPELTE_ADRESSE] + [_name(k) for k in OFFEN if not k["email"]]:
        brake.forget()
        antwort = browser().post("/api/auth/forgot", json={"name": eingabe})
        assert antwort.status_code in (202, 404, 405), antwort.text
    assert post == []


def test_after_the_change_a_renamed_account_keeps_its_calendar_program(bestand):
    """Rule 6: the name changes (profile, route of the plan ``PUT /api/me/profile`` with ``name`` and ``password``);
    the phone set up before keeps its user and every address it got, and they still answer."""
    konto, zugang = MIT_CALDAV, MIT_CALDAV["caldav"]
    alt = _name(konto)
    klient = browser()
    anmelden(klient, konto)
    antwort = klient.put("/api/me/profile", json={"name": alt + "-neu", "password": konto["passwort"]})
    assert antwort.status_code == 200, antwort.text
    assert ich(klient)["name"] == alt + "-neu"
    for pfad, tiefe in (("/dav/", "0"), (f"/dav/principals/{alt}/", "0"), (f"/dav/calendars/{alt}/", "1")):
        antwort = dav("PROPFIND", pfad, alt, zugang["passwort_name"], Depth=tiefe)
        assert antwort.status_code == 207, (pfad, antwort.text)
    assert zugang["kalender"] in hrefs(dav("PROPFIND", f"/dav/calendars/{alt}/", alt, zugang["passwort_name"],
                                           Depth="1"))
    assert dav("GET", zugang["termin"], alt, zugang["passwort_name"]).status_code == 200


# --- After the change (settings pass, plan "Einstellungen"): what the modules of 25.11.2026 change on purpose --------


def test_after_the_settings_pass_an_api_key_of_0_4_0_can_be_locked_and_unlocked(bestand):
    """E27: the column for the lock (``api_keys.blocked_at``) comes empty, so the key of 0.4.0 works as before; the
    operator locks it (refused at once) and unlocks it (works again), on the module's ways."""
    konto = next(k for k in KONTEN if k.get("api_token"))
    kopf = {"Authorization": f"Bearer {konto['api_token']}"}
    assert browser().get("/api/v1/me", headers=kopf).status_code == 200
    betrieb = browser()
    anmelden(betrieb, next(k for k in KONTEN if k.get("operator") and k.get("passwort")))
    alle = betrieb.get("/api/connections/admin/keys")
    assert alle.status_code == 200, alle.text
    (schluessel,) = [k for k in alle.json()["keys"] if k["account"]["name"] == _name(konto)]
    # The operator does not see the start of a key (schluessel, E36).
    assert schluessel["blocked"] is False and "prefix" not in schluessel
    assert betrieb.post(f"/api/connections/admin/keys/{schluessel['id']}/block").status_code == 200
    assert browser().get("/api/v1/me", headers=kopf).status_code == 401
    assert betrieb.post(f"/api/connections/admin/keys/{schluessel['id']}/unblock").status_code == 200
    assert browser().get("/api/v1/me", headers=kopf).status_code == 200


def test_after_the_settings_pass_addresses_of_0_4_0_need_no_link(bestand):
    """E23: an address that counted in 0.4.0 counts as proven, so "Adresse noch nicht bestätigt" never shows for it;
    the theme is not chosen yet (the first load takes the browser's)."""
    konto = next(k for k in EINDEUTIGE_ADRESSE if k.get("passwort"))
    klient = browser()
    anmelden(klient, konto)
    selbst = ich(klient)
    assert selbst["email_proven"] is True and selbst["theme"] == ""
