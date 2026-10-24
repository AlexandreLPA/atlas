"""The pseudo-terminals the server terminal runs its shells in (step 15).

ConPTY through `pywinpty` on Windows, a POSIX pty elsewhere, read in a thread of its own.

- **Windows**: `winpty.PTY`, the native object under `pywinpty`'s `PtyProcess`, which adds a
  loopback socket and a thread of its own per terminal that this reader does not need.
- **POSIX**: the standard library's pty, not `ptyprocess`: `ptyprocess` wraps the same
  calls, and one dependency fewer. The shell does not start through
  `pty.fork`, which forks a threaded server: `subprocess` starts a short Python that takes the pty
  as its controlling terminal and becomes the shell, so Ctrl+C reaches what runs in it.
"""

import codecs
import contextlib
import os
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from importlib import util
from pathlib import Path
from typing import Protocol

from atlas.terminals.errors import TerminalError, TerminalUnavailableError

#: The most one read takes.
READ_BYTES = 64 * 1024

#: What makes the pty the controlling terminal of the shell, then becomes it.
_BECOME = (
    "import fcntl, os, sys, termios\n"
    "fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"
    "os.execvp(sys.argv[1], sys.argv[1:])\n"
)


class Pty(Protocol):
    """One shell in a pseudo-terminal."""

    @property
    def pid(self) -> int: ...

    def read(self) -> str:
        """The next output, blocking until some comes; `""` once the shell is gone."""
        ...

    def write(self, data: str) -> None: ...

    def resize(self, cols: int, rows: int) -> None: ...

    def close(self) -> None:
        """Stop the shell and what runs in it; nothing when it is already gone."""
        ...

    def exit_code(self) -> int | None:
        """How it ended, once it did."""
        ...


class Spawn(Protocol):
    def __call__(
        self,
        program: str,
        args: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str],
        cols: int,
        rows: int,
    ) -> Pty: ...


def unavailable() -> str | None:
    """Why this machine cannot open a terminal, or `None` when it can."""
    if sys.platform == "win32" and util.find_spec("winpty") is None:
        return (
            "The terminal needs pywinpty on this machine, which is not installed (run "
            "`uv sync --all-extras`, or install Atlas again)."
        )
    return None


def spawn_pty(
    program: str,
    args: Sequence[str],
    *,
    cwd: str,
    env: Mapping[str, str],
    cols: int,
    rows: int,
) -> Pty:
    """This system's pseudo-terminal running `program`."""
    problem = unavailable()
    if problem is not None:
        raise TerminalUnavailableError(problem)
    if sys.platform == "win32":
        return _WindowsPty(program, args, cwd=cwd, env=env, cols=cols, rows=rows)
    return _PosixPty(program, args, cwd=cwd, env=env, cols=cols, rows=rows)


def which(program: str, env: Mapping[str, str]) -> str | None:
    return shutil.which(program, path=env.get("PATH") or env.get("Path"))


def write_files(folder: Path, files: Mapping[str, str]) -> None:
    """The files a shell reads first, readable by the user alone."""
    if not files:
        return
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, text in files.items():
        path = folder / name
        path.write_text(text, encoding="utf-8", newline="\n")
        path.chmod(0o600)


def remove_folder(folder: Path) -> None:
    shutil.rmtree(folder, ignore_errors=True)


