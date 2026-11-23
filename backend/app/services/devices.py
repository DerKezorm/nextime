"""Devices with the calendar account (TIME-20): their passwords, the setup link behind the QR code, the profile for
Apple devices, and who a request to ``/dav/`` comes from.

A device password is random (four groups of four letters and digits, about 82 bits), so a plain SHA-256 is enough to
keep it, like a key; it never counts anywhere but the calendar account (EC-17). The setup link holds the password
encrypted with the server's key for ten minutes or until it is used once (AC-8, AC-13), then it is gone.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from datetime import timedelta
from typing import Any
from xml.sax.saxutils import escape

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..errors import error
from ..models import Account, Device, DeviceSetup, utcnow
from ..security import account_out, decrypt_secret, encrypt_secret, hash_token, new_token

SETUP_MINUTES = 10
NAME_MAX = 80
_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"
#: Last use is written at most this often: a device asks many times in one sync.
_TOUCH_SECONDS = 60


def new_password() -> str:
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(4))


def password_hash(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def clean_name(raw: str) -> str:
    """A name is needed; umlauts, emoji and markup stay as typed text; two devices may share one (EC-9)."""
    name = (raw or "").strip()
    if not name:
        raise error("device_name_missing", "Give the device a name.", 422, field="name")
    if len(name) > NAME_MAX:
        raise error("device_name_too_long", f"At most {NAME_MAX} characters.", 422, field="name", max=NAME_MAX)
    return name


def create(db: Session, account: Account, name: str) -> tuple[Device, str, str]:
    """A device with its own password, and a setup link for it. Returns the device, the password (shown this once,
    AC-7) and the link's token."""
    password = new_password()
    device = Device(account_id=account.id, name=clean_name(name), password_hash=password_hash(password))
    db.add(device)
    db.flush()
    token = new_token()
    db.add(DeviceSetup(token_hash=hash_token(token), account_id=account.id, device_id=device.id,
                       password_enc=encrypt_secret(password, f"device-setup:{device.id}"),
                       expires_at=utcnow() + timedelta(minutes=SETUP_MINUTES)))
    db.commit()
    return device, password, token


def setup_of(db: Session, token: str) -> DeviceSetup:
    """The setup behind a link: unknown, used before (AC-13) and run out (EC-6) say so."""
    row = db.scalar(select(DeviceSetup).where(DeviceSetup.token_hash == hash_token(token or "")))
    if row is None:
        raise error("setup_invalid", "This setup link is not valid.", 404)
    if row.used_at is not None:
        raise error("setup_used", "This setup link was used already.", 410)
    if row.expires_at <= utcnow():
        row.password_enc = ""
        db.commit()
        raise error("setup_expired", "This setup link has expired.", 410)
    account = db.get(Account, row.account_id)
    device = db.get(Device, row.device_id) if row.device_id else None
    if account is None or account_out(account) or device is None:
        raise error("setup_invalid", "This setup link is not valid.", 404)
    return row


def use(db: Session, token: str, name: str | None) -> tuple[Account, Device, str]:
    """The device behind the link (renamed when a name comes along) and its password. Loading the profile again
    within the ten minutes is fine (Safari asks first, one may tap "Ignore"); the link is spent once the device
    signs in to the calendar account for the first time (``authenticate``), and never makes a second device."""
    row = setup_of(db, token)
    device = db.get(Device, row.device_id)
    account = db.get(Account, row.account_id)
    assert device is not None and account is not None
    password = decrypt_secret(row.password_enc, f"device-setup:{device.id}")
    if name is not None and name.strip():
        device.name = clean_name(name)
    db.commit()
    return account, device, password


def spend_setups(db: Session, device_id: int) -> None:
    """The device signed in: its setup link is used, and the password it carried goes (AC-13)."""
    for row in db.scalars(select(DeviceSetup).where(DeviceSetup.device_id == device_id,
                                                    DeviceSetup.used_at.is_(None))):
        row.used_at = utcnow()
        row.password_enc = ""


def forget_expired(db: Session) -> None:
    db.execute(delete(DeviceSetup).where(DeviceSetup.expires_at <= utcnow() - timedelta(days=1)))


def lock_out(db: Session, account: Account, device_id: int) -> None:
    """Locked out: the device gets nothing any more, at once; the others go on (AC-5, EC-2)."""
    device = db.get(Device, device_id)
    if device is None or device.account_id != account.id:
        raise error("not_found", "Not found.", 404)
    db.delete(device)
    db.commit()


def program_of(user_agent: str) -> str:
    """What the program calls itself, in words people know."""
    agent = user_agent or ""
    lowered = agent.lower()
    if "davx5" in lowered or "davdroid" in lowered:
        return "DAVx5"
    if "thunderbird" in lowered:
        return "Thunderbird"
    if "dataaccessd" in lowered or "ios/" in lowered or "iphone" in lowered or "ipad" in lowered:
        return "Apple Calendar"
    if "calendaragent" in lowered or "macos" in lowered or "mac os" in lowered or "mac+os" in lowered:
        return "Apple Calendar"
    if "evolution" in lowered:
        return "Evolution"
    return agent.split(" ")[0].split("/")[0][:40]


