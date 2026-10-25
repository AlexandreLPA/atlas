"""The host updater: the update of an Atlas that Docker Compose runs on a home server, done on the
host by root and asked for by the Atlas in the container (docs/ARCHITECTURE.md, The update of a
Compose host).

**Standalone on purpose.** The installer (`scripts/compose-host/install.sh`) copies this one file
to a root-owned folder, and systemd runs it there with the system's Python, 3.11 or newer, the
standard library only. Atlas imports it too, for its constants and its tests, but it imports
nothing of Atlas's.

**What root never does**: run Python, Compose YAML or a Dockerfile from the user's checkout, which
the user (and the agent, through them) can write. The source comes from a git mirror of its own
(`/var/lib/atlas-updater/mirror.git`), fetched from the address in its root-owned settings, with
hooks off; each build is a clean export of one commit, never a working tree. The Compose file and
the Dockerfile are root's, in `/etc/atlas-updater/`, checked before every update: no Docker
socket, no privileged service, no host folder but this updater's own.

**Who may ask.** The container reaches it through one Unix socket, owned by the container's user,
whose peer must be that user's main process in the container (`PeerCheck`): the server, never a
command the agent runs. A request is one line of JSON of four shapes, no path, no command, no
Compose override; anything else is refused. On the host, `atlas update` runs it as root through
`sudo` (`scripts/compose-host/atlas`).

**An update** moves only forward, to the commit the user was shown, verified by CI when GitHub
says so; one CI could not verify needs the user's explicit confirmation, and one CI failed is not
offered. The new image is built while the old service runs (a failed build changes nothing); then
the service stops, `/data` is snapshotted, the new container starts, and its health and image are
checked. Any failure after the stop puts the old image and the snapshot back.
"""

import argparse
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol, TextIO, cast

if sys.platform == "win32":  # the tests only: the updater itself runs on Linux
    import msvcrt
else:
    import fcntl

# -- fixed places ----------------------------------------------------------------------------------

#: The updater's settings, root's: what it updates and from where.
CONFIG_FILE = Path("/etc/atlas-updater/config.toml")
#: Its state, root's alone: the mirror, the builds, the snapshots, the log and `state.json`.
STATE_FOLDER = Path("/var/lib/atlas-updater")
#: The folder of its socket, the one host folder the container mounts, at the same path.
SOCKET_FOLDER = PurePosixPath("/run/atlas-updater")
SOCKET_FILE = SOCKET_FOLDER / "updater.sock"
#: The installation kind the container is told, by the variable Atlas reads
#: (`atlas.installation.installation_kind`).
INSTALLATION_VARIABLE = "ATLAS_INSTALLATION"
COMPOSE = "compose"

# -- limits ----------------------------------------------------------------------------------------

#: The version of the requests' shape.
PROTOCOL_VERSION = 1
#: The longest request line.
REQUEST_BYTES = 4096
#: How long a look is reused: GitHub lets an address 60 unauthenticated calls an hour.
CHECK_EVERY = 60.0
#: The least time between two updates started.
APPLY_EVERY = 120.0
#: The most requests the socket takes in a minute, of every kind.
REQUESTS_PER_MINUTE = 30
#: How long the new container has to call itself healthy.
HEALTH_SECONDS = 180.0
#: How many snapshots of `/data` are kept, the newest.
KEEP_SNAPSHOTS = 3
#: How much of the log the app is given.
LOG_TAIL_BYTES = 64 * 1024
#: The most commits a look names, as the app's own update (`atlas.installation.UPDATE_COMMITS`).
UPDATE_COMMITS = 30
#: The image's label naming the commit it was built from.
REVISION_LABEL = "org.atlas.revision"

#: The only host folder a service may mount: this updater's socket folder.
ALLOWED_BINDS = frozenset({str(SOCKET_FOLDER)})
#: The commands' PATH, fixed: never the caller's.
_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_SHA = re.compile(r"[0-9a-f]{40}")
_NAME = re.compile(r"[a-z0-9][a-z0-9_.-]{0,62}")
_IMAGE = re.compile(r"[a-z0-9][a-z0-9_./-]{0,127}:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}")
_GITHUB = re.compile(r"[A-Za-z0-9-]{1,39}/[A-Za-z0-9_.-]{1,100}")
_BRANCH = re.compile(r"[A-Za-z0-9][A-Za-z0-9_./-]{0,99}")
#: A commit's subject and its `Notes-pt-BR` trailer, as the app's own update reads them.
_SUBJECT_AND_NOTE = "%s%x1f%(trailers:key=Notes-pt-BR,valueonly,unfold,separator=%x20)%x1e"


class UpdaterError(Exception):
    """The updater cannot do what was asked; the message says why, for the user."""


class BuildError(UpdaterError):
    """The new image did not build: nothing was touched."""


def utc_text(moment: float) -> str:
    """A moment as the app reads it: UTC, to the millisecond, with a `Z`."""
    stamp = datetime.fromtimestamp(moment, UTC)
    return stamp.isoformat(timespec="milliseconds").replace("+00:00", "Z")


# -- settings --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Settings:
    """What the updater updates, and from where; `config.toml` in `/etc/atlas-updater/`."""

    #: Where the source comes from: an `https://` address, or an absolute folder (the tests').
    repository: str
    #: The branch it follows.
    branch: str
    #: The Compose project, its service and the image the service runs.
    project: str
    service: str
    image: str
    #: The Compose files, root's, in order.
    compose_files: tuple[Path, ...]
    #: The Dockerfile, root's; `None` builds with the commit's own `Dockerfile`.
    dockerfile: Path | None
    #: The named volume at `/data`, as Docker names it (`atlas_atlas-home`).
    volume: str
    #: The container's user, whose main process alone may ask.
    container_uid: int
    #: `owner/name` on GitHub, whose Actions verify a commit; "" verifies none.
    github: str = ""
    #: Where the server answers on the host, for an image without a health check.
    health_url: str = "http://127.0.0.1:18790/health"
    state: Path = STATE_FOLDER

    @property
    def image_name(self) -> str:
        """The image's name without its tag: each build is tagged by its commit beside it."""
        return self.image.rsplit(":", 1)[0]


