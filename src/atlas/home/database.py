"""One SQLite database for every subject, in WAL mode, with numbered migrations per subject.

**Every connection** gets the same settings, because SQLite keeps them per connection:
WAL (readers and writers do not block each other; it is stored in the file, and it needs a local
disk), `synchronous=NORMAL` (safe against a crash of Atlas; a power cut may lose the last
transaction), foreign keys on, and a 5-second wait for a busy database.

**Writes go through `transaction()`**, which starts with `BEGIN IMMEDIATE`. A plain `BEGIN` takes
the write lock only at the first write, and if another connection is writing then, SQLite fails at
once without waiting. Taking the lock at the start makes the wait apply. The connection runs in
autocommit mode, so nothing opens a transaction behind the code's back and holds a stale snapshot.

**Migrations.** Each subject declares a `Schema`: migrations numbered 1, 2, 3, never edited after
they ran. The ledger `home_migrations` records each one with its checksum and UTC time. At startup:

- a migration that changed after it ran stops Atlas (add a new one instead);
- a database migrated by a newer Atlas stops Atlas (it may not read what it does not know);
- each pending migration runs in its own transaction, together with its ledger row: it lands whole
  or not at all, and two processes starting at once apply it once;
- before pending migrations change a database that already holds applied ones, `before` runs once:
  the entry point backs the home up there.

Atlas V3 had none of this: 45 `CREATE TABLE IF NOT EXISTS`, 42 `ALTER TABLE` and 27 column checks
ran again at every start and for every thread, and the schema version stayed at 1.
"""

import logging
import sqlite3
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from atlas.home.clock import format_utc, utc_now
from atlas.home.errors import DatabaseError, DatabaseTooNewError, MigrationEditedError
from atlas.home.model import Migration, Schema, schema_problems

BUSY_TIMEOUT_MS = 5000
LEDGER = "home_migrations"
_LEDGER_TABLE = f"""
CREATE TABLE IF NOT EXISTS {LEDGER} (
    subject TEXT NOT NULL,
    number INTEGER NOT NULL,
    name TEXT NOT NULL,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY (subject, number)
) STRICT
"""

_logger = logging.getLogger("atlas.home")


@dataclass(frozen=True, slots=True)
class AppliedMigration:
    subject: str
    number: int
    name: str
    applied_at: str


class Database:
    """The database file. Each connection belongs to the thread that opened it."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        """A new connection with Atlas's settings. The caller closes it."""
        try:
            connection = sqlite3.connect(self.path, autocommit=True, timeout=BUSY_TIMEOUT_MS / 1000)
        except sqlite3.Error as error:
            raise DatabaseError(f"Could not open the database {self.path}: {error}.") from error
        try:
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            mode = self._wal(connection)
        except sqlite3.Error as error:
            connection.close()
            raise DatabaseError(f"Could not open the database {self.path}: {error}.") from error
        if mode != "wal":
            connection.close()
            raise DatabaseError(
                f"The database {self.path} cannot use WAL mode (it reports {mode!r}). "
                "WAL needs a file on a local disk; a network folder does not work."
            )
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _wal(connection: sqlite3.Connection) -> str:
        """Switch to WAL if the file is not in it yet, and return the journal mode.

        WAL is stored in the file, so this switches once in the database's life. The switch needs
        the file to itself for an instant, and SQLite fails at once instead of waiting when another
        process has it open (found by processes creating the same new database together), so the
        switch is retried within the usual busy wait.
        """
        deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
        while True:
            try:
                (mode,) = connection.execute("PRAGMA journal_mode").fetchone()
                if mode != "wal":
                    (mode,) = connection.execute("PRAGMA journal_mode = WAL").fetchone()
            except sqlite3.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.02)
            else:
                return str(mode)


