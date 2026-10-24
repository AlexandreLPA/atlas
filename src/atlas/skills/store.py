"""The skill catalog, the skills' usage in the database, and the curator's runs.

**The catalog** is the skill folders on disk, read when asked, so a skill written while Atlas runs
is there at once. Ported from Atlas V3's `application/skills.py`, which was the one place that knew
where a skill lives:

- `SKILL.md` is found whatever its case: Windows hands `Skill.md` to whoever asks for `SKILL.md`,
  Linux does not, and a home made on one must read on the other.
- A skill's folder must stay inside the skills folder, even through a symbolic link.
- A broken skill is logged and skipped; the others still load.
- A folder whose name starts with a dot (`.versions`, `.archive`) is not a skill.

**Usage** is a table of the home's database, the one place Atlas keeps state: where each skill
came from and when it was made, loaded, changed, archived and restored. A skill written by hand
has no row until it is first loaded, and then it is the user's. A failure to record is logged and
never stops the call that used the skill: usage is telemetry.
"""

import logging
import sqlite3
from collections.abc import Collection, Mapping, Sequence
from dataclasses import replace
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path

from atlas.home import (
    Database,
    DatabaseError,
    Migration,
    Schema,
    format_utc,
    parse_utc,
    transaction,
)
from atlas.skills.errors import SkillError
from atlas.skills.model import (
    NAME,
    PLUGIN_SKILL,
    SKILL_FILE,
    Origin,
    Skill,
    SkillRecord,
    parse_command,
    parse_skill,
)

SCHEMA = Schema(
    "skills",
    (
        Migration(
            1,
            "each skill's origin and usage, and the curator's runs",
            """
CREATE TABLE skills_usage (
    name TEXT PRIMARY KEY,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    patch_count INTEGER NOT NULL DEFAULT 0,
    last_patched_at TEXT,
    restored_at TEXT,
    archived_at TEXT
) STRICT;
CREATE TABLE skills_curator_runs (
    started_at TEXT PRIMARY KEY,
    archived INTEGER NOT NULL DEFAULT 0,
    first INTEGER NOT NULL DEFAULT 0
) STRICT;
""",
        ),
        Migration(
            2,
            "whose folder a skill is in, and who created it",
            """
ALTER TABLE skills_usage RENAME TO skills_usage_before_owners;
CREATE TABLE skills_usage (
    owner TEXT NOT NULL DEFAULT '',
    name TEXT NOT NULL,
    origin TEXT NOT NULL,
    created_at TEXT NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    last_used_at TEXT,
    patch_count INTEGER NOT NULL DEFAULT 0,
    last_patched_at TEXT,
    restored_at TEXT,
    archived_at TEXT,
    creator TEXT,
    PRIMARY KEY (owner, name)
) STRICT;
INSERT INTO skills_usage (
    owner, name, origin, created_at, use_count, last_used_at, patch_count,
    last_patched_at, restored_at, archived_at, creator
)
SELECT '', name, origin, created_at, use_count, last_used_at, patch_count,
    last_patched_at, restored_at, archived_at,
    CASE WHEN origin = 'review' THEN 'atlas' END
FROM skills_usage_before_owners;
DROP TABLE skills_usage_before_owners;
""",
        ),
    ),
)

_COLUMNS = (
    "name, origin, created_at, use_count, last_used_at, patch_count, last_patched_at, "
    "restored_at, archived_at, owner, creator"
)

_logger = logging.getLogger("atlas.skills")


class SkillCatalog:
    """The skills in a list of folders, the first folder winning a name they share. `only`
    names, for a folder, the skills read from it: a bot's list of the library's skills it may
    see. `plugins` are the skills folders of the plugins that are on, by the plugin's name, after
    the others: their skills are named after it (`video:render`), as Claude Code names a plugin's,
    so they never hide one of the user's, and they are read only."""

    def __init__(
        self,
        folders: Sequence[Path],
        *,
        only: Mapping[Path, Collection[str]] | None = None,
        plugins: Mapping[str, Path] | None = None,
        commands: Mapping[str, Path] | None = None,
    ) -> None:
        """`commands` are the `commands/` folders of the plugins that are on, by plugin: each
        `<name>.md` is a skill named `<plugin>:<name>`, after the plugin's skills (Claude Code
        reads a plugin's commands as skills, a skill winning a name both have)."""
        self.folders = tuple(folders)
        self._only = dict(only or {})
        self._plugins = dict(plugins or {})
        self._commands = dict(commands or {})

    def skills(self) -> list[Skill]:
        found: dict[str, Skill] = {}
        for root in self.folders:
            for folder in _children(root):
                skill = self._read(root, folder)
                if skill is None or skill.name not in self._only.get(root, (skill.name,)):
                    continue
                found.setdefault(skill.name, skill)
        for plugin, root in self._plugins.items():
            for folder in _children(root):
                skill = self._read(root, folder)
                if skill is None:
                    continue
                named = replace(skill, name=f"{plugin}:{skill.name}")
                if named.name in self._only.get(root, (named.name,)):
                    found.setdefault(named.name, named)
        for plugin, root in self._commands.items():
            for file in command_files(root):
                try:
                    skill = parse_command(file.read_text(encoding="utf-8", errors="replace"), file)
                except (OSError, SkillError) as error:
                    _logger.warning("A command was skipped: %s", error)
                    continue
                named = replace(skill, name=f"{plugin}:{skill.name}")
                if named.name in self._only.get(root, (named.name,)):
                    found.setdefault(named.name, named)
        return list(found.values())

    def get(self, name: str) -> Skill | None:
        if not NAME.fullmatch(name) and not PLUGIN_SKILL.fullmatch(name):
            return None
        for skill in self.skills():
            if skill.name == name:
                return skill
        return None

    def _read(self, root: Path, folder: Path) -> Skill | None:
        try:
            if not folder.resolve().is_relative_to(root.resolve()):
                _logger.warning("The skill folder %s leads outside %s; skipped.", folder, root)
                return None
        except OSError:
            return None
        file = skill_file(folder)
        if file is None:
            return None
        try:
            return parse_skill(file.read_text(encoding="utf-8", errors="replace"), folder)
        except (OSError, SkillError) as error:
            _logger.warning("A skill was skipped: %s", error)
            return None