def parse_settings(text: str, state: Path = STATE_FOLDER) -> Settings:
    """`config.toml`, checked whole: anything unknown or out of shape is refused."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise UpdaterError(f"The updater's settings are not TOML: {error}.") from None
    known = {
        "repository", "branch", "project", "service", "image", "compose_files", "dockerfile",
        "volume", "container_uid", "github", "health_url",
    }  # fmt: skip
    unknown = sorted(set(data) - known)
    if unknown:
        raise UpdaterError(f"The updater's settings have unknown keys: {', '.join(unknown)}.")

    def text_of(
        key: str, pattern: re.Pattern[str] | None = None, default: str | None = None
    ) -> str:
        value = data.get(key, default)
        if not isinstance(value, str) or (pattern is not None and not pattern.fullmatch(value)):
            raise UpdaterError(f"The updater's setting {key} is missing or out of shape.")
        return value

    repository = text_of("repository")
    if not (repository.startswith("https://") or Path(repository).is_absolute()):
        raise UpdaterError("The updater's repository is an https:// address or a folder.")
    files = data.get("compose_files")
    if not isinstance(files, list) or not files:
        raise UpdaterError("The updater's setting compose_files lists its Compose files.")
    compose_files = tuple(_absolute(item, "compose_files") for item in cast("list[object]", files))
    dockerfile = text_of("dockerfile", default="")
    uid = data.get("container_uid")
    if not isinstance(uid, int) or isinstance(uid, bool) or not 0 < uid < 2**31:
        raise UpdaterError("The updater's setting container_uid is the container's user id.")
    health_url = text_of("health_url", default="http://127.0.0.1:18790/health")
    try:
        parts = urllib.parse.urlsplit(health_url)
        loopback = (
            parts.scheme == "http"
            and parts.hostname in ("127.0.0.1", "localhost")
            and parts.username is None
            and parts.port is not None
        )
    except ValueError:
        loopback = False
    if not loopback:
        raise UpdaterError("The updater's health_url is on the host's loopback.")
    return Settings(
        repository=repository,
        branch=text_of("branch", _BRANCH, "main"),
        project=text_of("project", _NAME),
        service=text_of("service", _NAME),
        image=text_of("image", _IMAGE),
        compose_files=compose_files,
        dockerfile=_absolute(dockerfile, "dockerfile") if dockerfile else None,
        volume=text_of("volume", _NAME),
        container_uid=uid,
        github=text_of("github", _GITHUB) if data.get("github") else "",
        health_url=health_url,
        state=state,
    )


def _absolute(value: object, key: str) -> Path:
    if not isinstance(value, str) or not value.startswith("/") or ".." in value.split("/"):
        raise UpdaterError(f"The updater's setting {key} holds absolute paths only.")
    return Path(value)


def owner_problem(path: Path) -> str:
    """Why root must not trust `path`, or "": it and every folder above it are root's, written by
    nobody else, and nothing of it is under `/home`."""
    if PurePosixPath(path.as_posix()).parts[1:2] == ("home",):
        return f"{path} is under /home, which its user writes."
    current = path
    while True:
        try:
            found = current.lstat()
        except FileNotFoundError:
            return f"{current} does not exist."
        if found.st_uid != 0:
            return f"{current} is not root's."
        if found.st_mode & 0o022:
            return f"{current} is written by others than root."
        if current.parent == current:
            return ""
        current = current.parent


def load_settings(path: Path = CONFIG_FILE) -> Settings:
    """The settings, and the files they name, each checked to be root's."""
    problem = owner_problem(path)
    if problem:
        raise UpdaterError(f"The updater's settings are refused: {problem}")
    settings = parse_settings(path.read_text(encoding="utf-8"))
    named = [*settings.compose_files, *([settings.dockerfile] if settings.dockerfile else [])]
    # A folder to fetch from is read by root's git, which reads its settings too.
    if not settings.repository.startswith("https://"):
        named.append(Path(settings.repository))
    for each in named:
        problem = owner_problem(each)
        if problem:
            raise UpdaterError(f"The updater refuses {each}: {problem}")
    return settings


def check_state_folder(folder: Path) -> None:
    """The state folder, made root's alone when missing; refused when another user could write
    it, since root unpacks, deletes and builds in it."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    problem = owner_problem(folder)
    if problem:
        raise UpdaterError(f"The updater refuses its state folder: {problem}")


def _write_private(path: Path, text: str) -> None:
    """`text` into `path`, root's alone, through a file moved over it."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    temporary.replace(path)


# -- the Compose file's rules ----------------------------------------------------------------------


