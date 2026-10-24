"""The homes of one machine, the default one and named ones (ARCHITECTURE.md, Backup and homes).

A profile in Atlas is `USER.md`, so these are homes:

- **The default home is the root**, `~/.atlas` or `ATLAS_HOME`; named ones are whole homes in its
  `homes/<name>/`, each with its own settings, database, memory, skills, logs and vault. An
  `ATLAS_HOME` that points inside `homes/` names that home itself.
- **The active home** is the root's `active_home`, written by `atlas home use`; it opens when no
  `--home` names another.
- **A home being made lives in `homes/<name>.creating/`** and becomes `homes/<name>/` only once
  whole, so a half-made home is never listed nor opened.
- **`--clone` copies** the settings, the models, the MCP servers, the vault with its keys, the
  profile and memory files, and the skills. Conversations and the database start empty.
- **One server per home**: a server holds the home's `server.lock` while it runs, and a second
  server, a rename or a delete is refused while it is held: two agents never share one home.
"""

import os
import shutil
import stat
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from atlas.home.errors import FileBusyError, HomeBusyError, HomeNameError
from atlas.home.files import locked, replace_file
from atlas.home.model import (
    CONFIG_FILE,
    DEFAULT_HOME,
    HOMES_FOLDER,
    MCP_FILE,
    MEMORY_FOLDER,
    MODELS_FILE,
    SECRETS_FOLDER,
    SKILLS_FOLDER,
    SOUL_FILE,
    Home,
    home_name_problem,
)
from atlas.home.store import open_home, private_folder

#: What a home being made is called until it is whole.
CREATING_SUFFIX = ".creating"
#: The memory files a clone carries (the memory subject's `USER.md` and `MEMORY.md`): they are part
#: of the agent's identity, as its `SOUL.md` is, which a clone carries with the settings.
_CLONED_MEMORY = ("USER.md", "MEMORY.md")
#: The vault's own lock is per home and not copied.
_NOT_CLONED_SECRETS = ("vault.lock",)


@dataclass(frozen=True, slots=True)
class HomeInfo:
    name: str
    path: Path
    #: It opens when no home is named.
    active: bool
    #: A server serves it now.
    served: bool


def home_root(home: Path) -> Path:
    """The root of the homes a home belongs to: itself for the default one."""
    return home.parent.parent if home.parent.name == HOMES_FOLDER else home


def home_name(home: Path) -> str:
    """A home's name: `default` for the root."""
    return home.name if home.parent.name == HOMES_FOLDER else DEFAULT_HOME


def home_path(root: Path, name: str) -> Path:
    """Where a home of this name lives, whether or not it exists."""
    problem = home_name_problem(name)
    if problem is not None:
        raise HomeNameError(problem)
    return root if name == DEFAULT_HOME else root / HOMES_FOLDER / name


def active_home(root: Path) -> str:
    """The home that opens when none is named."""
    try:
        name = Home(root).active_home_file.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return DEFAULT_HOME
    return name or DEFAULT_HOME


def choose_home(declared: Path, name: str | None) -> Path:
    """The home a command runs in. `declared` is `ATLAS_HOME` (or `~/.atlas`); `name` is `--home`.

    A declared home inside `homes/` is that home; otherwise the root's named home, or its active
    one when none is named.
    """
    if name is None and declared.parent.name == HOMES_FOLDER:
        return declared
    root = home_root(declared)
    chosen = name if name is not None else active_home(root)
    path = home_path(root, chosen)
    if not path.is_dir() and chosen != DEFAULT_HOME:
        if name is None:
            raise HomeNameError(
                f"The active home {chosen!r} no longer exists. Switch back with `atlas home use "
                "default`."
            )
        raise HomeNameError(
            f"There is no home {chosen!r}. Make it with `atlas home create {chosen}`, or see "
            "`atlas home list`."
        )
    return path


def list_homes(root: Path) -> list[HomeInfo]:
    """The default home and the named ones, by name."""
    active = active_home(root)
    found = [HomeInfo(DEFAULT_HOME, root, active == DEFAULT_HOME, is_served(root))]
    folder = root / HOMES_FOLDER
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            if path.is_dir() and home_name_problem(path.name) is None:
                found.append(HomeInfo(path.name, path, active == path.name, is_served(path)))
    return found


