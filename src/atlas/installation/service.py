"""The installation apart from development (docs/ARCHITECTURE.md, Daily use): `atlas desktop`
builds the app when its sources changed and opens it; `atlas update` brings the checkout to `main`;
the app's update button looks for one (`check`) and hands it off as it closes (`hand_off`).
"""

import dataclasses
import json
import logging
import os
import time
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TextIO

from atlas.home import FileBusyError, Parent, format_utc, list_homes, locked, replace_file, utc_now
from atlas.installation.errors import InstallationBusyError, InstallationError
from atlas.installation.host_updater import Request
from atlas.installation.model import (
    BUILD_LOCK,
    BUILD_STAMP,
    PACKAGE_LOCK,
    PACKAGES_STAMP,
    UPDATE_COMMITS,
    HandedOff,
    UpdateProgress,
    UpdateStatus,
    installation_kind,
    progress_from_host,
    status_from_host,
)
from atlas.installation.store import (
    Checkout,
    HostChannel,
    Runner,
    SocketChannel,
    launch,
    sandbox_problem,
)

_logger = logging.getLogger("atlas.installation")


class Services(Protocol):
    """Servers that outlive the app (the always-on homes, `atlas.instances.AlwaysOnServices`):
    stopped before an update and started again after it, whatever it did."""

    def stop(self) -> list[str]: ...

    def start(self) -> None: ...


@dataclass(frozen=True, slots=True)
class Built:
    """What a build did: each step it ran, or none when the app was current."""

    installed: bool
    built: bool


@dataclass(frozen=True, slots=True)
class Updated:
    """What an update did: the commits it brought, oldest first."""

    before: str
    after: str
    commits: list[str] = field(default_factory=list[str])


#: How an update brings Python's packages: the dependencies only. Reinstalling Atlas itself would
#: replace `atlas.exe`, which the update runs from, and Windows refuses to replace a program in use
#: (found on 12/10/2026, the update rolled back); Atlas is installed editable, so its new code is
#: read where it is, and the app's next `uv run atlas serve` reinstalls it whole.
#: `--inexact` keeps Atlas installed: an exact sync without the project uninstalls it (found the
#: same day, the next update left the installation without Atlas). Every extra, as the installer
#: brings them (docs/ARCHITECTURE.md, Dependencies): without it an update never brought a new one,
#: and Anthropic's models would have said their SDK was missing (found by step 3b, 14/10/2026).
SYNC_COMMAND = ("uv", "sync", "--locked", "--no-install-project", "--inexact", "--all-extras")
#: How an update builds the app: in a new process, on the code it just brought, without syncing,
#: through the environment's Python: a bare `atlas` is looked up on the PATH, and with Atlas
#: missing from the environment it found Atlas V3's (12/10/2026).
BUILD_COMMAND = (
    "uv", "run", "--locked", "--no-sync", "python", "-m", "atlas", "desktop", "--build-only"
)  # fmt: skip


def build_desktop(checkout: Checkout, runner: Runner) -> Built:
    """Make the app current, under the lock an update shares."""
    try:
        with locked(checkout.desktop / BUILD_LOCK, timeout=0):
            return _build(checkout, runner)
    except FileBusyError as error:
        raise InstallationBusyError(
            "An update or another build of the app is running. Wait for it, and try again."
        ) from error


def _build(checkout: Checkout, runner: Runner) -> Built:
    packages = checkout.file_hash(PACKAGE_LOCK)
    installed = checkout.read_stamp(PACKAGES_STAMP) != packages
    if installed:
        runner.run(["npm", "ci"], checkout.desktop)
        checkout.write_stamp(PACKAGES_STAMP, packages)
    if not checkout.has_electron():
        # Electron's own installer, which its message names ("npx install-electron").
        runner.run(["node", str(Path("node_modules", "electron", "install.js"))], checkout.desktop)
    stamp = checkout.stamp()
    built = checkout.read_stamp(BUILD_STAMP) != stamp
    if built:
        runner.run(["npm", "run", "build"], checkout.desktop)
        checkout.write_stamp(BUILD_STAMP, stamp)
    return Built(installed, built)


