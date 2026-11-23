"""Sharing calendars (TIME-19): with members, each with a right, and with guests through a read link.

**Members.** Only the owner shares (AC-8, AC-15): an own calendar to read or to edit (AC-1), a calendar from iCloud,
Google or another CalDAV server whole (to read, for the other one it is read only, EC-13) or as "busy only" (AC-2).
Subscriptions and holidays are not shared: everybody can subscribe them alike. A share that comes, changes its right
or ends marks every event of the calendar as changed, so the collection tag and the sync token move on and each
device syncs anew (TIME-20 EC-16, EC-5); an ended share takes the calendar out of the account's collections
(TIME-13 EC-1). The same person twice changes only the right, oneself never (EC-9).

**Notices.** When a calendar shared with somebody goes (the share ended, the calendar deleted, its source
disconnected, its owner's account deleted), the other one finds a notice under "Shared with you" (AC-14, EC-3,
EC-17, EC-18) until putting it away. Leaving a calendar oneself leaves none.

**Guest links** (AC-5): one per own calendar, while the operator allows them (AC-7). The token is 192 random bits;
the database keeps its hash to find it and the token sealed with the server's key, so the owner can copy the same
address again (a new one would end every guest's subscription). It holds until revoked or until the chosen last day
on the server's clock (EC-19); revoked and switched on again, the address is new (EC-20). What a guest sees is in
``services/guest.py``.
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import false, func, insert, literal, or_, select
from sqlalchemy.orm import Session

from ..errors import error
from ..models import (
    RIGHT_BUSY,
    RIGHT_EDIT,
    RIGHT_READ,
    RIGHTS,
    Account,
    Calendar,
    CalendarShare,
    Event,
    EventChange,
    GuestLink,
    ShareNotice,
    Source,
    UtcDateTime,
    utcnow,
)
from ..security import decrypt_secret, encrypt_secret, hash_token
from . import collections, display, live, reminders, settings_service, symbols

#: The kinds of a source whose calendars can be shared (whole or busy only, AC-2).
SHAREABLE_SOURCES = ("icloud", "google", "caldav")
#: What an owner may give for an own calendar, and for one from a source.
OWN_RIGHTS = (RIGHT_READ, RIGHT_EDIT)
SOURCE_RIGHTS = (RIGHT_READ, RIGHT_BUSY)
#: Most people a search offers at once.
PEOPLE_MAX = 8
TOKEN_CONTEXT = "guest-link"


def _everything_changed(db: Session, calendar_id: int) -> None:
    """Every event of the calendar counts as changed for the calendar account (sync token and collection tag move
    on, each device syncs anew). Nothing goes to the calendar's source: a share changes no event there, and sending
    the events back would overwrite what changed at the source meanwhile (review of TIME-19, finding 1). One statement
    for all events, so that 20,000 of them take a moment, not half a minute (finding 3)."""
    now = utcnow()
    db.execute(insert(EventChange).from_select(
        ["calendar_id", "href", "deleted", "created_at"],
        select(literal(calendar_id), Event.href, false(), literal(now, UtcDateTime()))
        .where(Event.calendar_id == calendar_id, Event.deleted_at.is_(None))))
    live.touch_calendar(db, calendar_id)
    # Who sees the calendar changed: whose reminders are due changes with it (TIME-7).
    reminders.touch(calendar_id=calendar_id)
    calendar = db.get(Calendar, calendar_id)
    if calendar is not None:
        calendar.props_rev += 1
        calendar.updated_at = utcnow()


def set_right(db: Session, calendar_id: int, account_id: int, right: str) -> CalendarShare:
    """Shares the calendar with the account, or changes the right of its share."""
    if right not in RIGHTS:
        raise error("invalid_right", "This right does not exist.", 422)
    row = db.get(CalendarShare, (calendar_id, account_id))
    if row is None:
        row = CalendarShare(calendar_id=calendar_id, account_id=account_id, right=right)
        db.add(row)
        # Shared again: the notice of the end before is no longer true.
        for notice in db.scalars(select(ShareNotice).where(ShareNotice.account_id == account_id,
                                                           ShareNotice.calendar_id == calendar_id)):
            db.delete(notice)
    elif row.right == right:
        return row
    else:
        row.right = right
    _everything_changed(db, calendar_id)
    live.touch_accounts(db, {account_id})
    db.commit()
    return row


def end(db: Session, calendar_id: int, account_id: int, *, notice: bool = False) -> None:
    """Takes the share back: the calendar goes from the account and its devices at their next sync; with ``notice``
    the account finds "… is no longer there" under "Shared with you" (AC-14)."""
    row = db.get(CalendarShare, (calendar_id, account_id))
    if row is None:
        return
    calendar = db.get(Calendar, calendar_id)
    if notice and calendar is not None:
        _notice(db, calendar, account_id)
    db.delete(row)
    collections.leave(db, account_id, calendar_id)
    from . import notify

    notify.share_ended(db, calendar_id, account_id)
    _everything_changed(db, calendar_id)
    live.touch_accounts(db, {account_id})
    db.commit()


# --- Who may share what ------------------------------------------------------------------------------------------


def _kind(db: Session, calendar: Calendar) -> str:
    """``own`` for nextime's own calendars, else the kind of its source."""
    if calendar.source_id is None:
        return "own"
    return str(db.scalar(select(Source.kind).where(Source.id == calendar.source_id)) or "")


