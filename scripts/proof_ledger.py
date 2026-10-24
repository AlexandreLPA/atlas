"""The real home's call ledger, for the proofs, without migrating the real home.

The user's own Atlas migrates the real home. A proof may run newer code (a step's branch) or know
fewer subjects than it: migrating with only the models subject refused the home once every subject
was there ("a newer Atlas wrote it", found on 12/10/2026, the day after the first switch), and
migrating with a branch's subjects would leave tables the user's Atlas does not know, so it would
refuse the home in turn. A home with no ledger yet (never served) gets the models subject's table.
"""

import sqlite3
from contextlib import closing

from atlas.home import Database, migrate
from atlas.models import SCHEMA


def ensure_ledger(database: Database) -> None:
    """Make the ledger's table only when the home has none."""
    with closing(sqlite3.connect(database.path)) as connection:
        found = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'models_calls'"
        ).fetchone()
    if found is None:
        migrate(database, (SCHEMA,))


#: Every row of the ledger, for a proof that counts the calls it added: a home past 1,000 calls
#: read only its newest 1,000 before and after, and every proof said "0 new rows" (14/10/2026).
LEDGER_ROWS = 10_000_000
