"""The installation's rules, with no I/O: what the app's build is made from, the launcher entry,
the toolchain the installer leaves beside the checkout, and what the app's update says.

The build is redone when what it is made from changed: a content stamp, a hash of its inputs
taken in Python, since one app has few of them.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from typing import Literal, cast

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
#: The launcher's icon, under `apps/desktop`, as the app's `npm run icons` draws it: the Start
#: Menu's on Windows, the app grid's on Linux.
WINDOWS_ICON = "resources/icon.ico"
LINUX_ICON = "resources/icon.png"


def desktop_entry(exec_command: str, working_folder: str, icon: str) -> str:
    """The Linux app grid's entry."""
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        f"Name={APP_NAME}\n"
        "Comment=Launch the Atlas app\n"
        f"Exec={exec_command}\n"
        f"Path={working_folder}\n"
        f"Icon={icon}\n"
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

#: How an installation is updated (docs/ARCHITECTURE.md, The update of a Compose host): `native`,
#: the checkout the app or `atlas update` brings to `main` itself; `compose`, a container that
#: Docker Compose runs on a home server, which the host updater updates from outside.
type InstallationKind = Literal["native", "compose"]
#: Says which, set by whoever runs Atlas: the host updater's Compose file sets `compose`. Unset is
#: `native`, every installation before this one.
INSTALLATION_VARIABLE = "ATLAS_INSTALLATION"
#: Where the container reaches the host updater (`atlas.installation.host_updater.SOCKET_FILE`).
HOST_SOCKET = "/run/atlas-updater/updater.sock"


def installation_kind(environ: Mapping[str, str]) -> InstallationKind:
    """This installation's kind, from `ATLAS_INSTALLATION`; anything unknown is refused, never
    guessed: a wrong guess would update the wrong way."""
    value = environ.get(INSTALLATION_VARIABLE, "").strip().lower()
    if value in ("", "native"):
        return "native"
    if value == "compose":
        return "compose"
    raise InstallationError(
        f"{INSTALLATION_VARIABLE}={value!r} is no installation kind Atlas knows (native, compose)."
    )


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
    #: How this installation updates (`InstallationKind`).
    kind: str = "native"
    #: Why CI did not verify the target, when it could not say (a Compose host's update only):
    #: the user then confirms it explicitly. "" when verified, or when nobody asks CI (native).
    unverified: str = ""

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


@dataclass(frozen=True, slots=True)
class UpdateProgress:
    """A Compose host's update, as its updater says: whether one runs and its step (`building`,
    `snapshot`, `starting`, `rolling back`), and how the last one ended."""

    running: bool
    step: str = ""
    started_at: str = ""
    target: str = ""
    ended: HandedOff | None = None


def _texts(value: object) -> tuple[str, ...]:
    return (
        tuple(str(item) for item in cast("list[object]", value)) if isinstance(value, list) else ()
    )


def _fields(value: object) -> Mapping[str, object]:
    return cast("Mapping[str, object]", value) if isinstance(value, dict) else {}


def status_from_host(found: object) -> UpdateStatus:
    """The host updater's look, read into the app's status; one out of shape is a failed look."""
    data = _fields(found)
    if not isinstance(data.get("supported"), bool):
        return UpdateStatus(True, kind="compose", error="The host updater answered no status.")
    behind = data.get("behind")
    return UpdateStatus(
        supported=bool(data["supported"]),
        reason=str(data.get("reason", "")),
        current=str(data.get("current", "")),
        target=str(data.get("target", "")),
        behind=behind if isinstance(behind, int) and not isinstance(behind, bool) else 0,
        commits=_texts(data.get("commits")),
        commits_pt_br=_texts(data.get("commits_pt_br")),
        dirty=data.get("dirty") is True,
        error=str(data.get("error", "")),
        kind="compose",
        unverified=str(data.get("unverified", "")),
    )


def handed_off_from_host(found: object) -> HandedOff | None:
    """How the host updater's last update ended, or `None` when it says none."""
    data = _fields(found)
    if not isinstance(data.get("ok"), bool):
        return None
    return HandedOff(
        ok=bool(data["ok"]),
        before=str(data.get("before", "")),
        after=str(data.get("after", "")),
        commits=_texts(data.get("commits")),
        error=str(data.get("error", "")),
        log=str(data.get("log", "")),
        ended_at=str(data.get("ended_at", "")),
        reopen=str(data.get("reopen", "")),
    )


def progress_from_host(found: object) -> UpdateProgress:
    data = _fields(found)
    return UpdateProgress(
        running=data.get("running") is True,
        step=str(data.get("step", "")),
        started_at=str(data.get("started_at", "")),
        target=str(data.get("target", "")),
        ended=handed_off_from_host(data.get("ended")),
    )
