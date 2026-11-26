# Anbindung des Kontenmoduls (`nexkonten`) an nextime

Plan vom 24.11.2026, gebaut am 25.11.2026 auf den Modulen 1.0.0 (Abschnitt 0). Maßgeblich sind der Entwurf in nexlore
(`Development/nexapps/Baupläne/Entwurf Konten, Einrichtung und Anmeldung.md`) und die Schnittstelle des Moduls
(`bauplaene/konten/SCHNITTSTELLE.md`, Zweig `konten`, Fassung 0.1.0). Wo nextime etwas braucht, das die Schnittstelle
nicht hergibt, steht es unter „Passt das Modul?“ (Abschnitt 12).

Die Kontologik von nextime ist **eine Abschrift von nexnotes** (`services/accounts.py`, `services/oidc_store.py`,
`routers/auth.py`, `routers/invites.py`, `routers/accounts.py`, `db.py` unterscheiden sich nur in Wortlauten und
Kleinigkeiten, geprüft per `diff`). Was im Plan von nexnotes (`nexnotes/docs/konten-anbindung.md`, Zweig `konten`)
steht, gilt hier genauso; dieser Plan nennt das Gleiche kurz und das Eigene ausführlich. **Das Eigene ist das
Kalenderkonto (CalDAV)**: Dort ist der Benutzername Teil jeder Adresse und der Anmeldung, und ein Umbenennen würde
heute jedes eingerichtete iPhone, jeden Mac und jedes DAVx5 abhängen.

## 0. Stand

### Durchgang „Einstellungen“ (25./26.11.2026, Zweig `einst-desktop`)

Nach dem Bauplan in nexlore `Development/nexapps/Baupläne/Bauplan Einstellungen.md` (E1 bis E33). Aus
`bauplaene-stand` (zuletzt `b0d3618`: `7685875` mit E33 bis E38, dazu sicherung für strenges `tsc`) mit `tools/sync.py copy`, `check` aller sieben grün: **`ui` 1.1.0, `konten`
1.1.0, `oidc` 1.2.0, `post` 1.1.0, `log` 1.0.0, `sicherung` 1.0.1, `schluessel` 1.0.0, `sprachen` 1.0.0**. Die Frontend-Vorlagen tragen
ihre Kopfzeile `// Vorlage <Paket> <Fassung>`. App-Fassung unverändert 0.4.0, kein Release.

| Bereich | woher jetzt | was gewandert ist (alt → neu) |
|---|---|---|
| Gerüst | `SettingsLayout`, `SettingsNav`, `SettingsPage` (ui) in `pages/SettingsPage.tsx`; Titel und Zeilen der gemeinsamen Bereiche aus `areaText`, nextimes eigene (`own`) aus `settings.areas.*` | eigene `SettingsNav`/`SettingsJump` und `pages/settings/ui.tsx` → weg; Reihenfolge nach Bauplan (Profil, Sicherheit, Darstellung, Kalender und Konten, Freigaben, Auf iPhone und Co, Mitnehmen und umziehen, Benachrichtigungen, Verbindungen; Server: Allgemein, Anmeldung, Mail, Speicher und Sicherung, Google einrichten, Konten, Log); `/settings` auf dem Handy die Liste; alte Pfade (`/account/<reiter>`, `/server/<seite>`) leiten weiter um, neu `/settings/reminders` → `/settings/notifications` |
| Profil | `PAGES.profile` (konten), dazu `UnconfirmedEmailBox` (nur wo `showUnconfirmed`), `SignOutCard` des Moduls (`things` = „Kalender“; überall sonst dazu `thingsDative` = „Kalendern“) | Zeitzone aus Darstellung → unter „Sprache“ (E14); Zeile „Anmeldung mit Passwort / über …“ weg |
| Darstellung | `pages/settings/AppearanceArea.tsx`: `AppearanceCard` (Theme am Konto, `PUT /api/me/theme`, beim ersten Laden `POST /api/me/theme/adopt` mit `nextime.theme` des Browsers), dann je Wahl eine Karte: Erster Wochentag, Kalenderwochen (`CheckboxRow`), Uhrzeit, Doppelte Termine, Termine im Monat (`Segment`) | Karte „Darstellung“ mit `ThemeSwitcher` und Auswahllisten → Karten; Hell/Dunkel gilt jetzt für das Konto (E5, Spalte `accounts.theme`) |
| Benachrichtigungen | `features/reminders/RemindersArea.tsx`: `NotifyWays` (Dieser Browser mit „Probemeldung schicken“, „Browser mit Mitteilungen“ mit „Entfernen“, iPhone und iPad), dann „Erinnerungen“, „Vorgabe je Kalender“, „Geräte mit Erinnerungen“ | Slug `reminders` → `notifications` (E1), `Toggle` → `CheckboxRow`, Hinweise als `Notice`; neu (E34) `POST /api/notify/webpush/probe`, `DELETE /api/notify/webpush/{id}` (nur eigene Browser, fremde 404) |
| Verbindungen | `features/keys/ConnectionsArea.tsx`: „Geräte“ (`AppDevicesSection`), „Auf diesem Gerät installieren“ (`pwa/InstallCard.tsx`, `listenForInstall`), nextasks und nexpaper als `Collapsible variant="card"` mit Logo (`fold.nextasks`/`fold.nexpaper` je Gerät wie bisher), `ApiKeysCard`, `ToolsCard` (schluessel) | App-Geräte aus „Auf iPhone und Co“ → hierher (E4); „Auf iPhone und Co“ nur noch CalDAV; eigene Schlüsselkarte `features/keys/KeysSection.tsx` → Modulkarten (nextimes Sätze zu ausgeblendeten Kalendern, schreibenden KI-Werkzeugen und `/api/v1` als `children`) |
| Allgemein | `pages/settings/GeneralCards.tsx`: „Grenzen“ (`Field action`), „Funktionen für alle Konten“ (`CheckboxRow`), `InterfacesCard` (schluessel), „Sprachen“ (`Chip`, `Field action`) | „Zustand“, HTTPS-Hinweis, Proxy-Hinweis, lose Versionszeile weg; MCP/API-Schalter → „Schnittstellen“; keine Karte „Web Push“ (nichts einzustellen, E33) |
| Anmeldung | `pages/settings/SignInArea.tsx` = `SignInPage` (konten) + `SignInProviders` (oidc); Wege `GET/PUT /api/server/sign-in`, `GET /api/server/sign-in/readiness` (`routers/server_signin.py`) mit nextimes eigenem Punkt „Server erreicht das Internet“ am Ende (E34, `internet_point`, Wörter `server.ready.internet`, Schlüssel in `pages/settings/readyPoints.ts`) | `SignInCards.tsx` weg; `PUT /api/settings` mit den Schaltern geht durch dieselben Regeln (Passwort-Rückfrage beim Abschalten des Faktors) |
| Mail | `MailCard` (post) über `/api/server/mail`, Satz `server.mailSubtitle` | eigene `MailCard` weg; `/api/settings` (`smtp_*`) und `POST /api/settings/mail-test` bleiben (Testmail jetzt die des Moduls, alte Codes `mail_off`/`mail_failed`/`invalid_email`) |
| Speicher und Sicherung | `StorageArea` (sicherung) über `/api/server/storage`, Zeiten in Uhr und Zone des Kontos (`when`, „heute 3:00 AM“); Haken `services/backup_hooks.py` (Format unverändert: `nextime-backup.json`, `database/nextime.db`, `attachments/`, `secret.key`; schrittweises Zurückspielen wie TIME-1 EC-5, Sync-Epoche) | eigene `BackupsCard` weg; neu „Jede Woche“, „Jeden Monat“; alte Wege `/api/backups/*` mit `x-nextime-password` (`legacy_router`, Codes `disk_full`/`write_failed`), `GET /api/backups/summary` in alter Form aus `plan.summary()` |
| Konten | `AccountsTab` (konten, `capsOf('nextime', …)`), `WhatYouSeeCard`, `AllKeysCard` (schluessel) | Speichergrenze je Konto weg (E24: Knopf, Dialog, Prüfung bei Anhängen; Spalte `storage_limit` bleibt, `PUT …/storage-limit` antwortet `feature_off`); Zählzeile der Schlüssel → Tabelle mit Sperren/Entsperren |
| Log | `LogCard` (log) mit nextimes Uhr/Zone (`when`/`clock`), `things`/`thingsDative` = „Kalender“/„Kalendern“ | Datei, Format, Schlüssel `log_mode`/`log_mode_until` unverändert; geschwärzt auch `nt_api_`, `ntd_`, `/abo/…`, `/guest/…` |
| Über | `AboutPage` (ui) in `pages/settings/AboutArea.tsx`, Logo `/logo.svg`, Was ist neu aus `useWhatsNew` | drei Karten `about`, `about-updates`, `about-built-with` |