def update(checkout: Checkout, runner: Runner, served: Sequence[str]) -> Updated:
    """Bring the checkout to its branch's upstream (`main`), then Python and the app; when a step
    after the pull fails, go back to the commit it was on.

    The app is built by `atlas desktop --build-only` in a new process, so the code that just
    arrived builds it: this process still runs the code from before the pull (found the first time
    the installation updated, on 11/10/2026, when the build ran without the fix it had just pulled).

    `served` are the homes a server serves now: the app is open, and its servers run this code.
    The app's servers are all there is, so the update asks to close the app.
    """
    if served:
        raise InstallationError(
            f"The app is open (a server serves {', '.join(served)}). Close it, and run "
            "`atlas update` again."
        )
    root = checkout.root
    git = runner.output
    try:
        with locked(checkout.desktop / BUILD_LOCK, timeout=0):
            if git(["git", "status", "--porcelain"], root).strip():
                raise InstallationError(
                    f"{root} has local changes, and an installation is not edited: they are "
                    "left to you. Commit or discard them first."
                )
            before = git(["git", "rev-parse", "HEAD"], root).strip()
            runner.run(["git", "pull", "--ff-only"], root)
            after = git(["git", "rev-parse", "HEAD"], root).strip()
    except FileBusyError as error:
        raise InstallationBusyError(
            "An update or a build of the app is running. Wait for it, and try again."
        ) from error
    try:
        runner.run([*SYNC_COMMAND], root)
        runner.run([*BUILD_COMMAND], root)
    except InstallationError:
        if after != before:
            _logger.warning("The update failed; going back to %s.", before[:12])
            runner.run(["git", "reset", "--hard", before], root)
            runner.run([*SYNC_COMMAND], root)
            runner.run([*BUILD_COMMAND], root)
        raise
    commits: list[str] = []
    if after != before:
        log = git(["git", "log", "--oneline", "--reverse", f"{before}..{after}"], root)
        commits = [line for line in log.splitlines() if line.strip()]
    return Updated(before, after, commits)


# -- the update from the app -----------------------------------------------------------------------

#: How long a look at the upstream may take: a fetch on a slow network, never one waiting on a
#: login (its runner is unattended).
FETCH_SECONDS = 120.0
#: How long the update the app handed off waits for the app to close, then for its servers to let
#: the homes go (each is stopped with its whole tree, which takes a moment on Windows).
APP_CLOSE_SECONDS = 120.0
HOMES_FREE_SECONDS = 60.0
_WAIT_EVERY = 0.25
#: A commit's subject and its `Notes-pt-BR` trailer, apart by a unit separator, one record each.
_SUBJECT_AND_NOTE = "%s%x1f%(trailers:key=Notes-pt-BR,valueonly,unfold,separator=%x20)%x1e"


