"""The server terminal (step 15, docs/ARCHITECTURE.md, Several machines): shells the server opens on
its own machine for a client, each in a pseudo-terminal, carried over that client's connection.
One path, local too: the side panel's terminal always runs through here, so it works the same over
Tailscale, over SSH and in the container. A registry of sessions, each the user's shell.

- **A terminal is its client's**: only the connection that opened it writes to it, resizes it and
  closes it, and its terminals close with it.
- **Its output waits for `start`**: the server starts reading once the answer that names the
  terminal is on its way, so no output ever reaches the app before the terminal it belongs to.
- Whoever holds the token already runs commands through the agent: the terminal opens nothing new.
"""

import contextlib
import logging
import os
import sys
import tempfile
import threading
import uuid
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from pathlib import Path

from atlas.terminals.errors import TerminalError
from atlas.terminals.model import (
    NONCE,
    choose_shell,
    launch_for,
    shell_environment,
    shell_name,
)
from atlas.terminals.store import (
    Pty,
    Spawn,
    remove_folder,
    spawn_pty,
    unavailable,
    which,
    write_files,
)

_logger = logging.getLogger("atlas.terminals")

#: The most terminals one client keeps open.
MOST_PER_CLIENT = 16


@dataclass(frozen=True, slots=True)
class Opened:
    id: str
    #: The shell as the tab shows it (`pwsh`, `bash`, `cmd`).
    shell: str
    #: It writes marks: a command's end and exit code are known.
    marks: bool


@dataclass(slots=True)
class _Open:
    owner: Hashable
    pty: Pty
    scratch: Path
    on_output: Callable[[str], None]
    on_exit: Callable[[int | None], None]
    reader: threading.Thread | None = None


def environment_folder() -> str | None:
    """The scripts folder of the environment Atlas runs in, when it runs in one."""
    if sys.prefix == sys.base_prefix:
        return None
    return str(Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin"))


class Terminals:
    """The terminals the server keeps open, by id."""

    def __init__(
        self,
        *,
        spawn: Spawn = spawn_pty,
        env: Mapping[str, str] | None = None,
        platform: str = sys.platform,
        scratch: Path | None = None,
        missing: Callable[[], str | None] = unavailable,
    ) -> None:
        self._spawn = spawn
        self._env = env
        self._platform = platform
        self._scratch = scratch or Path(tempfile.gettempdir()) / "atlas-terminal"
        self._missing = missing
        self._open: dict[str, _Open] = {}
        self._lock = threading.Lock()

    def unavailable(self) -> str | None:
        """Why this machine cannot open a terminal, or `None` when it can."""
        return self._missing()

    def open(
        self,
        owner: Hashable,
        *,
        cols: int,
        rows: int,
        cwd: str,
        nonce: str,
        on_output: Callable[[str], None],
        on_exit: Callable[[int | None], None],
    ) -> Opened:
        """A shell for `owner`, in `cwd` (its home folder when `cwd` is no folder here), writing
        its marks with `nonce`. Its output and its end go to `on_output` and `on_exit`, from a
        thread of its own, once `start` is called."""
        if not NONCE.match(nonce):
            raise TerminalError("A terminal's nonce is 16 to 64 lowercase hexadecimal characters.")
        with self._lock:
            if sum(1 for kept in self._open.values() if kept.owner == owner) >= MOST_PER_CLIENT:
                raise TerminalError(f"{MOST_PER_CLIENT} terminals are open; close one first.")
        env = dict(os.environ if self._env is None else self._env)
        program = choose_shell(
            env, self._platform, exists=os.path.exists, which=lambda name: which(name, env)
        )
        folder = self._scratch / nonce
        launch = launch_for(program, nonce, str(folder))
        write_files(folder, launch.files)
        where = cwd if cwd and Path(cwd).is_dir() else str(Path.home())
        try:
            pty = self._spawn(
                program,
                launch.args,
                cwd=where,
                env={
                    **shell_environment(env, self._platform, environment_folder()),
                    **launch.env,
                },
                cols=max(2, cols),
                rows=max(2, rows),
            )
        except TerminalError:
            remove_folder(folder)
            raise
        terminal_id = uuid.uuid4().hex
        with self._lock:
            self._open[terminal_id] = _Open(owner, pty, folder, on_output, on_exit)
        _logger.info("A terminal opened.", extra={"shell": shell_name(program)})
        return Opened(terminal_id, shell_name(program), launch.marks)

    def start(self, terminal_id: str) -> None:
        """Read its output from now on."""
        with self._lock:
            kept = self._open.get(terminal_id)
            if kept is None or kept.reader is not None:
                return
            kept.reader = threading.Thread(
                target=self._read, args=(terminal_id, kept), name="atlas-terminal", daemon=True
            )
        kept.reader.start()

    def write(self, owner: Hashable, terminal_id: str, data: str) -> None:
        self._of(owner, terminal_id).pty.write(data)

    def resize(self, owner: Hashable, terminal_id: str, cols: int, rows: int) -> None:
        self._of(owner, terminal_id).pty.resize(max(2, cols), max(2, rows))

    def close(self, owner: Hashable, terminal_id: str) -> None:
        """Stop its shell; nothing when it is already gone."""
        with self._lock:
            kept = self._open.get(terminal_id)
            if kept is None or kept.owner != owner:
                return
            del self._open[terminal_id]
        self._end(kept)

    def close_owner(self, owner: Hashable) -> None:
        """Every terminal of a client that left."""
        with self._lock:
            leaving = [key for key, kept in self._open.items() if kept.owner == owner]
            ended = [self._open.pop(key) for key in leaving]
        for kept in ended:
            self._end(kept)

    def close_all(self) -> None:
        with self._lock:
            ended = list(self._open.values())
            self._open.clear()
        for kept in ended:
            self._end(kept)

    def _of(self, owner: Hashable, terminal_id: str) -> _Open:
        with self._lock:
            kept = self._open.get(terminal_id)
        if kept is None or kept.owner != owner:
            raise TerminalError("That terminal is gone.")
        return kept

    def _end(self, kept: _Open) -> None:
        kept.pty.close()
        if kept.reader is None:
            remove_folder(kept.scratch)

    def _read(self, terminal_id: str, kept: _Open) -> None:
        try:
            while text := kept.pty.read():
                kept.on_output(text)
        except Exception:  # the reader's edge: a terminal's failure must not end the server
            _logger.exception("A terminal's output could not be read.")
        with self._lock:
            self._open.pop(terminal_id, None)
        # How it ended is read before it is closed: closing a console whose shell is still
        # leaving stops it, and its own code was lost (found by the test, 19/10/2026).
        code = kept.pty.exit_code()
        kept.pty.close()
        remove_folder(kept.scratch)
        with contextlib.suppress(Exception):
            kept.on_exit(code)