**Server:** Rollen, Sperren, Löschen/Zurückholen über `manage.*` (`routers/accounts.py`), Store mit `ManageStore`,
`ThemeStore`, `SignInStore` (`konten_store.py`); Wanderung E23 beim Anlegen von `accounts.theme`
(`konten_wanderung.run(added=…)`, `db._add_missing_columns` meldet die neuen Spalten); Systemmail ohne eigenen Mailer
(`mailer.py` nur noch Sprache und alte Codes), Outbox mit `key`, `waiting`, `on_commit`, `recipient`, Runde nach dem
Commit (`outbox_runner`); Schlüssel: `services/schluessel_store.py` (`CONFIG`, `KeyStore`), Modulwege unter
`/api/connections`, alte Wege `/api/keys`, `/api/admin/keys` über `keys.listing/make/revoke/overview` in alter Form;
neue Spalte `api_keys.blocked_at`, `apikeys.authenticate` lehnt gesperrte ab; `/api/connections/keys` auch für ein
Konto mit geschuldetem zweiten Faktor (Frage 4).

**⚠️ Rückweg auf 0.4.0 (E27):** nur neue Spalten (`accounts.theme`, `api_keys.blocked_at`, `mail_outbox.key`). 0.4.0
liest sie nicht und startet. **Gesperrte API-Schlüssel wirken unter 0.4.0 wieder**: vor dem Rückweg sperrte Schlüssel
widerrufen oder die Sicherung zurückspielen. Spalte entfernen: `ALTER TABLE api_keys DROP COLUMN blocked_at`. Neue
Sicherungen bleiben für 0.4.0 lesbar (gleiches Format); Theme und Wochen-/Monatsplan gehen dabei verloren (0.4.0 kennt
nur aus/täglich; „Jede Woche“ oder „Jeden Monat“ liest es als „aus“, also vor dem Rückweg „Jede Nacht“ wählen).

**Umwege:** keine Übersprungenen mehr. `time3` „heute 3:00 AM in New York“ läuft wieder (nextime übergibt `when`),
`time2` prüft wieder den Tooltip (`title`). Kein `TODO <modul>` mehr (sicherung aus `b0d3618`, das die zwei Fehler
der Kopie aus `7685875` behebt). Fenster, die nextime auch außerhalb der Einstellungen öffnet, bleiben bis nach Stufe 1
(Fragen-Datei).

**Läufe (26.11.2026, `reihe.sh nextime ende`):** ruff sauber; pytest (`-n 6`) 1742 grün, 11 übersprungen; `-m timing`
8 grün; lint sauber; vitest 1262 grün, 1 übersprungen (der Umweg oben) in 73 Dateien; build grün. Playwright: nextime
hat keine Reihe. Neue Tests: `tests/test_einstellungen.py`, Bestand-Tests E23/E27 im Block „After the change“, Vertrag
`tests/nexlog`, `tests/nexsicherung`, `tests/nexschluessel`; Mutationsproben (Rückgabecode) für Schwärzen von `/abo/`,
Sperre eines Schlüssels, E24, Theme, Umleitung `reminders`.

**sprachen `68fd070` (26.11.2026, `0757a9c`):** der Vertragsadapter reicht nextimes Beschreibung über
`description()` (`app.openapi()`) weiter, kein Weg nur für Tests.

**sprachen `a335909`/`e18169e` und E52 (26.11.2026, `360a287`, `311776e`, `1a331c9`):** `/api/locales` behalten
nextimes OpenAPI-Namen; die Profilsprache nimmt, was sie vorher nahm (`known` nur für die Auswahl); beim Start
`locales.at_start()` (configure, `tidy_at_start`). Lauf `reihe.sh nextime ende`: ruff sauber; pytest 1825 grün, 11
übersprungen; `-m timing` 8 grün; lint sauber; vitest 1316 grün, keins übersprungen; build grün.

**Modul sprachen 1.0.0 (`4a5bb15`, E51) und schluessel `4f3e75e` (26.11.2026, `5b3ace0`, `830f01a`, `ba1c93c`):**
eigene Sprachen aus dem Modul (`services/locales.py` mit dem Ordner von vorher, `/api/locales` die Wege des Moduls,
Profilsprache über `known`, Karte „Sprachen“ und i18n über `ownLanguages`); alte Aufrufer der Liste lesen sie wie
vorher (Test). „Neuer MCP-Schlüssel“ md secondary. Lauf `reihe.sh nextime ende`: ruff sauber; pytest 1818 grün, 11
übersprungen (63 neue Vertragstests nexsprachen); `-m timing` 8 grün; lint sauber; vitest 1316 grün, keins
übersprungen, 75 Dateien; build grün.

**`c0b07ab` (26.11.2026, `c0adb91`):** `passkeys.can_add` mit der gesetzten Adresse allein (leer ohne). Lauf: pytest
1754 grün, 11 übersprungen; `-m timing` 8; vitest 1300 grün, keins übersprungen; ruff, lint, build sauber.

**E49 Passkeys (`1ce1a8f`, 26.11.2026, `a7aa4a2`):** `passkeys_can_add` in der Antwort des Kontos, im Kasten
„Passkeys“ `PasskeyAddition` des Moduls. Lauf `reihe.sh nextime ende`: ruff sauber; pytest 1754 grün, 11
übersprungen; `-m timing` 8 grün; lint sauber; vitest 1300 grün, keins übersprungen, 74 Dateien; build grün.

**Gemeinsame Runde `86a0cce` (E45 bis E49, 26.11.2026, `664a919`):** Module neu gezogen; neue Konten mit Theme
`system`, das Frontend schickt nur den wirklich gespeicherten Browserwert; Ortsangaben „Gruppe › Bereich“ mit Wächter
in `i18n/places.test.ts`; „Kalender und Konten“ und „Auf iPhone und Co“ nur aus Karten und Bausatz-Knöpfen; Galerie-
Punkte und E48 (Fragen-Datei, Frage 12). Mutationsproben rc=1 (Theme, Orte, Knöpfe, Karten, Symbol, Satz der Karte).
Lauf `reihe.sh nextime ende`: ruff sauber; pytest 1752 grün, 11 übersprungen; `-m timing` 8 grün; lint sauber; vitest
1295 grün, keins übersprungen, 74 Dateien; build grün.

