"""The I/O of talking to more than one machine: a home's always-on file, Tailscale, and the service
of each system (docs/ARCHITECTURE.md, Several machines).

Every command is given a deadline: `schtasks` sometimes wedges. `schtasks /End` stops the task's
whole process tree, the interpreter a virtual environment's `pythonw.exe` starts included (seen on
19/10/2026), so Windows needs no PID kept. There is no Startup-folder fallback for a machine whose
Task Scheduler refuses the user: a server started from there could not be stopped by its task, and
the switch says why it failed instead.
"""

import ctypes
import getpass
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from atlas.home import Home, replace_file
from atlas.instances.errors import InstancesError
from atlas.instances.model import (
    AlwaysOn,
    ServiceCommand,
    Tailnet,
    dump_always_on,
    launchd_plist,
    parse_always_on,
    parse_tailscale_status,
    systemd_unit,
    task_xml,
)

_logger = logging.getLogger("atlas.instances")

#: The longest any service or Tailscale command may take.
COMMAND_SECONDS = 15.0
#: Where Tailscale puts its command when it is not on the PATH.
_TAILSCALE_PLACES = (
    Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Tailscale" / "tailscale.exe",
    Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale"),
)


def read_always_on(home: Home) -> AlwaysOn | None:
    """The home's always-on file, or `None` when the home is not always on."""
    try:
        text = home.always_on_file.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return parse_always_on(text)


def write_always_on(home: Home, state: AlwaysOn) -> None:
    replace_file(home.always_on_file, dump_always_on(state).encode())


def forget_always_on(home: Home) -> None:
    home.always_on_file.unlink(missing_ok=True)


def free_port() -> int:
    """A port nothing listens on now, which the always-on file then keeps."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


def answers(port: int) -> bool:
    """Whether an Atlas server answers `/health` on this machine's port."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as response:
            data: object = json.loads(response.read())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and cast("dict[str, object]", data).get("status") == "ok"


def tailscale_program() -> str | None:
    """The `tailscale` command: on the PATH, else where its installer puts it."""
    found = shutil.which("tailscale")
    if found is not None:
        return found
    return next((str(place) for place in _TAILSCALE_PLACES if place.is_file()), None)


