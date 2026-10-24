"""What the user decided for good: the approvals answered "always", the ⚡ of each conversation, and
the addresses the user approved an email to (the known destinations, step 14b).

Atlas keeps the permanent allowlist and the auto mode of each conversation in the home's database,
the one place it keeps state. A connection is opened
for each operation, as the other stores do.
"""

from datetime import datetime

from atlas.home import Database, Migration, Schema, format_utc, transaction

SCHEMA = Schema(
    "trust",
    (
        Migration(
            1,
            "approvals kept for good, and each conversation's auto mode",
            """
CREATE TABLE trust_allowlist (
    key TEXT PRIMARY KEY,
    added_at TEXT NOT NULL
) STRICT;
CREATE TABLE trust_auto_mode (
    conversation_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL,
    updated_at TEXT NOT NULL
) STRICT;
""",
        ),
        Migration(
            2,
            "the addresses the user approved an email to",
            """
CREATE TABLE trust_destinations (
    address TEXT PRIMARY KEY,
    first_at TEXT NOT NULL
) STRICT;
""",
        ),
    ),
)


class TrustStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def allowed(self, key: str) -> bool:
        connection = self._database.connect()
        try:
            row = connection.execute(
                "SELECT 1 FROM trust_allowlist WHERE key = ?", (key,)
            ).fetchone()
        finally:
            connection.close()
        return row is not None

    def allow(self, key: str, now: datetime) -> None:
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    "INSERT OR IGNORE INTO trust_allowlist (key, added_at) VALUES (?, ?)",
                    (key, format_utc(now)),
                )
        finally:
            connection.close()

    def auto_mode(self, conversation_id: str) -> bool:
        connection = self._database.connect()
        try:
            row: tuple[int] | None = connection.execute(
                "SELECT enabled FROM trust_auto_mode WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()
        finally:
            connection.close()
        return bool(row and row[0])

    def set_auto_mode(self, conversation_id: str, enabled: bool, now: datetime) -> None:
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.execute(
                    "INSERT INTO trust_auto_mode (conversation_id, enabled, updated_at) "
                    "VALUES (?, ?, ?) ON CONFLICT (conversation_id) DO UPDATE SET "
                    "enabled = excluded.enabled, updated_at = excluded.updated_at",
                    (conversation_id, int(enabled), format_utc(now)),
                )
        finally:
            connection.close()

    def unknown(self, addresses: list[str]) -> list[str]:
        """The addresses no approved email went to yet, in their order."""
        if not addresses:
            return []
        connection = self._database.connect()
        try:
            marks = ", ".join("?" for _ in addresses)
            rows: list[tuple[str]] = connection.execute(
                f"SELECT address FROM trust_destinations WHERE address IN ({marks})",  # noqa: S608
                addresses,
            ).fetchall()
        finally:
            connection.close()
        known = {address for (address,) in rows}
        return [address for address in addresses if address not in known]

    def remember(self, addresses: list[str], now: datetime) -> None:
        connection = self._database.connect()
        try:
            with transaction(connection):
                connection.executemany(
                    "INSERT OR IGNORE INTO trust_destinations (address, first_at) VALUES (?, ?)",
                    [(address, format_utc(now)) for address in addresses],
                )
        finally:
            connection.close()
