# App interface `/api/app/1` (api 1)

The interface between the nextime server and the nextime app for iPhone and iPad (TIMEIOS). Both sides build against
this text; a change raises `api` or is agreed on both sides first. It follows nexnotes' interface
(`nexnotes-ios/docs/vertrag-app1.md`) one to one wherever a calendar allows: the same ways of pairing, the same device
token, `hello`, `me`, `changes` with `wait`, the same errors, the same browser pages. What differs, and why, is listed
in "Differences from nexnotes" at the end.

Status: draft 0.5 (24.11.2026), server TIME-29 on nextime branch `ios-app2` (0.1 `dd89332`, after the review
`6a158d7`, 0.2 `a857c14`, 0.3 `29a72a3`/`ae9393a`, released as nextime 0.4.0 `10f6f91`; 0.4 and 0.5 on `ios-app2`). Parts
marked **later** are named so that later stages dock on; the server does not have them yet and `hello.features` does
not name them.

Changes in 0.5 (24.11.2026, stage 4 of the app: TIMEIOS-11, 14, 20, TIME-10 for the app), each with the browser's
rights:
- Reminders (feature `reminders`, TIMEIOS-20): every event in `changes` and in the answers of `ops` carries the
  account's own `reminders` (minutes before the start, `null`: the calendar's default) and `reminder_occurrences`
  (single occurrences of a series with alarms of their own). `calendars[].reminder_default` is the account's default
  per calendar (`"none"`, `"source"` or minutes); the defaults for new events per kind stay in `calendars[].defaults`
  (`reminders`, `reminders_all_day`). Op `reminders` sets them, with any right, as `PUT /api/events/{id}/reminders`.
  A change of only the reminders moves the cursor and wakes `wait` and the nudge of the account (not of others).
- Attachments (feature `attachments`, TIMEIOS-11): every event carries `attachments` (nextime's own) and
  `remote_attachments` (those of the source); `POST /attachments` with the file as the body (also for an event made
  offline, by `event_ref`), `GET /attachments/{id}/file|view|thumb`, `GET /events/{id}/remote-attachments/{index}`,
  `DELETE /attachments/{id}`. Adding or removing one moves the cursor.
- Trash (feature `trash`, TIMEIOS-14): `GET /trash`, the three restores and `POST /trash/empty`, as the browser's
  `/api/trash…`.
- `removed[]` entries carry `trashed`: `true` when the event went into the trash (deleted, cancelled by the
  organizer, gone at the source), `false` when it only left this calendar (moved, a share ended, a free event under
  "busy only").
- `docs/quick-tests.json` in nextime's repository: the sentences of quick entry with what the web understands (see
  "Quick entry sentences"); a web test keeps the file true to `understand.ts`.

Changes in 0.4 (24.11.2026, stage 3 of the app: TIMEIOS-8, 9, 16, 17, G9), each with the browser's rights:
- Collections written from the app: ops `collection_create`, `collection_change`, `collection_delete` (feature
  `collections_write`, TIMEIOS-9 AC-1, EC-8). `collections` in `changes` carry `task_areas`.
- `GET /tasks?from=&to=` as the browser's `GET /api/tasks` (feature `tasks`, TIMEIOS-17), `GET /deadlines?from=&to=`
  as `GET /api/deadlines` (feature `deadlines`, G9).
- Every event in `changes` (and in the answers of `ops`) carries `invitation` (feature `invitations`, TIMEIOS-16).
- People (`created_by`, `updated_by`, `calendars[].owner`, `conflicts[].by`) carry `avatar` and `color`;
  `GET /avatars/{id}` with the device's token (feature `people`, TIMEIOS-9).
- `GET /year?year=&zone=&collection=|only=` with the browser's count (feature `year`, TIMEIOS-8 AC-16).
- `calendars[].synced_at`: when the last round with the calendar's source worked (ISO, UTC), beside `source_state`;
  `null` for nextime's own calendars, for a source never synced and for others' calendars (as `source_state`). For
  "<source> not reachable · state of 15:52" (TIMEIOS-6 EC-7), as the browser shows it.

Changes in 0.3 (24.11.2026, follow-up 5, TIME-29 AC-3):
- Notifications of changes in shared calendars (TIME-21) reach the app through the relay, as nexnotes sends its
  notifications: an alert whose `enc` carries the ready words in the account's language and the fields to say it in
  the device's language and to open the event or the day. See "Notifications".
- `PUT /push` takes `"alerts": true`: only a build whose notification service extension opens such messages says so.
  Without it (every build before 0.3) the device gets the silent nudge only, never an alert it cannot open.
- `hello.features` adds `notify`. Reminders never go to app devices (G6: the app plans them itself).
- After the review: `who` holds at most three names, `who_more` how many more.

Changes in 0.2 (24.11.2026, follow-up 3):
- The silent nudge through the push relay: on every change the account sees (an event, a calendar, a share, a
  collection, its display), every app device of the account that handed over a ticket (`PUT /push`) gets a quiet push
  (no alert) with `enc` = `{"type": "changed", "account", "server"}`; the app then asks `changes` for that account.
  At most one nudge per account every 10 seconds, the first about 2 seconds after a change. See "Notifications".
- `me.offers.push` is `true` while the operator leaves the relay on (`NEXTIME_PUSH_RELAY`, empty: off).
- `hello.features` adds `nudge`.
- A device disconnected (in the browser, by itself, a new password, a block) ends its running `wait` at once
  (`401 device_disconnected` / `account_blocked`), as nexnotes closes its open connections.
- `resolve` of an event the account does not see answers `not_found` (it said `done` for `restore: false`).

Conventions: JSON in UTF-8, times as ISO 8601 UTC with `Z`. Errors are
`{"detail": {"code": "<code>", "message": "<English text>"}}` with the HTTP status named below. The app sends
`Authorization: Bearer ntd_…` on every call except `hello` and the two pickups of a pairing. Every call may answer
`401 device_disconnected` (the device was disconnected, TIME-29 EC-1), `401 account_blocked` (account blocked or
deleted, EC-5) or `401 second_factor_required` (the operator requires a second factor the account has not set up; the
app treats it like a disconnected device: "sign in again"); the app keeps everything it has and shows the account as
disconnected (TIMEIOS-5 EC-7, EC-8). The routes of the browser (`/api/events`, `/api/calendars` …) do not take the
device token in api 1.

## Server version (TIME-29 EC-2)

`GET /api/app/1/hello` (no sign-in)

