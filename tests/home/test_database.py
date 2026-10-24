"""The database: WAL on every connection, writes that wait, and migrations that land whole once.

Every test runs real SQLite on a file in a temporary folder. Nothing stands in for the database.
"""

import sqlite3
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

from atlas.home import (
    Database,
    DatabaseError,
    DatabaseTooNewError,
    Migration,
    MigrationEditedError,
    Schema,
    migrate,
    parse_utc,
    transaction,
)

NOTES_1 = Migration(1, "notes", "CREATE TABLE memory_notes (id INTEGER PRIMARY KEY, body TEXT);")
NOTES_2 = Migration(2, "note title", "ALTER TABLE memory_notes ADD COLUMN title TEXT;")
JOBS_1 = Migration(1, "jobs", "CREATE TABLE jobs_runs (id INTEGER PRIMARY KEY);")


def _database(tmp_path: Path) -> Database:
    return Database(tmp_path / "atlas.db")


def _ledger(database: Database) -> list[tuple[str, int, str]]:
    connection = database.connect()
    try:
        return connection.execute(
            "SELECT subject, number, applied_at FROM home_migrations ORDER BY subject, number"
        ).fetchall()
    finally:
        connection.close()


def _tables(database: Database) -> set[str]:
    connection = database.connect()
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        return {name for (name,) in rows}
    finally:
        connection.close()


# -- every connection ----------------------------------------------------------------------------


def test_every_connection_has_wal_normal_sync_foreign_keys_and_a_busy_wait(tmp_path: Path) -> None:
    database = _database(tmp_path)

    for _ in range(2):
        connection = database.connect()
        try:
            assert connection.execute("PRAGMA journal_mode").fetchone() == ("wal",)
            assert connection.execute("PRAGMA synchronous").fetchone() == (1,)  # NORMAL
            assert connection.execute("PRAGMA foreign_keys").fetchone() == (1,)
            assert connection.execute("PRAGMA busy_timeout").fetchone() == (5000,)
        finally:
            connection.close()


def test_a_database_that_cannot_use_wal_is_refused_with_the_reason() -> None:
    with pytest.raises(DatabaseError, match="cannot use WAL mode"):
        Database(Path(":memory:")).connect()


def test_a_database_that_cannot_open_says_where(tmp_path: Path) -> None:
    with pytest.raises(DatabaseError, match="Could not open the database"):
        Database(tmp_path / "missing-folder" / "atlas.db").connect()


# -- transactions --------------------------------------------------------------------------------


def test_a_failed_transaction_leaves_nothing(tmp_path: Path) -> None:
    database = _database(tmp_path)
    connection = database.connect()
    connection.execute("CREATE TABLE home_probe (value TEXT)")

    def write_half_then_fail() -> None:
        with transaction(connection):
            connection.execute("INSERT INTO home_probe VALUES ('half')")
            raise RuntimeError("stop halfway")

    with pytest.raises(RuntimeError):
        write_half_then_fail()

    assert connection.execute("SELECT COUNT(*) FROM home_probe").fetchone() == (0,)
    connection.close()


def test_a_writer_waits_for_another_writer_instead_of_failing(tmp_path: Path) -> None:
    database = _database(tmp_path)
    setup = database.connect()
    setup.execute("CREATE TABLE home_probe (value TEXT)")
    setup.close()
    holding = threading.Event()

    def hold_the_write_lock() -> None:
        connection = database.connect()
        with transaction(connection):
            connection.execute("INSERT INTO home_probe VALUES ('first')")
            holding.set()
            time.sleep(0.4)
        connection.close()

    holder = threading.Thread(target=hold_the_write_lock)
    holder.start()
    holding.wait()
    started = time.monotonic()
    connection = database.connect()
    with transaction(connection):
        count = connection.execute("SELECT COUNT(*) FROM home_probe").fetchone()
        connection.execute("INSERT INTO home_probe VALUES ('second')")
    waited = time.monotonic() - started
    holder.join()

    assert count == (1,)  # it saw the first writer's row: it waited for that commit
    assert waited > 0.2
    assert connection.execute("SELECT COUNT(*) FROM home_probe").fetchone() == (2,)
    connection.close()


# -- migrations ----------------------------------------------------------------------------------


def test_migrations_run_in_order_and_are_recorded_with_utc_time(tmp_path: Path) -> None:
    database = _database(tmp_path)

    applied = migrate(database, (Schema("memory", (NOTES_1, NOTES_2)), Schema("jobs", (JOBS_1,))))

    assert [(a.subject, a.number) for a in applied] == [("memory", 1), ("memory", 2), ("jobs", 1)]
    assert {"memory_notes", "jobs_runs", "home_migrations"} <= _tables(database)
    for _subject, _number, applied_at in _ledger(database):
        parse_utc(applied_at)


def test_a_second_start_applies_nothing_and_a_new_migration_runs_alone(tmp_path: Path) -> None:
    database = _database(tmp_path)
    migrate(database, (Schema("memory", (NOTES_1,)),))

    assert migrate(database, (Schema("memory", (NOTES_1,)),)) == []
    later = migrate(database, (Schema("memory", (NOTES_1, NOTES_2)),))

    assert [(a.subject, a.number) for a in later] == [("memory", 2)]


