"""Changing skills on disk: writes applied whole or not at all, the previous version kept first,
and archiving in place of deleting.

**A call is a list of operations**: every operation is checked against what the ones before it
left, and only when all pass does anything reach the disk. A write that fails half way puts back
every file it had touched. One lock covers the skills folder while a call runs, so two processes
cannot interleave their changes (Atlas's writes are rare enough for one lock rather than one per
skill).

**The previous version is kept before every write**, Atlas V3's skill vault
(`application/skill_revisions.py`), ported:

- What a call changed or removed in a skill goes to `.versions/<skill>/<version>/files/`, and the
  files it created are listed in `absent.json`, so going back removes them. V3 kept only the old
  `SKILL.md`; a skill now has support files too.
- **Fixed in the port:** V3 logged a failure to keep the version and wrote anyway, which is the
  silent loss the vault exists to prevent. Here the write is refused.
- Versions survive the skill: they live beside it, not in it (V3's `my-code-review` vanished
  with no trace before the vault existed).
- 20 versions per skill; going back keeps the current state as a version first, so a revert is
  never the operation that finally loses something (V3).
- V3's revision log in its trace table is left behind: the call ledger and the usage table say
  what happened.

**Deleting archives**: the skill moves to `.archive/<skill>` (a timestamp added when that name is
taken) and can be restored, even when the user asks to delete: Atlas keeps V3's rule that a skill
is never lost without a way back.

**A bot's skills** (`SkillShelves`, docs/ARCHITECTURE.md, Bots) are its own folder, then the
home's library, of which it sees the skills its definition lists (all of them when it lists none);
on a same name its own folder wins. Writing to these shared folders follows one rule: a new skill
is created in the agent's own folder, and an existing one is changed where it is found. Every new
skill records who created it.
"""

import json
import logging
import re
import shutil
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath

from atlas.home import locked, replace_file, utc_now
from atlas.skills.errors import SkillChangeError
from atlas.skills.model import (
    PLUGIN_SKILL,
    SKILL_CHARS,
    SKILL_FILE,
    SUPPORT_FILE_BYTES,
    Origin,
    name_problem,
    newest_first,
    replaced,
    skill_text_problem,
    support_path,
    to_prune,
    version_name,
)
from atlas.skills.store import SkillCatalog, SkillUsage, skill_file

VERSIONS_FOLDER = ".versions"
ARCHIVE_FOLDER = ".archive"
LOCK_FILE = ".lock"
_ABSENT = "absent.json"
_FILES = "files"
_ARCHIVE_STAMP = "%Y%m%d%H%M%S"

_logger = logging.getLogger("atlas.skills")

#: Asked before an operation changes or removes a file that exists: the skill's name, the file and
#: the action. It returns why the change is refused, or `None`. The background review's
#: read-before-write rule plugs in here.
type WriteGuard = Callable[[str, Path, str], str | None]

#: Told, once a call is applied, each file it touched: the skill's name, the file's path inside the
#: skill, what it held and what it holds, `None` for a file that was not or is no longer there.
#: The background review's skill diff plugs in here.
type WriteNote = Callable[[str, PurePosixPath, bytes | None, bytes | None], None]


@dataclass(frozen=True, slots=True)
class SkillOperation:
    """One operation of a call: create, patch, write_file, remove_file or delete."""

    action: str
    name: str
    content: str | None = None
    file_path: str | None = None
    file_content: str | None = None
    old_string: str | None = None
    new_string: str | None = None
    replace_all: bool = False


@dataclass(slots=True)
class _Plan:
    """What a call will do: the new text of each file (`None` removes it) and what each held."""

    skills: dict[str, Path]
    pending: dict[Path, bytes | None] = field(default_factory=lambda: {})
    originals: dict[Path, bytes | None] = field(default_factory=lambda: {})
    #: The skill each touched file belongs to.
    owners: dict[Path, str] = field(default_factory=lambda: {})
    created: set[str] = field(default_factory=lambda: set[str]())
    lines: list[str] = field(default_factory=lambda: [])


class _RefusedError(Exception):
    pass