```json
{"app": "nextime", "api": 1, "min_api": 1, "version": "0.4.0", "sign_in": ["password", "oidc"],
 "features": ["pairing", "changes", "ops", "conflicts", "calendar_account", "push_ticket", "nudge", "notify",
              "collections_write", "tasks", "deadlines", "invitations", "people", "year", "reminders",
              "attachments", "trash"]}
```

The app speaks `api` 1. If `min_api` > 1 the app says "update the app"; if `api` < 1 it says "update the server"
(TIMEIOS-4 EC-5, TIMEIOS-5 EC-9). If `hello` answers 404, the app asks `GET /api/health`: JSON with
`"app": "nextime"` means "nextime is here but too old, update the server"; anything else, and any answer to `hello`
that is not this JSON, means "no nextime at this address" (TIMEIOS-4 EC-3).

`features` names the parts of this interface the server has. The app hides what the server does not name and says
"update the server" for that part. Later parts add their name (see "Later").

## Pairing

### With the QR code (TIME-29 AC-1)

Browser (session), in Settings › My account › On iPhone and co ("Auf iPhone und Co"), card "Devices" ("Geräte"),
button "Connect iPhone or iPad" ("iPhone oder iPad verbinden"):

- `POST /api/app-pairing {"address"?}` → `201 {"id", "secret", "expires_at", "link", "address", "qr_svg"}`, valid
  10 minutes, one device. `link` = `nextime://connect?server=<origin>&pairing=<id>&secret=<secret>`; the browser draws
  it as the QR code. `address` is the server's public address when the operator set one, else the one the browser
  sent, else the request's (`422 app_address_missing` when none works). At most 5 codes of an account wait at once
  (`409 too_many_pairings`).
- `GET /api/app-pairing/{id}` → `{"state": "waiting"|"used"|"expired", "device": {…}|null}` (the dialog asks every two
  seconds and closes itself when used).
- `DELETE /api/app-pairing/{id}` withdraws it (closing the dialog does).

The dialog's three steps (de / en):
1. „nextime auf dem iPhone oder iPad öffnen.“ / "Open nextime on the iPhone or iPad."
2. „„Server hinzufügen“ tippen und den Code scannen.“ / "Tap “Add Server” and scan the code." (the app's own name of
   the button, TIME-29 follow-up 12)
3. „Fertig. Deine Kalender laden, und die Anmeldung wandert über deine Apple-ID auf deine anderen Geräte.“ / "Done.
   Your calendars load, and the sign-in travels to your other devices through your Apple ID."

Below: „Ohne Kamera: In der App die Adresse <host> eingeben und anmelden.“ The app's hint under the camera names the
way there: „Im Browser: Einstellungen › Mein Konto › Auf iPhone und Co › „iPhone oder iPad verbinden“. Der Code gilt
10 Minuten.“ / "In the browser: Settings › My account › On iPhone and co › “Connect iPhone or iPad”. The code is valid
for 10 minutes."

App:

- `POST /api/app/1/pairing/{id}/key {"secret", "device_name", "device_kind"}` → `200 {"token", "account", "device"}`.
  `device_kind` is `ios` or `ipados` (`mac` is taken too, as in nexnotes). Expired, used, unknown or wrong secret:
  `410 pairing_gone`; failures count towards the sign-in brake per sender (`429 too_many_attempts` with
  `Retry-After`) (TIME-29 EC-3). A blocked or deleted account: `410 pairing_gone` as well.

### With the address and sign-in (TIME-29 AC-5)

1. The app opens `https://<server>/app-connect?state=<random 8–128 chars of A-Z a-z 0-9 _ ->` in the system sign-in
   sheet (`ASWebAuthenticationSession`, callback scheme `nextime`).
2. Without a session the page goes to the sign-in first (password, provider with passkey, second factor) and comes
   back; then it asks „Die nextime-App mit diesem Konto verbinden?“ ("Connect the nextime app to this account?") with
   „Angemeldet als <name>. Die App bekommt dieselben Rechte wie du im Browser.“ and „Verbinden“ / „Abbrechen“.
3. „Verbinden“ (`POST /api/app-connect {"state"}` → `{"link"}`) leads to
   `nextime://connected?code=<one-time code>&state=<state>`; „Abbrechen“ to
   `nextime://connected?error=cancelled&state=<state>`. The code is valid 5 minutes, once.
4. App: `POST /api/app/1/pairing/code {"code", "device_name", "device_kind"}` → `200 {"token", "account", "device"}`;
   bad, used or expired code `410 pairing_gone` (counts at the brake like the QR code).

### Another own device (TIMEIOS-4 AC-9)

`POST /api/app/1/devices {"device_name", "device_kind"}` with the token of a device of the same account →
`201 {"token", "account", "device"}`. The new device shows up on its own in the list and can be disconnected on its
own. At most 5 per account and hour (`429 too_many_attempts`, `Retry-After`).

### Shapes

```json
"account": {"id": 7, "name": "Mika Example", "login": "mika", "email": "mika@example.com", "avatar": "",
            "operator": false},
"device":  {"id": 12, "name": "Mika's iPhone", "kind": "ios", "created_at": "…", "last_seen_at": "…"}
```

`operator` (TIME-29 follow-up 8): whether the account runs the server; the app names the browser's server pages only
then. `avatar` is when the picture was set (empty for none); the picture is `GET /api/app/1/avatars/{account id}`
(0.4, see "People").

## The own device