**Runde `a374e3c` und E39 (26.11.2026, `ccd49cd`, `51697bc`):** post, log, konten neu gezogen, Vorlage
`nexkonten.cards.test.tsx` von Hand („Gespeichert.“ erst nach echtem Speichern); log hält `Bearer nt_api_…` fest.
Verweise auf andere Apps und alte Orte auf den Stand des Bauplans (Liste in der Fragen-Datei, Frage 11), Test
`i18n/places.test.ts` (Mutationsprobe rc=1). Lauf `reihe.sh nextime ende`: ruff sauber; pytest 1750 grün, 11
übersprungen; `-m timing` 8 grün; lint sauber; vitest 1289 grün, keins übersprungen, 74 Dateien; build grün.

**Nachziehen auf `bauplaene-stand` `7685875`/`b0d3618` (26.11.2026, `580b446`, `31e4120`):** zwei Wortformen
„Kalender“/„Kalendern“, Sicherungskarte mit nextimes Uhr und Zone, Tooltip in der Navigation, Vorlagen mit neuer
Kopfzeile und den Änderungen seit `bef0132`. konten `on_shut_out`/`on_back` (E38): einem Konto, das draußen ist, geht
nichts zu, seine Browser bleiben angemeldet, Quellen und Haken an nextasks ruhen und laufen beim Zurückkommen sofort
wieder; `push_ready` im Vertragsadapter über die echte Senderunde. Mutationsproben rc=1: Senderunde ohne
`account_out`, Quellen ohne Halt, `on_back` ohne `wake`, Browser beim Sperren gelöscht, nextasks-Haken ohne Halt,
Sicherungskarte ohne `when`. Lauf `reihe.sh nextime ende`: ruff sauber; pytest 1750 grün, 11 übersprungen (keins aus
Modulgründen); `-m timing` 8 grün; lint sauber; vitest 1285 grün, keins übersprungen, 73 Dateien; build grün.
Playwright: nextime hat keine Reihe.

**Nacharbeit E34 (26.11.2026, `d0359fc`):** Probemeldung und „Browser mit Mitteilungen“ unter Benachrichtigungen
(Frage 5), Punkt „Server erreicht das Internet“ in „Bereit fürs Internet?“ (Frage 6); Fragen 4 und 8 bleiben so.
Mutationsproben rc=1 für das Entfernen nur eigener Browser (Server und Oberfläche) und für den Internetpunkt. Lauf
`reihe.sh nextime ende` danach: ruff sauber; pytest 1745 grün, 11 übersprungen; `-m timing` 8 grün; lint sauber; vitest
1264 grün, 1 übersprungen (weiter der Umweg `TODO sicherung 1.0.2`) in 73 Dateien; build grün. Offen bis zu den
Modulen: `when` an der Sicherungskarte (time3 wieder ein), `title` in `SettingsNav` (time2), `things`/`thingsDative`,
die Modulfehler aus E33.

Stand 25.11.2026, Zweig `konten`. **Gebaut.** Module aus `bauplaene` (Zweig `konten`), kopiert mit `tools/sync.py copy`,
`check` grün: `konten` 0.3.9, `oidc` 1.1.0, `post` 0.1.1, `ui` 0.1.1. Vorlage war nexnotes 1.1.0 (dessen Änderungen
gegen 1.0.1 Datei für Datei dreiseitig auf nextime gelegt, dann das Eigene von nextime nachgezogen). App-Fassung
unverändert 0.4.0 (kein Release vorbereitet). Die Abschnitte 1 bis 13 sind der Plan vom 24.11.2026; wo der Bau anders
entschieden hat, steht es hier.

**Auf den Modulen 1.0.0** (`konten` 1.0.0, `ui` 1.0.0, `post` 1.0.0, `oidc` 1.1.2, eingefroren; 25.11.2026): die Mail
„Neue Anmeldung“ führt zur Seite Sicherheit (`/settings/security`) und nennt die Uhrzeit in der Zeitzone der Person
(`display.zone_of`: gewählte Zone, sonst Heimatzone, sonst die des Servers), nie eine Netzadresse; „Grenze entfernen“
mit dem Knopf `removeValue` des Moduls; **Profilbilder sieht jedes angemeldete Konto** (Regel aller nex-Apps, Vertragstest
über nextimes Route; bis hierhin nur, wer einen Kalender mit der Person sah, TIME-2 AC-5/TIME-14 AC-2), gesperrte und
gelöschte Personen weiter nur für sich und den Betreiber (TIME-14 EC-8). Vertragsadapter mit `forgot_through_route`,
`picture_status` (Konten) und `new_sign_in_letter` (Post) über nextimes Routen; vitest-Vorlage mit dem Abschnitt
„Helpers“. Die Bestand-Fixture behält ihren eigenen Post-Fänger (Ausgangsliste und `nexpost.send.deliver`).
Läufe: Backend 1629 grün, 12 übersprungen (Vertrag nexkonten, nexoidc, nexpost im selben Lauf); Frontend 1180 grün in
66 Dateien; `tsc -b`, lint, build, ruff sauber; `sync.py check` aller vier Module grün.

**Nachgezogen auf `konten` 0.3.10, `ui` 0.1.2, `oidc` 1.1.1, `post` 0.1.2 (25.11.2026):** Anmelden über
`login.sign_in` (wartet nur der Topf des Kontos, antwortet die Anmeldung wie bei falschem Passwort, 401; 429 erst bei
vollem Absender-Topf); „5 frei“ heißt genau 5 an jeder Tür (App-Tests angepasst); „Zuletzt geändert am …“ im Kasten
Passwort (`passwordChanged`, Spalte `password_changed_at` gab es schon); Zugänge mit den festen Arten des Moduls
(`device`, `calendar`, `api_key`, `passkey`); `account_code` neben der alten Kennung, die Seite liest sie über
`accountErrorKeyOf`; „Passwort vergessen“ schickt sofort über den Mailserver (`kontenpost.deliver_now`), nie über die
Ausgangsliste, eine gescheiterte Mail nimmt ihren Link mit; die Kontenliste malt den Buchstaben auf die
Personenfarbe (`ListedAccount.color`, `Avatar color`), wie überall in nextime; am eigenen Konto kein „Überall
abmelden“ und kein „Lösen“ (`NOT_ON_SELF`). Vertragsadapter mit `sign_in_from` und `listed_invites` über nextimes
Routen. Bestand-Fixture fängt Post aus der Ausgangsliste und aus `nexpost.send.deliver`; die Prüfungen sind unverändert.
Läufe danach: Backend 1627 grün, 12 übersprungen; Frontend 1179 grün in 66 Dateien; lint, `tsc -b`, build, ruff sauber;
`sync.py check` aller vier Module grün.

**Backend:**