if sys.platform == "win32":

    class _WindowsPty:
        def __init__(
            self,
            program: str,
            args: Sequence[str],
            *,
            cwd: str,
            env: Mapping[str, str],
            cols: int,
            rows: int,
        ) -> None:
            # Loaded only here: it is optional, and `unavailable` said whether it is there.
            from winpty import PTY  # noqa: PLC0415  # pyright: ignore[reportMissingTypeStubs]

            self._pty = PTY(cols, rows)
            block = "".join(f"{name}={value}\0" for name, value in env.items()) + "\0"
            line = subprocess.list2cmdline([program, *args])
            try:
                self._pty.spawn(program, cmdline=line, cwd=cwd, env=block)
            except Exception as error:  # winpty's own error, untyped
                raise TerminalError(f"The shell {program} did not start: {error}") from error
            self._pid = self._pty.pid or 0
            self._closed = False

        @property
        def pid(self) -> int:
            return self._pid

        def read(self) -> str:
            while not self._closed:
                try:
                    text = self._pty.read(blocking=True)
                except Exception:  # noqa: BLE001 - a closed console raises winpty's own error
                    return ""
                if text:
                    return text
                if self._pty.iseof():
                    return ""
            return ""

        def write(self, data: str) -> None:
            if not self._closed:
                with contextlib.suppress(Exception):
                    self._pty.write(data)

        def resize(self, cols: int, rows: int) -> None:
            if not self._closed:
                with contextlib.suppress(Exception):
                    self._pty.set_size(cols, rows)  # pyright: ignore[reportUnknownMemberType]

        def close(self) -> None:
            if self._closed:
                return
            self._closed = True
            if self._pty.isalive():
                with contextlib.suppress(OSError):
                    os.kill(self._pid, signal.SIGTERM)
            with contextlib.suppress(Exception):
                self._pty.cancel_io()

        def exit_code(self) -> int | None:
            """Once it ended: the console's output may end a moment before its process."""
            deadline = time.monotonic() + 2
            with contextlib.suppress(Exception):
                while self._pty.isalive() and time.monotonic() < deadline:
                    time.sleep(0.02)
                return self._pty.get_exitstatus()
            return None

else:

    class _PosixPty:
        def __init__(
            self,
            program: str,
            args: Sequence[str],
            *,
            cwd: str,
            env: Mapping[str, str],
            cols: int,
            rows: int,
        ) -> None:
            import termios  # noqa: PLC0415 - POSIX's only

            main, child = os.openpty()
            _size(main, cols, rows)
            attributes = termios.tcgetattr(child)
            attributes[3] |= termios.ECHO
            termios.tcsetattr(child, termios.TCSANOW, attributes)
            try:
                self._process = subprocess.Popen(  # noqa: S603 - the user's shell, by design
                    [sys.executable, "-c", _BECOME, program, *args],
                    stdin=child,
                    stdout=child,
                    stderr=child,
                    cwd=cwd,
                    env=dict(env),
                    start_new_session=True,
                    close_fds=True,
                )
            except OSError as error:
                os.close(main)
                raise TerminalError(f"The shell {program} did not start: {error}") from error
            finally:
                os.close(child)
            self._main = main
            self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
            self._closed = False

        @property
        def pid(self) -> int:
            return self._process.pid

        def read(self) -> str:
            while True:
                try:
                    data = os.read(self._main, READ_BYTES)
                except OSError:  # EIO: the shell and everything on the pty are gone
                    data = b""
                if not data:
                    return self._decoder.decode(b"", final=True)
                text = self._decoder.decode(data)
                if text:
                    return text

        def write(self, data: str) -> None:
            if not self._closed:
                with contextlib.suppress(OSError):
                    os.write(self._main, data.encode("utf-8"))

        def resize(self, cols: int, rows: int) -> None:
            if not self._closed:
                with contextlib.suppress(OSError):
                    _size(self._main, cols, rows)

        def close(self) -> None:
            if self._closed:
                return
            self._closed = True
            # The shell leads its own session: the hang-up reaches every job of it.
            with contextlib.suppress(OSError):
                os.killpg(self._process.pid, signal.SIGHUP)
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                with contextlib.suppress(OSError):
                    os.killpg(self._process.pid, signal.SIGKILL)
            with contextlib.suppress(OSError):
                os.close(self._main)

        def exit_code(self) -> int | None:
            try:
                return self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                return None

    def _size(fd: int, cols: int, rows: int) -> None:
        import fcntl  # noqa: PLC0415 - POSIX's only
        import struct  # noqa: PLC0415
        import termios  # noqa: PLC0415

        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