class SkillLibrary:
    """The skills folder, as `skill_manage` and the curator change it."""

    def __init__(
        self,
        folder: Path,
        usage: SkillUsage | None = None,
        clock: Callable[[], datetime] = utc_now,
        *,
        creator: str | None = None,
    ) -> None:
        """`creator` is who makes the skills created here: the main agent, or a bot by its
        name; `None` for the user at the command line."""
        self.folder = folder
        self.usage = usage
        self._clock = clock
        self.creator = creator
        #: The skills folders of the plugins that are on, by plugin (step 16j): listed after the
        #: library's, named after their plugin, and never written.
        self.plugin_skills: Mapping[str, Path] = {}
        #: Their `commands/` folders, by plugin (step 16k), read as skills the same way.
        self.plugin_commands: Mapping[str, Path] = {}

    @property
    def catalog(self) -> SkillCatalog:
        return SkillCatalog(
            [self.folder], plugins=self.plugin_skills, commands=self.plugin_commands
        )

    # -- writing --------------------------------------------------------------------------------

    def apply(
        self,
        operations: Sequence[SkillOperation],
        origin: Origin,
        guard: WriteGuard | None = None,
        note: WriteNote | None = None,
    ) -> list[str]:
        """Apply the operations together, a line said for each. Raises `SkillChangeError`, with
        nothing changed, when one is refused or the disk fails. `note` hears what was written."""
        if not operations:
            raise SkillChangeError("There are no operations to apply.")
        deletes = [op for op in operations if op.action == "delete"]
        if deletes and len(operations) > 1:
            raise SkillChangeError(
                "A delete must be the only operation of its call. Nothing was changed."
            )
        self.folder.mkdir(parents=True, exist_ok=True)
        with locked(self.folder / LOCK_FILE):
            if deletes:
                name = deletes[0].name
                target = self._archive(name, guard)
                return [
                    f"Skill '{name}' archived to {target}; `atlas skills restore {name}` brings "
                    "it back."
                ]
            plan = _Plan(
                skills={
                    skill.name: skill.folder
                    for skill in self.catalog.skills()
                    if not PLUGIN_SKILL.fullmatch(skill.name)  # a plugin's is read only
                }
            )
            for number, operation in enumerate(operations, start=1):
                try:
                    plan.lines.append(self._plan(plan, operation, guard))
                except _RefusedError as refusal:
                    raise SkillChangeError(
                        f"Operation {number} ({operation.action} '{operation.name}') was refused: "
                        f"{refusal} Nothing was changed."
                    ) from None
            now = self._clock()
            kept = self._keep_versions(plan, now)
            self._write(plan)
        self._record(plan, origin, now)
        if note is not None:
            for path, data in plan.pending.items():
                name = plan.owners[path]
                note(name, _relative(path, plan.skills[name]), plan.originals[path], data)
        if kept:
            plan.lines.append(f"The previous version of {', '.join(kept)} was kept.")
        return plan.lines

    def _plan(self, plan: _Plan, op: SkillOperation, guard: WriteGuard | None) -> str:
        if op.action == "create":
            return self._plan_create(plan, op)
        folder = self._skill_folder(plan, op.name)
        if op.action == "patch":
            return self._plan_patch(plan, op, folder, guard)
        if op.action == "write_file":
            path = self._support(folder, op.file_path, SKILL_FILE, "patch SKILL.md instead")
            text = op.file_content
            if text is None:
                raise _RefusedError("file_content is required.")
            size = len(text.encode("utf-8"))
            if size > SUPPORT_FILE_BYTES:
                raise _RefusedError(
                    f"File content is {size:,} bytes (limit: {SUPPORT_FILE_BYTES:,} bytes / 1 "
                    "MiB). Consider splitting into smaller files."
                )
            if len(text) > SKILL_CHARS:
                raise _RefusedError(f"{op.file_path} is over {SKILL_CHARS:,} characters.")
            if self._current(plan, path) is not None:
                self._ask(guard, op.name, path, op.action)
            self._stage(plan, op.name, path, text.encode("utf-8"))
            return f"File '{op.file_path}' written to skill '{op.name}'."
        if op.action == "remove_file":
            path = self._support(folder, op.file_path, SKILL_FILE, "delete the skill instead")
            if self._current(plan, path) is None:
                raise _RefusedError(f"File '{op.file_path}' not found in skill '{op.name}'.")
            self._ask(guard, op.name, path, op.action)
            self._stage(plan, op.name, path, None)
            return f"File '{op.file_path}' removed from skill '{op.name}'."
        raise _RefusedError(
            f"Unknown action '{op.action}'. Use: create, patch, write_file, remove_file, delete."
        )

    def _plan_create(self, plan: _Plan, op: SkillOperation) -> str:
        if problem := name_problem(op.name):
            raise _RefusedError(problem)
        folder = self.folder / op.name
        if op.name in plan.skills or op.name in plan.created or folder.exists():
            where = plan.skills.get(op.name, folder)
            raise _RefusedError(f"A skill named '{op.name}' already exists at {where}.")
        if op.content is None:
            raise _RefusedError("content is required: the whole SKILL.md.")
        if problem := skill_text_problem(op.content, op.name, new=True):
            raise _RefusedError(problem)
        plan.created.add(op.name)
        plan.skills[op.name] = folder
        self._stage(plan, op.name, folder / SKILL_FILE, op.content.encode("utf-8"))
        return f"Skill '{op.name}' created at {folder}."

    def _plan_patch(
        self, plan: _Plan, op: SkillOperation, folder: Path, guard: WriteGuard | None
    ) -> str:
        target = op.file_path or SKILL_FILE
        path = self._support(folder, target, None, "")
        main = path.name == SKILL_FILE and path.parent == folder
        if main:
            path = skill_file(folder) or path
        current = self._current(plan, path)
        if current is None:
            raise _RefusedError(f"File not found: {target}")
        if op.content is not None and (op.old_string or op.new_string is not None):
            raise _RefusedError(
                "Give either content (a whole new SKILL.md) or old_string and new_string, not both."
            )
        if op.content is not None:
            if not main:
                raise _RefusedError(
                    "content rewrites SKILL.md only; use write_file for a support file."
                )
            if problem := skill_text_problem(op.content, op.name, new=False):
                raise _RefusedError(problem)
            self._ask(guard, op.name, path, op.action)
            self._stage(plan, op.name, path, op.content.encode("utf-8"))
            return f"Skill '{op.name}' updated (full rewrite)."
        if op.new_string is None:
            raise _RefusedError("new_string is required; an empty string deletes the match.")
        result = replaced(current, op.old_string or "", op.new_string, op.replace_all)
        if isinstance(result, str):
            raise _RefusedError(result)
        text, count = result
        if main and (problem := skill_text_problem(text, op.name, new=False)):
            raise _RefusedError(f"Patch would break SKILL.md structure: {problem}")
        if len(text) > SKILL_CHARS:
            raise _RefusedError(f"{target} would be over {SKILL_CHARS:,} characters.")
        self._ask(guard, op.name, path, op.action)
        self._stage(plan, op.name, path, text.encode("utf-8"))
        return f"Patched {target} in skill '{op.name}' ({count} replacement(s))."

    def _skill_folder(self, plan: _Plan, name: str) -> Path:
        folder = plan.skills.get(name)
        if folder is None and PLUGIN_SKILL.fullmatch(name):
            raise _RefusedError(
                f"'{name}' is a plugin's skill, which only its plugin changes. Create a skill of "
                "your own instead."
            )
        if folder is None:
            known = ", ".join(sorted(plan.skills)) or "none"
            raise _RefusedError(
                f"Skill '{name}' not found. The skills are: {known}. Create it first with a "
                "create operation."
            )
        return folder

    @staticmethod
    def _support(folder: Path, file_path: str | None, refused: str | None, instead: str) -> Path:
        relative = support_path(file_path or "")
        if isinstance(relative, str):
            raise _RefusedError(relative)
        if refused is not None and relative.parts == (refused,):
            raise _RefusedError(f"This operation cannot change {refused}; {instead}.")
        path = folder.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(folder.resolve()):
            raise _RefusedError(f"'{file_path}' leads outside the skill's folder.")
        return path

    @staticmethod
    def _current(plan: _Plan, path: Path) -> str | None:
        try:
            if path in plan.pending:
                data = plan.pending[path]
            else:
                data = path.read_bytes() if path.is_file() else None
            return None if data is None else data.decode("utf-8")
        except UnicodeDecodeError:
            raise _RefusedError(
                f"{path.name} is not UTF-8 text, so it is not changed here."
            ) from None
        except OSError as error:
            raise _RefusedError(f"{path.name} could not be read: {error}") from None

    @staticmethod
    def _ask(guard: WriteGuard | None, name: str, path: Path, action: str) -> None:
        if guard is not None and (refusal := guard(name, path, action)):
            raise _RefusedError(refusal)

    @staticmethod
    def _stage(plan: _Plan, name: str, path: Path, data: bytes | None) -> None:
        if path not in plan.originals:
            try:
                plan.originals[path] = path.read_bytes() if path.is_file() else None
            except OSError as error:
                raise _RefusedError(f"{path.name} could not be read: {error}") from None
        plan.owners[path] = name
        plan.pending[path] = data

    def _keep_versions(self, plan: _Plan, now: datetime) -> list[str]:
        """Keep, for each skill that existed before the call, what its touched files held. A
        failure refuses the whole call: nothing is written without its way back."""
        kept: list[str] = []
        for name in sorted({plan.owners[path] for path in plan.pending}):
            if name in plan.created:
                continue
            folder = plan.skills[name]
            before = {
                _relative(path, folder): original
                for path, original in plan.originals.items()
                if plan.owners[path] == name
            }
            try:
                self._save_version(name, before, now)
            except OSError as error:
                raise SkillChangeError(
                    f"The previous version of '{name}' could not be kept ({error}), so nothing "
                    "was changed."
                ) from error
            kept.append(name)
        return kept

    def _save_version(
        self, name: str, before: dict[PurePosixPath, bytes | None], now: datetime
    ) -> str:
        versions = self.folder / VERSIONS_FOLDER / name
        versions.mkdir(parents=True, exist_ok=True)
        version = version_name(_names(versions), now)
        target = versions / version
        target.mkdir()
        absent: list[str] = []
        for relative, data in sorted(before.items()):
            if data is None:
                absent.append(str(relative))
                continue
            path = target / _FILES / Path(*relative.parts)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (target / _ABSENT).write_text(json.dumps(absent), encoding="utf-8")
        for old in to_prune(_names(versions)):
            shutil.rmtree(versions / old, ignore_errors=True)
        return version

    def _write(self, plan: _Plan) -> None:
        done: list[Path] = []
        try:
            for path, data in plan.pending.items():
                if data is None:
                    path.unlink(missing_ok=True)
                    _drop_empty(path.parent, plan.skills[plan.owners[path]])
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    replace_file(path, data)
                done.append(path)
        except OSError as error:
            self._undo(plan, done)
            raise SkillChangeError(
                f"Writing the skills failed ({error}); every file was put back as it was."
            ) from error

    def _undo(self, plan: _Plan, done: Iterable[Path]) -> None:
        for path in done:
            original = plan.originals.get(path)
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    replace_file(path, original)
            except OSError:
                _logger.exception("A skill file could not be put back: %s", path)
        for name in plan.created:
            shutil.rmtree(plan.skills[name], ignore_errors=True)

    def _record(self, plan: _Plan, origin: Origin, now: datetime) -> None:
        if self.usage is None:
            return
        for name in sorted({plan.owners[path] for path in plan.pending}):
            if name in plan.created:
                self.usage.created(name, origin, now, self.creator)
            else:
                self.usage.patched(name, now)

    def put(self, name: str, files: Mapping[PurePosixPath, bytes]) -> None:
        """Make a skill's folder hold exactly these files: a bundled skill copied in, updated or
        put back. What the folder held before is kept as a version first, as for every write, so
        `atlas skills revert` undoes it."""
        self.folder.mkdir(parents=True, exist_ok=True)
        folder = self.folder / name
        with locked(self.folder / LOCK_FILE):
            plan = _Plan(skills={name: folder})
            existed = folder.is_dir()
            try:
                if existed:
                    for path in sorted(folder.rglob("*")):
                        if path.is_file() and _relative(path, folder) not in files:
                            self._stage(plan, name, path, None)
                else:
                    plan.created.add(name)
                for relative, data in files.items():
                    self._stage(plan, name, folder.joinpath(*relative.parts), data)
            except _RefusedError as refused:
                raise SkillChangeError(str(refused)) from None
            if existed:
                self._keep_versions(plan, self._clock())
            self._write(plan)

    # -- versions -------------------------------------------------------------------------------

    def versions(self, name: str) -> list[str]:
        """The kept versions of a skill, newest first; there even when the skill is gone."""
        return newest_first(_names(self.folder / VERSIONS_FOLDER / name))

    def revert(self, name: str, version: str | None = None) -> str:
        """Put a skill's files back as they were before a version's write: the latest, or the one
        named. The current state is kept as a version first. Returns the version restored."""
        self.folder.mkdir(parents=True, exist_ok=True)
        with locked(self.folder / LOCK_FILE):
            kept = self.versions(name)
            if not kept:
                raise SkillChangeError(f"No version of '{name}' is kept.")
            chosen = version or kept[0]
            if chosen not in kept:
                raise SkillChangeError(
                    f"'{chosen}' is not a version of '{name}'. The versions are: {', '.join(kept)}."
                )
            source = self.folder / VERSIONS_FOLDER / name / chosen
            files = _version_files(source)
            skill = self.catalog.get(name)
            folder = skill.folder if skill is not None else self.folder / name
            plan = _Plan(skills={name: folder})
            for relative, data in files.items():
                self._stage(plan, name, folder.joinpath(*relative.parts), data)
            now = self._clock()
            if folder.exists():
                self._keep_versions(plan, now)
            self._write(plan)
        if self.usage is not None:
            self.usage.patched(name, now)
        return chosen

    # -- archive and restore --------------------------------------------------------------------

    def archive(self, name: str) -> Path:
        """Move a skill to the archive. Returns where it went."""
        self.folder.mkdir(parents=True, exist_ok=True)
        with locked(self.folder / LOCK_FILE):
            return self._archive(name, None)

    def _archive(self, name: str, guard: WriteGuard | None) -> Path:
        skill = self.catalog.get(name)
        if skill is None:
            known = ", ".join(sorted(s.name for s in self.catalog.skills())) or "none"
            raise SkillChangeError(f"Skill '{name}' not found. The skills are: {known}.")
        if guard is not None and (refusal := guard(name, skill.folder, "delete")):
            raise SkillChangeError(refusal)
        archive = self.folder / ARCHIVE_FOLDER
        archive.mkdir(exist_ok=True)
        now = self._clock()
        target = archive / name
        if target.exists():
            target = archive / f"{name}-{now.strftime(_ARCHIVE_STAMP)}"
        try:
            skill.folder.rename(target)
        except OSError:
            try:
                shutil.move(skill.folder, target)
            except OSError as error:
                raise SkillChangeError(f"Skill '{name}' could not be archived: {error}") from error
        if self.usage is not None:
            self.usage.archived(name, now)
        return target

    def archived(self) -> list[str]:
        return sorted(_names(self.folder / ARCHIVE_FOLDER))

    def restore(self, name: str) -> Path:
        """Bring an archived skill back. Returns its folder."""
        self.folder.mkdir(parents=True, exist_ok=True)
        with locked(self.folder / LOCK_FILE):
            archive = self.folder / ARCHIVE_FOLDER
            exact = archive / name
            stamp = re.compile(rf"{re.escape(name)}-\d{{14}}")
            stamped = sorted((n for n in _names(archive) if stamp.fullmatch(n)), reverse=True)
            source = exact if exact.is_dir() else (archive / stamped[0] if stamped else None)
            if source is None:
                listed = ", ".join(self.archived()) or "none"
                raise SkillChangeError(
                    f"No archived skill is named '{name}'. The archived ones are: {listed}."
                )
            target = self.folder / name
            if target.exists() or self.catalog.get(name) is not None:
                raise SkillChangeError(
                    f"A skill named '{name}' exists already; rename or archive it first."
                )
            try:
                source.rename(target)
            except OSError as error:
                raise SkillChangeError(f"Skill '{name}' could not be restored: {error}") from error
        if self.usage is not None:
            self.usage.restored(name, self._clock())
        return target


