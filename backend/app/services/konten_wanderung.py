"""What a database of 0.4.0 needs for the shared account module beyond new columns and tables, at every start after
``db.init_db`` made those (with a backup before, ``db._add_missing_columns``). Only adding, never renaming or deleting;
a second start finds nothing to do.

- Every session of 0.4.0 gets the id the list "Where you are signed in" names it by.
- Every account of 0.4.0 gets its name of then as ``dav_name``, the calendar account's fixed name in every CalDAV
  address: each address a calendar program keeps stays the same for good, also after a rename. Once set, never
  touched again.
- Usernames are unique without regard to case in the database too, as the module asks (two changes at the same moment
  never both get through). 0.4.0 always wrote them in lower case, so the index fits every database it made; should a
  database hold two names that differ only in case anyway, the index is left out with a warning and the start goes on
  (both still sign in with the name written exactly).
- Addresses get an index without regard to case, **not** a unique one: 0.4.0 could leave one address at two accounts
  (two profile saves at the same moment), and such a pair signs in by name only (the module, rule 3 of the draft).
- mcp 1.1.0 (decision 5): at the start that makes the table ``mcp_keys`` on a database that has accounts, the switch
  "MCP-Zugang" is written down as on where nothing was stored, before its default becomes off.
- konten 1.1.0 (E23): at the start that adds the column ``accounts.theme`` (exactly once), every address that counts
  becomes proven, so the box "Address not confirmed yet" shows only for an address entered from now on.
"""

from __future__ import annotations

import logging
import secrets

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger("nextime.db")


def run(engine: Engine, *, added: list[str] | tuple[str, ...] = (), older_without_mcp: bool = False) -> None:
    with engine.begin() as connection:
        if older_without_mcp:
            # The shared module mcp (decision 5): "MCP-Zugang" is off on a new installation. 0.4.0 had it on from the
            # start without storing it; such an installation keeps it on, written down before the default turns.
            stored = connection.execute(text("SELECT 1 FROM settings WHERE key = 'mcp_enabled'")).first()
            if stored is None:
                connection.execute(text("INSERT INTO settings (key, value) VALUES ('mcp_enabled', 'true')"))
                logger.info("MCP stays on as before the update (it was never switched)")
        if "accounts.theme" in added:
            proven = connection.execute(
                text("UPDATE accounts SET email_proven = 1 WHERE email IS NOT NULL AND email != '' "
                     "AND email_confirmed = 1 AND email_proven = 0")
            ).rowcount
            logger.info("Addresses of before konten 1.1.0 count as proven count=%s", proven or 0)
        missing = [row[0] for row in connection.execute(text("SELECT id FROM auth_sessions WHERE uid IS NULL"))]
        for session_id in missing:
            connection.execute(text("UPDATE auth_sessions SET uid = :uid WHERE id = :id"),
                               {"uid": secrets.token_hex(12), "id": session_id})
        if missing:
            logger.info("Sessions of an older version named for the session list count=%s", len(missing))

        named = connection.execute(text("UPDATE accounts SET dav_name = name WHERE dav_name = '' OR dav_name IS NULL"))
        if named.rowcount:
            logger.info("Accounts of an older version keep their calendar addresses count=%s", named.rowcount)

        doubles = connection.execute(
            text("SELECT lower(name) FROM accounts GROUP BY lower(name) HAVING count(*) > 1")
        ).first()
        if doubles is None:
            connection.execute(
                text("CREATE UNIQUE INDEX IF NOT EXISTS uq_accounts_name_lower ON accounts (lower(name))")
            )
        else:
            logger.warning("Two accounts have names that differ only in case; the unique index waits until one is "
                           "renamed")
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_accounts_email_lower ON accounts (lower(email))"))
        # Expression indexes here, not in the model: SQLAlchemy cannot read them back and would make them twice.
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_former_names_lower ON former_names (lower(name))"))
