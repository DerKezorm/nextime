"""The shared module geraete (``vendor/nexgeraete``, bauplaene/geraete 0.1.4) in nextime: pairing the iPhone and iPad
app, its devices, the notifications through the push relay (``docs/vertrag-app1.md``, TIME-29).

The module keeps no table of its own: ``Store`` translates its calls to nextime's ``app_devices``, ``app_pairings``,
``app_codes`` and reads ``app_gone_devices`` (G31, G42). Keys of before stay as they are (``ntd_`` and SHA-256 hex);
rows of before keep ``NULL`` in the module's columns. The published app (TestFlight) speaks the form ``nex``; its
``alerts`` in ``PUT /push`` becomes what it understands (``changed`` and ``changes``, G16), so it keeps getting the
changes of shared calendars.

A device rests while its account is blocked or deleted with its days to come back (G26): ``Store.holder`` says
``out`` and the module answers ``account_blocked``. Disconnecting (in the browser, in the app, "sign out everywhere",
a new password, the purge) removes the row.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..models import (
    OPERATOR,
    Account,
    AppCode,
    AppDevice,
    AppGoneDevice,
    AppGoneSibling,
    AppPairing,
    Device,
    utcnow,
)
from ..security import account_out
from ..vendor import nexgeraete
from ..vendor.nexgeraete import AppConfig, PushKind
from ..vendor.nexgeraete.model import (
    ConnectCode,
    DeviceChange,
    Holder,
    NewDevice,
    NewPairing,
    Pairing,
    PushState,
)
from ..vendor.nexgeraete.model import Device as ModuleDevice
from . import live, settings_service, totp

logger = logging.getLogger("nextime.app")

#: The kinds of notification of nextime. ``changed`` is the silent nudge every device with a ticket got since
#: contract 0.2 (the app syncs in the background); ``changes`` the notification with text about changes of others in
#: shared calendars (contract 0.3, before ``alerts``).
KIND_CHANGED = "changed"
KIND_CHANGES = "changes"
KINDS = (
    PushKind(
        id=KIND_CHANGED,
        title={"de": "Abgleich im Hintergrund", "en": "Background sync", "fr": "Synchronisation en arrière-plan"},
        hint={
            "de": "Holt Änderungen sofort aufs Gerät, ohne etwas anzuzeigen.",
            "en": "Brings changes to the device at once without showing anything.",
            "fr": "Apporte les modifications sur l'appareil tout de suite, sans rien afficher.",
        },
    ),
    PushKind(
        id=KIND_CHANGES,
        title={
            "de": "Änderungen in geteilten Kalendern",
            "en": "Changes in shared calendars",
            "fr": "Modifications dans les calendriers partagés",
        },
        hint={
            "de": "Wenn jemand anderes Termine in einem geteilten Kalender ändert, für den du Mitteilungen an hast.",
            "en": "When someone else changes events in a shared calendar you have notifications on for.",
            "fr": "Lorsque quelqu'un d'autre modifie des événements d'un calendrier partagé pour lequel tu as "
            "activé les notifications.",
        },
    ),
)
#: What a device of the published app understands that said ``alerts``; without it only ``changed`` (G16).
ALERTS = (KIND_CHANGED, KIND_CHANGES)

#: The operator's switches under nextime's names (the same as in ``schluessel_store.SWITCHES``).
SWITCHES = {"app": "app_enabled", "pairing": "app_pairing_enabled"}

#: The browser routes of the module: beside the old ones under ``/api`` (``routers/app_pairing.py``), whose answers
#: the published web page and its tests know, the cards of the module call them here.
BROWSER_PREFIX = "/api/geraete"


def _relay() -> str:
    from . import apppush

    return apppush.relay_url()


def _server_id() -> str:
    from . import appdevices

    return appdevices.server_id()


CONFIG = AppConfig(
    app="nextime",
    scheme="nextime",
    token_prefix="ntd_",
    form="nex",
    relay_url=_relay,
    push_info="nextime-push-v1",
    kinds=KINDS,
    legacy_kinds=(KIND_CHANGED,),
    server_id=_server_id,
    app_store_url="",
)

#: How far the module's clock is ahead of the real one (the contract tests move it; never in operation).
OFFSET = timedelta(0)


def now() -> datetime:
    return utcnow() + OFFSET


def send(url: str, body: dict[str, Any]) -> int:
    """What posts to the relay: ``apppush.POST``, which the tests replace by a recorder."""
    from . import apppush

    return apppush.POST(url, body)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def understands(row: AppDevice) -> tuple[str, ...] | None:
    """The kinds the device said it understands; the published app: ``None`` (``changed``), or with ``alerts``
    ``changed`` and ``changes``."""
    said = _json(row.push_understands)
    if isinstance(said, list):
        return tuple(str(kind) for kind in said)
    return ALERTS if row.push_alerts else None


def device_of(row: AppDevice) -> ModuleDevice:
    kinds = _json(row.push_kinds)
    return ModuleDevice(
        id=row.id,
        account_id=row.account_id,
        name=row.name,
        kind=row.kind or None,
        model=row.model,
        system=row.system,
        app_version=row.app_version,
        via=row.via,
        created_at=_aware(row.created_at),
        last_seen_at=_aware(row.last_used_at),
        # G61: ``push_alerts``: the kinds come from nextime's older form ``alerts``, not from the device.
        push=PushState(row.push_ticket, row.push_key, understands(row), chosen=False if row.push_alerts else None)
        if row.push_ticket else None,
        kinds={str(key): bool(value) for key, value in kinds.items()} if isinstance(kinds, dict) else {},
    )


def language_of(account: Account) -> str:
    chosen = (account.language or "").split("-")[0].lower()
    if chosen in nexgeraete.config.LANGUAGES:
        return chosen
    return "de" if (account.browser_language or "").lower().startswith("de") else "en"


def holder_of(db: Session, account: Account) -> Holder:
    from .appsync import account_out as named

    shown = named(account)
    return Holder(
        id=account.id,
        name=shown["name"],
        login=shown["login"],
        email=shown["email"],
        avatar=shown["avatar"],
        operator=account.role == OPERATOR,
        out=account_out(account),
        language=language_of(account),
        factor_owed=totp.setup_required(db, account),
    )


def _pairing(row: AppPairing) -> Pairing:
    return Pairing(
        id=row.id,
        account_id=row.account_id,
        secret_hash=row.secret_hash,
        code_hash=row.code_hash or "",
        created_at=_aware(row.created_at) or now(),
        expires_at=_aware(row.expires_at) or now(),
        used_at=_aware(row.used_at),
        device_id=row.device_id,
    )


class Store:
    """The module's ``Store`` (and ``GoneStore``) over nextime's tables, one database session per request."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- devices ---------------------------------------------------------------------------------------------------

    def devices_of(self, account_id: Any) -> list[ModuleDevice]:
        rows = self.db.scalars(
            select(AppDevice).where(AppDevice.account_id == int(account_id)).order_by(AppDevice.id.desc())
        )
        return [device_of(row) for row in rows]

    def device(self, device_id: Any) -> ModuleDevice | None:
        row = self.db.get(AppDevice, int(device_id))
        return device_of(row) if row is not None else None

    def device_by_hash(self, token_hash: str) -> ModuleDevice | None:
        row = self.db.scalar(select(AppDevice).where(AppDevice.token_hash == token_hash))
        return device_of(row) if row is not None else None

    def add_device(self, new: NewDevice) -> ModuleDevice:
        row = AppDevice(
            account_id=int(new.account_id),
            name=new.name[:100],
            kind=new.kind,
            token_hash=new.token_hash,
            via_id=int(new.via_device) if new.via_device is not None else None,
            created_at=new.created_at,
            model=(new.model or None) and new.model[:64],
            system=(new.system or None) and new.system[:64],
            app_version=(new.app_version or None) and new.app_version[:32],
            via=new.via,
        )
        self.db.add(row)
        self.db.flush()
        logger.info("App device paired device_id=%s kind=%s via=%s", row.id, row.kind, row.via)
        return device_of(row)

    def change_device(self, device_id: Any, change: DeviceChange) -> ModuleDevice:
        row = self.db.get(AppDevice, int(device_id))
        if row is None:
            raise LookupError(device_id)
        if change.name is not None:
            row.name = change.name[:100]
        if change.model is not None:
            row.model = change.model[:64]
        if change.system is not None:
            row.system = change.system[:64]
        if change.app_version is not None:
            row.app_version = change.app_version[:32]
        self.db.flush()
        return device_of(row)

    def remove_device(self, device_id: Any) -> None:
        """The key goes with its push ticket. A calendar account set up from the device stays and gets its alarms
        again (TIMEIOS-4 EC-15); a ``wait`` of the device going on right now ends at once. A device that came in over
        iCloud leaves when it was made, for the limit of the hour (``sibling_times``, geraete 0.1.5)."""
        row = self.db.get(AppDevice, int(device_id))
        if row is None:
            return
        account_id = row.account_id
        hour_ago = now() - timedelta(hours=1)
        self.db.execute(delete(AppGoneSibling).where(AppGoneSibling.created_at < hour_ago))
        made = _aware(row.created_at)
        if row.via == "icloud" and made is not None and made >= hour_ago:
            self.db.add(AppGoneSibling(account_id=account_id, created_at=made))
        self.db.execute(update(Device).where(Device.app_device_id == row.id).values(app_device_id=None))
        self.db.delete(row)
        self.db.flush()
        live.touch_accounts(self.db, {account_id})
        logger.info("App device disconnected device_id=%s", device_id)

    def sibling_times(self, account_id: Any, since: datetime) -> list[datetime]:
        """geraete 0.1.5 (G33 anew): when each device of the account that came in over iCloud at or after ``since``
        was made, the ones disconnected since included."""
        owner = int(account_id)
        living = self.db.scalars(select(AppDevice.created_at).where(
            AppDevice.account_id == owner, AppDevice.via == "icloud", AppDevice.created_at >= since))
        gone = self.db.scalars(select(AppGoneSibling.created_at).where(
            AppGoneSibling.account_id == owner, AppGoneSibling.created_at >= since))
        return [moment for moment in (_aware(at) for at in [*living, *gone]) if moment is not None]

    def touch(self, device_id: Any, when: datetime) -> None:
        self.db.execute(update(AppDevice).where(AppDevice.id == int(device_id)).values(last_used_at=when))

    def idle_devices(self, before: datetime) -> list[ModuleDevice]:
        last = func.coalesce(AppDevice.last_used_at, AppDevice.created_at)
        return [device_of(row) for row in self.db.scalars(select(AppDevice).where(last < before))]

    # --- accounts --------------------------------------------------------------------------------------------------

    def holder(self, account_id: Any) -> Holder | None:
        row = self.db.get(Account, int(account_id))
        return holder_of(self.db, row) if row is not None else None

    def operators(self) -> list[Any]:
        return list(self.db.scalars(select(Account.id).where(Account.role == OPERATOR).order_by(Account.id)))

    # --- pairings --------------------------------------------------------------------------------------------------

    def add_pairing(self, new: NewPairing) -> Pairing:
        self.db.execute(delete(AppPairing).where(AppPairing.expires_at < new.created_at - timedelta(days=1)))
        row = AppPairing(
            id=new.id,
            account_id=int(new.account_id),
            secret_hash=new.secret_hash,
            code_hash=new.code_hash,
            created_at=new.created_at,
            expires_at=new.expires_at,
        )
        self.db.add(row)
        self.db.flush()
        return _pairing(row)

    def pairing(self, pairing_id: str) -> Pairing | None:
        row = self.db.get(AppPairing, pairing_id[:32])
        return _pairing(row) if row is not None else None

    def pairing_by_code(self, code_hash: str) -> Pairing | None:
        row = self.db.scalar(select(AppPairing).where(AppPairing.code_hash == code_hash))
        return _pairing(row) if row is not None else None

    def open_pairings(self, account_id: Any, when: datetime) -> list[Pairing]:
        rows = self.db.scalars(
            select(AppPairing).where(
                AppPairing.account_id == int(account_id), AppPairing.used_at.is_(None), AppPairing.expires_at > when
            )
        )
        return [_pairing(row) for row in rows]

    def use_pairing(self, pairing_id: str, device_id: Any, when: datetime) -> bool:
        hit = self.db.execute(
            update(AppPairing)
            .where(AppPairing.id == pairing_id, AppPairing.used_at.is_(None), AppPairing.expires_at > when)
            .values(used_at=when, device_id=int(device_id) if device_id is not None else None)
        ).rowcount
        return bool(hit)

    def remove_pairing(self, pairing_id: str) -> None:
        self.db.execute(delete(AppPairing).where(AppPairing.id == pairing_id))

    def add_connect_code(self, new: ConnectCode) -> None:
        self.db.execute(delete(AppCode).where(AppCode.expires_at < new.created_at - timedelta(days=1)))
        self.db.add(
            AppCode(
                code_hash=new.code_hash,
                account_id=int(new.account_id),
                state=new.state,
                challenge=new.challenge,
                created_at=new.created_at,
                expires_at=new.expires_at,
            )
        )
        self.db.flush()

    def take_connect_code(self, code_hash: str, when: datetime) -> ConnectCode | None:
        hit = self.db.execute(
            update(AppCode)
            .where(AppCode.code_hash == code_hash, AppCode.used_at.is_(None), AppCode.expires_at > when)
            .values(used_at=when)
        ).rowcount
        if not hit:
            return None
        row = self.db.get(AppCode, code_hash)
        if row is None:
            return None
        return ConnectCode(
            code_hash=row.code_hash,
            account_id=row.account_id,
            state=row.state,
            challenge=row.challenge,
            created_at=_aware(row.created_at) or when,
            expires_at=_aware(row.expires_at) or when,
        )

    # --- push ------------------------------------------------------------------------------------------------------

    def set_push(self, device_id: Any, state: PushState | None) -> None:
        row = self.db.get(AppDevice, int(device_id))
        if row is None:
            return
        if state is None:
            row.push_ticket, row.push_key, row.push_alerts, row.push_understands = "", "", False, None
        else:
            # G61: what nextime set from ``alerts`` (``chosen`` False) is kept as ``alerts``, as before 0.1.0.
            from_alerts = state.chosen is False and state.understands is not None and tuple(state.understands) == ALERTS
            row.push_ticket, row.push_key, row.push_alerts = state.ticket, state.key, from_alerts
            row.push_understands = (json.dumps(list(state.understands))
                                    if state.understands is not None and not from_alerts else None)
        self.db.flush()

    def set_kinds(self, device_id: Any, kinds: dict[str, bool]) -> None:
        row = self.db.get(AppDevice, int(device_id))
        if row is not None:
            row.push_kinds = json.dumps(kinds, sort_keys=True)
            self.db.flush()

    # --- the rest --------------------------------------------------------------------------------------------------

    def switch(self, name: str) -> bool:
        return bool(settings_service.get(self.db, SWITCHES[name]))

    def gone_account(self, token_hash: str) -> Any:
        row = self.db.get(AppGoneDevice, token_hash)
        return row.account_id if row is not None else None

    def commit(self) -> None:
        self.db.commit()

    def rollback(self) -> None:
        self.db.rollback()