@contextmanager
def transaction(connection: sqlite3.Connection) -> Generator[sqlite3.Connection]:
    """A write transaction that holds the write lock from its start. Rolled back on any error."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise
    connection.execute("COMMIT")


def migrate(
    database: Database,
    schemas: tuple[Schema, ...],
    *,
    before: Callable[[], object] | None = None,
) -> list[AppliedMigration]:
    """Bring the database up to the migrations declared in the code. Returns what was applied.

    `before` runs once when migrations are pending on a database that already holds applied ones.
    """
    problems = schema_problems(schemas)
    if problems:
        raise DatabaseError("The migrations in the code are inconsistent: " + "; ".join(problems))
    connection = database.connect()
    try:
        with transaction(connection):
            connection.execute(_LEDGER_TABLE)
        done = _check_ledger(database.path, connection, schemas)
        declared = {(s.subject, m.number) for s in schemas for m in s.migrations}
        if before is not None and done and declared - done:
            before()
        applied: list[AppliedMigration] = []
        for schema in schemas:
            for migration in schema.migrations:
                done = _apply(database.path, connection, schema.subject, migration)
                if done is not None:
                    applied.append(done)
        return applied
    finally:
        connection.close()


def check_not_newer(path: Path, schemas: tuple[Schema, ...]) -> None:
    """Refuse a database a newer Atlas migrated, or whose migrations changed after they ran."""
    connection = sqlite3.connect(path)
    try:
        has_ledger = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (LEDGER,)
        ).fetchone()
        if has_ledger:
            _check_ledger(path, connection, schemas)
    finally:
        connection.close()


def _check_ledger(
    path: Path, connection: sqlite3.Connection, schemas: tuple[Schema, ...]
) -> set[tuple[str, int]]:
    """The migrations the ledger says ran, once each is known to this Atlas and unchanged."""
    known = {schema.subject: schema.migrations for schema in schemas}
    rows: list[tuple[str, int, str]] = connection.execute(
        f"SELECT subject, number, checksum FROM {LEDGER} ORDER BY subject, number"  # noqa: S608
    ).fetchall()
    for subject, number, checksum in rows:
        migrations = known.get(subject, ())
        if number > len(migrations):
            raise DatabaseTooNewError(
                f"The database {path} has migration {subject} #{number}, which this Atlas does "
                "not know: a newer Atlas wrote it. Run that version, or restore a backup taken "
                "before it."
            )
        migration = migrations[number - 1]
        if migration.checksum != checksum:
            raise MigrationEditedError(
                f"Migration {subject} #{number} ({migration.name}) changed after it ran in {path}. "
                "Migrations are forward-only: restore its original text and add a new one."
            )
    return {(subject, number) for subject, number, _ in rows}


def _apply(
    path: Path, connection: sqlite3.Connection, subject: str, migration: Migration
) -> AppliedMigration | None:
    label = f"{subject} #{migration.number} ({migration.name})"
    with transaction(connection):
        row: tuple[str] | None = connection.execute(
            f"SELECT checksum FROM {LEDGER} WHERE subject = ? AND number = ?",  # noqa: S608
            (subject, migration.number),
        ).fetchone()
        if row is not None:  # another process applied it while this one waited for the lock
            return None
        try:
            connection.executescript(migration.sql)
        except sqlite3.Error as error:
            raise DatabaseError(
                f"Migration {label} failed in {path}: {error}. Nothing of it was applied."
            ) from error
        if not connection.in_transaction:
            raise DatabaseError(
                f"Migration {label} ended the transaction it runs in. A migration must not contain "
                "BEGIN, COMMIT or ROLLBACK: each one already runs in its own transaction."
            )
        applied_at = format_utc(utc_now())
        connection.execute(
            f"INSERT INTO {LEDGER} (subject, number, name, checksum, applied_at) "  # noqa: S608
            "VALUES (?, ?, ?, ?, ?)",
            (subject, migration.number, migration.name, migration.checksum, applied_at),
        )
    _logger.info("Applied migration %s.", label, extra={"subject": subject})
    return AppliedMigration(subject, migration.number, migration.name, applied_at)