| Teil | Wo |
|---|---|
| Store | `services/konten_store.py` (Store, FactorStore, PasskeyStore, SessionStore, PictureStore) über `accounts`, `invites`, `password_resets`, `auth_sessions`, `app_devices`, `devices`, `api_keys` und die Tabellen aus `models_accounts.py` (`former_names`, `setup_claims`, `email_changes`, `passkeys`, `failed_unknown`, `mail_outbox`) |
| Neue Spalten | wie nexnotes (`accounts.email_confirmed` 1, `email_proven` 0, `provider_email_from`, `totp_pending(_until)`, `avatar_source`, `passkey_handle`, `new_sign_in_mail` 1, `new_password_origin`; `auth_sessions.uid`, `remember` 1, `stage`, `kind`; `invites.display_name`, `mailed`; `password_resets.origin` `operator`), dazu **`accounts.dav_name`** |
| Wanderung | `services/konten_wanderung.py` aus `db.init_db`: `uid` für alte Sitzungen, **`dav_name = name` für jedes Konto ohne**, eindeutiger Index auf `lower(name)` erst nach Zählen, Indizes auf `lower(email)` und `former_names`. Nur ergänzend, Sicherung davor wie bisher |
| Anmelden, Einrichtung, Einladung, Passwort vergessen, Reset-Link, Profil, Sitzungen, Passkeys, zweiter Faktor, OIDC 1.1.0, Mails | wie nexnotes 1.1.0 (dort Abschnitt 0), Routen und Feldnamen gleich; Mails über `services/post_store.py` und `services/kontenpost.py`, Logo `app/mail-logo.png` (nextimes Symbol 192 px) |
| Alte Fehlerkennungen | `konten_store.OLD_CODES` (`invalid_email`, `name_taken`, `invalid_account_name`) |
| Betreiber | `PUT /api/accounts/{id}/name`, `/display-name`, `DELETE /api/accounts/{id}/email`; **Speichergrenze jetzt mit eigenem Passwort** (`current_password`, konten 0.3.9); `GET /api/invites` liefert auch abgelaufene Einladungen mit `expired` (bleiben sichtbar bis zum Zurücknehmen) |
| Notfallbefehl | `python -m app.emergency reset-link <Benutzername oder E-Mail> [--days 1/7/30]` (README) |
| Nothammer | `NEXTIME_SECOND_FACTOR_OFF` (README, Warnung beim Start, Hinweis in „Mein Konto“) |

**Kalenderkonto (CalDAV), umgesetzt wie Abschnitt 2 „Lösung“, mit den Entscheidungen vom 24.11.2026:**

- **Feste Kennung je Konto:** `accounts.dav_name`. Bestandskonten bekommen beim ersten Start ihren heutigen Namen
  (Wanderung), neue Konten ihren ersten Benutzernamen (`before_insert` in `models.py`, also auf jedem Weg:
  Einrichtung, Einladung, Anbieter). Ein Umbenennen fasst `dav_name` nie an. Alle Adressen unter `/dav/` (Principal,
  Kalenderheim, Kalender, Termine, `DAV:owner` geteilter Kalender) und die `CalDAVPrincipalURL` neuer Profile kommen
  aus `devices.dav_name_of(account)` (leer: der Name, wie 0.4.0).
- **Benutzer beim DAV-Anmelden:** aktueller Benutzername, jeder frühere oder die Adresse, ohne Groß/Klein
  (`login.background_account` des Moduls); entscheidend bleibt das Geräte-Passwort, und der Benutzer muss dasselbe
  Konto nennen. Eine Tabelle `account_aliases` (Plan) gibt es nicht: frühere Namen führt das Modul in `former_names`.
  **Frühere Adressen zählen nicht** (das Modul kennt sie nicht): wer seine Adresse als Benutzer von Hand eingetippt
  hat und sie ändert, tippt im Programm den Benutzernamen ein.
- **Alte Namen sind für immer reserviert** (Modul): kein anderes Konto bekommt sie, also nennt niemand mit ihnen ein
  fremdes Konto.
- **Neue Geräte** (`/api/devices`, `/api/device-setup/…`, App) bekommen den aktuellen Benutzernamen zum Abtippen
  (`user`, Feldname bleibt) und den Principal aus `dav_name`. Beschriftung „Benutzername“ statt „Name“.
- **Neues Passwort, „Überall abmelden“, Betreiber-Abmelden:** Kalender-Programme bleiben angemeldet (eigene
  Geräte-Passwörter). Sie stehen nach einem Link in „Diese Geräte haben noch Zugriff“ (`Access.kind = "calendar"`,
  neben `device` für die iPhone-App, `api_key`, `passkey`); „Alle trennen“ nimmt sie mit (samt offener
  Einrichtungslinks), ebenso die gekoppelten Apps, Schlüssel und Passkeys.
- **Umbenennen ist gleich mit diesem Umbau freigeschaltet** (Entscheidungstabelle des Entwurfs), im Profil und unter
  Konten.

**⚠️ Rückweg auf 0.4.0:** geht eine Fassung lang (nur neue Spalten und Tabellen). **Wer in dieser Fassung umbenannt
wurde, muss nach dem Rückweg seine Kalender neu einrichten:** 0.4.0 kennt `dav_name` nicht, baut die Adressen wieder
aus dem (neuen) Namen und nimmt nur den aktuellen Namen oder die Adresse als Benutzer; jedes Gerät, das vorher
eingerichtet wurde, bekommt 404 bzw. 401. Konten, die nicht umbenannt wurden, merken vom Rückweg nichts. Vor dem
Rückweg also nachsehen, ob jemand umbenannt wurde (`SELECT name, dav_name FROM accounts WHERE name <> dav_name`), und
die Betroffenen vorwarnen; nach dem Rückweg dort in „Auf iPhone und Co“ ein neues Gerät einrichten.

**Frontend:** Anmelden, Einrichten, Einladung, Passwort vergessen, neues Passwort, Adresse bestätigen (`/forgot`,
`/email/:token`), „Sitzung abgelaufen“ wie nexnotes aus `SCREENS`/`PAGES` und dem Bausatz `ui`; Profil und Sicherheit
aus `PAGES`, danach nextimes Eigenes (`APP_PAGES`: Abmelden, Anbieter; im Profil die Zeile „Anmeldung mit …“,
TIME-2 AC-8). **Kontenliste aus konten 0.3.9** (`pages/settings/AccountsTab.tsx`, `accountList.ts`): je Konto eine
Zeile mit Bild, Marken, „Benutzername · E-Mail“ und „Verwalten“; Fenster mit Angaben (dazu „Speicher“), Gruppen
Ändern, Zugang, Beenden. Schalter (`APP_CAPS`): alle zehn. Eigenes: Knopf „Speichergrenze“ (`storageLimit`, Ändern,
nach „Zum Mitglied machen“), Angabe Speicher mit Grenze, Lösch-Rückfrage mit Kalendern, Terminen und geteilten
Kalendern, dann „30 Tage lang kannst du es zurückholen“. Offene Einladungen als Karte des Moduls (abgelaufene bleiben),
darunter die Karten der App: „Was du siehst“ mit dem Datenschutzsatz `accounts.privacy` und „Schlüssel aller Konten“.
Akzentfarbe Schieferblau über `--nexui-*`, Avatar-Variablen hell und dunkel in `styles/index.css`.

**Bewusst anders als der Plan bzw. als nexnotes:**

- Der eigene Wortlaut `account.later` hieß wie der Namensraum des Moduls (`account`) und wäre beim Zusammenführen der
  Texte verloren gegangen; er heißt jetzt `settings.later`.
