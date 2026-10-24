"""A home's backup: one zip of its `user` entries with a manifest first, checked once written, and
its restore (docs/ARCHITECTURE.md, Backup and homes).

With Atlas V3's checked manifest (M-83):

- **No secrets.** Only what the home's boundary calls `user` goes in: the vault, the logs and what
  a day leaves behind stay. The boundary makes one file both the backup and the export.
- **The database through SQLite's backup API**, a consistent copy while a server writes (V3 used
  `VACUUM INTO` for the same reason). Its `-wal` and `-shm` never travel: beside a copy
  they would let a restore see a half-committed state.
- **Published whole or not at all**: written under a hidden name beside the target, checked, and
  only then moved into place, so a failed backup never lands over a good one.
- **Checked once written**, V3's rule ("a backup nobody read is a guess"): every member's CRC, the
  database's `integrity_check`, and its row counts against the manifest.
- **The last 3 of a home kept** where the backups go by default; a failed one prunes nothing, and a
  file named by hand is never pruned.
- **A restore checks the whole archive before it touches the home**: a damaged member, a newer
  format, a database a newer Atlas migrated, or a member outside the home's `user` entries refuses
  it. It then overwrites: the database is written into the live file through the same API, so a
  running server reads the same data; and the tables that came back with fewer rows than they had
  are named, a warning that the backup is older.
- **An import makes a new home** from a backup (decided on 15/10/2026, docs/ARCHITECTURE.md, Homes
  in the app): checked whole first, restored into the home while it is being made, its database
  brought forward, and the home published only once whole. The caller names it, and
  `suggest_home_name` offers a free one, since the default home's backup is the common one and
  `default` always exists.
"""

import contextlib
import json
import os
import re
import sqlite3
import tempfile
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, cast

from atlas.home.database import LEDGER, Database, check_not_newer, migrate
from atlas.home.errors import BackupError, DatabaseError
from atlas.home.files import replace_file
from atlas.home.homes import create_home, home_name, list_homes
from atlas.home.model import (
    DATABASE_FILE,
    HOMES_FOLDER,
    LAYOUT,
    MACHINE_INSIDE_USER_HOME,
    USER_HOME_FOLDER,
    EntryClass,
    Home,
    Schema,
    classify,
    home_name_problem,
)

#: The archive's layout version. A restore refuses a higher one: a newer Atlas wrote it.
FORMAT = 1
MANIFEST = "manifest.json"
BACKUP_PREFIX = "atlas-backup-"
PRE_MIGRATION_PREFIX = "pre-migration-"
#: How many of a home's backups stay where they go by default (V3's three).
KEEP = 3
#: How many backups taken before a migration stay.
PRE_MIGRATION_KEEP = 5
_STAMP = "%Y%m%dT%H%M%SZ"
_STAMP_PATTERN = r"\d{8}T\d{6}Z"
#: Folders a backup leaves out wherever they are: regenerated, or another tool's.
_SKIPPED_FOLDERS = frozenset({"__pycache__", ".git", "node_modules"})
_SIDECARS = frozenset({f"{DATABASE_FILE}-wal", f"{DATABASE_FILE}-shm"})
#: The programs' logins inside the user home folder, as archive paths: never written, never
#: restored.
_MACHINE_PATHS = tuple(f"{USER_HOME_FOLDER}/{path}" for path in MACHINE_INSIDE_USER_HOME)


def _stays_here(name: str) -> bool:
    return any(name == path or name.startswith(f"{path}/") for path in _MACHINE_PATHS)


_ARCHIVE_ERRORS = (zipfile.BadZipFile, zlib.error, EOFError, OSError, ValueError)


@dataclass(frozen=True, slots=True)
class Manifest:
    format: int
    #: The Atlas version that wrote it.
    atlas: str
    #: The name of the home it came from.
    home: str
    created_at: str
    #: Rows per table of the database in it.
    tables: dict[str, int]
    #: The members besides the manifest.
    files: int


@dataclass(frozen=True, slots=True)
class Written:
    path: Path
    manifest: Manifest
    size: int


@dataclass(frozen=True, slots=True)
class Shrunk:
    """A table that came back with fewer rows than it had: the backup is older than the home."""

    table: str
    before: int
    after: int


@dataclass(frozen=True, slots=True)
class Restored:
    manifest: Manifest
    files: int
    shrunk: list[Shrunk]