def platform_of(user_agent: str, name: str = "") -> str:
    """``iphone``, ``ipad`` or ``mac`` for Apple Calendar there (it reminds by itself, TIME-7 AC-7); "" for every other
    program. An iPad's calendar calls itself "iOS" like an iPhone's: then the device's name decides (review of TIME-7,
    finding 3)."""
    lowered = (user_agent or "").lower()
    if program_of(user_agent) != "Apple Calendar":
        return ""
    if "ipad" in lowered:
        return "ipad"
    if "iphone" in lowered:
        return "iphone"
    if "macos" in lowered or "mac os" in lowered or "mac+os" in lowered or "calendaragent" in lowered:
        return "mac"
    if "ios/" in lowered or "dataaccessd" in lowered:
        return "ipad" if "ipad" in (name or "").lower() else "iphone"
    return ""


def authenticate(db: Session, header: str, user_agent: str) -> tuple[Account, Device] | None:
    """The account and device behind ``Authorization: Basic``, or ``None``. The name may be the account's name or
    its address; a blocked or deleted account gets nothing (EC-18)."""
    if header[:6].lower() != "basic ":
        return None
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    user, _, password = decoded.partition(":")
    if not password:
        return None
    device = db.scalar(select(Device).where(Device.password_hash == password_hash(password)))
    if device is None:
        return None
    account = db.get(Account, device.account_id)
    if account is None or account_out(account):
        return None
    if user.strip().casefold() not in {account.name.casefold(), (account.email or "").casefold()} - {""}:
        return None
    now = utcnow()
    if device.last_used_at is None:
        spend_setups(db, device.id)
    program = program_of(user_agent)
    platform = platform_of(user_agent, device.name)
    if device.last_used_at is None or (now - device.last_used_at).total_seconds() > _TOUCH_SECONDS or (
            program and device.client != program) or (platform and device.platform != platform):
        device.last_used_at = now
        if program:
            device.client = program
        if platform:
            device.platform = platform
        db.commit()
    return account, device


def listing(db: Session, account: Account) -> list[dict[str, Any]]:
    rows = db.scalars(select(Device).where(Device.account_id == account.id).order_by(Device.created_at, Device.id))
    return [{"id": row.id, "name": row.name, "client": row.client, "created_at": row.created_at.isoformat(),
             "last_used_at": row.last_used_at.isoformat() if row.last_used_at else None} for row in rows]


# --- The profile for Apple devices (AC-2) ----------------------------------------------------------------------------


def mobileconfig(*, host: str, port: int, https: bool, base: str, account: Account, device: Device, password: str,
                 description: str) -> bytes:
    """An unsigned configuration profile with one CalDAV account. iOS shows it as "not verified", which is usual
    for such profiles; the setup page says so."""
    payload_id = f"nextime.caldav.{uuid.uuid4()}"
    values = {
        "host": escape(host),
        "port": port,
        "ssl": "<true/>" if https else "<false/>",
        "user": escape(account.name),
        "password": escape(password),
        "principal": escape(f"{base}/dav/principals/{account.name}/"),
        "description": escape(description),
        "device": escape(device.name),
        "inner": str(uuid.uuid4()).upper(),
        "outer": str(uuid.uuid4()).upper(),
        "payload_id": escape(payload_id),
    }
    text = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>PayloadContent</key>
  <array>
    <dict>
      <key>CalDAVAccountDescription</key><string>{values["description"]}</string>
      <key>CalDAVHostName</key><string>{values["host"]}</string>
      <key>CalDAVPort</key><integer>{values["port"]}</integer>
      <key>CalDAVUseSSL</key>{values["ssl"]}
      <key>CalDAVUsername</key><string>{values["user"]}</string>
      <key>CalDAVPassword</key><string>{values["password"]}</string>
      <key>CalDAVPrincipalURL</key><string>{values["principal"]}</string>
      <key>PayloadDescription</key><string>{values["description"]}</string>
      <key>PayloadDisplayName</key><string>{values["description"]}</string>
      <key>PayloadIdentifier</key><string>{values["payload_id"]}.account</string>
      <key>PayloadType</key><string>com.apple.caldav.account</string>
      <key>PayloadUUID</key><string>{values["inner"]}</string>
      <key>PayloadVersion</key><integer>1</integer>
    </dict>
  </array>
  <key>PayloadDescription</key><string>{values["device"]}</string>
  <key>PayloadDisplayName</key><string>{values["description"]}</string>
  <key>PayloadIdentifier</key><string>{values["payload_id"]}</string>
  <key>PayloadRemovalDisallowed</key><false/>
  <key>PayloadType</key><string>Configuration</string>
  <key>PayloadUUID</key><string>{values["outer"]}</string>
  <key>PayloadVersion</key><integer>1</integer>
</dict>
</plist>
"""
    return text.encode("utf-8")