def compose_problems(config: Mapping[str, object], settings: Settings) -> list[str]:
    """What makes a resolved Compose file (`docker compose config --format json`) unsafe for the
    update, or wrong for it; empty when it may run."""
    problems: list[str] = []
    services = _mapping(config.get("services"))
    if settings.service not in services:
        return [f"The Compose file has no service {settings.service}."]
    named_volumes = _mapping(config.get("volumes"))
    # A named volume a driver's options make, `type: none, o: bind, device: /`, is a host folder.
    for name, raw in named_volumes.items():
        volume = _mapping(raw)
        if volume.get("driver", "local") != "local" or _mapping(volume.get("driver_opts")):
            problems.append(
                f"The volume {name} is made by a driver's options, maybe of a host folder."
            )
    # A config or a secret read from a file is a host file in the container.
    for kind in ("configs", "secrets"):
        for name, raw in _mapping(config.get(kind)).items():
            if "file" in _mapping(raw):
                problems.append(f"The {kind[:-1]} {name} is read from a host file.")
    for name, raw in services.items():
        service = _mapping(raw)
        if service.get("privileged") is True:
            problems.append(f"{name} is privileged.")
        if _sequence(service.get("cap_add")):
            problems.append(f"{name} adds capabilities.")
        if _sequence(service.get("devices")) or _sequence(service.get("device_cgroup_rules")):
            problems.append(f"{name} reaches host devices.")
        for key in ("network_mode", "pid", "ipc", "userns_mode", "uts", "cgroup"):
            if service.get(key) == "host":
                problems.append(f"{name} shares the host's {key}.")
        options = [
            str(option).replace(":", "=") for option in _sequence(service.get("security_opt"))
        ]
        if any("unconfined" in option or option == "label=disable" for option in options):
            problems.append(f"{name} turns a security profile off.")
        if _sequence(service.get("volumes_from")):
            problems.append(f"{name} takes another container's volumes.")
        # A build names a folder, maybe the user's checkout: the updater builds the commit itself.
        if "build" in service:
            problems.append(f"{name} is built by Compose from a host folder.")
        # Read again at each `up`, from wherever they are: the installer inlines them instead.
        for key in ("env_file", "label_file"):
            if service.get(key):
                problems.append(f"{name} reads a host file ({key}) as it starts.")
        for raw_volume in _sequence(service.get("volumes")):
            volume = _mapping(raw_volume)
            source = str(volume.get("source", ""))
            kind = volume.get("type")
            if "docker.sock" in source or "docker.sock" in str(volume.get("target", "")):
                problems.append(f"{name} mounts the Docker socket, which the update never allows.")
            elif kind == "bind" and source not in ALLOWED_BINDS:
                problems.append(f"{name} mounts the host folder {source}.")
            elif kind == "bind" and volume.get("read_only") is not True:
                problems.append(f"{name} mounts {source} writable; it is read-only.")
            elif kind not in ("bind", "volume", "tmpfs"):
                problems.append(f"{name} mounts a {kind}, which the update does not know.")
    target = _mapping(services[settings.service])
    if target.get("image") != settings.image:
        problems.append(f"{settings.service} runs {target.get('image')!r}, not {settings.image}.")
    environment = _mapping(target.get("environment"))
    if environment.get(INSTALLATION_VARIABLE) != COMPOSE:
        problems.append(f"{settings.service} is not told {INSTALLATION_VARIABLE}={COMPOSE}.")
    mounts = [_mapping(each) for each in _sequence(target.get("volumes"))]
    if not any(
        m.get("source") in ALLOWED_BINDS and m.get("target") == str(SOCKET_FOLDER) for m in mounts
    ):
        problems.append(f"{settings.service} does not mount {SOCKET_FOLDER}.")
    data = [m for m in mounts if m.get("target") == "/data" and m.get("type") == "volume"]
    names = [str(_mapping(named_volumes.get(str(m.get("source")))).get("name", "")) for m in data]
    if names != [settings.volume]:
        problems.append(f"{settings.service} does not keep /data in the volume {settings.volume}.")
    return problems


def _mapping(value: object) -> Mapping[str, object]:
    return cast("Mapping[str, object]", value) if isinstance(value, dict) else {}


def _sequence(value: object) -> Sequence[object]:
    return cast("Sequence[object]", value) if isinstance(value, list) else []


# -- the requests ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Request:
    """One request from the container: `check`, `status`, `log`, or `apply` with the commit the
    user was shown and whether they confirmed one CI did not verify."""

    action: str
    target: str = ""
    allow_unverified: bool = False


_SHAPES: Mapping[str, frozenset[str]] = {
    "check": frozenset({"version", "action"}),
    "status": frozenset({"version", "action"}),
    "log": frozenset({"version", "action"}),
    "apply": frozenset({"version", "action", "target", "allow_unverified"}),
}


def parse_request(line: bytes) -> Request:
    """A request line, checked against its shape: its exact keys, nothing more."""
    if len(line) > REQUEST_BYTES:
        raise UpdaterError("The request is too long.")
    try:
        found: object = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise UpdaterError("The request is not JSON.") from None
    data = _mapping(found)
    action = data.get("action")
    if not isinstance(action, str) or action not in _SHAPES:
        raise UpdaterError("The request asks for nothing the updater does.")
    if frozenset(data) != _SHAPES[action]:
        raise UpdaterError(
            f"A {action} request holds exactly {', '.join(sorted(_SHAPES[action]))}."
        )
    if data.get("version") != PROTOCOL_VERSION:
        raise UpdaterError(f"The request's version is not {PROTOCOL_VERSION}.")
    if action != "apply":
        return Request(action)
    target = data.get("target")
    allow = data.get("allow_unverified")
    if not isinstance(target, str) or not _SHA.fullmatch(target):
        raise UpdaterError("The update names its commit by its 40 hexadecimal characters.")
    if not isinstance(allow, bool):
        raise UpdaterError("allow_unverified is true or false.")
    return Request(action, target, allow)


def request_line(request: Request) -> bytes:
    """A request as the container sends it."""
    data: dict[str, object] = {"version": PROTOCOL_VERSION, "action": request.action}
    if request.action == "apply":
        data["target"] = request.target
        data["allow_unverified"] = request.allow_unverified
    return json.dumps(data).encode("utf-8") + b"\n"


class Gate:
    """At most `most` requests in any minute."""

    def __init__(
        self, most: int = REQUESTS_PER_MINUTE, clock: Callable[[], float] = time.monotonic
    ):
        self._most = most
        self._clock = clock
        self._seen: list[float] = []

    def admit(self) -> bool:
        now = self._clock()
        self._seen = [moment for moment in self._seen if now - moment < 60.0]
        if len(self._seen) >= self._most:
            return False
        self._seen.append(now)
        return True


# -- CI's verification -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Verification:
    """What CI says of a commit: `verified`, `unverified` (it could not say: the user may confirm)
    or `failed` (it said no: not offered)."""

    state: str
    reason: str = ""


_GREEN = frozenset({"success", "skipped", "neutral"})


def judge_runs(
    runs: Sequence[Mapping[str, object]], jobs_of: Callable[[int], Sequence[Mapping[str, object]]]
) -> Verification:
    """GitHub Actions' runs for one commit, judged: verified only when it has some, and all ended
    green. A run whose failed jobs never started a step did not run at all (a billing or quota
    block stops every job before its first step): unverified, not failed."""
    if not runs:
        return Verification("unverified", "GitHub Actions has no run for this commit")
    for run in runs:
        if run.get("status") != "completed":
            return Verification("unverified", "CI is still running on this commit")
    for run in runs:
        if run.get("conclusion") in _GREEN:
            continue
        run_id = run.get("id")
        jobs: Sequence[Mapping[str, object]] = jobs_of(run_id) if isinstance(run_id, int) else ()
        failed = [job for job in jobs if job.get("conclusion") not in _GREEN]
        if failed and all(not _sequence(job.get("steps")) for job in failed):
            return Verification(
                "unverified", "GitHub Actions could not start its jobs (a billing or quota block)"
            )
        names = ", ".join(str(job.get("name", "?")) for job in failed) or str(run.get("name", "?"))
        return Verification("failed", f"CI failed on this commit ({names})")
    return Verification("verified")


