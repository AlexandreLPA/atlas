"""A Compose host for the host updater's tests (`atlas.installation.host_updater`).

Git is real: an origin that is also the user's checkout, with its untracked homelab files, and the
updater's own mirror fetched from it. Docker is played by `FakeDocker`: its images by tag, the one
container of the service, its health, and the `/data` volume as a real folder, which a snapshot
tars and a restore puts back for real. Only Docker stands in: what these tests prove is which steps
run, in what order, and the way back.
"""

import json
import os
import shutil
import subprocess
import tarfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from atlas.installation.host_updater import (
    COMPOSE,
    INSTALLATION_VARIABLE,
    SOCKET_FOLDER,
    Request,
    Service,
    Settings,
    Updater,
    UpdaterError,
    Verification,
    request_line,
)

OLD_IMAGE = "sha256:" + "0" * 64
GIT_IDENTITY = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]


def git(folder: Path, *args: str) -> str:
    done = subprocess.run(  # noqa: S603 - git, the test's own arguments
        ["git", *GIT_IDENTITY, *args],  # noqa: S607 - the system's git
        cwd=folder,
        capture_output=True,
        text=True,
        check=True,
    )
    return done.stdout.strip()


def commit(folder: Path, name: str, subject: str, note: str = "") -> str:
    (folder / name).write_text(subject + "\n", encoding="utf-8")
    git(folder, "add", name)
    message = subject if not note else f"{subject}\n\nNotes-pt-BR: {note}"
    git(folder, "commit", "--quiet", "-m", message)
    return git(folder, "rev-parse", "HEAD")


@dataclass
class Checkout:
    """The user's checkout, which the updater fetches from: its commits, oldest first."""

    root: Path
    commits: list[str]


def make_checkout(root: Path) -> Checkout:
    """Three commits on `main`, and the homelab's files untracked beside them."""
    root.mkdir(parents=True)
    git(root, "init", "--quiet", "--initial-branch=main")
    first = commit(root, "Dockerfile", "Step one")
    second = commit(root, "voice.txt", "The voice plugin", "O plugin de voz")
    third = commit(root, "secret.txt", "A plugin secret already stored")
    (root / "Dockerfile.homelab").write_text("FROM atlas\n# the homelab's own\n", "utf-8")
    (root / "docker-compose.homelab.yml").write_text("services: {}\n", "utf-8")
    return Checkout(root, [first, second, third])


def compose_config(**changes: object) -> dict[str, object]:
    """The Compose file as the installer leaves it, resolved: sound unless `changes` say."""
    service: dict[str, object] = {
        "image": "atlas:local",
        "environment": {INSTALLATION_VARIABLE: COMPOSE, "TZ": "UTC"},
        "volumes": [
            {"type": "volume", "source": "atlas-home", "target": "/data"},
            {
                "type": "bind",
                "source": str(SOCKET_FOLDER),
                "target": str(SOCKET_FOLDER),
                "read_only": True,
            },
        ],
        "ports": [{"host_ip": "127.0.0.1", "published": "18790", "target": 18790}],
    }
    service.update(changes)
    return {
        "name": "atlas",
        "services": {"atlas": service},
        "volumes": {"atlas-home": {"name": "atlas_atlas-home"}},
    }


