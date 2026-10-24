"""A home always on and open to the user's machines, and its connection code (docs/ARCHITECTURE.md,
Several machines).

- **Always on** writes the home's always-on file (its port, chosen once) and this system's service,
  then starts it: the service runs `atlas serve --always-on <home>`. While another server holds the
  home (the app's own), the service's server is refused, and the service tries again (Windows every
  minute, systemd every 5 seconds, launchd at once), so it takes over once that one stops.
- **Open** marks the file and restarts the service, whose server then listens on the tailnet too.
- **Off** removes the service and the file; the next server of the home is the app's again.
"""

import logging
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from atlas.home import Home, home_name, is_served, list_homes
from atlas.instances.errors import InstancesError
from atlas.instances.model import (
    AlwaysOn,
    ConnectionCode,
    ServiceCommand,
    Tailnet,
    service_label,
)
from atlas.instances.store import (
    ServiceManager,
    answers,
    forget_always_on,
    free_port,
    read_always_on,
    service_manager,
    tailnet,
    write_always_on,
)

_logger = logging.getLogger("atlas.instances")

#: How long turning on waits for the service's server to answer.
START_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class Status:
    """Where a home stands: always on or not, open or not, its port, whether its service is
    written, whether a server serves it now, and its tailnet address or why there is none."""

    on: bool
    open: bool
    port: int | None
    installed: bool
    served: bool
    tailnet: Tailnet | None = None
    tailnet_problem: str | None = None


class AlwaysOnHome:
    """One home's always-on service."""

    def __init__(
        self,
        home: Home,
        *,
        manager: ServiceManager | None = None,
        command: ServiceCommand | None = None,
        find_tailnet: Callable[[], Tailnet] = tailnet,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.home = home
        self.name = home_name(home.root)
        self.label = service_label(self.name, str(home.root.resolve()))
        self._manager = manager
        self._command = command
        self._find_tailnet = find_tailnet
        self._sleep = sleep

    @property
    def manager(self) -> ServiceManager:
        if self._manager is None:
            self._manager = service_manager()
        return self._manager

    @property
    def command(self) -> ServiceCommand:
        return self._command or serve_command(self.home)

    def status(self) -> Status:
        state = read_always_on(self.home)
        found: Tailnet | None = None
        problem: str | None = None
        if state is not None and state.open:
            try:
                found = self._find_tailnet()
            except InstancesError as error:
                problem = str(error)
        return Status(
            on=state is not None,
            open=state is not None and state.open,
            port=None if state is None else state.port,
            installed=self.manager.installed(self.label),
            served=is_served(self.home.root),
            tailnet=found,
            tailnet_problem=problem,
        )

    def turn_on(self) -> AlwaysOn:
        """Write the file and the service, and start it. A home already on keeps its port."""
        state = read_always_on(self.home) or AlwaysOn(port=free_port())
        write_always_on(self.home, state)
        try:
            self.manager.install(self.label, self.command, f"Atlas, home {self.name}")
            self.manager.start(self.label)
        except InstancesError:
            self.manager.uninstall(self.label)
            forget_always_on(self.home)
            raise
        return state

    def turn_off(self) -> None:
        """Remove the service, stopping its server, and the file."""
        self.manager.uninstall(self.label)
        forget_always_on(self.home)

    def set_open(self, is_open: bool) -> AlwaysOn:
        """Open the home to the user's machines, or close it; its service starts again to listen
        where it now should. Opening needs Tailscale up, and the home always on."""
        state = read_always_on(self.home)
        if state is None:
            raise InstancesError(
                f"The home {self.name} is not always on; turn that on first (`atlas always-on on`)."
            )
        if is_open:
            self._find_tailnet()
        state = AlwaysOn(state.port, is_open)
        write_always_on(self.home, state)
        self.restart()
        return state

    def restart(self) -> None:
        """Stop the service's server and start it again, as after a new token. The new one starts
        only once the old one let the home go: started while it holds it, the new server is
        refused, and Windows tries again only a minute later."""
        if not self.manager.installed(self.label):
            return
        self.manager.stop(self.label)
        self.wait_free()
        self.manager.start(self.label)

    def ensure(self) -> bool:
        """Start the service when its server does not answer, and say whether it answers then."""
        state = read_always_on(self.home)
        if state is None or not self.manager.installed(self.label):
            raise InstancesError(f"The home {self.name} is not always on.")
        if answers(state.port):
            return True
        self.wait_free()
        self.manager.start(self.label)
        return self.wait_ready()

    def wait_free(self, seconds: float = START_SECONDS) -> None:
        """Wait, `seconds` at most, for the home's server to let it go."""
        deadline = time.monotonic() + seconds
        while is_served(self.home.root) and time.monotonic() < deadline:
            self._sleep(0.25)

    def wait_ready(self, seconds: float = START_SECONDS) -> bool:
        """Whether the service's server answers on the home's port within `seconds`. Holding the
        home is not enough: a server holds it before it migrates the database and listens (found
        by the real service's test, 19/10/2026)."""
        state = read_always_on(self.home)
        if state is None:
            return False
        deadline = time.monotonic() + seconds
        while not answers(state.port):
            if time.monotonic() > deadline:
                return False
            self._sleep(0.25)
        return True

    def code(self, token: str, port: int) -> ConnectionCode:
        """The connection code of this home: its always-on port, else `port` (a container's), and
        the tailnet's name or address when the home is open."""
        state = read_always_on(self.home)
        reach: str | None = None
        if state is not None and state.open:
            found = self._find_tailnet()
            reach = found.name or found.address
        return ConnectionCode(
            home=self.name, port=port if state is None else state.port, token=token, tailnet=reach
        )


def serve_command(home: Home) -> ServiceCommand:
    """What the service runs: this interpreter, with no console on Windows (`pythonw`), serving
    the home by its folder."""
    program = Path(sys.executable)
    if sys.platform == "win32":
        windowless = program.with_name("pythonw.exe")
        if windowless.is_file():
            program = windowless
    return ServiceCommand(
        program=str(program),
        args=("-m", "atlas", "serve", "--always-on", str(home.root.resolve())),
        folder=str(home.root.resolve()),
        path=os.environ.get("PATH", ""),
    )


class AlwaysOnServices:
    """The always-on homes under a homes' root, stopped while the installation updates and started
    again after it, on the new code or, rolled back, the old (docs/ARCHITECTURE.md, Several
    machines). Their servers outlive the app, so closing it leaves them serving: the update would
    wait for them forever, and on Windows `uv sync` cannot replace a package their Python loaded."""

    def __init__(self, root: Path, *, manager: ServiceManager | None = None) -> None:
        self.root = root
        self._manager = manager
        self._stopped: list[AlwaysOnHome] = []

    def stop(self) -> list[str]:
        """Stop every always-on home's service and wait for its server to let the home go; the
        names of the homes stopped."""
        for found in list_homes(self.root):
            home = Home(found.path)
            if read_always_on(home) is None:
                continue
            service = AlwaysOnHome(home, manager=self._manager)
            if not service.manager.installed(service.label):
                continue
            service.manager.stop(service.label)
            self._stopped.append(service)
        for service in self._stopped:
            service.wait_free()
        return [service.name for service in self._stopped]

    def start(self) -> None:
        """Start again what `stop` stopped; one that fails does not keep the others off."""
        stopped, self._stopped = self._stopped, []
        for service in stopped:
            try:
                service.manager.start(service.label)
            except InstancesError as error:
                _logger.warning(
                    "The always-on home %s did not start again: %s", service.name, error
                )