def command_files(folder: Path) -> list[Path]:
    """A plugin's `commands/`: its Markdown files, each a command, as Claude Code finds them."""
    try:
        return sorted(
            file
            for file in folder.iterdir()
            if file.is_file() and file.suffix.lower() == ".md" and not file.name.startswith(".")
        )
    except OSError:
        return []


def support_files(folder: Path, limit: int) -> str:
    """The files of a skill's folder besides its `SKILL.md`, as a line that names the first
    `limit`; empty when it has none."""
    try:
        names = sorted(
            str(path.relative_to(folder)).replace("\\", "/")
            for path in folder.rglob("*")
            if path.is_file() and path.name.lower() != SKILL_FILE.lower()
        )
    except OSError:
        return ""
    if not names:
        return ""
    shown = names[:limit]
    more = f" and {len(names) - len(shown)} more" if len(names) > len(shown) else ""
    return f"Files: {', '.join(shown)}{more}."


def skill_file(folder: Path) -> Path | None:
    """The folder's `SKILL.md`, whatever its case."""
    exact = folder / SKILL_FILE
    if exact.is_file():
        return exact
    try:
        for child in folder.iterdir():
            if child.is_file() and child.name.lower() == SKILL_FILE.lower():
                return child
    except OSError:
        return None
    return None


def _children(root: Path) -> list[Path]:
    try:
        return sorted(
            child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")
        )
    except OSError:
        return []


class CuratorClaim(StrEnum):
    #: The curator had never run: the run is recorded and its work deferred one interval, so a new
    #: home never loses a skill on its first pass.
    SEEDED = "seeded"
    #: The run is this process's to do.
    CLAIMED = "claimed"
    #: The last run is too recent.
    NOT_DUE = "not_due"


