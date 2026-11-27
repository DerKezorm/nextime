"""geraete 0.1.5 (G33 anew): nextime's ``Store.sibling_times`` counts the devices that came in over iCloud within the
hour, the ones disconnected since included (table ``app_gone_siblings``, made by ``create_all`` in a database of
before); an account deleted for good leaves nothing of them. The module's contract case is in ``tests/nexgeraete``."""

from __future__ import annotations

import secrets
from datetime import timedelta

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Account, AppDevice, AppGoneSibling, utcnow
from app.services import accounts, geraete

from .conftest import make_account


def _device(account_id: int, via: str, made_ago: timedelta) -> int:
    with SessionLocal() as db:
        row = AppDevice(account_id=account_id, name="iPhone", kind="ios", token_hash=secrets.token_hex(32), via=via,
                        created_at=utcnow() - made_ago)
        db.add(row)
        db.commit()
        return row.id


def test_disconnected_siblings_count_for_the_hour_and_older_rows_go() -> None:
    anna = make_account("anna")
    fresh = _device(anna.id, "icloud", timedelta(minutes=5))
    paired = _device(anna.id, "qr", timedelta(minutes=5))
    old = _device(anna.id, "icloud", timedelta(hours=3))
    with SessionLocal() as db:
        store = geraete.Store(db)
        for device_id in (fresh, paired, old):
            store.remove_device(device_id)
        db.commit()
        since = utcnow() - timedelta(hours=1)
        # Only the one from iCloud within the hour counts; the one paired by QR code never did.
        assert len(geraete.Store(db).sibling_times(anna.id, since)) == 1
        # One made three hours ago is not kept, and rows older than an hour go with the next disconnection.
        kept = db.scalars(select(AppGoneSibling.created_at).where(AppGoneSibling.account_id == anna.id)).all()
        assert len(kept) == 1


def test_an_account_deleted_for_good_leaves_no_siblings() -> None:
    anna = make_account("anna")
    _device(anna.id, "icloud", timedelta(minutes=5))
    with SessionLocal() as db:
        row = db.get(Account, anna.id)
        assert row is not None
        row.deleted_at = utcnow() - timedelta(days=31)
        db.commit()
        assert accounts.purge_deleted(db) == 1
    with SessionLocal() as db:
        assert db.scalars(select(AppGoneSibling).where(AppGoneSibling.account_id == anna.id)).first() is None