def rights_for(kind: str) -> tuple[str, ...]:
    return OWN_RIGHTS if kind == "own" else SOURCE_RIGHTS if kind in SHAREABLE_SOURCES else ()


def _owned(db: Session, account: Account, calendar_id: int) -> tuple[Calendar, str]:
    """The calendar, when the account owns it and it can be shared. Somebody it is shared with gets 403, even with the
    right to edit (AC-8, AC-15); a calendar the account does not see, 404."""
    from . import calendars

    found = calendars.access(db, account.id, calendar_id)
    if found is None:
        raise error("not_found", "Not found.", 404)
    if found.right != calendars.OWNER:
        raise error("calendar_not_own", "Only the owner shares this calendar.", 403)
    kind = _kind(db, found.calendar)
    if not rights_for(kind):
        raise error("calendar_not_shareable", "Subscriptions and holidays are not shared.", 409)
    return found.calendar, kind


def _person_view(db: Session, person: Account) -> dict[str, Any]:
    gone = person.deleted_at is not None or person.blocked_at is not None
    return {"id": person.id, "name": person.shown_name, "email": person.email, "color": person.color,
            "avatar": None if gone or not person.avatar_at else person.avatar_at.isoformat()}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


# --- Members -----------------------------------------------------------------------------------------------------


def people(db: Session, account: Account, query: str) -> list[dict[str, Any]]:
    """Members by name or address, upper and lower case alike (EC-8); never the account itself, nor blocked or deleted
    ones. Two of the same name tell apart by the address."""
    text = (query or "").strip()
    if not text:
        return []
    folded = text.casefold()
    pattern = "%" + folded.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = db.scalars(
        select(Account).where(
            Account.id != account.id, Account.blocked_at.is_(None), Account.deleted_at.is_(None),
            or_(func.nx_fold(Account.display_name).like(pattern, escape="\\"),
                func.nx_fold(Account.name).like(pattern, escape="\\"),
                func.nx_fold(Account.email).like(pattern, escape="\\")))
        .order_by(func.nx_sort(Account.display_name), Account.id).limit(PEOPLE_MAX))
    return [_person_view(db, row) for row in rows]