def github_verifier(github: str, seconds: float = 20.0) -> Callable[[str], Verification]:
    """CI's verification of a commit, from GitHub's public API (no token: 60 calls an hour)."""

    def get(path: str) -> Mapping[str, object]:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{github}/{path}",
            headers={"Accept": "application/vnd.github+json", "User-Agent": "atlas-host-updater"},
        )
        with urllib.request.urlopen(request, timeout=seconds) as answer:  # noqa: S310
            return _mapping(json.loads(answer.read().decode("utf-8")))

    def verify(sha: str) -> Verification:
        if not github:
            return Verification("unverified", "no GitHub repository is set to verify it")
        try:
            runs = _sequence(
                get(f"actions/runs?head_sha={sha}&event=push&per_page=50").get("workflow_runs")
            )

            def jobs_of(run_id: int) -> Sequence[Mapping[str, object]]:
                jobs = _sequence(get(f"actions/runs/{run_id}/jobs?per_page=100").get("jobs"))
                return [_mapping(job) for job in jobs]

            return judge_runs([_mapping(run) for run in runs], jobs_of)
        except (OSError, urllib.error.URLError, json.JSONDecodeError, ValueError) as error:
            return Verification("unverified", f"GitHub could not be reached ({error})")

    return verify


# -- the commands ----------------------------------------------------------------------------------


class Commands(Protocol):
    """Runs git and docker: their output, or `UpdaterError` with its tail when one fails."""

    def run(
        self, argv: Sequence[str], *, timeout: float = 600.0, log: TextIO | None = None
    ) -> str: ...