@dataclass
class FakeDocker:
    """Runs git for real; plays Docker for one Compose service."""

    volume: Path
    fail_build: bool = False
    #: The new image's container calls itself unhealthy.
    unhealthy: bool = False
    fail_restore: bool = False
    config: dict[str, object] = field(default_factory=compose_config)
    images: dict[str, str] = field(default_factory=lambda: {"atlas:local": OLD_IMAGE})
    ran: list[list[str]] = field(default_factory=list[list[str]])
    #: The files of each build's context, and the Dockerfile it was given.
    built: list[tuple[list[str], str]] = field(default_factory=list[tuple[list[str], str]])
    container: str = "c0"
    container_image: str = OLD_IMAGE
    running: bool = True
    _made: int = 0

    def run(self, argv: Sequence[str], *, timeout: float = 600.0, log: TextIO | None = None) -> str:
        argv = list(argv)
        self.ran.append(argv)
        if argv[0] == "git":
            return self._git(argv)
        if argv[:2] == ["docker", "compose"]:
            return self._compose(argv[argv.index("-p") + 2 :])
        return self._docker(argv[1:])

    def said(self, *words: str) -> list[list[str]]:
        """The Docker commands that hold `words`, in order."""
        return [argv for argv in self.ran if argv[0] == "docker" and all(w in argv for w in words)]

    def index(self, *words: str) -> int:
        """Where the first Docker command holding `words` ran."""
        return next(i for i, argv in enumerate(self.ran) if all(w in argv for w in words))

    def _git(self, argv: list[str]) -> str:
        environment = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"}
        done = subprocess.run(  # noqa: S603 - git, the updater's arguments
            argv, capture_output=True, text=True, env=environment, check=False
        )
        if done.returncode != 0:
            raise UpdaterError(f"`{' '.join(argv[:3])}` failed: {done.stderr.strip()}")
        return done.stdout

    def _compose(self, args: list[str]) -> str:
        while args and args[0] == "-f":
            args = args[2:]
        verb = args[0]
        if verb == "config":
            return json.dumps(self.config)
        if verb == "ps":
            return self.container + "\n" if self.running else ""
        if verb == "stop":
            self.running = False
            return ""
        if verb == "up":
            self._made += 1
            self.container = f"c{self._made}"
            self.container_image = self.images["atlas:local"]
            self.running = True
            if self.container_image != OLD_IMAGE:
                # The new version migrates the data as it starts.
                (self.volume / "migrated-by-new-version").write_text("v2", "utf-8")
            return ""
        raise AssertionError(f"compose {verb} is not played")

    def _docker(self, args: list[str]) -> str:
        if args[0] == "build":
            context = Path(args[-1])
            files = sorted(
                p.relative_to(context).as_posix() for p in context.rglob("*") if p.is_file()
            )
            self.built.append((files, args[args.index("--file") + 1]))
            if self.fail_build:
                raise UpdaterError("`docker build --tag` failed: step 7 of 12: exit 1")
            self.images[args[args.index("--tag") + 1]] = "sha256:" + "1" * 64
            return ""
        if args[:2] == ["image", "inspect"]:
            if args[-1] not in self.images:
                raise UpdaterError(f"No such image: {args[-1]}")
            return self.images[args[-1]] + "\n"
        if args[0] == "tag":
            self.images[args[2]] = self.images.get(args[1], args[1])
            return ""
        if args[0] == "inspect":
            template = args[2]
            if template == "{{.State.Pid}}":
                return "4242\n"
            if template == "{{json .Image}}":
                return json.dumps(self.container_image)
            new = self.container_image != OLD_IMAGE
            health = "unhealthy" if new and self.unhealthy else "healthy"
            return json.dumps({"Running": self.running, "Health": {"Status": health}})
        if args[0] == "run":
            return self._run_container(args)
        raise AssertionError(f"docker {args[0]} is not played")

    def _run_container(self, args: list[str]) -> str:
        mounts = [args[i + 1] for i, word in enumerate(args) if word == "--volume"]
        snapshots = Path(next(m for m in mounts if ":/snapshot" in m).rsplit(":/snapshot", 1)[0])
        if "tar" in args:  # the snapshot
            name = args[args.index("-czf") + 1].removeprefix("/snapshot/")
            with tarfile.open(snapshots / name, "w:gz") as archive:
                for path in sorted(self.volume.rglob("*")):
                    archive.add(path, path.relative_to(self.volume).as_posix(), recursive=False)
            return ""
        if self.fail_restore:
            raise UpdaterError("`docker run --rm` failed: no space left on device")
        name = args[-1]
        shutil.rmtree(self.volume)
        self.volume.mkdir()
        with tarfile.open(snapshots / name) as archive:
            archive.extractall(self.volume, filter="data")
        return ""


@dataclass
class Host:
    """A Compose host: the user's checkout, the updater's state and its settings, a clock."""

    checkout: Checkout
    docker: FakeDocker
    settings: Settings
    now: list[float]
    verdict: list[Verification]

    def updater(self) -> Updater:
        def verify(sha: str) -> Verification:
            return self.verdict[0]

        def clock() -> float:
            return self.now[0]

        return Updater(
            self.settings,
            self.docker,
            verify,
            clock=clock,
            sleep=lambda seconds: None,
            health_seconds=0.5,
        )

    def deploy(self, commit: str) -> None:
        """The commit that runs now, as the installer records it."""
        state = self.settings.state
        state.mkdir(parents=True, exist_ok=True)
        (state / "state.json").write_text(json.dumps({"deployed": commit}), "utf-8")


def make_host(tmp: Path) -> Host:
    checkout = make_checkout(tmp / "checkout")
    volume = tmp / "volume"
    volume.mkdir()
    (volume / "atlas.db").write_text("conversations", "utf-8")
    (volume / "home").mkdir()
    (volume / "home" / "notes.md").write_text("the user's files", "utf-8")
    settings = Settings(
        repository=str(checkout.root),
        branch="main",
        project="atlas",
        service="atlas",
        image="atlas:local",
        compose_files=(tmp / "etc" / "compose.yml", tmp / "etc" / "compose.updater.yml"),
        dockerfile=None,
        volume="atlas_atlas-home",
        container_uid=10001,
        state=tmp / "state",
    )
    host = Host(checkout, FakeDocker(volume), settings, [1_000_000.0], [Verification("verified")])
    host.deploy(checkout.commits[0])
    return host


#: How a test starts the update a request admitted: at once, in the test's own thread.
def at_once(work: Callable[[], object]) -> None:
    work()


class Through:
    """The container's side of the socket, in this process (`atlas.installation.HostChannel`):
    each request as its JSON line, each answer read back from JSON."""

    def __init__(self, service: Service) -> None:
        self.service = service
        self.asked: list[Request] = []

    def ask(self, request: Request) -> Mapping[str, object]:
        self.asked.append(request)
        answer = self.service.answer(request_line(request).strip())
        return json.loads(json.dumps(answer))