def sharing(db: Session, account: Account, calendar_id: int, base_url: str) -> dict[str, Any]:
    """What the dialog "Share …" shows (AC-9, AC-10): the people with their right, the rights offered, the guest
    link of an own calendar. Recipients deleted by the operator are no longer listed (EC-17)."""
    calendar, kind = _owned(db, account, calendar_id)
    rows = db.execute(select(CalendarShare, Account).join(Account, Account.id == CalendarShare.account_id)
                      .where(CalendarShare.calendar_id == calendar.id, Account.deleted_at.is_(None))
                      .order_by(CalendarShare.created_at, Account.id)).all()
    allowed = bool(settings_service.get(db, "share_links"))
    link = db.scalar(select(GuestLink).where(GuestLink.calendar_id == calendar.id)) if kind == "own" else None
    return {
        "calendar": {"id": calendar.id, "name": calendar.name, "color": calendar.color,
                     "icon": symbols.shown(calendar.icon), "kind": kind},
        "rights": list(rights_for(kind)),
        "people": [{**_person_view(db, person), "right": share.right, "since": _iso(share.created_at)}
                   for share, person in rows],
        "links_allowed": allowed and kind == "own",
        "link": _link_view(link, base_url) if link is not None and allowed else None,
    }


def share(db: Session, account: Account, calendar_id: int, person_id: int, right: str) -> None:
    """Shares with a member, or changes the right; the same request twice makes one share (EC-9, EC-15)."""
    calendar, kind = _owned(db, account, calendar_id)
    if person_id == account.id:
        raise error("share_self", "You cannot share a calendar with yourself.", 422)
    if right not in rights_for(kind):
        raise error("invalid_right", "This right is not offered for this calendar.", 422, field="right")
    person = db.get(Account, person_id)
    if person is None or person.deleted_at is not None or person.blocked_at is not None:
        raise error("person_not_found", "This member does not exist.", 404)
    set_right(db, calendar.id, person.id, right)


def unshare(db: Session, account: Account, calendar_id: int, person_id: int) -> None:
    """The owner takes a share back (AC-3): at once, and the other one finds a notice (AC-14)."""
    calendar = _owned(db, account, calendar_id)[0]
    end(db, calendar.id, person_id, notice=True)


def leave(db: Session, account: Account, calendar_id: int) -> None:
    """Somebody leaves a calendar shared with it (AC-4): gone for it, for the owner nothing changes."""
    if db.get(CalendarShare, (calendar_id, account.id)) is None:
        raise error("not_found", "Not found.", 404)
    end(db, calendar_id, account.id)


# --- Notices ---------------------------------------------------------------------------------------------------------


def _notice(db: Session, calendar: Calendar, account_id: int, *, for_good: bool = False) -> None:
    owner = db.get(Account, calendar.owner_id)
    db.add(ShareNotice(account_id=account_id, calendar_id=None if for_good else calendar.id,
                       owner_id=calendar.owner_id,
                       calendar_name=calendar.name, owner_name=owner.shown_name if owner else "",
                       color=calendar.color))
    live.touch_accounts(db, {account_id})


def calendar_going(db: Session, calendar: Calendar, *, for_good: bool = False) -> None:
    """The calendar goes for everybody it is shared with (deleted, its source disconnected or no longer chosen, gone
    at the source, EC-3, EC-18): each finds a notice. The shares themselves stay with a calendar in the trash, so that
    restoring it brings them back (TIME-23); with a calendar removed for good (``for_good``) they go with it."""
    for account_id in db.scalars(select(CalendarShare.account_id).where(CalendarShare.calendar_id == calendar.id)):
        _notice(db, calendar, account_id, for_good=for_good)


def calendar_restored(db: Session, calendar_id: int) -> None:
    """The calendar came back from the trash (TIME-23): its shares and its guest link hold again, the notices of its
    end go (EC-18)."""
    for notice in db.scalars(select(ShareNotice).where(ShareNotice.calendar_id == calendar_id)):
        db.delete(notice)
    live.touch_calendar(db, calendar_id)


def _owner_deleted(db: Session, account_id: int) -> None:
    """``content.on_deleted``: the owner's shared calendars go for everybody with a notice, its guest links stop
    (EC-17); the shares stay for the 30 days the operator can bring the account back."""
    for calendar in db.scalars(select(Calendar).where(Calendar.owner_id == account_id,
                                                      Calendar.deleted_at.is_(None))):
        calendar_going(db, calendar)
        live.touch_calendar(db, calendar.id)
    db.commit()