- `GET /api/app/1/me` → `{"account", "device", "display", "language", "limits", "offers", "server_id",
  "calendar_account"}`:
  - `display`: as the browser's Settings › My account › Appearance, `{"week_start": 0–6 (0 Sunday), "week_numbers",
    "clock": "auto"|"24"|"12", "time_zone": ""|IANA, "home_zone", "merge_duplicates", "month_style": "dot"|"tint"}`
    (TIMEIOS-2 AC-11, TIMEIOS-19).
  - `language`: the account's chosen language (`de`, `en`) or empty (the app follows iOS anyway, G7).
  - `limits`: `{"ops": 50, "changes_page": 500, "wait_seconds": 25, "title_chars": 200, "notes_chars": 10000,
    "location_chars": 2000, "upload_mb", "trash_days"}` (the web's limits, TIME-29 AC-8).
  - `offers`: `{"push": bool}`: whether the server sends through the push relay (the operator may switch it off);
    see "Notifications".
  - `server_id`: a fixed mark of this server (16 hex characters), the same for every account and call.
  - `calendar_account`: see "Calendar account on this device".
- `DELETE /api/app/1/device` → `204`: the device disconnects itself (TIMEIOS-4 AC-11, follow-up 9): the token goes, the
  push ticket with it, it leaves "Geräte" in the browser at once. Calling it again answers `401 device_disconnected`
  (the app counts that as done). A calendar account linked to it stays and reminds again (see below).
- `GET /api/app/1/devices` → `[{"id", "name", "kind", "created_at", "last_seen_at", "push", "this"}]`;
  `DELETE /api/app/1/devices/{id}` → `204` (the own one too). **later** in the app (TIMEIOS-19), built now.

## Calendar account on this device (TIMEIOS-4 AC-6, AC-7, AC-12, AC-13, EC-14, EC-15; follow-up 4)

The calendar account (CalDAV, TIME-20) set up from the app on the same iPhone is linked to the app's device: there
the server sends **no reminders** (no VALARM) into Apple Calendar, so only the app reminds; an event Apple Calendar
writes back without alarms does not count as "reminders removed", and the web's "not twice" (TIME-7 AC-7) does not
count that device as one that reminds.

- `POST /api/app/1/calendar-account {"device_name"}` → `201 {"link", "expires_at", "device": {"id", "name",
  "set_up": false}}`: a calendar-account device for this app device (one; a second call replaces a device not yet set
  up and answers anew) and its one-time setup link `<origin>/setup/<code>` (10 minutes, once, signed in through the
  link itself). The app opens it in Safari: the page is the browser's "Auf diesem iPhone einrichten" with „Profil
  laden“; the profile installs in the iOS settings. `409 https_required` when the server is not on HTTPS (EC-14); the
  server account stays connected.
- `calendar_account` in `/me`: `null` (none for this device) or `{"id", "name", "set_up": bool, "last_seen_at"}`;
  `set_up` turns true once the profile signed in for the first time (TIME-20 EC-7). The app shows „eingerichtet“
  (AC-13) or „Auf diesem iPhone nicht eingerichtet.“ (AC-12).
- `DELETE /api/app/1/calendar-account` → `204`: "Entfernen, auch aus Apple Kalender" (EC-15): the calendar-account
  device is locked out like „Aussperren“ in the browser (TIME-20 AC-5); the app then says how to delete the profile.
  For "Entfernen, Apple Kalender behalten" the app only calls `DELETE /device`: the link goes with the app device, the
  calendar account stays and Apple Calendar reminds again.

## What changed (TIME-29 AC-6, AC-8, EC-4; TIMEIOS-5 AC-1, AC-5, AC-6)

`GET /api/app/1/changes?cursor=<cursor>[&calendars=<id,id,…>]` (no cursor or `0` = everything)

The cursor is an opaque string; the app keeps it as it came and never builds one.

```json
{
  "cursor": "9f1c2a4e.4711.1b2c3d4e",
  "full": false,
  "more": false,
  "events": [
    {"id": 41, "calendar_id": 3, "href": "c0ffee.ics", "uid": "c0ffee@example.com", "etag": "6f1e…",
     "ical": "BEGIN:VCALENDAR\r\n…END:VCALENDAR\r\n",
     "title": "Dentist", "all_day": false, "starts_at": "2026-11-26T09:00:00Z", "ends_at": "2026-11-26T09:45:00Z",
     "time_zone": "Europe/Berlin", "recurring": false, "busy": true, "location": "Main Street 1",
     "busy_only": false, "waiting": false, "origin": "",
     "created_by": {"id": 7, "name": "Mika Example", "avatar": "2026-11-20T10:00:00Z", "color": 4},
     "updated_by": {"id": 7, "name": "Mika Example", "avatar": "2026-11-20T10:00:00Z", "color": 4},
     "updated_at": "…", "invitation": null,
     "reminders": [60, 15], "reminder_occurrences": {"20261203T090000Z": [5]},
     "attachments": [{"id": "Qm9vazEyMzQ1Njc4OTAxMg", "name": "Plan.pdf", "kind": "pdf",
                      "mime": "application/pdf", "size": 48213, "pages": 2, "width": null, "height": null,
                      "view": false, "thumb": true, "inline": true, "created_at": "2026-11-24T10:00:00+00:00"}],
     "remote_attachments": []}
  ],
  "removed": [{"kind": "event", "calendar_id": 3, "href": "old.ics", "trashed": true}],
  "calendars": [
    {"id": 3, "name": "Home", "color": "green", "icon": "house", "background": false, "holiday": false,
     "reminder": 0, "reminder_default": "none", "defaults": {}, "group": "own", "kind": "", "right": "owner", "writable": true,
     "read_only": false, "hidden": false, "position": 0, "default": true, "owner": null, "shared": false,
     "source_state": null, "synced_at": null}
  ],
  "collections": [{"id": 2, "name": "Family", "calendar_ids": [3, 5], "with_tasks": false, "task_areas": []}],
  "conflicts": [
    {"event_id": 41, "mine": "BEGIN:VCALENDAR…", "by": {"id": 9, "name": "Kim Example"}, "at": "…"}
  ],
  "account": {"id": 7, "name": "Mika Example", "login": "mika", "email": "mika@example.com", "avatar": "",
              "operator": false, "language": "de", "display": {…as in /me}}
}
```

- **Events** (G1: all events of all times): every event of every calendar the account sees (own, shared with it,
  from iCloud/Google/CalDAV/subscriptions/holidays through the server), also of calendars it hid (`hidden`), so that
  showing them again works offline. One entry per event: `ical` is the whole object, a `VCALENDAR` with the
  `VEVENT` of the series (RRULE, RDATE, EXDATE) and every changed occurrence (`RECURRENCE-ID`), `VTIMEZONE`s
  included; the app rolls series out itself. The columns beside it (`title`, `starts_at` = first start, `ends_at` =
  last end, `null` for a series without end; an all-day event's dates at midnight UTC, `all_day`, `time_zone`,
  `recurring`, `busy`, `location`) are what the server derived, for lists and the search before parsing.
  - `ical` carries no alarms (VALARM): the app plans reminders itself (G6) from `reminders` (0.5, see
    "Reminders").
  - `attachments`, `remote_attachments` (0.5): see "Attachments"; both `[]` under "busy only".
  - A calendar shared as "busy only" (`right: "busy"`): `busy_only: true`, `ical` holds times and rule only with the
    summary „Belegt“/"Busy" (as the calendar account gives it), `title` and `location` are empty; free events of such
    a calendar never come.
  - `waiting`: a change made in nextime still has to reach iCloud, Google or CalDAV ("wartet", TIMEIOS-5 AC-8, EC-4).
  - `origin`: `imip` for a copy entered from an invitation mail (TIMEIOS-16 AC-16), else empty.
  - `etag` is the version; the app sends it back with a change (see "Writing").
  - `created_by`, `updated_by`: a person (see "People", 0.4) or `null`.
  - `invitation` (0.4, TIMEIOS-16): `null`, or the account's invitation in this event:
    `{"state": "needs-action"|"accepted"|"tentative"|"declined", "from": "<organizer's name or address>",
    "cancelled": bool, "occurrences": {"20261210T090000Z": "declined", …}}`. `occurrences` names the changed
    occurrences of a series whose state differs from the series' (by the original start, as everywhere). Only where
    the account connected the calendar's source (its addresses there) or for an invitation entered from a mail into
    an own calendar, as in the browser (TIME-18); never in a "busy only" calendar. Answering comes with op `answer`
    (**later**, TIMEIOS-16).
- **removed**: an event that is gone from a calendar (deleted, moved to another calendar, a series' last occurrence
  gone, a free event in a busy-only calendar): `{"kind": "event", "calendar_id", "href", "trashed"}`. `trashed`
  (0.5): `true` when the event lies in the account's trash or the owner's now (deleted here, in the browser, by the
  organizer's cancellation or at the source), `false` when it only left (moved, a free event under "busy only", gone
  for good already); for "Gelöscht" versus a silent drop (TIMEIOS-14). The app drops its event with
  that `calendar_id` and `href`, if it still has it there; an event moved to another calendar comes again in `events`
  with its new `calendar_id` (same `id`). The app skips any `kind` it does not know.
- **calendars**, **collections**, **conflicts**, **account**: complete on every page; each replaces what the app had.
  Events of a calendar that is not (or no longer) in `calendars` are gone for the app (share ended, calendar deleted,
  source disconnected; TIMEIOS-5 EC-16). A calendar the app did not have before: the app fetches its events with
  `changes?calendars=<id>` and no cursor (a full listing of only that calendar, paged like any other; the app keeps its
  main cursor).
  - `calendars` in the order of the browser's list. `group`: `own`, `icloud`, `google`, `caldav`, `subscriptions`,
    `holidays` or `shared`; `kind`: the source's kind or empty; `right`: `owner`, `edit`, `read`, `busy`; `writable`:
    the account may enter events now (false also while a source's access expired); `holiday`: „Tage hervorheben“
    (the account's own); `background`, `color` (a name of the eleven colours or `#rrggbb`), `icon` (a name of the
    server's symbols or empty), `reminder` (minutes, the calendar's default), `defaults` (the account's own defaults
    for new events, as in the browser); `owner`: `{"id", "name"}` of a shared calendar's owner, else `null`; `shared`:
    an own calendar shared with somebody; `source_state`: `ok`, `expired`, `unreachable`, `paused` or `null` (only for
    the one who connected the source); `synced_at` (0.4) the last round with the source that worked, ISO in UTC, or
    `null`; `reminder_default` (0.5): the account's default reminder for this calendar's events, `"none"`,
    `"source"` (as iCloud/Google/CalDAV say, only for the one who connected the source) or minutes as text (`"15"`;
    for a whole-day event `"1440"` and up means the day before at 18:00, less on the day at 09:00, as TIME-7).
  - `collections`: the account's own collections (TIME-13), with the calendars it sees now.
  - `conflicts`: see "Conflicts".
