"""The installation's rules, with no I/O: what the app's build is made from, the launcher entry,
the toolchain the installer leaves beside the checkout, and what the app's update says.

The build is redone when what it is made from changed: a content stamp, a hash of its inputs
taken in Python, since one app has few of them.
"""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import cast

from atlas.installation.errors import InstallationError

#: What the app's build is made from, under `apps/desktop`: folders whole, files by name.
BUILD_INPUTS: tuple[str, ...] = (
    "electron",
    "src",
    "scripts",
    "index.html",
    "package.json",
    "package-lock.json",
    "vite.config.ts",
    "tsconfig.base.json",
    "tsconfig.json",
    "tsconfig.electron.json",
)
#: What the app's packages are installed from.
PACKAGE_LOCK = "package-lock.json"
#: The stamps, beside what they stamp: both folders are rebuilt whole, so a stamp never outlives
#: its build.
BUILD_STAMP = "out/.atlas-build-stamp"
PACKAGES_STAMP = "node_modules/.atlas-packages-stamp"
#: Held by a build and by an update, so neither runs under the other.
BUILD_LOCK = ".atlas-build.lock"
#: The launcher's name in the Start Menu and the app grid.
APP_NAME = "Atlas"


def desktop_entry(exec_command: str, working_folder: str) -> str:
    """The Linux app grid's entry."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Launch the Atlas app\n"
        f"Exec={exec_command}\n"
        f"Path={working_folder}\n"
        "Terminal=false\n"
        "Categories=Utility;\n"
        "StartupNotify=true\n"
    )


#: The installer's tools, beside the checkout (`<installation>/tools/`), and the file that names
#: each program in them (docs/ARCHITECTURE.md, The installer). A development checkout has none.
TOOLS_FOLDER = "tools"
TOOLCHAIN_FILE = "toolchain.json"
TOOLCHAIN_VERSION = 1
_PROGRAM_NAME = re.compile(r"[a-z][a-z0-9-]*")


def parse_toolchain(text: str) -> dict[str, PurePosixPath]:
    """The programs `toolchain.json` names, each a path under the tools folder.

    The installer writes it: `{"version": 1, "programs": {"uv": "uv-0.12.5-x86_64-pc-windows-msvc/
    uv.exe", ...}}`. A path is relative, with `/`, and never leaves the folder, so the whole
    installation may move. Anything else is refused: a broken toolchain is repaired by running the
    installer again, never guessed around.
    """
    broken = f"The installation's {TOOLCHAIN_FILE} is damaged; run the installer again to repair it"
    try:
        found: object = json.loads(text)
    except json.JSONDecodeError as error:
        raise InstallationError(f"{broken} ({error.msg}).") from None
    if not isinstance(found, dict):
        raise InstallationError(f"{broken} (it is not an object).")
    data = cast("dict[str, object]", found)  # a JSON object's keys are text
    if data.get("version") != TOOLCHAIN_VERSION:
        raise InstallationError(
            f"{broken} (version {data.get('version')!r}; this Atlas reads {TOOLCHAIN_VERSION})."
        )
    listed = data.get("programs")
    if not isinstance(listed, dict):
        raise InstallationError(f"{broken} (no programs).")
    programs: dict[str, PurePosixPath] = {}
    for name, where in cast("dict[str, object]", listed).items():
        if not _PROGRAM_NAME.fullmatch(name):
            raise InstallationError(f"{broken} (a program named {name!r}).")
        if not isinstance(where, str) or not where or "\\" in where:
            raise InstallationError(f"{broken} ({name}'s path {where!r}).")
        path = PurePosixPath(where)
        if path.is_absolute() or ".." in path.parts or ":" in where:
            raise InstallationError(f"{broken} ({name}'s path {where!r} leaves the tools folder).")
        programs[name] = path
    return programs


# -- the update from the app -----------------------------------------------------------------------

#: The most commits an update's status names; its count says how many there are.
UPDATE_COMMITS = 30


@dataclass(frozen=True, slots=True)
class UpdateStatus:
    """Whether the installation's branch has commits it lacks, for the app's update button."""

    #: The app offers updates here: an installation whose branch has an upstream. A development
    #: checkout updates with git, left to the user.
    supported: bool
    #: Why not, when not supported: `development`, `no-upstream`.
    reason: str = ""
    current: str = ""
    target: str = ""
    #: How many commits it lacks, and the newest ones' subjects, oldest first.
    behind: int = 0
    commits: tuple[str, ...] = ()
    #: Each of those commits' `Notes-pt-BR` trailer, the subject said in Portuguese for the app in
    #: Portuguese, or "" for a commit with none (AGENTS.md, Commits).
    commits_pt_br: tuple[str, ...] = ()
    #: Changes to the installation's files, which the update refuses: an installation is not
    #: edited.
    dirty: bool = False
    #: Why the look failed (offline, a login refused): nothing is offered, and it looks again
    #: later.
    error: str = ""

    @property
    def available(self) -> bool:
        return self.supported and not self.error and self.behind > 0

    def to_json(self) -> str:
        return json.dumps({**asdict(self), "available": self.available}, ensure_ascii=False)


@dataclass(frozen=True, slots=True)
class HandedOff:
    """How the update the app handed off ended, for the app it opens again to say: `ok` only
    on the new version; one that failed went back to the commit it was on, and says why."""

    ok: bool
    before: str = ""
    after: str = ""
    commits: tuple[str, ...] = ()
    error: str = ""
    #: Where the steps' output is, in the app's own folder.
    log: str = ""
    #: When it ended, UTC.
    ended_at: str = ""
    #: Why the app was not opened again, when it was not (Linux's sandbox helper, rebuilt, is no
    #: longer root's).
    reopen: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)