def _owner_restored(db: Session, account_id: int) -> None:
    owned = select(Calendar.id).where(Calendar.owner_id == account_id, Calendar.deleted_at.is_(None))
    for notice in db.scalars(select(ShareNotice).where(ShareNotice.calendar_id.in_(owned))):
        db.delete(notice)
    db.commit()


def dismiss(db: Session, account: Account, notice_id: int) -> None:
    row = db.get(ShareNotice, notice_id)
    if row is None or row.account_id != account.id:
        raise error("not_found", "Not found.", 404)
    db.delete(row)
    db.commit()


# --- "Shares" under Settings › My account (AC-6) ---------------------------------------------------------------


def overview(db: Session, account: Account) -> dict[str, Any]:
    """Everything the account shares and everything shared with it, in one place: per own calendar the people with
    right and since when, and the guest link with since and until; per calendar shared with it the owner and the
    right, never the other people it is shared with (AC-16); and the notices of calendars that went (AC-14)."""
    from . import calendars

    mine: list[dict[str, Any]] = []
    allowed = bool(settings_service.get(db, "share_links"))
    owned = db.scalars(select(Calendar).where(Calendar.owner_id == account.id, Calendar.deleted_at.is_(None))
                       .order_by(func.nx_sort(Calendar.name), Calendar.id)).all()
    ids = [calendar.id for calendar in owned]
    shares_of: dict[int, list[tuple[CalendarShare, Account]]] = {}
    if ids:
        for share_row, person in db.execute(
                select(CalendarShare, Account).join(Account, Account.id == CalendarShare.account_id)
                .where(CalendarShare.calendar_id.in_(ids), Account.deleted_at.is_(None))
                .order_by(CalendarShare.created_at, Account.id)):
            shares_of.setdefault(share_row.calendar_id, []).append((share_row, person))
    links = {row.calendar_id: row for row in db.scalars(select(GuestLink).where(GuestLink.calendar_id.in_(ids)))} \
        if ids else {}
    kinds = {}
    for calendar in owned:
        kinds[calendar.id] = _kind(db, calendar) if calendar.source_id is not None else "own"
    for calendar in owned:
        head = {"id": calendar.id, "name": calendar.name, "color": calendar.color,
                "icon": symbols.shown(calendar.icon), "kind": kinds[calendar.id]}
        for share_row, person in shares_of.get(calendar.id, []):
            mine.append({"calendar": head, "kind": "person", "person": _person_view(db, person),
                         "right": share_row.right, "since": _iso(share_row.created_at)})
        link = links.get(calendar.id)
        if link is not None:
            mine.append({"calendar": head, "kind": "link", "since": _iso(link.created_at),
                         "until": link.expires_on or None, "active": allowed and _holds(link)})
    with_me = []
    for item in calendars.accesses(db, account.id):
        if item.right == calendars.OWNER:
            continue
        owner = db.get(Account, item.calendar.owner_id)
        with_me.append({"calendar": {"id": item.calendar.id, "name": item.calendar.name, "color": item.calendar.color,
                                     "kind": "own" if item.kind == "" else item.kind},
                        "owner": owner.shown_name if owner else "", "right": item.right})
    notices = [{"id": row.id, "calendar": row.calendar_name, "owner": row.owner_name, "color": row.color,
                "at": _iso(row.created_at)}
               for row in db.scalars(select(ShareNotice).where(ShareNotice.account_id == account.id)
                                     .order_by(ShareNotice.created_at.desc(), ShareNotice.id.desc()))]
    return {"mine": mine, "with_me": with_me, "notices": notices}


# --- Guest links (AC-5, AC-7, EC-1, EC-19 to EC-21) -----------------------------------------------------------------


def guest_path(token: str) -> str:
    return f"/abo/{token}"


def _link_view(link: GuestLink, base_url: str) -> dict[str, Any]:
    token = decrypt_secret(link.token_enc, TOKEN_CONTEXT)
    url = f"{base_url}{guest_path(token)}" if token else None
    return {"url": url, "feed": f"{url}.ics" if url else None, "since": _iso(link.created_at),
            "expires_on": link.expires_on or None}