def _relative(path: Path, folder: Path) -> PurePosixPath:
    return PurePosixPath(*path.relative_to(folder).parts)


def _names(folder: Path) -> list[str]:
    try:
        return [child.name for child in folder.iterdir() if child.is_dir()]
    except OSError:
        return []


def _version_files(source: Path) -> dict[PurePosixPath, bytes | None]:
    files: dict[PurePosixPath, bytes | None] = {}
    root = source / _FILES
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file():
                files[_relative(path, root)] = path.read_bytes()
    try:
        absent: object = json.loads((source / _ABSENT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        absent = []
    if isinstance(absent, list):
        for item in absent:  # pyright: ignore[reportUnknownVariableType]
            files[PurePosixPath(str(item))] = None  # pyright: ignore[reportUnknownArgumentType]
    return files


def _drop_empty(folder: Path, stop: Path) -> None:
    """Remove `folder` and its parents up to `stop` while they are empty."""
    while folder != stop and folder.is_relative_to(stop):
        try:
            folder.rmdir()
        except OSError:
            return
        folder = folder.parent


class SkillShelves:
    """A bot's skills: its own folder first, then the home's library."""

    def __init__(
        self, own: SkillLibrary, shared: SkillLibrary, allowed: Collection[str] | None = None
    ) -> None:
        """`allowed` names the library's skills the bot sees; `None`, all of them. Its own are
        always seen."""
        self.own = own
        self.shared = shared
        self._allowed = None if allowed is None else frozenset(allowed)

    @property
    def catalog(self) -> SkillCatalog:
        plugins = self.shared.plugin_skills
        commands = self.shared.plugin_commands
        only = (
            None
            if self._allowed is None
            else {self.shared.folder: self._allowed}
            | dict.fromkeys([*plugins.values(), *commands.values()], self._allowed)
        )
        return SkillCatalog(
            [self.own.folder, self.shared.folder], only=only, plugins=plugins, commands=commands
        )

    def apply(
        self,
        operations: Sequence[SkillOperation],
        origin: Origin,
        guard: WriteGuard | None = None,
        note: WriteNote | None = None,
    ) -> list[str]:
        """Apply the operations in the folder they belong to: a new skill in the bot's own, a
        change where the skill is. Raises `SkillChangeError`, with nothing changed, when they
        belong to both folders, or a new skill would hide one of the library's."""
        mine = {skill.name for skill in self.own.catalog.skills()}
        seen = {skill.name for skill in self.catalog.skills()}
        libraries: list[SkillLibrary] = []
        for operation in operations:
            in_library = operation.name in seen and operation.name not in mine
            if operation.action == "create" and in_library:
                raise SkillChangeError(
                    f"A skill named '{operation.name}' is already in the home's library. Patch "
                    "it, or pick another name. Nothing was changed."
                )
            library = self.shared if in_library else self.own
            if library not in libraries:
                libraries.append(library)
        if len(libraries) > 1:
            raise SkillChangeError(
                "One call changes your own skills or the home's library, not both. Split it in "
                "two calls. Nothing was changed."
            )
        return (libraries[0] if libraries else self.own).apply(operations, origin, guard, note)

    def used(self, name: str, now: datetime) -> None:
        """Count a load where the skill is."""
        library = self.own if self.own.catalog.get(name) is not None else self.shared
        if library.usage is not None:
            library.usage.used(name, now)


def share(name: str, source: SkillLibrary, library: SkillLibrary) -> Path:
    """Move a bot's skill to the home's library, with what Atlas knows of it: who created it
    stays. Raises `SkillChangeError` when it is not there, or the library has the name."""
    skill = source.catalog.get(name)
    if skill is None:
        raise SkillChangeError(f"There is no skill '{name}' in {source.folder}.")
    target = library.folder / skill.folder.name
    library.folder.mkdir(parents=True, exist_ok=True)
    with locked(source.folder / LOCK_FILE), locked(library.folder / LOCK_FILE):
        if target.exists() or library.catalog.get(name) is not None:
            raise SkillChangeError(
                f"The library already has a skill '{name}'. Rename one of them first."
            )
        try:
            shutil.move(str(skill.folder), str(target))
        except OSError as error:
            raise SkillChangeError(f"'{name}' could not be moved to {target}: {error}") from error
    if source.usage is not None and library.usage is not None:
        source.usage.moved_to(name, library.usage.owner)
    return target