- Das Profilbild ohne Foto zeigt überall, auch in der Kontenliste, den Buchstaben auf nextimes Personenfarbe aus
  TIME-2 EC-11 (seit `ui` 0.1.2 `Avatar color`; Entscheidung: gibt es eine Personenfarbe, nimmt das Ersatzbild sie).
- Der eigene Hinweis am Profilbild („Erscheint an deinen Terminen …“) weicht dem Satz des Moduls („Alle angemeldeten
  Konten auf diesem Server sehen es.“).
- Ob der zweite Faktor an ist, steht nicht mehr im Profil, sondern unter Sicherheit („An“/„Aus“, Modul).
- Kein Satz „Deine Kalenderprogramme laufen weiter“ beim Umbenennen (entschieden: keiner).

**Tests:** Bestand ohne `xfail` (die drei Fälle des Umbaus halten: doppelte Adresse meldet keinen an, „Passwort
vergessen“ per Mail, umbenanntes Konto behält sein Kalenderprogramm); die Post-Fixture liest jetzt aus `mail_outbox`.
Neu `tests/test_konten_dav.py` (feste Kennung, alle Namen und die Adresse als DAV-Benutzer, fremde Namen nie, Umbenennen
durch Betreiber, neues Passwort und „Überall abmelden“ lassen Kalender-Programme, Einrichtung und Einladung setzen
`dav_name`, Notfallbefehl). Vertragsadapter `tests/nexkonten_adapter.py`, `tests/nexpost_adapter.py` (App-Geräte,
API-Schlüssel, Kalender-Programme als Zugänge). vitest-Vorlagen `nexkonten.test.ts` (mit Kontenliste),
`nexpost.test.ts`, `nexui.vendored.test.ts`. Läufe am 25.11.2026: Backend `pytest -n 2` 1619 grün, 12 übersprungen
(darin Vertrag `tests/nexkonten`, `tests/nexpost`, `tests/nexoidc` 360 grün, 2 übersprungen; Bestand 54 grün;
`test_konten_dav.py` 8 grün), ruff sauber; Frontend `vitest --maxWorkers=2` 1175 grün in 66 Dateien, `npm run lint`,
`tsc -b` und `npm run build` sauber; `sync.py check` aller vier Module grün. Playwright hat nextime nicht.

**Offen:** Sichtprüfung im Browser; Probe auf einer Kopie der Live-Datenbank (doppelte Adressen zählen, Namen nur in
Groß/Klein verschieden); echtes iPhone: umbenennen und prüfen, dass Apple Kalender ohne Rückfrage weiter abgleicht;
nextime-ios „Angemeldet als“ auffrischen und „Benutzername“ (Begleit-App, bricht nichts).

## 1. Bestand heute (0.4.0)

### Datenmodell (`backend/app/models.py`)

| Tabelle | Was zum Konto gehört |
|---|---|
| `accounts` | wie nexnotes: `id`, `name` (unique, immer klein, unsichtbar erzeugt), `display_name`, `role`, `sign_in`, `password_hash`, `email` (ohne eindeutigen Index), `email_source`, `provider_email(_off)`, `language`, `failed_logins`, `locked_until`, `blocked_at`, `deleted_at`, `password_reset_notice`, `totp_*`, `avatar*`, `device_key`; dazu `color` (Kreis mit Initiale) |
| `invites`, `password_resets`, `auth_sessions`, `oidc_providers`, `oidc_links` | wie nexnotes (Einladung und Reset-Link 1/7/30 Tage, Reset nur vom Betreiber) |
| `devices` | **Kalenderkonto** (TIME-20): je Gerät ein eigenes Passwort (nur SHA-256), `name`, `client`, `platform`, `app_device_id` |
| `device_setups` | Einrichtungslink hinter dem QR-Code (10 Minuten, einmal) |
| `app_devices`, `app_pairings`, `app_codes` | gekoppelte iPhone-/iPad-App (TIME-29) |
| `api_keys` | Schlüssel für Programme und MCP (TIME-26) |
| `calendars.dav_name` | Name des Kalenders in der CalDAV-Adresse (32 Hex, je Kalender) |

### Dienste und Router

Wie nexnotes (`accounts.by_login` mit `limit(1)` bei der Adresse, Sperre am Konto, Blindhash, Einrichtungscode
`NEXTIME_SETUP_TOKEN`, Reset nur über Betreiber-Link, keine Mail außer Einladung und Test). Eigen:

| Ort | Was |
|---|---|
| `routers/dav.py` | CalDAV unter `/dav/`: `/dav/principals/<name>/`, `/dav/calendars/<name>/`, `/dav/calendars/<name>/<dav_name>/`, `…/<objekt>.ics`; jede Anfrage prüft `parts[1] == caller.name` (`dav.py:531, 535, 607, 742, 785, 864, 928, 963`), jede Antwort baut die Adressen aus `caller.name` (`dav.py:161-174, 315-341, 522-562, 586-632, 735`) |
| `services/devices.py:155-188` `authenticate` | HTTP Basic: Gerätepasswort, **und** der Benutzer muss Name oder Adresse des Kontos sein (ohne Groß/Klein) |
| `services/devices.py:200-245` `mobileconfig` | Profil für Apple: `CalDAVUsername` = Name, `CalDAVPrincipalURL` = `<adresse>/dav/principals/<name>/` |
| `routers/devices.py:62-140` | `GET/POST /api/devices` und `/api/device-setup/{code}/credentials` geben `user: account.name` zum Abtippen (DAVx5, Thunderbird) |
| `routers/app1.py:262-287` | die iPhone-App lässt sich ein Kalenderkonto machen (`/setup/<token>`, gleiches Profil) |
| `security.end_all_sessions` | Browser und **App-Geräte**; die Kalenderkonto-Geräte (`devices`) bleiben (eigene Passwörter) |

### Wanderung

Wie nexnotes: `init_db()` bei jedem Start (`create_all`, `_add_missing_columns` mit Sicherung davor, Indizes ohne
Prüfung), dazu `nextasks.move_choices`; `_backup_before_update` bei Fassungswechsel.

### Frontend

Wie nexnotes (`pages/AuthPages.tsx`, `pages/settings/AccountTab.tsx`, `pages/settings/AccountsTab.tsx`, gleiche
Bausteine). Eigen: `features/calendars/Devices.tsx` zeigt Adresse, **„Name“** (der Benutzername fürs Kalenderkonto,
`devices.user`) und Passwort zum Abtippen.

### iPhone-App (`nextime-ios`, geprüft)

Kein eigenes Formular: Koppeln per QR-Code oder Anmeldeseite im Browser. Erkannt wird ein Serverkonto über Adresse und
Konto-ID (`Core/Accounts.swift:41` `identity`). „Angemeldet als <login>“ (`Views/ServerAccountPage.swift:57`) und die
Zeile „<adresse> · <login>“ (`Core/Accounts.swift:52`) lesen den beim Koppeln gespeicherten `login`
(`Core/Server/ServerHub.swift:331`), der nie aufgefrischt wird: nach einer Änderung alt bis zum Neukoppeln, brechen
tut nichts. **Das Kalenderkonto, das die App einrichtet, ist ein gewöhnliches CalDAV-Konto in Apple Kalender** (Profil
mit dem Namen) und hängt an Abschnitt 2.

## 2. Wo der Benutzername mehr ist als Anzeige