class SkillUsage:
    """The skills' rows in the database. A connection is opened for each operation."""

    def __init__(self, database: Database, owner: str = "") -> None:
        """`owner` is whose folder the rows are about: the home's library, or a bot's name."""
        self._database = database
        self.owner = owner

    def owned_by(self, owner: str) -> "SkillUsage":
        """The same table, for another folder's skills."""
        return SkillUsage(self._database, owner)

    def created(self, name: str, origin: Origin, now: datetime, creator: str | None = None) -> None:
        """A new skill: its row starts over. `creator` is who made it."""
        self._write(
            "INSERT OR REPLACE INTO skills_usage (owner, name, origin, created_at, creator) "
            "VALUES (?, ?, ?, ?, ?)",
            (self.owner, name, origin.value, format_utc(now), creator),
        )

    def used(self, name: str, now: datetime) -> None:
        if PLUGIN_SKILL.fullmatch(name):
            # A plugin's skill is its plugin's: no row, so the curator never archives it.
            return
        self._write(
            "INSERT INTO skills_usage (owner, name, origin, created_at, use_count, last_used_at) "
            "VALUES (?, ?, 'user', ?, 1, ?) ON CONFLICT (owner, name) DO UPDATE SET "
            "use_count = use_count + 1, last_used_at = excluded.last_used_at",
            (self.owner, name, format_utc(now), format_utc(now)),
        )

    def patched(self, name: str, now: datetime) -> None:
        self._write(
            "INSERT INTO skills_usage "
            "(owner, name, origin, created_at, patch_count, last_patched_at) "
            "VALUES (?, ?, 'user', ?, 1, ?) ON CONFLICT (owner, name) DO UPDATE SET "
            "patch_count = patch_count + 1, last_patched_at = excluded.last_patched_at",
            (self.owner, name, format_utc(now), format_utc(now)),
        )

    def archived(self, name: str, now: datetime) -> None:
        self._write(
            "INSERT INTO skills_usage (owner, name, origin, created_at, archived_at) "
            "VALUES (?, ?, 'user', ?, ?) ON CONFLICT (owner, name) DO UPDATE SET "
            "archived_at = excluded.archived_at",
            (self.owner, name, format_utc(now), format_utc(now)),
        )

    def restored(self, name: str, now: datetime) -> None:
        self._write(
            "INSERT INTO skills_usage (owner, name, origin, created_at, restored_at) "
            "VALUES (?, ?, 'user', ?, ?) ON CONFLICT (owner, name) DO UPDATE SET "
            "archived_at = NULL, restored_at = excluded.restored_at",
            (self.owner, name, format_utc(now), format_utc(now)),
        )

    def record(self, name: str) -> SkillRecord | None:
        rows = self._read(
            f"SELECT {_COLUMNS} FROM skills_usage WHERE owner = ? AND name = ?",  # noqa: S608
            (self.owner, name),
        )
        return _record(rows[0]) if rows else None

    def records(self) -> list[SkillRecord]:
        """The rows of this folder's skills."""
        rows = self._read(
            f"SELECT {_COLUMNS} FROM skills_usage WHERE owner = ?",  # noqa: S608
            (self.owner,),
        )
        return [_record(row) for row in rows]

    def every_record(self) -> list[SkillRecord]:
        """The rows of every folder's skills: the library's and each bot's."""
        rows = self._read(f"SELECT {_COLUMNS} FROM skills_usage ORDER BY owner, name", ())  # noqa: S608
        return [_record(row) for row in rows]

    def moved_to(self, name: str, owner: str) -> None:
        """A skill moved to another folder: its row goes with it, who created it included,
        over whatever that folder knew of the name."""
        try:
            connection = self._database.connect()
            try:
                with transaction(connection):
                    connection.execute(
                        "DELETE FROM skills_usage WHERE owner = ? AND name = ?", (owner, name)
                    )
                    connection.execute(
                        "UPDATE skills_usage SET owner = ? WHERE owner = ? AND name = ?",
                        (owner, self.owner, name),
                    )
            finally:
                connection.close()
        except (sqlite3.Error, DatabaseError) as error:
            _logger.warning("A skill's usage did not move: %s", error)

    def forget(self) -> None:
        """Drop this folder's rows: its bot was deleted, and its skills with it."""
        self._write("DELETE FROM skills_usage WHERE owner = ?", (self.owner,))

    def claim_curator_run(self, now: datetime, every: timedelta) -> CuratorClaim:
        """Decide, in one transaction, whether the curator runs now; record the run if so. Two
        processes due at once run it once: two launched seconds apart must
        not both prune the tree."""
        connection = self._database.connect()
        try:
            with transaction(connection):
                row: tuple[str | None] = connection.execute(
                    "SELECT max(started_at) FROM skills_curator_runs"
                ).fetchone()
                last = parse_utc(row[0]) if row[0] else None
                if last is not None and now - last < every:
                    return CuratorClaim.NOT_DUE
                connection.execute(
                    "INSERT INTO skills_curator_runs (started_at, first) VALUES (?, ?)",
                    (format_utc(now), int(last is None)),
                )
                return CuratorClaim.SEEDED if last is None else CuratorClaim.CLAIMED
        finally:
            connection.close()

    def finish_curator_run(self, started_at: datetime, archived: int) -> None:
        self._write(
            "UPDATE skills_curator_runs SET archived = ? WHERE started_at = ?",
            (archived, format_utc(started_at)),
        )

    def _write(self, sql: str, parameters: tuple[object, ...]) -> None:
        try:
            connection = self._database.connect()
            try:
                with transaction(connection):
                    connection.execute(sql, parameters)
            finally:
                connection.close()
        except (sqlite3.Error, DatabaseError) as error:
            _logger.warning("A skill's usage was not recorded: %s", error)

    def _read(self, sql: str, parameters: tuple[object, ...]) -> list[tuple[object, ...]]:
        connection = self._database.connect()
        try:
            return connection.execute(sql, parameters).fetchall()
        finally:
            connection.close()


def _record(row: tuple[object, ...]) -> SkillRecord:
    def moment(value: object) -> datetime | None:
        return parse_utc(value) if isinstance(value, str) else None

    return SkillRecord(
        name=str(row[0]),
        origin=Origin(str(row[1])),
        created_at=parse_utc(str(row[2])),
        use_count=int(str(row[3])),
        last_used_at=moment(row[4]),
        patch_count=int(str(row[5])),
        last_patched_at=moment(row[6]),
        restored_at=moment(row[7]),
        archived_at=moment(row[8]),
        owner=str(row[9]),
        creator=None if row[10] is None else str(row[10]),
    )