- **Paging**: at most 500 events (`limits.changes_page`) per page; `more: true` means ask again with the new `cursor`
  at once.
- **full**: no cursor, a cursor of another database (a restored backup, TIMEIOS-5 decision log) or one the server
  cannot read answers the full listing: `full: true` on every page of it; after its last page (`more: false`) the app
  drops every event of the listed calendars that no page held (TIME-29 EC-4). With `calendars=` the full listing and
  the dropping concern only those calendars. Changes made during a full listing come with the next call.
- The cursor also stands for `calendars`, `collections`, `conflicts` and `account`: a change of only those gives a
  new cursor too (and wakes `wait`).

`GET /api/app/1/wait?cursor=<cursor>` → `{"cursor"}` as soon as something the account sees changed after the cursor
(an event, a calendar's properties, a share, a collection, a conflict, the account's display), at the latest after 25
seconds (then with the same cursor). The app then asks `changes` with its own cursor (TIMEIOS-5 AC-12, open app). A
device waits once: a second `wait` of the same device ends the first.

## Writing (TIME-29 AC-2, AC-8, EC-6, EC-7; TIMEIOS-5 AC-2, AC-3, EC-1, EC-3, EC-5, EC-13, EC-15)

The app keeps its changes in a queue and sends them in order. Every change carries `ref`, made by the app once per
queued change (16 to 64 characters of `A-Z a-z 0-9 _ -`; 22 characters of base64url from 16 random bytes): the same
`ref` again gives back the answer of the first time and changes nothing (TIME-29 EC-6, a retry after a broken
connection or a restart never doubles anything). The server keeps the answers 30 days.

`POST /api/app/1/ops`

```json
{"ops": [
  {"ref": "q4…", "op": "create", "event": {"calendar_id": 3, "title": "Dentist", "all_day": false,
    "start": "2026-11-26T09:00", "end": "2026-11-26T09:45", "time_zone": "Europe/Berlin", "busy": true,
    "notes": "", "location": "", "geo": null, "rule": null}},
  {"ref": "r8…", "op": "change", "id": 41, "etag": "6f1e…", "scope": "all", "occurrence": null,
    "event": {"title": "Dentist (moved)", "start": "2026-11-26T10:00", "end": "2026-11-26T10:45",
              "all_day": false, "time_zone": "Europe/Berlin"}},
  {"ref": "s1…", "op": "delete", "id": 41, "scope": "this", "occurrence": "20261203T090000Z"},
  {"ref": "t5…", "op": "resolve", "id": 41, "restore": true},
  {"ref": "u2…", "op": "hide", "calendar_id": 5, "hidden": true}
]}
```

→ `200`

```json
{"ops": [
  {"ref": "q4…", "state": "done", "event": {…as in changes}},
  {"ref": "r8…", "state": "done", "event": {…}},
  {"ref": "s1…", "state": "done", "event": {…the series without that occurrence}, "trash": {"kind": "part", "id": 9}},
  {"ref": "t5…", "state": "done", "event": {…}},
  {"ref": "u2…", "state": "done"}
], "cursor": "…"}
```