def tailnet() -> Tailnet:
    """This machine on its tailnet; raises `InstancesError` saying what to do when Tailscale is
    missing, stopped or signed out."""
    program = tailscale_program()
    if program is None:
        raise InstancesError(
            "Tailscale is not installed on this machine. Install it from "
            "https://tailscale.com/download and sign in, on this machine and on the one you "
            "will open Atlas from."
        )
    try:
        completed = subprocess.run(  # noqa: S603 - the program found above
            [program, "status", "--json"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=COMMAND_SECONDS,
            stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstancesError(f"Tailscale did not answer: {error}") from error
    if not completed.stdout.strip():
        said = completed.stderr.strip() or f"exit {completed.returncode}"
        raise InstancesError(f"Tailscale is not running on this machine ({said}); open it.")
    return parse_tailscale_status(completed.stdout)


class ServiceManager(Protocol):
    """What runs a home's server as the user's service on this system."""

    def install(self, label: str, command: ServiceCommand, description: str) -> None:
        """Write the service, so it starts with the computer; it does not start it."""
        ...

    def start(self, label: str) -> None: ...

    def stop(self, label: str) -> None:
        """Stop the server it runs, if it runs; the service stays."""
        ...

    def uninstall(self, label: str) -> None:
        """Stop and remove the service; nothing when there is none."""
        ...

    def installed(self, label: str) -> bool: ...


def service_manager() -> ServiceManager:
    """This system's."""
    if sys.platform == "win32":
        return ScheduledTasks()
    if sys.platform == "darwin":
        return LaunchAgents()
    return SystemdUser()


class ScheduledTasks:
    """Windows: a task at the user's logon, written as XML."""

    def install(self, label: str, command: ServiceCommand, description: str) -> None:
        # Local time, as Task Scheduler reads a boundary without an offset.
        start = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        with tempfile.TemporaryDirectory(prefix="atlas-task-") as folder:
            definition = Path(folder) / "task.xml"
            # Task Scheduler reads the XML it declares: UTF-16, with its byte-order mark.
            definition.write_text(
                task_xml(windows_account(), command, description, start), encoding="utf-16"
            )
            _run(["schtasks", "/Create", "/TN", label, "/XML", str(definition), "/F"])

    def start(self, label: str) -> None:
        _run(["schtasks", "/Run", "/TN", label])

    def stop(self, label: str) -> None:
        if self.installed(label):
            # A task that is not running answers with an error; nothing runs, which is the aim.
            _run(["schtasks", "/End", "/TN", label], check=False)

    def uninstall(self, label: str) -> None:
        if self.installed(label):
            self.stop(label)
            _run(["schtasks", "/Delete", "/TN", label, "/F"])

    def installed(self, label: str) -> bool:
        # Judged by the exit code, never by the text, which Windows translates.
        return _run(["schtasks", "/Query", "/TN", label], check=False).returncode == 0


def windows_account() -> str:
    """The user as Task Scheduler names them, `MACHINE\\user` or `DOMAIN\\user`, asked of Windows.

    Never `%USERDOMAIN%`: Windows' OpenSSH server sets it to `WORKGROUP` on a machine outside a
    domain, and the task is refused ("No mapping between account names and security IDs", seen on
    an Azure VM on 21/10/2026)."""
    if sys.platform == "win32":
        name_sam_compatible = 2
        secur32 = ctypes.WinDLL("secur32", use_last_error=True)
        size = ctypes.c_ulong(0)
        secur32.GetUserNameExW(name_sam_compatible, None, ctypes.byref(size))
        buffer = ctypes.create_unicode_buffer(size.value)
        if size.value and secur32.GetUserNameExW(name_sam_compatible, buffer, ctypes.byref(size)):
            return buffer.value
    return f"{os.environ.get('USERDOMAIN', '')}\\{getpass.getuser()}".lstrip("\\")


class LaunchAgents:
    """macOS: a `launchd` agent in the user's `LaunchAgents`."""

    def __init__(self, folder: Path | None = None) -> None:
        self.folder = folder or Path.home() / "Library" / "LaunchAgents"

    def install(self, label: str, command: ServiceCommand, description: str) -> None:
        del description  # a plist has no place for one
        self.folder.mkdir(parents=True, exist_ok=True)
        replace_file(self._plist(label), launchd_plist(label, command).encode())

    def start(self, label: str) -> None:
        domain = _gui_domain()
        if _run(["launchctl", "print", f"{domain}/{label}"], check=False).returncode != 0:
            _run(["launchctl", "bootstrap", domain, str(self._plist(label))])
        _run(["launchctl", "kickstart", f"{domain}/{label}"])

    def stop(self, label: str) -> None:
        _run(["launchctl", "bootout", f"{_gui_domain()}/{label}"], check=False)

    def uninstall(self, label: str) -> None:
        self.stop(label)
        self._plist(label).unlink(missing_ok=True)

    def installed(self, label: str) -> bool:
        return self._plist(label).is_file()

    def _plist(self, label: str) -> Path:
        return self.folder / f"{label}.plist"


def _gui_domain() -> str:
    """The logged-in user's `launchd` domain."""
    if sys.platform == "win32":
        raise InstancesError("launchd is macOS's.")
    return f"gui/{os.getuid()}"


class SystemdUser:
    """Linux: a `systemd --user` unit, with the user's linger so it starts with nobody logged in."""

    def __init__(self, folder: Path | None = None) -> None:
        config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        self.folder = folder or Path(config) / "systemd" / "user"

    def install(self, label: str, command: ServiceCommand, description: str) -> None:
        if shutil.which("systemctl") is None:
            raise InstancesError(
                "This machine has no systemd, so Atlas cannot run as its service. In a container, "
                "the container is what stays on (`restart: unless-stopped`)."
            )
        self.folder.mkdir(parents=True, exist_ok=True)
        replace_file(self._unit(label), systemd_unit(command, description).encode())
        _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", f"{label}.service"])
        lingering = _run(["loginctl", "enable-linger", getpass.getuser()], check=False)
        if lingering.returncode != 0:
            _logger.warning(
                "The user's linger could not be turned on, so Atlas starts only once the user logs "
                "in. As root: `loginctl enable-linger %s`.",
                getpass.getuser(),
            )

    def start(self, label: str) -> None:
        _run(["systemctl", "--user", "start", f"{label}.service"])

    def stop(self, label: str) -> None:
        _run(["systemctl", "--user", "stop", f"{label}.service"], check=False)

    def uninstall(self, label: str) -> None:
        if not self.installed(label):
            return
        _run(["systemctl", "--user", "disable", "--now", f"{label}.service"], check=False)
        self._unit(label).unlink(missing_ok=True)
        _run(["systemctl", "--user", "daemon-reload"], check=False)

    def installed(self, label: str) -> bool:
        return self._unit(label).is_file()

    def _unit(self, label: str) -> Path:
        return self.folder / f"{label}.service"


_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a service command, with no window and nothing waiting on a person; raises
    `InstancesError` saying what it said when it fails and `check` is set."""
    program = shutil.which(command[0])
    if program is None:
        raise InstancesError(f"{command[0]} is not on this machine.")
    try:
        completed = subprocess.run(  # noqa: S603 - a system command, by its name
            [program, *command[1:]],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=COMMAND_SECONDS,
            stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as error:
        raise InstancesError(
            f"`{' '.join(command)}` did not finish in {COMMAND_SECONDS:.0f} seconds."
        ) from error
    if check and completed.returncode != 0:
        said = (completed.stderr or completed.stdout).strip()
        raise InstancesError(f"`{' '.join(command)}` failed (exit {completed.returncode}): {said}")
    return completed