@dataclass(frozen=True, slots=True)
class Imported:
    """A new home made from a backup."""

    path: Path
    manifest: Manifest
    files: int


def backup_name(home: str, now: datetime) -> str:
    """`atlas-backup-<home>-<UTC>.zip`: sorted by name, sorted by time."""
    return f"{BACKUP_PREFIX}{home}-{now.strftime(_STAMP)}.zip"


def write_backup(
    home: Home, target: Path, *, version: str, now: datetime, replace: bool = False
) -> Written:
    """Write the home's backup to `target`, checked, or raise and leave nothing there. `replace`
    is for a file the user chose to replace (the app's save dialog asked): it is replaced only by
    a checked backup."""
    if target.is_dir() or (target.exists() and not replace):
        raise BackupError(f"{target} already exists; a backup never writes over a file.")
    target.parent.mkdir(parents=True, exist_ok=True)
    files = list(_user_files(home))
    partial = target.with_name(f".{target.name}.{os.getpid()}.partial")
    with tempfile.TemporaryDirectory(prefix="atlas-backup-") as scratch:
        snapshot: Path | None = None
        tables: dict[str, int] = {}
        if home.database_file.is_file():
            snapshot = Path(scratch) / DATABASE_FILE
            _copy_database(home.database_file, snapshot)
            tables = _counts(snapshot)
        manifest = Manifest(
            format=FORMAT,
            atlas=version,
            home=home_name(home.root),
            created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            tables=tables,
            files=len(files) + (snapshot is not None),
        )
        problems: list[str] = []
        try:
            with zipfile.ZipFile(partial, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
                archive.writestr(MANIFEST, json.dumps(asdict(manifest), indent=2))
                if snapshot is not None:
                    archive.write(snapshot, DATABASE_FILE)
                for path, name in files:
                    try:
                        archive.write(path, name)
                    except OSError as error:
                        problems.append(f"{name}: {error}")
            if problems:
                raise BackupError(
                    "These files could not be read, so no backup was written: "
                    + "; ".join(problems)
                )
            _check_archive(partial, None)
            partial.replace(target)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
    return Written(target, manifest, target.stat().st_size)


def check_backup(path: Path, schemas: tuple[Schema, ...]) -> Manifest:
    """The backup's manifest once all of it was read and found sound; `BackupError` otherwise."""
    return _check_archive(path, schemas)


def restore_backup(home: Home, path: Path, schemas: tuple[Schema, ...]) -> Restored:
    """Put the backup's entries back over the home, after checking all of it."""
    return _restore(home, path, check_backup(path, schemas))


def import_backup(root: Path, name: str, path: Path, schemas: tuple[Schema, ...]) -> Imported:
    """A new named home holding a backup: the archive is checked whole
    before the home is made, restored into it while it is being made, and its database brought up
    to this Atlas; the home is published only once all of that worked."""
    manifest = check_backup(path, schemas)
    restored: list[Restored] = []

    def fill(staging: Path) -> None:
        home = Home(staging)
        restored.append(_restore(home, path, manifest))
        # An older backup comes forward to this Atlas's tables, as `atlas restore` brings it.
        migrate(Database(home.database_file), schemas)

    made = create_home(root, name, fill=fill)
    return Imported(made, manifest, restored[0].files)


def suggest_home_name(root: Path, manifest: Manifest) -> str:
    """A free name for the home a backup is imported into: the name of the home it came from, or
    `<home>-<date of the backup>` when that one exists (the default home always does), numbered
    when that one does too."""
    taken = {info.name for info in list_homes(root)}
    if manifest.home not in taken and home_name_problem(manifest.home) is None:
        return manifest.home
    date = manifest.created_at[:10]
    base = f"{manifest.home}-{date}"[:60]
    if home_name_problem(base) is not None:
        base = "imported"
    name, number = base, 2
    while name in taken or (root / HOMES_FOLDER / name).exists():
        name, number = f"{base}-{number}", number + 1
    return name


def _restore(home: Home, path: Path, manifest: Manifest) -> Restored:
    """`restore_backup` once the archive was checked."""
    before = _counts(home.database_file) if home.database_file.is_file() else {}
    written = 0
    with (
        tempfile.TemporaryDirectory(prefix="atlas-restore-") as scratch,
        zipfile.ZipFile(path) as archive,
    ):
        for info in archive.infolist():
            if info.filename == MANIFEST or info.is_dir():
                continue
            if _stays_here(info.filename):
                continue  # a program's login in a backup written before step 14b: not brought over
            if info.filename == DATABASE_FILE:
                extracted = Path(archive.extract(info, scratch))
                _write_database(extracted, home.database_file)
            else:
                target = home.root.joinpath(*PurePosixPath(info.filename).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                replace_file(target, archive.read(info))
            written += 1
    shrunk = [
        Shrunk(table, rows, manifest.tables.get(table, 0))
        for table, rows in sorted(before.items())
        if manifest.tables.get(table, 0) < rows
    ]
    return Restored(manifest, written, shrunk)


def prune_backups(folder: Path, prefix: str, keep: int) -> list[Path]:
    """Remove the oldest `<prefix><UTC>.zip` beyond `keep`; returns what went. 0 keeps all."""
    if keep < 0:
        raise BackupError(f"Keep {keep}? Give 0 to keep every backup, or a positive number.")
    if keep == 0 or not folder.is_dir():
        return []
    pattern = re.compile(rf"^{re.escape(prefix)}{_STAMP_PATTERN}\.zip$")
    found = sorted((p for p in folder.iterdir() if pattern.match(p.name)), reverse=True)
    removed: list[Path] = []
    for old in found[keep:]:
        with contextlib.suppress(OSError):
            old.unlink()
            removed.append(old)
    return removed


def pre_migration_backup(home: Home, *, version: str, now: datetime) -> Written:
    """The home's backup in its `backups/`, before a migration changes its database."""
    folder = home.backups_folder
    written = write_backup(
        home, folder / f"{PRE_MIGRATION_PREFIX}{now.strftime(_STAMP)}.zip", version=version, now=now
    )
    prune_backups(folder, PRE_MIGRATION_PREFIX, PRE_MIGRATION_KEEP)
    return written


def has_data(home: Home) -> bool:
    """Whether a restore would overwrite something: a user entry, or a row in the database."""
    for entry in LAYOUT:
        if entry.entry_class is not EntryClass.USER or entry.name == DATABASE_FILE:
            continue
        path = home.root / entry.name
        if entry.name in _SIDECARS or not path.exists():
            continue
        if path.is_file() or any(p.is_file() for p in path.rglob("*")):
            return True
    if not home.database_file.is_file():
        return False
    return any(rows for table, rows in _counts(home.database_file).items() if table != LEDGER)


# -- writing -------------------------------------------------------------------------------------


def _user_files(home: Home) -> Iterator[tuple[Path, str]]:
    """Each regular file of the home's `user` entries, with its name in the archive."""
    for entry in LAYOUT:
        if entry.entry_class is not EntryClass.USER or entry.name in _SIDECARS:
            continue
        if entry.name == DATABASE_FILE:
            continue  # copied through SQLite, not as a file
        path = home.root / entry.name
        if path.is_symlink():
            continue
        if not entry.is_folder:
            if path.is_file():
                yield path, entry.name
            continue
        if not path.is_dir():
            continue
        for folder, folders, names in os.walk(path):
            folders[:] = sorted(f for f in folders if f not in _SKIPPED_FOLDERS)
            for name in sorted(names):
                file = Path(folder) / name
                # A backup left in the user's folder must not go into the next one, which would
                # nest every backup in the one after it.
                if file.is_symlink() or not file.is_file() or name.startswith(BACKUP_PREFIX):
                    continue
                archived = file.relative_to(home.root).as_posix()
                if not _stays_here(archived):
                    yield file, archived


def _copy_database(source: Path, target: Path) -> None:
    """A consistent copy of a database another process may be writing."""
    origin = sqlite3.connect(source, timeout=10)
    copy = sqlite3.connect(target)
    try:
        with copy:
            origin.backup(copy)
    except sqlite3.Error as error:
        raise BackupError(f"The database {source} could not be copied: {error}.") from error
    finally:
        copy.close()
        origin.close()


def _counts(database: Path) -> dict[str, int]:
    """Rows per table, leaving out SQLite's own and the full-text indexes' inner tables."""
    connection = sqlite3.connect(database, timeout=10)
    try:
        rows: list[tuple[str, str | None]] = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        virtual = {name for name, sql in rows if (sql or "").upper().startswith("CREATE VIRTUAL")}
        counted: dict[str, int] = {}
        for name, _ in sorted(rows):
            if name in virtual or any(name.startswith(f"{v}_") for v in virtual):
                continue
            (count,) = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()  # noqa: S608
            counted[name] = int(count)
        return counted
    except sqlite3.Error as error:
        raise BackupError(f"The database {database} cannot be read: {error}.") from error
    finally:
        connection.close()


# -- checking ------------------------------------------------------------------------------------


def _check_archive(path: Path, schemas: tuple[Schema, ...] | None) -> Manifest:
    try:
        archive = zipfile.ZipFile(path)
    except _ARCHIVE_ERRORS as error:
        raise BackupError(f"{path} is not a backup Atlas can read: {error}.") from error
    with archive:
        names = archive.namelist()
        if not names or names[0] != MANIFEST:
            raise BackupError(f"{path} is not an Atlas backup: its manifest is not first.")
        try:
            damaged = archive.testzip()
        except _ARCHIVE_ERRORS as error:
            raise BackupError(f"{path} is damaged: {error}. Nothing was restored.") from error
        if damaged is not None:
            raise BackupError(f"{path} is damaged: {damaged} does not read back whole.")
        manifest = _read_manifest(path, archive)
        problems = [p for name in names[1:] if (p := _member_problem(name)) is not None]
        if problems:
            raise BackupError(f"{path} holds what a backup never carries: " + "; ".join(problems))
        members = [name for name in names[1:] if not name.endswith("/")]
        if len(members) != manifest.files:
            raise BackupError(
                f"{path} holds {len(members)} files, and its manifest says {manifest.files}."
            )
        if DATABASE_FILE in members:
            with tempfile.TemporaryDirectory(prefix="atlas-check-") as scratch:
                database = Path(archive.extract(DATABASE_FILE, scratch))
                _check_database(path, database, manifest, schemas)
    return manifest


def _read_manifest(path: Path, archive: zipfile.ZipFile) -> Manifest:
    try:
        raw = cast("dict[str, Any]", json.loads(archive.read(MANIFEST).decode("utf-8")))
        found = Manifest(
            format=int(raw["format"]),
            atlas=str(raw["atlas"]),
            home=str(raw["home"]),
            created_at=str(raw["created_at"]),
            tables={str(k): int(v) for k, v in cast("dict[str, Any]", raw["tables"]).items()},
            files=int(raw["files"]),
        )
    except (KeyError, TypeError, ValueError, UnicodeDecodeError) as error:
        raise BackupError(f"{path} has a manifest Atlas cannot read: {error}.") from error
    if found.format > FORMAT:
        raise BackupError(
            f"{path} is a backup of format {found.format}, written by a newer Atlas "
            f"({found.atlas}); this one reads up to {FORMAT}. Update Atlas to restore it."
        )
    return found


def _member_problem(name: str) -> str | None:
    """Why a member cannot be restored into a home, or `None`."""
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or "\\" in name or ":" in name or ".." in parts:
        return f"{name} (a path outside the home)"
    top = parts[0]
    entry = classify(top, is_folder=len(parts) > 1)
    if entry is None or entry.entry_class is not EntryClass.USER or top in _SIDECARS:
        return f"{name} (not the user's data)"
    return None


def _check_database(
    path: Path, database: Path, manifest: Manifest, schemas: tuple[Schema, ...] | None
) -> None:
    connection = sqlite3.connect(database)
    try:
        (verdict,) = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as error:
        raise BackupError(f"The database in {path} does not open: {error}.") from error
    finally:
        connection.close()
    if verdict != "ok":
        raise BackupError(f"The database in {path} fails its integrity check: {verdict}.")
    counted = _counts(database)
    if counted != manifest.tables:
        changed = sorted(
            t
            for t in counted.keys() | manifest.tables.keys()
            if counted.get(t) != manifest.tables.get(t)
        )
        raise BackupError(
            f"The database in {path} does not hold what its manifest says: {', '.join(changed)}."
        )
    if schemas is not None:
        try:
            check_not_newer(database, schemas)
        except DatabaseError as error:
            raise BackupError(f"{path} cannot be restored here: {error}") from error


# -- restoring -----------------------------------------------------------------------------------


def _write_database(source: Path, target: Path) -> None:
    """The backup's pages written into the home's database file, which stays the same file, so a
    process holding it open reads the restored data."""
    origin = sqlite3.connect(source)
    live = sqlite3.connect(target, timeout=10)
    try:
        with live:
            origin.backup(live)
    except sqlite3.Error as error:
        raise BackupError(f"The database {target} could not be restored: {error}.") from error
    finally:
        live.close()
        origin.close()