def store() -> Iterator[Store]:
    """The dependency of the module's routes: one session per request."""
    with SessionLocal() as db:
        yield Store(db)


def remove_all(db: Session, account_id: int) -> int:
    """Every device of the account goes ("sign out everywhere", a new password, "disconnect all", the purge); the
    caller commits."""
    data = Store(db)
    found = data.devices_of(account_id)
    for device in found:
        data.remove_device(device.id)
    return len(found)


# --- What the module hands back to nextime -------------------------------------------------------------------------


def on_paired(holder: Holder, device: ModuleDevice) -> None:
    """After a new device (G29, G50): the mail "Neue Anmeldung" as after a sign-in from a browser not known yet, unless
    the person switched it off; it names the device ("„Kims iPhone“ (iPhone 16 Pro)"), not a browser."""
    from ..vendor import nexpost
    from ..vendor.nexkonten import sessions
    from . import kontenpost, mailer
    from .konten_store import KontenStore
    from .post_store import PostStore

    with SessionLocal() as db:
        row = db.get(Account, int(holder.id))
        if row is None:
            return
        konto = KontenStore(db).get_account(row.id)
        mail = sessions.new_sign_in(konto, known=False, wanted=bool(row.new_sign_in_mail)) if konto else None
        if mail is None or not mailer.configured(db):
            return
        try:
            letter = nexgeraete.notices.letter(
                nexpost.letters, mail, device, language=mailer.language_for(db, row),
                link=kontenpost.link_for(db, mail), time_zone=kontenpost.zone_for(row),
            )
        except (nexpost.MailError, ValueError) as exc:
            logger.warning("Mail about a new device not queued reason=%s", type(exc).__name__)
            return
        if letter is None:
            return
        nexpost.outbox.queue(PostStore(db), letter)
        db.commit()
        logger.info("Mail about a new device queued name=%s", row.name)