- At most 50 ops per call (`422 too_many_ops` with `max`). Each op stands on its own, in the order given, in its own
  transaction: one refused never stops the others, nothing of a refused op is kept (EC-7). A refused op answers
  `{"ref", "state": "refused", "code", "message"}` with the browser's codes (below); the app shows it at the event
  (TIMEIOS-5 EC-3: „Als neuen Termin anlegen“ / „Verwerfen“).
- `cursor` in the answer: as `changes` would give it right now; the app may still ask `changes` with its own cursor
  (its own changes come in it too).
- **create**: `event` as the browser's `POST /api/events` (TIME-5, TIME-6): `calendar_id` (none: the account's
  default calendar), `title` (required, at most 200 characters), `all_day`, `start` and `end` (`YYYY-MM-DD` for a
  whole day, `end` its last day; otherwise `YYYY-MM-DDTHH:MM` on the clock of `time_zone`), `time_zone` (IANA; empty:
  the account's), `busy`, `notes`, `location`, `geo` (`{"lat", "lon"}` or `null`), `rule` (`null` or
  `{"freq": "daily"|"weekly"|"monthly"|"yearly", "interval", "weekdays": ["MO", …], "monthly": "date"|"nth"|"last",
  "end": "never"|"until"|"count", "until": "YYYY-MM-DD", "count"}`), `reminders` (minutes, the account's own; left
  out: the calendar's default). The op's `ref` is the event's `client_ref`: the same `ref` is the same event.
- **change**: `id`, `etag` (the version the change started from, TIMEIOS-5 AC-3), `scope` `all` | `this` |
  `following` with `occurrence` (an occurrence by its original start, `20261126T090000Z` or `20261126` for a whole
  day, as the browser names it), and `event` with only what changed (as the browser's `PATCH /api/events/{id}`: times
  come together — `all_day`, `start`, `end`, `time_zone`; `rule` sent, even as `null`, only when the rule changed;
  `geo` sent with a changed place; `calendar_id` moves the event, not for `scope: this`).
- **delete**: `id`, `scope`, `occurrence`; into the trash (TIMEIOS-14), one occurrence (EXDATE) or this and the
  following. `trash` names the entry of the trash (`event` or `part`).
- **resolve**: `id`, `restore`: „Meine zurückholen“ (`true`) or „So lassen“ (`false`), see "Conflicts".
- **hide**: `calendar_id`, `hidden`: show or hide a calendar for this account (TIMEIOS-5 AC-14, offline).
- **reminders** (0.5, TIMEIOS-20): `id`, `minutes` (`[minutes before the start]`, `[]` none at all, `null` back to the
  calendar's default) → `done` with `event`. As `PUT /api/events/{id}/reminders`: with any right (also "read" and
  "busy only", they are the account's own), for the whole series; occurrences with alarms of their own a device set
  over the calendar account keep them. `change` does not take `reminders` (it needs the right to edit); `create`
  does. One the account does not see: `not_found`; a value out of range: `invalid_reminder`.
- **collection_create** (0.4): `collection` = `{"name", "calendar_ids": [ids], "with_tasks"?, "task_areas"?:
  [area ids of nextasks]}` → `done` with `collection` (as in `changes`, its `id`). The op's `ref` is the
  collection's `client_ref`: the same `ref` is the same collection.
- **collection_change** (0.4): `id`, `collection` with only what changed (`name`, `calendar_ids`, `with_tasks`,
  `task_areas`) → `done` with `collection`. The later change wins.
- **collection_delete** (0.4): `id` → `done` (calendars and events stay). One gone already is `done` too.
- Collections keep the browser's rules (TIME-13): `name_missing`, `name_too_long` (with `max`),
  `collection_name_taken` (the same name in any case), `calendar_unknown` (a calendar the account does not see),
  `collection_gone` (not the account's, or deleted: for `collection_change`), `invalid_input`.
- Later in the same queue (**later**, names fixed now): `answer` (invitation), `tick` (nextasks), `calendar_order`.

**Who wins** (TIME-29 AC-2, TIME-5 EC-2): the change that reaches the server later counts, whatever the clock of the
device said (EC-13). A change whose `etag` is older than the event's version still goes through; the account whose
version it overwrote (another account, or the same account on another device or in the browser) gets a conflict with
both versions. A change to an event that was deleted meanwhile (in the trash) brings it back with the change; a
delete of an event changed meanwhile deletes it (into the trash, TIMEIOS-5 EC-5). A change or delete of a series'
occurrence that no longer exists answers `occurrence_unknown`.

Refusal codes of an op (`code`), the browser's: `not_found` (the event or calendar is gone for good or the account
does not see it), `calendar_gone` (the calendar went into the trash meanwhile), `event_deleted` (a change of an event
in the trash that the server cannot bring back: it is not the account's own calendar; with `by`),
`calendar_read_only` (the calendar may only be read now, also a share made read-only or a source whose access expired;
with `owner`), `no_writable_calendar`, `occurrence_unknown`, `scope_calendar`, `event_exists` (moving into a calendar
that holds the same event), `title_missing`, `title_too_long`, `notes_too_long`, `location_too_long`,
`invalid_date`, `invalid_time_zone`, `end_before_start`, `rule_invalid`, `rule_interval`, `rule_weekdays`, `rule_*`,
`invalid_reminder`, `invalid_input` (an op the server cannot read; `fields` names what), `op_unknown`, `source_busy`
(the source's sync round holds the event; send the same op again later: the only code after which the same `ref` is
not remembered). The answer may carry the error's further values (`field`, `max`, `by`, `owner`). The same `ref` with
another op answers the first answer.

If the source of a calendar (iCloud, Google, CalDAV) does not answer, the server takes the change all the same and
the event says `waiting` until the source has it (TIME-5 EC-4).

## Conflicts (TIMEIOS-5 AC-4)

`conflicts` in `changes`: every open conflict of this account, newest first: `{"event_id", "mine", "by", "at"}`.
`mine` is the account's overwritten version (a whole `VCALENDAR` as in `events`), the event's current version is the
event itself; `by` who saved over it (`{"id", "name"}`; the account itself when it was its own other device or the
browser: the app then says „an einem anderen Gerät“). One conflict per event (the newest counts).

- „Meine zurückholen“: op `resolve` with `restore: true`: `mine` becomes the valid version as a new change (the other
  account hears of it as of any change). Needs the right to write (`read_only` else).
- „So lassen“: op `resolve` with `restore: false`.
- An event gone for good drops its conflicts; one in the trash keeps them.

## Notifications (TIME-29 AC-3; follow-ups 3, 5)

- `PUT /api/app/1/push {"ticket", "key", "alerts"?}` → `204`: the relay's ticket and the device's X25519 public key
  (base64, 32 bytes), as nexnotes (`422 push_ticket_invalid` when unusable); `alerts: true` (0.3) when the build shows
  notifications with text (its extension opens them), else only the silent nudge comes. `DELETE /api/app/1/push` → `204`. The browser's
  list shows „Mitteilungen an“ while a ticket is kept, else „Mitteilungen aus“ (TIME-29 AC-7, EC-8).
- **The silent nudge (0.2, follow-up 3; TIMEIOS-5 AC-12):** the server calls the relay (`https://push.nexapps.dev`,
  app `dev.nexapps.nextime`) with `POST /v1/send {"ticket", "enc", "quiet": true, "collapse"}` for every device of the
  account with a ticket, whenever something the account sees changed: an event (also of its own calendars, also one
  the device made itself), a calendar's properties, a share, a collection, its display. `enc` is
  `{"type": "changed", "account": <account id>, "server": <me.server_id>}` encrypted for exactly that device as nexnotes
  does (a fresh X25519 pair and 12-byte nonce per message, HKDF-SHA256 with empty salt and info `nextime-push-v1` to
  32 bytes, ChaCha20-Poly1305; `enc` = base64 of ephemeral public key + nonce + ciphertext with tag). `collapse` is
  `nt-<server_id>-<account id>`: a newer nudge replaces one Apple still holds. The app wakes in the background, finds
  the account by `account` and `server`, and asks `changes` with its cursor; nothing about the change travels.
  - Timing: the first nudge of a burst about 2 seconds after the first change, then at most one every 10 seconds per
    account (the relay takes 30 a minute per device). Kept in memory: a restart of the server may drop a nudge; the
    app still syncs when it opens.
  - A `410` from the relay drops the device's ticket (the browser then shows „Mitteilungen aus“); the app brings a
    new one with `PUT /push` on its next start. Other failures are logged without the ticket and not retried.
- **Notifications of changes (0.3, follow-up 5; TIMEIOS-20):** when somebody else changes events in a calendar the
  account sees shared (its own shared with others, or shared with it) and the account switched „Mitteilungen“ on for
  that calendar in the browser (TIME-21 AC-1, the same switch for every device), every app device with
  `"alerts": true` gets one alert per bundle, as the browser does (two minutes after the last change in that calendar, at the
  latest 30 minutes after its first). `POST /v1/send {"ticket", "enc", "collapse"}` without `quiet`: the alert carries the app's own strings
  `push_title`/`push_body`, which say nothing about content; the extension replaces them with what it opens. `enc` is
  encrypted as the nudge is, the message:

```json
{"type": "changes", "title": "Familie", "text": "Kim Example hat „Elternabend“ am Do, 3.12. um 19:00 eingetragen.",
 "account": 7, "server": "9c1d2e4b3f9a0c1d", "calendar_id": 3, "calendar": "Familie", "who": ["Kim Example"],
 "who_more": 0, "busy": false, "count": 1,
 "items": [{"kind": "created", "event_id": 41, "title": "Elternabend", "starts_at": "2026-12-03T18:00:00Z",
            "all_day": false, "before_start": null, "recurring": false}],
 "day": "2026-12-03", "event_id": 41}
```

  - `title` (the calendar, „<Kalender> (von <Besitzer>)“ for one shared with the account) and `text` are the browser's
    words in the account's language and zone, plain text, at most 120 and 300 characters. The app may show them as
    they are or say the same in the device's language (G7) from `calendar`, `who`, `items` and `count`.
  - `items`: the first three changes of the bundle (`count` is all of them), oldest first. `kind` is `created`,
    `changed`, `moved` (another time, `before_start` the time before) or `deleted`; `event_id` is `null` for a deleted
    one. A whole-day event's `starts_at` is its date at midnight UTC. `who`: the people who made the changes (not the
    account itself), at most three, each at most 40 characters, `who_more` how many more (so `enc` stays far below
    the relay's 3000 characters); empty when only the calendar's source (iCloud and co) changed it.
  - „Busy only“ (`busy: true`): no titles, no names, no `event_id`; the times only.
  - Opening („Öffnen“, a tap): `day` and `event_id` as the browser's link `/day?day=…&event=…`; with several
    changes or a deleted event only `day`. `account` and `server` name the account in the app.
  - `collapse` is a keyed mark per calendar (`nt-c…`): a newer bundle replaces an older one Apple still holds.
  - Nothing of the account's own changes; nothing after the account lost the calendar or switched it off (checked
    again before each try). A failure is tried again after 1, 5, 15 and 60 minutes; `410` drops the ticket (and the
    alerts) of the device; `400`, `403`, `413` give the message up.
- Reminders never go through the relay (G6): the app plans them itself from its copy. Switches „Erinnern“ and
  „Mitteilungen“ per app device and the app devices in the browser's list of TIME-21 stay **later**.

## People (0.4, TIMEIOS-9)

A person, wherever the interface names one (`created_by`, `updated_by`, `calendars[].owner`, `conflicts[].by`):
`{"id", "name", "avatar", "color"}`. `avatar` is when the picture was set (`2026-11-20T10:00:00Z`), empty when there is
none, when the account may not see it (the browser's rule: the account itself, the operator, or somebody who shares
a calendar with it) or when the person is blocked or deleted (TIME-14 EC-8). `color` is the index (0 to 10) of the
color of the person's initial circle, the same as in the browser. Names and pictures are those of the moment of the
page: a new picture of a calendar's owner comes with the next page (the cursor changes with it); at an event with
the event's next change or a full listing, as in nexnotes (the app may load the picture of a known person anew when
an owner's `avatar` changed).

- `GET /api/app/1/avatars/{account id}` → the picture (WebP), with the device's token; the browser's rules;
  else `404 not_found`. The app adds `?v=<avatar>` to the address and keeps it.

## Tasks and deadlines (0.4, TIMEIOS-17, G9)

- `GET /api/app/1/tasks?to=<YYYY-MM-DD>[&from=<YYYY-MM-DD>][&previews=true]` → exactly the browser's `GET /api/tasks`
  for the account: `{"connected", "hidden", "problem", "tasks": [...], "areas": [...], "notices": [...], "level"}`
  (`notices` and `level` only when connected; fields as nextime's web
  gets them, TIME-25). Without `from` the overdue ones come too; at most about a year at once. Ticking off stays
  **later** (op `tick`, TIMEIOS-17).
- `GET /api/app/1/deadlines?to=<YYYY-MM-DD>[&from=<YYYY-MM-DD>]` → exactly the browser's `GET /api/deadlines`:
  `{"connected", "hidden", "problem", "deadlines": [{"id", "document_id", "kind", "what", "title", "sender",
  "amount", "currency", "note", "day", "status", "link"}]}`; to look at only.
- Both answer `{"connected": false, …}` while the account has not connected nextasks or nexpaper in the browser; the
  app never sees keys or addresses beyond what the browser sees.

## The year (0.4, TIMEIOS-8 AC-16)

`GET /api/app/1/year?year=<1900…2200>[&zone=<IANA>][&collection=<id> | &only=<calendar id>]` → the browser's count
(`GET /api/events/year`): `{"year", "zone", "days": {"2026-03-03": 4, …}, "holidays": ["2026-01-01", …]}`, days
without events left out. `zone` defaults to the account's. `collection` counts that collection's calendars, `only`
one calendar ("Nur diesen", also a hidden one); both at once `422 invalid_input`; one the account does not have
`404 not_found`. For the app's comparison test of its own count.

## Reminders (0.5, TIMEIOS-20, TIME-7)

Each event carries the account's own choice; nobody else sees it.

- `reminders`: `null` = the calendar's default (`calendars[].reminder_default`; for a new event the kind's default in
  `calendars[].defaults.reminders` / `reminders_all_day` decided at creation), `[]` = none, else minutes before the
  start, largest first. Whole-day events: `360` the day before at 18:00, `-540` on the day at 09:00 (TIME-7).
- `reminder_occurrences`: `{"<occurrence>": [minutes]}` for single occurrences of a series whose alarms a device set
  apart over the calendar account (by the original start as everywhere, `20261203T090000Z`); `{}` otherwise. The app
  shows and plans them for that occurrence; the browser and op `reminders` change only the series.
- Op `reminders` sets the series' choice (see "Writing"). A change of only reminders comes as a changed event in
  `changes` (other accounts with the calendar get the event again too, unchanged for them) and wakes `wait` and the
  nudge of this account only.

## Attachments (0.5, TIMEIOS-11, TIME-9)

Per event in `changes` and in answers of `ops`:

- `attachments`: nextime's own, oldest first, each as the browser's attachment: `{"id", "name", "kind"
  (pdf|image|other), "mime", "size", "pages", "width", "height", "view" (a WebP to show exists), "thumb" (a preview
  exists), "inline" (shown in the page rather than downloaded), "created_at"}`. Never under "busy only".
- `remote_attachments`: those an event of iCloud, Google or CalDAV carries (`ATTACH`), read only:
  `{"index", "name", "mime", "size" (or null), "link" (the https address, or "" for one carried inline)}`. Events of
  a source take no attachments of nextime (`409 attachments_own_only`).

`POST /api/app/1/attachments?event_id=<id>&name=<file name>` (or `event_ref=<ref>` instead of `event_id`), body = the
file (`Content-Type: application/octet-stream`), with `ref=<ref>` in the query or the header
`Idempotency-Key: <ref>` (same rules as an op's `ref`). → `201` with the attachment; the same `ref` again → `200`
with the same attachment, nothing doubles (also after a broken upload that finished on the server).
- `event_ref`: the `ref` of the `create` op that made the event offline; `404 event_unknown` while that op has not
  reached the server (the app keeps the file queued and tries again after its ops went). Only the account's own ops.
- The size limit is the operator's (`me.limits.upload_mb`): larger → `413 attachment_too_large` with `name`, `size`,
  `max_mb`; storage full → `507 storage_limit_reached` or `507 storage_failed`. Rights as the browser: the right to
  edit the event (`403 calendar_read_only` with `owner`), `404 not_found` for one the account does not see,
  `409 attachments_own_only` for a source's event, `422 invalid_input` (`fields`) without a valid `ref` or with
  neither or both of `event_id` and `event_ref`.

`GET /api/app/1/attachments/{id}/file[?download=1]`, `…/view` (a picture as WebP), `…/thumb` (the preview; `404`
while it is drawn) and `GET /api/app/1/events/{id}/remote-attachments/{index}` (the source's file through the server)
answer as the browser's routes, with any right to see the event (not "busy only").

`DELETE /api/app/1/attachments/{id}` → `204`, for good (attachments never go into the trash, TIME-9 AC-9); with the
right to edit (`403 calendar_read_only`). One gone already (or never seen) answers `204` too.

Adding or removing an attachment moves the cursor (the event comes again with its list) and wakes `wait`.

## Trash (0.5, TIMEIOS-14, TIME-23)

As the browser's `/api/trash…`, with the device token:

- `GET /api/app/1/trash?offset=&limit=` (`limit` 1 to 500, default 100) → `{"now", "trash_days", "warn_days",
  "total", "offset", "items": […]}`, newest first. `total` counts every entry; `limit=1` gives it cheaply. Items:
  - `{"kind": "event", "id", "title", "color", "calendar": {"id", "name", "source"}, "calendar_gone", "all_day",
    "start", "time_zone", "recurring", "exceptions", "deleted_by": {"self": true, "name"} | {"name"} | null,
    "deleted_via", "reason", "deleted_at", "expires_at", "days_left", "warn"}`; `reason`: `cancelled` (the organizer
    cancelled), `source_has_it` (iCloud/Google/CalDAV removed it), `""` when somebody deleted it.
  - `{"kind": "part", …}` one occurrence (`part: "occurrence"`) or this and the following (`part: "following"`) of a
    series, with `series_gone`.
  - `{"kind": "calendar", "id", "title", "color", "events", "deleted_by", "deleted_at", "expires_at", "days_left",
    "warn"}`.
- `POST /api/app/1/trash/events/{id}/restore` with `{"calendar_id"?}` (needed when its calendar is gone) →
  `{"id", "calendar_id"}`.
- `POST /api/app/1/trash/parts/{id}/restore` → `{"id", "calendar_id"}` (the series).
- `POST /api/app/1/trash/calendars/{id}/restore` → `{"id"}` with its events, shares and guest link.
- `POST /api/app/1/trash/empty` with `{"until": "<now of the listing>"}` → `{"count", "events", "calendars"}`;
  what was deleted after `until` stays (nothing goes unseen).
- Errors as the browser: `404 not_in_trash` (restored already, gone for good, not the account's: there is no
  separate "already back"), `409 target_needed` (its calendar is gone: send `calendar_id`), `403 calendar_read_only`,
  `409 event_exists` (the target holds the same event), `409 source_busy` (the source's round holds it: again
  later), `422 invalid_input`.
- What comes back arrives in `changes` as usual (events and calendars again; the cursor moves).

## Quick entry sentences (0.5, TIME-10)

`docs/quick-tests.json` in nextime's repository is the list of sentences the web's quick entry is tested with,
with what it understands. The app reads the file in its own tests and checks its parser against it (TIMEIOS: same
sentence, same event). The web test `frontend/src/quickTests.test.ts` checks `understand.ts` against the same file;
after a wanted change of the web's parser `QUICK_TESTS_WRITE=1 npx vitest run src/quickTests.test.ts` writes the
answers anew (the app then follows).

```json
[{"now": "2026-11-26T15:05:00Z", "zone": "Europe/Berlin", "lang": "de", "text": "morgen 15 Uhr Zahnarzt",
  "calendars": [{"id": 1, "name": "Familie", "writable": true}],
  "expect": {"title": "Zahnarzt", "all_day": false, "start": "2026-11-27T15:00", "end": "2026-11-27T16:00",
             "default_length": true, "rule": null, "calendar": null, "tag": null, "error": null}}]
```

- `now`: the moment (UTC), `zone`: the device's zone the sentence is read in; `lang`: the language the sentence is
  written in (a hint for grouping only: the parser always reads German and English at once).
- `calendars`: the calendars `#Name` may name (in the order of the list; `writable` false: "only read"); left out =
  none.
- `expect.start`/`end`: `YYYY-MM-DD` for a whole day (`end` its last day), else `YYYY-MM-DDTHH:MM` in `zone`;
  `default_length`: no length was said (one hour); `rule` as in ops (`null`: no series); `calendar`: the id `#Name`
  chose, or `null`; `tag`: `{"text", "problem": "unknown"|"readOnly"}` for a `#Name` that fits no writable calendar.
- `expect.error`: `null`, `empty` (nothing typed), `no_date` (no date or an impossible one: the app opens a new event
  with the text as title), `end_before_start`, `no_title`, `title_too_long` (over 200), `length_without_time`. With
  `empty` and `no_date` every other field is `null`.

## Later (names fixed, not built)

So that later stages dock on; each comes with its name in `hello.features`.

- `member` (follow-up 1, TIMEIOS-13 to 19, 28): the browser's member ways with the device token — profile and picture
  (`PUT /profile`, `PUT`/`DELETE /avatar`; `GET /avatars/{id}` is there since 0.4), display (`PUT /display`), devices (built), calendars
  and accounts (create, change, order, delete; iCloud/CalDAV/subscription with check and preview; holidays; reconnect,
  disconnect), Google with the sign-in led back into the app, shares and guest links, invitations
  (`POST /events/{id}/answer`), nextasks connection, import, export and move as jobs, `GET /upcoming` (the trash is
  there since 0.5).
- `sync_more` (follow-up 2, rest): ops `answer`, `tick`, `calendar_order`.
- `reminder_switches` (follow-up 5, rest): switches „Erinnern“ und „Mitteilungen“ per app device;
  `offers.relay_ok` (the server reaches the relay). The reminders themselves are there since 0.5.
- `marks` (follow-up 6): marks per person and event that travel: `alarm` (Wecker, TIMEIOS-21), `leave_off` („Los um“
  off, TIMEIOS-22).
- `links` (follow-up 7): `GET /.well-known/apple-app-site-association` for `/day?…&event=…`, `/calendar/*`; the
  browser's „In der App öffnen“.

## Browser: devices (TIME-29 AC-4, AC-7)

- `GET /api/app-devices` → `[{"id", "name", "kind", "created_at", "last_seen_at", "push": true|false}]`
- `DELETE /api/app-devices/{id}` → `204`; the device has no access from that moment (TIME-29 AC-4).
- A new password, „Überall abmelden“, a reset second factor, a block or deletion of the account disconnect every
  device (`401 device_disconnected`; a blocked or deleted account's devices hear `401 account_blocked`).

## Test server for the app

A throwaway server for the app's tests is a local tool beside the repository (not part of it): nextime with an empty
database on port 8591 (or a port given at the start), invented accounts on example.com, a pairing link and a device
token printed at the start. Each port keeps its own data and access file, so several such servers run side by side.
The app's build machine reaches it through an SSH tunnel to that port; the address in the pairing link is then
`http://localhost:<port>` (no HTTPS: the calendar account answers `409 https_required` there).

## Differences from nexnotes

Only where a calendar needs it:

1. **Writing goes through one queue** (`POST /ops` with `ref`), not one route per object: a calendar has no Yjs merge,
   so the order of the offline changes and "exactly once" per change matter; nexnotes' `sync` takes a batch too.
2. **Events are iCalendar text** with derived columns instead of a Yjs document; writes use the browser's fields
   (the same checks and limits, TIME-29 AC-8).
3. **Calendars, collections, conflicts and the account are complete on every page** of `changes` instead of numbered
   per object: they are few, and their rights change with shares; the cursor still changes with them. Gone events are
   named by `calendar_id` and `href` (an event keeps its `id` when it moves to another calendar).
4. **Conflicts with both versions** (`conflicts`, op `resolve`): nexnotes merges text and has none.
5. **Calendar account on the same device** (`/calendar-account`): nexnotes has no CalDAV.
6. **The browser's card "Geräte" stands in Settings › My account › On iPhone and co** (above the calendar account),
   not under Connections: TIME-29 AC-7 and TIMEIOS-4 AC-3 put it there. The QR dialog's step 2 names „Server
   hinzufügen“, the button of the app (nexnotes' web still says „Server verbinden“).
7. **No device token on the browser's routes** in api 1 (nexnotes takes it for attachments); attachments (0.5) and
   the member ways come as `/api/app/1/…` routes that do what the browser's do.
8. Token prefix `ntd_`, schemes `nextime://connect` and `nextime://connected`.
9. **A silent nudge on every change** of the account (`quiet`, 0.2): nexnotes sends only notifications with text;
   the calendar app keeps widgets and its copy current in the background (TIMEIOS-5 AC-12).
10. **`alerts` in `PUT /push`** (0.3): every nexnotes build had its extension from the start; nextime's builds before
    0.3 have none, so only a build that says so gets notifications with text. The message carries the fields to say
    it in the device's language besides the server's words (nexnotes: `who` and `note` for the app lock).