def create_home(
    root: Path,
    name: str,
    *,
    clone: Path | None = None,
    fill: Callable[[Path], None] | None = None,
) -> Path:
    """A new named home, empty or with `clone`'s settings, keys, memory and skills. `fill` writes
    into the home while it is being made (an import restores a backup there), so a failure leaves
    no home behind."""
    path = home_path(root, name)
    if name == DEFAULT_HOME or path.exists():
        raise HomeNameError(f"There is already a home {name!r}.")
    open_home(root)
    folder = root / HOMES_FOLDER
    folder.mkdir(exist_ok=True)
    staging = folder / f"{name}{CREATING_SUFFIX}"
    if staging.exists():
        _remove_tree(staging)  # what an interrupted create left
    open_home(staging)
    try:
        if clone is not None:
            _clone_into(clone, staging)
        if fill is not None:
            fill(staging)
        staging.rename(path)
    except BaseException:
        _remove_tree(staging)
        raise
    return path


def use_home(root: Path, name: str) -> None:
    """Make `name` the home that opens when none is named."""
    path = home_path(root, name)
    if not path.is_dir():
        raise HomeNameError(f"There is no home {name!r}.")
    active_file = Home(root).active_home_file
    if name == DEFAULT_HOME:
        active_file.unlink(missing_ok=True)
    else:
        replace_file(active_file, f"{name}\n".encode())


def rename_home(root: Path, old: str, new: str) -> Path:
    """Rename a named home; the active home follows it."""
    if DEFAULT_HOME in (old, new):
        raise HomeNameError("The default home keeps its name.")
    source, target = home_path(root, old), home_path(root, new)
    if not source.is_dir():
        raise HomeNameError(f"There is no home {old!r}.")
    if target.exists():
        raise HomeNameError(f"There is already a home {new!r}.")
    _refuse_served(source, "renamed")
    source.rename(target)
    if active_home(root) == old:
        use_home(root, new)
    return target


def delete_home(root: Path, name: str) -> None:
    """Delete a named home and everything in it; the active home falls back to the default."""
    if name == DEFAULT_HOME:
        raise HomeNameError("The default home cannot be deleted.")
    path = home_path(root, name)
    if not path.is_dir():
        raise HomeNameError(f"There is no home {name!r}.")
    _refuse_served(path, "deleted")
    if active_home(root) == name:
        use_home(root, DEFAULT_HOME)
    _remove_tree(path)


def is_served(home: Path) -> bool:
    """Whether a server serves this home now."""
    lock_file = Home(home).server_lock_file
    if not lock_file.exists():
        return False
    try:
        with locked(lock_file, timeout=0):
            return False
    except FileBusyError:
        return True


@contextmanager
def serving(home: Home) -> Generator[None]:
    """Hold the home for one server; another server holding it is refused at once."""
    try:
        with locked(home.server_lock_file, timeout=0):
            yield
    except FileBusyError as error:
        raise HomeBusyError(
            f"Another Atlas server is serving {home.root}. Two would write the same memory; stop "
            "it first, or serve another home with --home."
        ) from error


def _refuse_served(home: Path, what: str) -> None:
    if is_served(home):
        raise HomeBusyError(
            f"A server is serving the home at {home}, so it cannot be {what} now. Stop it first."
        )


def _clone_into(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise HomeNameError(f"There is no home at {source} to clone.")
    for name in (CONFIG_FILE, MODELS_FILE, MCP_FILE, SOUL_FILE):
        if (source / name).is_file():
            shutil.copy2(source / name, target / name)
    secrets = source / SECRETS_FOLDER
    if secrets.is_dir():
        # Closed while empty, so the copied key keeps only the owner's access.
        private_folder(target / SECRETS_FOLDER)
        for item in secrets.iterdir():
            if item.is_file() and item.name not in _NOT_CLONED_SECRETS:
                shutil.copyfile(item, target / SECRETS_FOLDER / item.name)
                if os.name != "nt":
                    (target / SECRETS_FOLDER / item.name).chmod(0o600)
    for name in _CLONED_MEMORY:
        memory = source / MEMORY_FOLDER / name
        if memory.is_file():
            (target / MEMORY_FOLDER).mkdir(exist_ok=True)
            shutil.copy2(memory, target / MEMORY_FOLDER / name)
    skills = source / SKILLS_FOLDER
    if skills.is_dir():
        shutil.copytree(skills, target / SKILLS_FOLDER, symlinks=True)


def _remove_tree(path: Path) -> None:
    """Remove a folder; a read-only file (git's objects on Windows) is made writable first."""

    def writable(function: Callable[..., object], failed: str, _error: BaseException) -> None:
        os.chmod(failed, stat.S_IWRITE)  # noqa: PTH101 - the path rmtree hands over
        function(failed)

    shutil.rmtree(path, onexc=writable)