def update_wanted() -> bool:
    """For ``updates`` (``devices_want``, U24, G56): a device of an operator wants to hear of new versions. Only then
    the background looks, at most once a day and only while the operator allows the check."""
    with SessionLocal() as db:
        return nexgeraete.notices.update_wanted(Store(db), CONFIG)


def newer(version: str) -> None:
    """For ``updates`` (``on_devices``, U24): a newer nextime; the operators' devices that want ``update`` hear it
    once, with the running version and the way to "Was ist neu"."""
    from .. import __version__

    # The tag without its "v", as the bell of the other apps names it (``nexupdates.notice``).
    shown = version.strip().removeprefix("v")
    with SessionLocal() as db:
        link = settings_service.public_url(db)
        told = nexgeraete.notices.newer(
            Store(db), CONFIG, shown, __version__, f"{link}/settings/about" if link else "", send=send
        )
    logger.info("Newer version told to devices sent=%s", told.sent)


def sweep() -> None:
    """The daily run (G27): a device not used for 365 days is disconnected."""
    with SessionLocal() as db:
        gone = nexgeraete.devices.sweep(Store(db), CONFIG, now())
    if gone:
        logger.info("App devices unused for a year disconnected count=%s", gone)


def register_sweep() -> None:
    from . import cleanup

    if not any(name == "app-devices" for name, _job in cleanup._jobs):
        cleanup.register("app-devices", sweep, every=24 * 3600)