| Stelle | Rolle des Namens | Bewertung |
|---|---|---|
| `routers/dav.py:161-174` `principal_path`, `home_path`, `calendar_path`, `object_path` | Name in **jeder** CalDAV-Adresse, nicht nur im Principal: Principal, Kalenderheim, jeder Kalender, jeder Termin | **bricht**: Apple Kalender, DAVx5 und Thunderbird merken sich diese Adressen (Principal-URL, Heim, Kalender-URLs mit Sync-Token, Termin-URLs mit ETag). Nach einem Umbenennen antwortet jede gemerkte Adresse 404 (`dav.py:531, 535 …`), die Kalender verschwinden vom Gerät |
| `services/devices.py:173` Basic-Benutzer = Name oder Adresse | Anmeldung des Kalenderprogramms | **bricht**: das Gerät schickt den alten Namen (aus dem Profil oder abgetippt) → 401, das iPhone fragt nach dem Passwort. Gleiches heute schon beim Ändern der **Adresse**, wenn sie als Benutzer eingetippt wurde |
| `services/devices.py:209-211` `mobileconfig`, `routers/devices.py:69, 90, 140` `user` | was neue Geräte bekommen | lösen: neue Geräte bekommen den aktuellen Namen; die Adressen siehe Lösung |
| `routers/dav.py:315` `DAV:owner` mit `principal_path(owner.name)` | Besitzer eines geteilten Kalenders auf dem Gerät eines anderen | harmlos mit der Lösung (bleibt dann gleich), sonst eine geänderte Eigenschaft |
| `services/accounts.py:67-71` `by_email(...).limit(1)` | erste von mehreren gleichen Adressen meldet sich an | **lösen** (Regel 3), wie nexnotes |
| `routers/auth.py` Bremse `login:<ip>|<eingetippt>` | Zähler je eingetipptem Text | **lösen** wie nexnotes; die Sperre am Konto zählt schon beides (Test „Bestand“) |
| `oidc_store.py:351, 358-379` | Name fürs OIDC-Modul, Anzeigename leer bei neuen Anbieter-Konten | lösen mit `nexoidc` 1.1.0, wie nexnotes |
| `routers/totp.py` `provisioning_uri`, `deps.py` `logs.set_actor`, Logzeilen | Beschriftung, Protokoll | harmlos |
| `routers/auth.py:144` `name`, `services/appsync.py:90` `login`, `routers/api_v1.py:148` `name` | Antwortfelder | harmlos, Feldnamen bleiben |
| `services/shares.py:175` Suche beim Teilen | findet auch über den Namen | harmlos: geteilt wird an die Konto-ID |
| `services/invitations.py:117`, `dav.py:323` `calendar-user-address-set` | **Adresse** als Kennung für Einladungen (TIME-18) | harmlos für den Namen; eine geänderte Adresse ändert, welche Einladungen „meine“ sind (wie heute) |
| Sitzungen, App-Geräte, API-Schlüssel, Gast-Links, Freigaben, Sammlungen, Erinnerungen, Push, Anhänge (`/dav/attachments/<id>`), nextasks-/nexpaper-Verbindungen, Sicherungen | an der ID | harmlos |
| `frontend/src/features/calendars/Devices.tsx:122, 319` Beschriftung „Name“ | Benutzer fürs Kalenderprogramm | Wort: wird „Benutzername“ |

### Lösung für das Kalenderkonto (vor dem Ändern-Knopf)

1. **Feste CalDAV-Kennung je Konto**: neue Spalte `accounts.dav_name` (String 64). Die Wanderung setzt sie für jedes
   Bestandskonto auf seinen heutigen `name`; neue Konten bekommen beim Anlegen ihren Benutzernamen. **Sie ändert sich
   nie**, auch nicht beim Umbenennen. Alle CalDAV-Adressen werden aus `dav_name` gebaut (`Caller.name` →
   `account.dav_name`), Anfragen werden gegen `dav_name` geprüft. Damit bleibt jede Adresse, die ein Gerät kennt, für
   immer dieselbe; es gibt keinen Umzug, keinen neuen Abgleich, keine doppelten Kalender. `dav_name` ist nur innerhalb
   des angemeldeten Kontos gültig (das Gerätepasswort bestimmt das Konto), deshalb stört es nicht, wenn später ein
   anderes Konto den alten Namen als Benutzernamen wählt.
   - Warum nicht die Konto-ID im Pfad: Neue Geräte bekämen `/dav/principals/7/`, alte behielten den Namen; zwei
     Schreibweisen für dasselbe wären dauerhaft zu pflegen, und die Bestandsgeräte brauchten den alten Namen sowieso.
     Für nexfolk (CardDAV, neu) ist die ID von Anfang an richtig.
   - Kosmetik: Wer sich umbenennt, sieht in der Principal-Adresse weiter den ersten Namen. Das steht nirgends sichtbar
     außer im Profil des Geräts.
2. **Basic-Benutzer**: angenommen werden der aktuelle Benutzername, `dav_name`, **jeder frühere Benutzername** und jede
   frühere Adresse des Kontos (neue Tabelle `account_aliases`: `account_id`, `kind` `name`/`email`, `value`, `until`;
   `Store.set_username` und `set_email` schreiben den alten Wert hinein). Grund: ein Gerät, das nach einer ersten
   Umbenennung eingerichtet wurde, schickt den Zwischennamen. Das Gerätepasswort bleibt der eigentliche Schlüssel
   (16 Zeichen aus 32, also 80 Bit Zufall, Hash eindeutig). Einfachere Alternative (Frage 1): den Benutzer gar nicht mehr vergleichen.
3. **Neue Geräte** (`mobileconfig`, `/api/devices`): `CalDAVUsername` = aktueller Benutzername, `CalDAVPrincipalURL`
   aus `dav_name`.
4. Test „Bestand“ hat dafür das strikte `xfail` `test_after_the_change_a_renamed_account_keeps_its_calendar_program`.

## 3. Zuordnung nextime ↔ Modul

Wie nexnotes (Abschnitt 3 dort): `username` = `accounts.name` (Feldname `name` bleibt), `email_confirmed` neu,
`pending_email` neue Tabelle `email_changes`, `has_password` = `sign_in == "password" and password_hash`, `blocked` =
gesperrt oder gelöscht, `operator` = `role == "operator"`, `picture` aus `avatar_at` plus neue `avatar_source`,
`Invite.rights` = Zeile (Rolle), `Invite.display_name` neue Spalte, `Invite.username` immer `""`. Store
`services/konten_store.py`. Eigen:

| Store | nextime |
|---|---|
| `add_account` | zusätzlich `dav_name = username` |
| `set_username` | alter Name nach `account_aliases`; `dav_name` bleibt |
| `set_email` | alte Adresse nach `account_aliases` (Basic-Benutzer) |
| `end_sessions` | `security.end_all_sessions` (Browser und App-Geräte). **Kalenderkonto-Geräte bleiben**, wie heute beim Reset-Link (Frage 2, Lücke L7) |

`nexkonten.configure(AppConfig(app_name="nextime", setup_token=lambda: get_settings().setup_token,
logger_name="nextime.auth"))`; `accounts.setup_code` und `announce_setup_code` fallen weg.

## 4. Neue Spalten und Wanderung (nur ergänzend)