def test_a_failing_migration_lands_nothing_and_says_so(tmp_path: Path) -> None:
    database = _database(tmp_path)
    broken = Migration(
        2,
        "half done",
        "CREATE TABLE memory_tags (name TEXT);\nINSERT INTO memory_nowhere VALUES (1);",
    )

    with pytest.raises(DatabaseError, match=r"memory #2 \(half done\) failed.*Nothing of it"):
        migrate(database, (Schema("memory", (NOTES_1, broken)),))

    assert "memory_tags" not in _tables(database)  # its first statement was rolled back
    assert "memory_notes" in _tables(database)  # the migration before it stays
    assert [(s, n) for s, n, _ in _ledger(database)] == [("memory", 1)]


def test_a_migration_edited_after_it_ran_stops_atlas(tmp_path: Path) -> None:
    database = _database(tmp_path)
    migrate(database, (Schema("memory", (NOTES_1,)),))
    edited = Migration(1, "notes", "CREATE TABLE memory_notes (id INTEGER PRIMARY KEY);")

    with pytest.raises(MigrationEditedError, match=r"memory #1 \(notes\) changed after it ran"):
        migrate(database, (Schema("memory", (edited, NOTES_2)),))

    assert [(s, n) for s, n, _ in _ledger(database)] == [("memory", 1)]


def test_line_endings_alone_do_not_count_as_an_edit(tmp_path: Path) -> None:
    database = _database(tmp_path)
    migrate(database, (Schema("memory", (NOTES_1,)),))
    crlf = Migration(1, "notes", NOTES_1.sql.replace("\n", "\r\n") + "\r\n")

    assert migrate(database, (Schema("memory", (crlf,)),)) == []


@pytest.mark.parametrize(
    "older_code",
    [
        (Schema("memory", (NOTES_1,)),),  # the database has memory #2
        (Schema("memory", (NOTES_1, NOTES_2)),),  # the database has a subject this code lacks
    ],
)
def test_a_database_from_a_newer_atlas_stops_an_older_one(
    tmp_path: Path, older_code: tuple[Schema, ...]
) -> None:
    database = _database(tmp_path)
    migrate(database, (Schema("memory", (NOTES_1, NOTES_2)), Schema("jobs", (JOBS_1,))))

    with pytest.raises(DatabaseTooNewError, match="a newer Atlas wrote it"):
        migrate(database, older_code)


@pytest.mark.parametrize(
    ("schemas", "problem"),
    [
        ((Schema("memory", (NOTES_2,)),), r"memory #2 is out of place"),
        ((Schema("memory", (NOTES_1,)), Schema("memory", ())), "declared twice"),
        ((Schema("Memory", ()),), "lowercase"),
        ((Schema("memory", (Migration(1, " ", "SELECT 1;"),)),), "has no name"),
        (
            (Schema("memory", (Migration(1, "x", "CREATE TABLE notes (id INTEGER);"),)),),
            "touches 'notes'",
        ),
        (
            (Schema("memory", (Migration(1, "x", "DROP TABLE IF EXISTS jobs_runs;"),)),),
            "touches 'jobs_runs'",
        ),
        (
            (
                Schema(
                    "memory", (Migration(1, "x", 'CREATE INDEX "by_body" ON memory_notes(body);'),)
                ),
            ),
            "touches 'by_body'",
        ),
    ],
)
def test_inconsistent_migrations_are_refused_before_the_database_is_touched(
    tmp_path: Path, schemas: tuple[Schema, ...], problem: str
) -> None:
    database = _database(tmp_path)

    with pytest.raises(DatabaseError, match=problem):
        migrate(database, schemas)

    assert not database.path.exists()


def test_a_migration_that_commits_by_itself_is_refused(tmp_path: Path) -> None:
    sneaky = Migration(1, "sneaky", "COMMIT; CREATE TABLE memory_notes (id INTEGER);")

    with pytest.raises(DatabaseError, match="must not contain"):
        migrate(_database(tmp_path), (Schema("memory", (sneaky,)),))


def test_processes_starting_together_apply_each_migration_once(tmp_path: Path) -> None:
    code = textwrap.dedent(
        """
        import sys
        from pathlib import Path
        from atlas.home import Database, Migration, Schema, migrate

        schema = Schema("memory", (
            Migration(1, "counter", "CREATE TABLE memory_counter (n INTEGER);"),
            Migration(2, "count once", "INSERT INTO memory_counter VALUES (1);"),
        ))
        migrate(Database(Path(sys.argv[1])), (schema,))
        """
    )
    database = _database(tmp_path)

    processes = [
        subprocess.Popen([sys.executable, "-c", code, str(database.path)])  # noqa: S603
        for _ in range(4)
    ]
    exit_codes = [process.wait(timeout=60) for process in processes]

    assert exit_codes == [0, 0, 0, 0]
    connection = database.connect()
    try:
        assert connection.execute("SELECT COUNT(*) FROM memory_counter").fetchone() == (1,)
    finally:
        connection.close()
    assert [(s, n) for s, n, _ in _ledger(database)] == [("memory", 1), ("memory", 2)]


def test_sqlite_error_type_is_not_leaked_to_callers(tmp_path: Path) -> None:
    broken = Migration(1, "typo", "CREAT TABLE memory_notes (id INTEGER);")

    with pytest.raises(DatabaseError) as raised:
        migrate(_database(tmp_path), (Schema("memory", (broken,)),))

    assert isinstance(raised.value.__cause__, sqlite3.Error)