def _holds(link: GuestLink, now: datetime | None = None) -> bool:
    return link.expires_at is None or (now or utcnow()) < link.expires_at


def _expiry(account: Account, expires_on: str | None) -> tuple[datetime | None, str]:
    """The chosen last day as the first instant it no longer holds; a day before tomorrow is refused (EC-19)."""
    if not expires_on:
        return None, ""
    try:
        day = date.fromisoformat(expires_on)
    except ValueError:
        raise error("invalid_date", "This date does not exist.", 422, field="expires_on") from None
    zone = display.zone_of(account)
    today = utcnow().astimezone(zone).date()
    if day < today:
        raise error("date_past", "Choose a day from today on.", 422, field="expires_on")
    end = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=zone)
    return end, day.isoformat()


def set_link(db: Session, account: Account, calendar_id: int, expires_on: str | None) -> None:
    """Switches the guest link on, or changes its last day; on only for an own calendar (AC-12) while the operator
    allows links (AC-7). The same request twice makes one link (EC-15)."""
    calendar, kind = _owned(db, account, calendar_id)
    if kind != "own":
        raise error("link_own_only", "Guest links are only for own calendars.", 409)
    if not settings_service.get(db, "share_links"):
        raise error("links_off", "The operator switched guest links off.", 403)
    expires_at, shown = _expiry(account, expires_on)
    row = db.scalar(select(GuestLink).where(GuestLink.calendar_id == calendar.id))
    if row is None:
        token = secrets.token_urlsafe(24)
        row = GuestLink(calendar_id=calendar.id, token_hash=hash_token(token),
                        token_enc=encrypt_secret(token, TOKEN_CONTEXT), created_by=account.id)
        db.add(row)
    row.expires_at, row.expires_on = expires_at, shown
    live.touch_accounts(db, {account.id})
    db.commit()


def revoke_link(db: Session, account: Account, calendar_id: int) -> None:
    """"Revoke link": the address stops at once, for the page and every subscription (EC-1)."""
    calendar = _owned(db, account, calendar_id)[0]
    row = db.scalar(select(GuestLink).where(GuestLink.calendar_id == calendar.id))
    if row is not None:
        db.delete(row)
    live.touch_accounts(db, {account.id})
    db.commit()


def renew_link(db: Session, account: Account, calendar_id: int) -> None:
    """A new address for a link whose stored token cannot be read any more (a backup restored with another secret
    key): the old address keeps working until then, the new one replaces it."""
    calendar = _owned(db, account, calendar_id)[0]
    row = db.scalar(select(GuestLink).where(GuestLink.calendar_id == calendar.id))
    if row is None:
        raise error("not_found", "Not found.", 404)
    token = secrets.token_urlsafe(24)
    row.token_hash, row.token_enc = hash_token(token), encrypt_secret(token, TOKEN_CONTEXT)
    db.commit()


def find_link(db: Session, token: str) -> tuple[GuestLink, Calendar, Account] | None:
    """The link behind a token while it holds: the operator allows links (AC-7), not revoked, not run out (EC-1,
    EC-19), its calendar not in the trash (EC-18) and its owner not deleted (EC-17; a blocked owner's link holds)."""
    if not token or len(token) > 100:
        return None
    link = db.scalar(select(GuestLink).where(GuestLink.token_hash == hash_token(token)))
    if link is None or not _holds(link) or not settings_service.get(db, "share_links"):
        return None
    calendar = db.get(Calendar, link.calendar_id)
    if calendar is None or calendar.deleted_at is not None or calendar.source_id is not None:
        return None
    owner = db.get(Account, calendar.owner_id)
    if owner is None or owner.deleted_at is not None:
        return None
    return link, calendar, owner


def register() -> None:
    from . import content

    if _owner_deleted not in content._deleted:
        content.on_deleted(_owner_deleted)
    if _owner_restored not in content._restored:
        content.on_restored(_owner_restored)


register()