| Neu | Typ, Vorgabe | Bedeutung der Vorgabe |
|---|---|---|
| `accounts.dav_name` | String(64), `server_default ''`, danach **einmal** `UPDATE accounts SET dav_name = name WHERE dav_name = ''` in `services/konten_wanderung.py` | jede heutige CalDAV-Adresse bleibt. Solange `dav_name` leer ist, gilt `name` (der Rückfall in `dav.py` macht den Schritt selbst idempotent) |
| `accounts.email_confirmed` | Boolean, `server_default '1'` | alle Bestandsadressen bestätigt, auch übernommene Anbieteradressen (`sam` im Fixture) |
| `accounts.avatar_source` | String(16), `server_default ''` | vorhandene Bilder sind eigene |
| `invites.display_name` | String(80), `server_default ''` | Bestandseinladungen ohne Anzeigename |
| Tabelle `email_changes` | wie nexnotes | leer |
| Tabelle `account_aliases` | `account_id` (FK CASCADE), `kind`, `value`, `created_at`; Index auf `(kind, lower(value))` | leer; kein eindeutiger Index (ein früherer Name darf neu vergeben werden) |
| Index auf `lower(email)` | nicht eindeutig | Doppel im Bestand möglich (Fixture `kim`/`lou`) |

Rückweg auf 0.4.0 eine Fassung lang: neue Spalten mit Vorgabe, 0.4.0 baut die CalDAV-Adressen wieder aus `name`.
**Achtung:** Wer in der neuen Fassung umbenannt wurde, verliert beim Rückweg seine Kalenderprogramme (0.4.0 kennt
`dav_name` nicht). Der Rückweg ist also nur vor dem ersten Umbenennen gefahrlos; das gehört in den Hinweis zur
Fassung. Alternative: Umbenennen erst eine Fassung nach `dav_name` freischalten (Frage 3).

## 5. Routen

Wie nexnotes (Abschnitt 5 dort): `POST /api/setup` dazu `name`; `POST /api/auth/login` Feld `name` bleibt; neu
`POST /api/auth/forgot`; `PUT /api/me/profile` dazu `name`, `password`; neu `GET/POST /api/email/{token}`,
`DELETE /api/me/pending-email`; `POST /api/invites` dazu `display_name`; `GET/POST /api/invite/{token}` dazu `name`;
`POST /api/accounts/{id}/reset-link` mit Mailversand; neu `PUT /api/accounts/{id}/name`, `…/display-name`,
`DELETE /api/accounts/{id}/email`. Fehlerkennungen `invalid_email` → `email_invalid`, `name_taken` →
`username_taken`, `invalid_account_name` → `username_invalid`.

Eigen:

| Route | Heute | Künftig |
|---|---|---|
| `GET/POST /api/devices`, `POST /api/device-setup/{code}/credentials` | `user` = Name | Feld bleibt, Wert der aktuelle Benutzername; `address` unverändert |
| `GET /api/device-setup/{code}/profile` | Profil mit Name im Principal | Principal aus `dav_name` |
| `/dav/…` | Name im Pfad, Basic mit Name oder Adresse | `dav_name` im Pfad, Basic mit aktuellem oder früherem Namen bzw. Adresse (Abschnitt 2) |
| `PUT /api/me/profile` | nur Browser-Sitzung (`deps.require_account` kennt keinen Bearer) | bleibt so; die App ändert dort nichts |

## 6. Oberfläche

Wie nexnotes (Abschnitt 6 dort), nextime hat dieselben Bausteine (`AuthFrame`, `AuthField`, `Primary`, `Problem`,
`PasswordConfirmField`, `Card`, `Field`, `Dialog`, `Input`, `Select`, `MenuItem`, `CopyLink`) und dieselben Seiten:

| Bildschirm | Heute | Künftig | Sichtbar anders |
|---|---|---|---|
| **Ersteinrichtung** | Code, „Name“, E-Mail, Passwort zweimal; Hinweis „ohne HTTPS“ | Code, Anzeigename, **Benutzername**, E-Mail, Passwort zweimal, (Sprache); HTTPS-Hinweis bleibt | ein Feld mehr, Wortlaute |
| **Anmelden** | Feld „E-Mail“, Passwort, „Passwort vergessen?“ mit Betreiber-Satz | „Benutzername oder E-Mail“; „Passwort vergessen?“ mit Mailserver zur neuen Seite | Beschriftung |
| **Passwort vergessen**, **E-Mail bestätigen** | – | neu, wie nexnotes | nur mit Mailserver |
| **Einladung annehmen** | „Name“, E-Mail, Passwort zweimal, Anbieter | Anzeigename, **Benutzername**, E-Mail, Passwort zweimal, Anbieter | ein Feld mehr |
| **Profil** (Einstellungen › Mein Konto) | Bild, „Name“ und E-Mail mit „Speichern“, Rolle, Anbieterangebot, Sprache | Anzeigename, **Benutzername**, E-Mail (wartend mit „Zurücknehmen“), Passwortdialog beim Ändern | Benutzername sichtbar; Passwortabfrage bei der Adresse |
| **Konten** (Einstellungen › Server) | Anzeigename, darunter `email || name`; Reset-Link mit Gültigkeit | darunter „Benutzername · E-Mail“, Hinweis doppelte Adresse, Menü: Benutzername/Anzeigename ändern, Adresse entfernen; Einladen mit Anzeigename | neue Menüpunkte |
| **Auf iPhone und Co** (`features/calendars/Devices.tsx`) | „Adresse“, **„Name“**, „Passwort“ zum Abtippen | „Adresse“, **„Benutzername“**, „Passwort“ | ein Wort |

Beim Umbenennen im Profil ein Satz unter dem Feld, wenn Geräte am Kalenderkonto hängen: „Deine Kalenderprogramme
laufen weiter, du musst nichts ändern.“ (neuer Wortlaut, nextime-eigen, Frage 4).

## 7. Passwort vergessen

Wie nexnotes: beide Wege; der Weg per Mail ist neu. Ein neues Passwort beendet Browser und App-Geräte, **nicht** die
Kalenderprogramme (sie haben eigene Passwörter, heute schon so; der Satz auf der Reset-Seite „Alle Sitzungen und
gekoppelten Geräte enden“ stimmt für sie nicht ganz). Frage 2.

## 8. Test „Bestand“

`backend/tests/test_bestand.py` mit `backend/tests/bestand/bestand-0.4.0.sql.gz` (gebaut von 0.4.0 mit
`tests/bestand/bauen.py`, Beschreibung in `bestand-0.4.0.json`). Start:

    cd backend && .venv/Scripts/python -m pytest tests/test_bestand.py -q

Konten wie nexnotes (`betrieb` mit zweitem Faktor, `erika-muster` ohne Adresse mit offener Sitzung und offenem
Reset-Link, `kim`/`lou` mit doppelter Adresse, `mona` mit zweitem Faktor und gekoppeltem iPhone, `sam` mit Anbieter und
übernommener Anbieteradresse, `robin.sso` nur Anbieter und Betreiber, `gesperrt`, `weg`, offene Einladung). Dazu
`jonas` mit API-Schlüssel und **zwei Kalenderkonto-Geräten** (eins wie vom Profil eingerichtet mit dem Namen als
Benutzer, eins von Hand mit der Adresse) und einem Termin, der über CalDAV kam; der Kalender heißt in der Fixture
lesbar `bestand-kalender-1`.