class SystemCommands:
    """The real ones, with an environment of their own: a fixed PATH, no user's git settings, no
    prompt."""

    def __init__(self, home: Path) -> None:
        self.environment = {
            "PATH": _PATH,
            "HOME": str(home),
            "LANG": "C.UTF-8",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "DOCKER_CLI_HINTS": "false",
        }

    def run(self, argv: Sequence[str], *, timeout: float = 600.0, log: TextIO | None = None) -> str:
        try:
            done = subprocess.run(  # noqa: S603 - fixed programs, arguments the updater made
                list(argv),
                env=self.environment,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise UpdaterError(
                f"`{argv[0]} {argv[1]}` did not finish in {timeout:.0f} s."
            ) from None
        except OSError as error:
            raise UpdaterError(f"{argv[0]} could not run: {error}.") from None
        out = done.stdout.decode("utf-8", "replace")
        err = done.stderr.decode("utf-8", "replace")
        if log is not None:
            log.write(f"$ {' '.join(argv)}\n{out}{err}")
            log.flush()
        if done.returncode != 0:
            tail = (err.strip() or out.strip()).splitlines()[-5:]
            raise UpdaterError(f"`{' '.join(argv[:3])}` failed: {' / '.join(tail)}")
        return out


# -- the lock and the state ------------------------------------------------------------------------


class Lock:
    """The one update at a time: a lock on a file, which the system lets go if the process dies."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._file: TextIO | None = None

    def acquire(self) -> bool:
        handle = self._path.open("a+", encoding="utf-8")
        try:
            if sys.platform == "win32":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        return True

    def release(self) -> None:
        handle, self._file = self._file, None
        if handle is None:
            return
        if sys.platform == "win32":
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


class State:
    """`state.json`: the commit running, the last look, the update running and how the last one
    ended. Written whole, through a file moved over it."""

    def __init__(self, folder: Path) -> None:
        self.file = folder / "state.json"
        self._guard = threading.Lock()

    def load(self) -> dict[str, object]:
        try:
            found: object = json.loads(self.file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError as error:
            raise UpdaterError(f"{self.file} is damaged ({error.msg}).") from None
        return dict(_mapping(found))

    def change(self, **values: object) -> dict[str, object]:
        with self._guard:
            data = {**self.load(), **values}
            temporary = self.file.with_name(self.file.name + ".tmp")
            temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.file)
            return data


# -- the update ------------------------------------------------------------------------------------


@dataclass
class Plan:
    """An update admitted: its lock held until it ends."""

    lock: Lock
    before: str
    target: str
    commits: list[str]
    previous_image: str
    started: float


@dataclass
class _Run:
    """What one update did so far, for the way back."""

    plan: Plan
    log: TextIO
    echo: TextIO | None
    stopped: bool = False
    snapshot: str = ""
    notes: list[str] = field(default_factory=list[str])


def _status(**values: object) -> dict[str, object]:
    """A look's answer, in the fields of `atlas.installation.UpdateStatus`."""
    status: dict[str, object] = {
        "supported": True,
        "reason": "",
        "current": "",
        "target": "",
        "behind": 0,
        "commits": [],
        "commits_pt_br": [],
        "dirty": False,
        "error": "",
        "kind": COMPOSE,
        "unverified": "",
    }
    status.update(values)
    behind = status["behind"]
    status["available"] = (
        not status["error"] and isinstance(behind, int) and behind > 0 and bool(status["supported"])
    )
    return status


class Updater:
    """The looks and the updates of one Compose service."""

    def __init__(
        self,
        settings: Settings,
        commands: Commands,
        verify: Callable[[str], Verification],
        *,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        http_ok: Callable[[str], bool] | None = None,
        health_seconds: float = HEALTH_SECONDS,
    ) -> None:
        self.settings = settings
        self.commands = commands
        self.verify = verify
        self.clock = clock
        self.sleep = sleep
        self.http_ok = http_ok or _http_ok
        self.health_seconds = health_seconds
        self.folder = settings.state
        self.mirror = self.folder / "mirror.git"
        self.builds = self.folder / "build"
        self.snapshots = self.folder / "snapshots"
        self.log_file = self.folder / "update.log"
        #: The Compose file as checked, resolved: what the update's containers are made from.
        self.resolved = self.folder / "compose.json"
        self.state = State(self.folder)
        self._lock_file = self.folder / "update.lock"

    # -- git, on the mirror only --

    def git(self, *args: str, timeout: float = 120.0) -> str:
        scheme = "https" if self.settings.repository.startswith("https://") else "file"
        return self.commands.run(
            [
                "git", "--git-dir", str(self.mirror),
                "-c", "core.hooksPath=/dev/null",
                "-c", "protocol.allow=never",
                "-c", f"protocol.{scheme}.allow=always",
                *args,
            ],
            timeout=timeout,
        )  # fmt: skip

    def fetch(self) -> str:
        """The branch's newest commit, fetched into the mirror."""
        if not (self.mirror / "HEAD").is_file():
            self.mirror.mkdir(parents=True, exist_ok=True)
            self.commands.run(["git", "init", "--quiet", "--bare", str(self.mirror)])
        branch = self.settings.branch
        self.git("fetch", "--quiet", "--no-tags", self.settings.repository,
                 f"+refs/heads/{branch}:refs/heads/{branch}")  # fmt: skip
        return self.git("rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}").strip()

    def _is_ancestor(self, older: str, newer: str) -> bool:
        try:
            self.git("merge-base", "--is-ancestor", older, newer)
        except UpdaterError:
            return False
        return True

    # -- docker, through fixed arguments --

    def compose(self, *args: str, timeout: float = 600.0, log: TextIO | None = None) -> str:
        files = [part for each in self.settings.compose_files for part in ("-f", str(each))]
        argv = ["docker", "compose", "-p", self.settings.project, *files, *args]
        return self.commands.run(argv, timeout=timeout, log=log)

    def docker(self, *args: str, timeout: float = 600.0, log: TextIO | None = None) -> str:
        return self.commands.run(["docker", *args], timeout=timeout, log=log)

    def compose_config(self) -> list[str]:
        """What is wrong with the Compose file, read as Compose resolves it. When nothing is, the
        resolved file is kept, root's alone (`compose.json`): the update's containers are made from
        it, never from files read again after the check."""
        try:
            found: object = json.loads(self.compose("config", "--format", "json", timeout=60))
        except json.JSONDecodeError:
            return ["docker compose config did not answer JSON."]
        config = _mapping(found)
        problems = compose_problems(config, self.settings)
        if not problems:
            _write_private(self.resolved, json.dumps(config, ensure_ascii=False))
        return problems

    def image_id(self, reference: str) -> str:
        return self.docker("image", "inspect", "--format", "{{.Id}}", reference, timeout=60).strip()

    def container(self) -> str:
        """The service's container now, or ""."""
        return self.compose("ps", "--quiet", self.settings.service, timeout=60).strip()

    def _running_image(self) -> str:
        """The image the way back returns to: the running container's, which a tag built by
        hand since may no longer name; the tag's when no container runs."""
        container = self.container()
        found = self._inspect(container, "{{json .Image}}") if container else None
        if isinstance(found, str) and found.startswith("sha256:"):
            return found
        return self.image_id(self.settings.image)

    # -- the look --

    def check(self, *, fresh: bool = False) -> dict[str, object]:
        """Whether the branch has commits the service lacks, the last look reused for a minute."""
        state = self.state.load()
        cached = state.get("check")
        checked_at = state.get("checked_at")
        if (
            not fresh
            and isinstance(cached, dict)
            and isinstance(checked_at, (int, float))
            and 0 <= self.clock() - checked_at < CHECK_EVERY
        ):
            return dict(cast("dict[str, object]", cached))
        status = self._look(state)
        self.state.change(check=status, checked_at=self.clock())
        return status

    def _look(self, state: Mapping[str, object]) -> dict[str, object]:
        deployed = state.get("deployed")
        if not isinstance(deployed, str) or not _SHA.fullmatch(deployed):
            return _status(
                error="The host updater does not know which commit runs; run its installer again."
            )
        try:
            target = self.fetch()
            if target != deployed and not self._is_ancestor(deployed, target):
                return _status(
                    current=deployed,
                    target=target,
                    error=f"{deployed[:12]} is not in {self.settings.branch}'s history: the host "
                    "updater only moves forward.",
                )
            behind = int(self.git("rev-list", "--count", f"{deployed}..{target}").strip() or "0")
            log = self.git(
                "log", f"--format={_SUBJECT_AND_NOTE}", "--reverse",
                f"--max-count={UPDATE_COMMITS}", f"{deployed}..{target}",
            )  # fmt: skip
        except (UpdaterError, ValueError) as error:
            return _status(current=deployed, error=str(error))
        records = [record.strip().split("\x1f", 1) for record in log.split("\x1e")]
        found = [(r[0], r[1].strip() if len(r) > 1 else "") for r in records if r and r[0]]
        unverified = ""
        error = ""
        if behind:
            verdict = self.verify(target)
            if verdict.state == "failed":
                error = (
                    f"{verdict.reason} ({target[:12]}): nothing is offered until a commit passes."
                )
            elif verdict.state != "verified":
                unverified = verdict.reason or "CI did not verify it"
        return _status(
            current=deployed,
            target=target,
            behind=behind,
            commits=[subject for subject, _ in found],
            commits_pt_br=[note for _, note in found],
            unverified=unverified,
            error=error,
        )

    # -- the progress --

    def progress(self) -> dict[str, object]:
        """The update running, if one runs, and how the last one ended."""
        state = self.state.load()
        running = _mapping(state.get("running"))
        ended = state.get("ended")
        return {
            "running": bool(running),
            "step": str(running.get("step", "")),
            "started_at": str(running.get("started_at", "")),
            "target": str(running.get("target", "")),
            "ended": dict(_mapping(ended)) or None,
        }

    def log_tail(self) -> str:
        try:
            with self.log_file.open("rb") as handle:
                size = handle.seek(0, os.SEEK_END)
                handle.seek(max(0, size - LOG_TAIL_BYTES))
                return handle.read().decode("utf-8", "replace")
        except FileNotFoundError:
            return ""

    # -- the update --

    def begin(self, target: str, *, allow_unverified: bool) -> Plan:
        """Admit an update to `target`, the commit the user was shown, or say why not. Its lock is
        held from here to the end of `finish`."""
        if not _SHA.fullmatch(target):
            raise UpdaterError("The update names its commit by its 40 hexadecimal characters.")
        self.folder.mkdir(parents=True, exist_ok=True)
        lock = Lock(self._lock_file)
        if not lock.acquire():
            raise UpdaterError("An update is running already.")
        try:
            return self._admitted(lock, target, allow_unverified)
        except BaseException:
            lock.release()
            raise

    def _admitted(self, lock: Lock, target: str, allow_unverified: bool) -> Plan:
        state = self.state.load()
        now = self.clock()
        last = state.get("last_apply_at")
        if isinstance(last, (int, float)) and 0 <= now - last < APPLY_EVERY:
            wait = int(APPLY_EVERY - (now - last)) + 1
            raise UpdaterError(f"An update started moments ago; try again in {wait} s.")
        status = self._look(state)
        self.state.change(check=status, checked_at=now)
        if status["error"]:
            raise UpdaterError(str(status["error"]))
        if not status["behind"]:
            raise UpdaterError("Atlas is up to date already.")
        if status["target"] != target:
            now_at = str(status["target"])[:12]
            raise UpdaterError(
                f"{self.settings.branch} moved since the look (it is at {now_at}): look again, "
                "and confirm what it brings now."
            )
        if status["unverified"] and not allow_unverified:
            raise UpdaterError(
                f"CI did not verify {target[:12]} ({status['unverified']}): confirm to update "
                "anyway."
            )
        problems = self.compose_config()
        if problems:
            raise UpdaterError("The Compose file is refused: " + " ".join(problems))
        previous = self._running_image()
        before = str(status["current"])
        log = self.git("log", "--oneline", "--reverse", f"{before}..{target}")
        commits = [line for line in log.splitlines() if line.strip()]
        running = {"step": "building", "started_at": utc_text(now), "target": target}
        self.state.change(running=running, last_apply_at=now)
        return Plan(lock, before, target, commits, previous, now)

    def finish(self, plan: Plan, echo: TextIO | None = None) -> dict[str, object]:
        """Run an admitted update to its end: build while the old service runs, stop it, snapshot
        `/data`, start the new one and check it; any failure after the stop goes back. How it
        ended, never raised."""
        self.folder.mkdir(parents=True, exist_ok=True)
        try:
            with self.log_file.open("w", encoding="utf-8") as log:
                run = _Run(plan, log, echo)
                try:
                    ended = self._update(run)
                except Exception as error:  # noqa: BLE001 - the update's edge: said, and kept
                    self._say(run, f"The update failed: {error}")
                    ended = self._ended(run, ok=False, error=self._recover(run, error))
                finally:
                    self._clean(plan)
                self._say(run, f"Ended: {json.dumps(ended, ensure_ascii=False)}")
            self.state.change(running=None, ended=ended)
        finally:
            plan.lock.release()
        return ended

    def _update(self, run: _Run) -> dict[str, object]:
        plan = run.plan
        short = plan.target[:12]
        self._say(run, f"Updating {plan.before[:12]} to {short}: {len(plan.commits)} commits.")
        context = self._export(plan.target)
        tag = f"{self.settings.image_name}:{short}"
        dockerfile = self.settings.dockerfile or context / "Dockerfile"
        self._step(run, "building", f"Building {tag}; the server keeps running meanwhile.")
        try:
            self.docker(
                "build", "--tag", tag, "--label", f"{REVISION_LABEL}={plan.target}",
                "--file", str(dockerfile), str(context),
                timeout=3600, log=run.log,
            )  # fmt: skip
            new_image = self.image_id(tag)
        except UpdaterError as error:
            raise BuildError(f"The new version did not build ({error}).") from None
        self._step(run, "snapshot", "Stopping the server to snapshot /data.")
        run.stopped = True
        self.compose("stop", self.settings.service, timeout=120, log=run.log)
        run.snapshot = self._snapshot(run, plan.previous_image)
        self._step(run, "starting", f"Starting {short}.")
        self.docker("tag", new_image, self.settings.image, timeout=60, log=run.log)
        self._up(run)
        self._healthy(run, new_image)
        self.state.change(deployed=plan.target)
        self._say(run, f"{short} runs and answers.")
        return self._ended(run, ok=True)

    def _up(self, run: _Run) -> None:
        """A new container of the service, on the image its tag names now, from the Compose file
        checked when the update was admitted: nothing is built or pulled by Compose."""
        self.commands.run(
            [
                "docker", "compose", "-p", self.settings.project, "-f", str(self.resolved),
                "up", "--detach", "--no-build", "--pull", "never", "--force-recreate",
                self.settings.service,
            ],
            timeout=300, log=run.log,
        )  # fmt: skip

    def _export(self, target: str) -> Path:
        """The commit's files, alone, in a folder of root's: never a working tree."""
        if not hasattr(tarfile, "data_filter"):
            raise UpdaterError("The host's Python is too old to unpack safely (3.11.4 or newer).")
        self.builds.mkdir(parents=True, exist_ok=True)
        archive = self.builds / f"{target}.tar"
        context = self.builds / target
        shutil.rmtree(context, ignore_errors=True)
        self.git("archive", "--format=tar", f"--output={archive}", target, timeout=300)
        with tarfile.open(archive) as files:
            files.extractall(context, filter="data")
        archive.unlink()
        return context

    def _snapshot(self, run: _Run, image: str) -> str:
        """`/data` whole into a compressed tar, by a container of the old image with no network."""
        self.snapshots.mkdir(parents=True, exist_ok=True)
        stamp = datetime.fromtimestamp(self.clock(), UTC).strftime("%Y%m%dT%H%M%SZ")
        name = f"{stamp}-{run.plan.before[:12]}.tar.gz"
        self._say(run, f"Snapshot of /data: {self.snapshots / name}")
        self.docker(
            "run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "tar",
            "--volume", f"{self.settings.volume}:/data:ro",
            "--volume", f"{self.snapshots}:/snapshot",
            image, "-czf", f"/snapshot/{name}", "-C", "/data", ".",
            timeout=1800, log=run.log,
        )  # fmt: skip
        return name

    def _restore(self, run: _Run, image: str) -> None:
        """`/data` emptied and filled again from the snapshot."""
        self.docker(
            "run", "--rm", "--network", "none", "--user", "0:0", "--entrypoint", "sh",
            "--volume", f"{self.settings.volume}:/data",
            "--volume", f"{self.snapshots}:/snapshot:ro",
            image, "-c", 'find /data -mindepth 1 -delete && tar -xzf "/snapshot/$1" -C /data',
            "restore", run.snapshot,
            timeout=1800, log=run.log,
        )  # fmt: skip

    def _healthy(self, run: _Run, image: str) -> None:
        """The new container runs the new image and calls itself healthy, in time."""
        deadline = time.monotonic() + self.health_seconds
        while True:
            container = self.container()
            if container:
                state = _mapping(self._inspect(container, "{{json .State}}"))
                health = _mapping(state.get("Health")).get("Status")
                if state.get("Running") is not True:
                    raise UpdaterError("The new container stopped as it started.")
                if health == "unhealthy":
                    raise UpdaterError("The new container calls itself unhealthy.")
                if health == "healthy" or (
                    health is None and self.http_ok(self.settings.health_url)
                ):
                    running = self._inspect(container, "{{json .Image}}")
                    if running != image:
                        raise UpdaterError("The new container does not run the new image.")
                    return
            if time.monotonic() > deadline:
                raise UpdaterError(
                    f"The new container was not healthy in {self.health_seconds:.0f} s."
                )
            self.sleep(2.0)

    def _inspect(self, container: str, template: str) -> object:
        text = self.docker("inspect", "--format", template, container, timeout=60)
        try:
            return cast("object", json.loads(text))
        except json.JSONDecodeError:
            return None

    def _recover(self, run: _Run, error: Exception) -> str:
        """Put the old image and `/data` back after a failure past the stop; what to tell."""
        said = str(error)
        if not run.stopped:
            return f"{said} The server kept running {run.plan.before[:12]}."
        plan = run.plan
        self._step(run, "rolling back", f"Going back to {plan.before[:12]}.")
        try:
            self.compose("stop", self.settings.service, timeout=120, log=run.log)
            self.docker("tag", plan.previous_image, self.settings.image, timeout=60, log=run.log)
            if run.snapshot:
                self._restore(run, plan.previous_image)
            self._up(run)
            self._healthy(run, plan.previous_image)
        except Exception as again:  # noqa: BLE001 - said whole: the user must act
            self._say(run, f"The way back failed: {again}")
            where = f" The snapshot is {self.snapshots / run.snapshot}." if run.snapshot else ""
            return f"{said} Going back to {plan.before[:12]} failed too ({again}).{where}"
        restored = " and /data as it was" if run.snapshot else ""
        return f"{said} The server is back on {plan.before[:12]}{restored}."

    def _ended(self, run: _Run, *, ok: bool, error: str = "") -> dict[str, object]:
        """How it ended, in the fields of `atlas.installation.HandedOff`."""
        plan = run.plan
        return {
            "ok": ok,
            "before": plan.before,
            "after": plan.target if ok else "",
            "commits": plan.commits if ok else [],
            "error": error,
            "log": str(self.log_file),
            "ended_at": utc_text(self.clock()),
            "reopen": "",
        }

    def _clean(self, plan: Plan) -> None:
        shutil.rmtree(self.builds / plan.target, ignore_errors=True)
        (self.builds / f"{plan.target}.tar").unlink(missing_ok=True)
        if self.snapshots.is_dir():
            kept = sorted(self.snapshots.glob("*.tar.gz"))
            for old in kept[:-KEEP_SNAPSHOTS]:
                old.unlink(missing_ok=True)

    def _step(self, run: _Run, step: str, text: str) -> None:
        running = {
            "step": step,
            "started_at": utc_text(run.plan.started),
            "target": run.plan.target,
        }
        self.state.change(running=running)
        self._say(run, text)

    def _say(self, run: _Run, text: str) -> None:
        line = f"[{utc_text(self.clock())}] {text}\n"
        run.log.write(line)
        run.log.flush()
        if run.echo is not None:
            run.echo.write(text + "\n")
            run.echo.flush()


def _http_ok(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=3) as answer:  # noqa: S310 - the settings' loopback
            return answer.status == 200
    except (OSError, urllib.error.URLError):
        return False


# -- who may ask -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProcessInfo:
    parent: int
    #: When it started, in clock ticks since the boot.
    started: int


def read_process(proc: Path, pid: int) -> ProcessInfo | None:
    """A process's parent and start, from `/proc/<pid>/stat`; its name may hold spaces and
    parentheses, so the fields are counted from the last `)`."""
    try:
        text = (proc / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fields = text[text.rfind(")") + 2 :].split()
    try:
        return ProcessInfo(parent=int(fields[1]), started=int(fields[19]))
    except (IndexError, ValueError):
        return None


class PeerCheck:
    """Whether the process at the socket's other end may ask: the container's user, and its main
    process, the first child of the container's init. A command the agent runs is the server's
    child, and one that left it for the init started later, so neither passes."""

    def __init__(self, updater: Updater, proc: Path = Path("/proc")) -> None:
        self.updater = updater
        self.proc = proc

    def refusal(self, pid: int, uid: int) -> str:
        """Why the peer may not ask, or ""."""
        if uid != self.updater.settings.container_uid:
            return "Only Atlas in its container may ask."
        try:
            container = self.updater.container()
            pid_of = ("inspect", "--format", "{{.State.Pid}}", container)
            init = int(self.updater.docker(*pid_of, timeout=30).strip()) if container else 0
        except (UpdaterError, ValueError):
            init = 0
        if init <= 0:
            return "Atlas's container is not running."
        found = read_process(self.proc, pid)
        if found is None or found.parent != init:
            return "Only the server in Atlas's container may ask."
        # The peer itself may have ended since it was read: no children then, and no answer.
        starts = [info.started for info in self._processes() if info.parent == init]
        if not starts or min(starts) != found.started:
            return "Only the server in Atlas's container may ask."
        return ""

    def _processes(self) -> Iterator[ProcessInfo]:
        for entry in self.proc.iterdir():
            if entry.name.isdigit():
                info = read_process(self.proc, int(entry.name))
                if info is not None:
                    yield info


# -- the socket ------------------------------------------------------------------------------------


class Service:
    """The answers to the container's requests; the socket around it is `serve`'s."""

    def __init__(
        self,
        updater: Updater,
        *,
        gate: Gate | None = None,
        start: Callable[[Callable[[], object]], object] | None = None,
    ) -> None:
        self.updater = updater
        self.gate = gate or Gate()
        self._start = start or _thread

    def answer(self, line: bytes, refusal: str = "") -> dict[str, object]:
        """The answer to one request line from a peer, refused when `refusal` says why."""
        if refusal:
            return {"ok": False, "error": refusal}
        if not self.gate.admit():
            return {"ok": False, "error": "Too many requests; wait a minute."}
        try:
            request = parse_request(line)
            if request.action == "check":
                return {"ok": True, "status": self.updater.check()}
            if request.action == "status":
                return {"ok": True, "progress": self.updater.progress()}
            if request.action == "log":
                return {"ok": True, "log": self.updater.log_tail()}
            plan = self.updater.begin(request.target, allow_unverified=request.allow_unverified)
        except UpdaterError as error:
            return {"ok": False, "error": str(error)}
        self._start(lambda: self.updater.finish(plan))
        return {"ok": True}


def respond(
    service: Service, peers: PeerCheck, pid: int, uid: int, line: bytes
) -> dict[str, object]:
    """The answer to one connection's request. A failure the updater did not foresee is said to
    that peer alone: the socket keeps answering, and systemd never restarts it in a loop."""
    try:
        return service.answer(line, peers.refusal(pid, uid))
    except Exception as error:  # noqa: BLE001 - the socket's edge: one request fails, not the service
        sys.stderr.write(f"atlas-host-updater: a request failed: {error!r}\n")
        return {"ok": False, "error": "The host updater failed on this request; see its journal."}


def _thread(work: Callable[[], object]) -> threading.Thread:
    thread = threading.Thread(target=work, name="atlas-update", daemon=False)
    thread.start()
    return thread


def peer_credentials(connection: socket.socket) -> tuple[int, int]:  # pragma: no cover - Linux
    """The peer's process id and user id, from the kernel (`SO_PEERCRED`)."""
    if sys.platform == "win32":
        raise UpdaterError("The host updater runs on Linux.")
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, _ = struct.unpack("3i", raw)
    return pid, uid


def read_line(connection: socket.socket) -> bytes:  # pragma: no cover - Linux
    """One request line, at most `REQUEST_BYTES` and one more to know it was too long."""
    data = b""
    while b"\n" not in data and len(data) <= REQUEST_BYTES:
        part = connection.recv(REQUEST_BYTES + 1 - len(data))
        if not part:
            break
        data += part
    return data.split(b"\n", 1)[0]


def serve(  # pragma: no cover - Linux
    service: Service, peers: PeerCheck, path: Path = Path(str(SOCKET_FILE))
) -> None:
    """Answer the container on its socket, one request at a time, for ever."""
    if sys.platform == "win32":
        raise UpdaterError("The host updater runs on Linux.")
    path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    problem = owner_problem(path.parent)
    if problem:
        raise UpdaterError(f"The updater refuses its socket's folder: {problem}")
    path.unlink(missing_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    # The folder is root's alone, so the path is the socket just bound; never a link followed.
    os.chown(path, service.updater.settings.container_uid, 0, follow_symlinks=False)
    path.chmod(0o600)
    listener.listen(4)
    while True:
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(5.0)
            try:
                pid, uid = peer_credentials(connection)
                answer = respond(service, peers, pid, uid, read_line(connection))
                connection.sendall(json.dumps(answer, ensure_ascii=False).encode("utf-8") + b"\n")
            except OSError:
                continue


# -- the host's command ----------------------------------------------------------------------------


def run_update(updater: Updater, out: TextIO, ask: Callable[[str], str]) -> int:
    """`atlas update` on the host: the look, what it brings, the user's yes, the update."""
    status = updater.check(fresh=True)
    if status["error"]:
        out.write(f"atlas: {status['error']}\n")
        return 1
    current = str(status["current"])[:12]
    if not status["behind"]:
        out.write(f"Already up to date ({current}).\n")
        return 0
    target = str(status["target"])
    out.write(
        f"Atlas here runs {current}; {updater.settings.branch} brings {status['behind']} commits:\n"
    )
    for subject in cast("list[str]", status["commits"]):
        out.write(f"  {subject}\n")
    unverified = str(status["unverified"])
    if unverified:
        out.write(f"CI did not verify {target[:12]}: {unverified}.\n")
        said = ask("Type `update anyway` to update without CI's verification: ")
        if said.strip() != "update anyway":
            out.write("Nothing was updated.\n")
            return 1
    else:
        out.write(f"CI verified {target[:12]}.\n")
        said = ask(
            "The server keeps running while the new version builds, then restarts. Update? [y/N] "
        )
        if said.strip().lower() not in ("y", "yes"):
            out.write("Nothing was updated.\n")
            return 1
    try:
        plan = updater.begin(target, allow_unverified=bool(unverified))
    except UpdaterError as error:
        out.write(f"atlas: {error}\n")
        return 1
    ended = updater.finish(plan, echo=out)
    if ended["ok"]:
        out.write(f"Updated {current} to {target[:12]}.\n")
        return 0
    out.write(f"atlas: {ended['error']}\nThe log: {ended['log']}\n")
    return 1


def build_updater(settings: Settings) -> Updater:
    check_state_folder(settings.state)
    return Updater(settings, SystemCommands(settings.state), github_verifier(settings.github))


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - the installed program
    parser = argparse.ArgumentParser(
        prog="atlas-host-updater", description="The update of Atlas run by Docker Compose."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="Answer Atlas's container on the socket (systemd's).")
    commands.add_parser("update", help="Look, say what an update brings, and update on a yes.")
    commands.add_parser("check", help="Say, as JSON, what an update would bring.")
    commands.add_parser("status", help="Say, as JSON, the update running and the last one.")
    commands.add_parser("validate", help="Check the settings and the Compose file.")
    args = parser.parse_args(argv)
    if sys.platform == "win32" or os.geteuid() != 0:
        sys.stderr.write("atlas-host-updater runs as root (sudo).\n")
        return 1
    # Its state, snapshots of /data among it, is root's alone, also under a user's sudo.
    os.umask(0o077)
    try:
        updater = build_updater(load_settings())
        if args.command == "serve":
            serve(Service(updater), PeerCheck(updater))
        if args.command == "update":
            return run_update(updater, sys.stdout, input)
        if args.command == "check":
            sys.stdout.write(json.dumps(updater.check(fresh=True), ensure_ascii=False) + "\n")
        if args.command == "status":
            sys.stdout.write(json.dumps(updater.progress(), ensure_ascii=False) + "\n")
        if args.command == "validate":
            problems = updater.compose_config()
            for problem in problems:
                sys.stderr.write(f"atlas-host-updater: {problem}\n")
            if problems:
                return 1
            sys.stdout.write("The settings and the Compose file are sound.\n")
    except UpdaterError as error:
        sys.stderr.write(f"atlas-host-updater: {error}\n")
        return 1
    except (KeyboardInterrupt, EOFError):
        sys.stderr.write("\nNothing was updated.\n")
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover - the installed program
    sys.exit(main())