def check(checkout: Checkout, runner: Runner, *, fetch: bool = True) -> UpdateStatus:
    """Whether the branch's upstream has commits this installation lacks: fetch, and count,
    for the app's button. An installation only: a development checkout, with no toolchain beside
    it, is updated with git, and its app offers nothing."""
    if runner.toolchain.folder is None:
        return UpdateStatus(False, reason="development")
    root = checkout.root
    git = runner.output
    try:
        upstream = git(["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], root)
    except InstallationError:
        return UpdateStatus(False, reason="no-upstream")
    try:
        current = git(["git", "rev-parse", "HEAD"], root).strip()
        if fetch:
            remote = upstream.strip().split("/", 1)[0]
            git(["git", "fetch", "--quiet", remote], root, seconds=FETCH_SECONDS)
        target = git(["git", "rev-parse", "@{u}"], root).strip()
        behind = int(git(["git", "rev-list", "--count", "HEAD..@{u}"], root).strip() or "0")
        newest = ["--reverse", f"--max-count={UPDATE_COMMITS}", "HEAD..@{u}"]
        log = git(["git", "log", f"--format={_SUBJECT_AND_NOTE}", *newest], root)
        dirty = bool(git(["git", "status", "--porcelain"], root).strip())
    except (InstallationError, ValueError) as error:
        return UpdateStatus(True, error=str(error))
    records = [record.strip().split("\x1f", 1) for record in log.split("\x1e")]
    found = [(fields[0], fields[1].strip() if len(fields) > 1 else "") for fields in records]
    found = [(subject, note) for subject, note in found if subject]
    return UpdateStatus(
        True,
        current=current,
        target=target,
        behind=behind,
        commits=tuple(subject for subject, _ in found),
        commits_pt_br=tuple(note for _, note in found),
        dirty=dirty,
    )


def hand_off(
    checkout: Checkout,
    runner: Runner,
    app: Parent,
    homes: Path,
    *,
    marker: Path,
    result: Path,
    reopen: bool = True,
    reopen_flags: Sequence[str] = (),
    close_seconds: float = APP_CLOSE_SECONDS,
    free_seconds: float = HOMES_FREE_SECONDS,
    services: Services | None = None,
) -> HandedOff:
    """The update the app hands off as it closes: wait for the app to close and its servers to
    let the homes go, update with the way back of `update`, say how it went in `result`, and open
    the app again as it was opened (`reopen_flags`, the flags the app was given). `services`, the
    always-on homes' servers, which closing the app leaves running, are stopped first and started
    again after.

    `marker` holds this process's id from the start to just before the app opens again: the app
    that handed off waits for it to know the hand-off took, and one opened meanwhile sees an update
    runs. The result is written before the app opens, so the app always finds it: nothing claims an
    outcome the files do not hold yet. Whatever fails on the way, nobody is left without the app:
    it opens again, and says what it could."""
    replace_file(
        marker,
        json.dumps({"pid": os.getpid(), "started_at": format_utc(utc_now())}).encode(),
    )
    log = runner.log
    reopened = reopen
    problem = ""
    outcome = HandedOff(False, error="The update stopped before it could say how it went.")
    try:
        if not _waited(lambda: not app.alive(), close_seconds):
            # It is still open, and runs this code: nothing is touched, nor opened again.
            reopened = False
            outcome = HandedOff(False, error="The app did not close, so nothing was updated.")
        else:
            _said(log, "The app closed.")
            outcome = _updated(checkout, runner, homes, free_seconds, services)
        problem = sandbox_problem(checkout) if reopened else ""
        outcome = dataclasses.replace(
            outcome,
            log=str(getattr(log, "name", "")),
            ended_at=format_utc(utc_now()),
            reopen=problem,
        )
        _said(log, f"Ended: {outcome.to_json()}")
        replace_file(result, outcome.to_json().encode())
    finally:
        marker.unlink(missing_ok=True)
        if reopened and not problem:
            try:
                launch(checkout, reopen_flags)
            except (OSError, InstallationError) as error:
                _said(log, f"The app could not be opened again: {error}")
    return outcome


def _updated(
    checkout: Checkout,
    runner: Runner,
    homes: Path,
    free_seconds: float,
    services: Services | None,
) -> HandedOff:
    """The update, once the homes are let go; how it went, a failure said, never raised: the
    hand-off is the edge, nobody else would hear it."""
    try:
        if services is not None:
            stopped = services.stop()
            if stopped:
                _said(runner.log, f"Stopped the always-on homes: {', '.join(stopped)}.")
        _waited(lambda: not _served(homes), free_seconds)
        updated = update(checkout, runner, _served(homes))
    except InstallationError as error:
        return HandedOff(False, error=str(error))
    except Exception as error:  # noqa: BLE001 - the hand-off's edge: said, and logged whole
        _said(runner.log, traceback.format_exc())
        return HandedOff(False, error=f"{type(error).__name__}: {error}")
    finally:
        if services is not None:
            services.start()
    return HandedOff(True, updated.before, updated.after, tuple(updated.commits))


def _waited(done: Callable[[], bool], seconds: float) -> bool:
    """Whether `done` came true within `seconds`."""
    deadline = time.monotonic() + seconds
    while not done():
        if time.monotonic() > deadline:
            return False
        time.sleep(_WAIT_EVERY)
    return True


def _served(homes: Path) -> list[str]:
    return [found.name for found in list_homes(homes) if found.served]


def _said(log: TextIO | None, text: str) -> None:
    if log is not None:
        log.write(f"{text}\n")
        log.flush()


def checkout_of(path: Path | None = None) -> Checkout:
    """The checkout at `path`, or the one the running Atlas comes from."""
    return Checkout.running() if path is None else Checkout(path)


# -- the update of a Compose host (docs/ARCHITECTURE.md, The update of a Compose host) -------------

#: What `atlas update` says inside a Compose host's container: the update runs on the host, where
#: the container cannot reach Docker, and a command run in the container could be the agent's.
COMPOSE_UPDATE_ELSEWHERE = (
    "This Atlas runs in a container that Docker Compose runs, and the host updates it: type "
    "`atlas update` on the host, or use the app's Update button."
)


def host_channel(environ: Mapping[str, str]) -> HostChannel | None:
    """The way to the host updater, for a Compose host's server; `None` for a native one."""
    return SocketChannel() if installation_kind(environ) == "compose" else None


def _asked(channel: HostChannel, request: Request) -> Mapping[str, object]:
    answer = channel.ask(request)
    if answer.get("ok") is not True:
        raise InstallationError(str(answer.get("error") or "The host updater refused."))
    return answer


def compose_check(channel: HostChannel) -> UpdateStatus:
    """The host updater's look: what `main` brings, and whether CI verified it."""
    try:
        return status_from_host(_asked(channel, Request("check")).get("status"))
    except InstallationError as error:
        return UpdateStatus(True, kind="compose", error=str(error))


def compose_apply(channel: HostChannel, target: str, *, allow_unverified: bool) -> None:
    """Ask the host updater to update to `target`, the commit the user was shown; raises with
    its reason when it refuses. It answers once the update is admitted, and runs it after."""
    _asked(channel, Request("apply", target, allow_unverified))


def compose_progress(channel: HostChannel) -> UpdateProgress:
    """The update running on the host, and how the last one ended."""
    return progress_from_host(_asked(channel, Request("status")).get("progress"))


def compose_log(channel: HostChannel) -> str:
    """The tail of the host's last update's log."""
    return str(_asked(channel, Request("log")).get("log", ""))