Geprüft (48 grün): alles wie nexnotes, dazu das Kalenderkonto mit Name, Name in Großbuchstaben und Adresse als
Benutzer: Wurzel nennt den Principal, Principal nennt das Heim, Heim listet den Kalender, der Termin kommt; falsches
Passwort und fremder Benutzer bleiben draußen.

Strikte `xfail` (6): doppelte Adresse meldet keinen an; „Passwort vergessen“ per Mail (4 Konten); **ein umbenanntes
Konto behält sein Kalenderprogramm** (alter Benutzer, alte Principal-, Heim-, Kalender- und Terminadressen).

Gegen elf Mutationen geprüft, jede Rückgabecode 1 (Abschnitt 13).

Der Vertragstest des Moduls (`tests/nexkonten/test_nexkonten_existing.py`) fragt die App über `Adapter.legacy()`
nach einer Datenbank der Vorfassung und einer Liste `(eingetippt, Passwort, geht)`. Die Fixture hier liefert beides:
`legacy()` lädt `bestand-0.4.0.sql.gz` wie `test_bestand.py` und baut die Liste aus `bestand-0.4.0.json` (Name und
eindeutige Adresse: geht; doppelte Adresse, gesperrt, gelöscht, nur Anbieter: geht nicht).

Vor dem Aufspielen auf einer **Kopie** der Live-Datenbank: doppelte Adressen zählen, die neue Fassung starten, dann
mit einem echten iPhone ein Konto umbenennen und prüfen, dass Apple Kalender ohne Rückfrage weiter abgleicht.

## 9. Risiken

1. **Kalenderprogramme nach dem Umbenennen** (Abschnitt 2): ohne `dav_name` und Aliasse verlieren alle Geräte ihre
   Kalender. Der Ändern-Knopf darf erst kommen, wenn das `xfail` grün ist.
2. **Rückweg nach einem Umbenennen** hängt die Geräte ab (Abschnitt 4).
3. **Adresse als CalDAV-Benutzer**: Wer seine Adresse ändert (künftig mit Bestätigungslink häufiger), verliert heute
   die von Hand eingerichteten Programme; mit `account_aliases` nicht mehr.
4. Wie nexnotes: doppelte Adressen, unsichtbare Benutzernamen, eindeutiger Adressindex nie beim Start erzwingen,
   Betreiber ohne Passwort, Fehlerkennungen, Link-Dauern, neue Mailtexte.

## 10. Offene Fragen

1. Basic-Benutzer beim Kalenderkonto: frühere Namen und Adressen als Aliasse führen (Vorschlag), oder den Benutzer
   gar nicht mehr prüfen (das Gerätepasswort reicht)?
2. Soll ein neues Passwort (Reset, Passwort ändern) auch die Kalenderprogramme abmelden? Heute nein; das Modul sagt
   „alle Sitzungen und gekoppelten Geräte“.
3. Umbenennen in derselben Fassung wie `dav_name` freischalten, oder eine Fassung später (sicherer Rückweg)?
4. Satz beim Umbenennen „Deine Kalenderprogramme laufen weiter“: gewünscht?
5. Wie nexnotes: Betreiber nur über Anbieter, Gültigkeit 1/7/30 Tage, übernommene Anbieteradresse bestätigt,
   Sprache bei der Einrichtung, Reset-Link für gesperrte Konten.

## 11. Reihenfolge beim Bau

1. **Zuerst, eigene Fassung:** `dav_name` und Aliasse, CalDAV darauf umstellen, ohne Ändern-Knopf; `xfail` für das
   Umbenennen bleibt (es gibt noch keine Route), ein eigener Test prüft `dav_name` statt `name`.
2. `nexkonten` und `nexoidc` 1.1.0 holen, Store, Spalten, Login/Setup/Einladung/Reset über das Modul.
3. Profil mit Benutzername und Adresse, Betreiberaktionen, „Passwort vergessen“ per Mail; alle `xfail` weg.
4. Oberfläche (Abschnitt 6) zeigen und abnehmen lassen; iPhone-App: „Benutzername“, `login` auffrischen.

## 12. Passt das Modul?

Alle Lücken und Reibungen von nexnotes gelten hier genauso (L1 Betreiber ohne Passwort, L2 Sperre am Konto und
bekannter Browser, L3 Gültigkeit 1/7/30 Tage, R4 Hinweis nach Betreiber-Link, L5 gelöschte Konten halten Name und
Adresse, L6 übernommene Anbieteradresse bestätigt, R7 Einrichtungscode aus `.env`, R9 Sprache, R10 Fehlerkennungen,
R11 Reset für gesperrte Konten, R12 Anzeigename leer). nextime-eigen:

| | Was nextime braucht | Was die Schnittstelle hergibt | Vorschlag |
|---|---|---|---|
| **L7** | Klare Grenze, was ein neues Passwort beendet: Browser und App-Geräte ja, Kalenderprogramme mit eigenem Gerätepasswort (heute) nein | `Store.end_sessions`: „alle Sitzungen und gekoppelten Geräte“ | im Vertrag festlegen, ob Protokoll-Passwörter (CalDAV, künftig CardDAV in nexfolk) dazugehören; sonst entscheidet jede App anders |
| **L8** | Beim Umbenennen und beim Ändern der Adresse den **alten Wert** behalten (CalDAV-Benutzer) | `set_username(id, name)`/`set_email(id, …)` geben den alten Wert nicht mit; der Store kann ihn vorher selbst lesen | geht ohne Änderung; im Vertrag erwähnen, dass Apps mit Protokoll-Anmeldung (DAV) frühere Namen als Alias führen dürfen und dass `username_taken` frühere Namen **nicht** als belegt zählt (sonst könnte niemand einen Namen wiederverwenden). Eine Zeile in SCHNITTSTELLE „Was je App bleibt“ |
| **R13** | Hinweis beim Umbenennen, dass Kalenderprogramme weiterlaufen | Modul liefert Wortlaute nur im Namensraum `account` | app-eigener Wortlaut, kein Modulthema |
| **R14** | Mitteilung „Dein Benutzername ist jetzt …“ sollte bei nextime sagen, dass am Kalenderprogramm nichts zu ändern ist | `Mail(kind=username_changed)` ohne Zusatz | die App baut den Text selbst (das Modul schickt nie); kein Modulthema |

## 13. Mutationen (Test „Bestand“, je `pytest tests/test_bestand.py -x`)

Elf Mutationen, jede Rückgabecode 1:

| Mutation | rot in |
|---|---|
| Adresssuche mit Groß/Klein | Anmelden mit Adresse |
| Anmelden nur mit dem Namen | Anmelden mit Adresse |
| CalDAV-Benutzer nur der Name (nicht die Adresse) | Kalenderprogramm mit Adresse |
| CalDAV-Benutzer mit Groß/Klein | Kalenderprogramm mit `JONAS` |
| CalDAV-Adressen über die Konto-ID statt den Namen | Kalenderprogramm (gemerkte Adressen) |
| Start löscht offene Einladungen / beendet Sitzungen / löscht Kalenderkonto-Geräte / löscht App-Geräte | „Start ergänzt nur“ (Zeilen gezählt) |
| Sperre zählt nur bei Eingabe des Namens | Sperre am Konto |
| Regel 3 vorab gebaut (doppelte Adresse meldet keinen an) | das strikte `xfail` wird rot, wie es soll |
