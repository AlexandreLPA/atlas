"""The server terminal for real: the shell installed on this machine (PowerShell on Windows, bash
elsewhere) in this system's pseudo-terminal, with the marks it is made to write. Nothing stands in
for the shell or the pseudo-terminal: they are what is proved."""

import os
import re
import secrets
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from atlas.terminals import MOST_PER_CLIENT, Opened, TerminalError, Terminals

WINDOWS = sys.platform == "win32"
needs_bash = pytest.mark.skipif(
    not WINDOWS and shutil.which("bash") is None, reason="bash is not installed"
)


class Screen:
    """What a terminal wrote, and how it ended."""

    def __init__(self) -> None:
        self.text = ""
        self.code: int | None = None
        self.ended = threading.Event()
        self._lock = threading.Lock()

    def output(self, data: str) -> None:
        with self._lock:
            self.text += data

    def exit(self, code: int | None) -> None:
        self.code = code
        self.ended.set()

    def until(self, found: Callable[[str], bool], seconds: float = 20) -> str:
        deadline = time.monotonic() + seconds
        while not found(self.text):
            if time.monotonic() > deadline:
                raise AssertionError(f"Not seen in time:\n{self.text[-2000:]}")
            time.sleep(0.05)
        return self.text


@pytest.fixture
def terminals(tmp_path: Path) -> Iterator[Terminals]:
    env = dict(os.environ)
    if not WINDOWS:
        env["ATLAS_TERMINAL_SHELL"] = "bash"
    else:
        env.pop("ATLAS_TERMINAL_SHELL", None)
    kept = Terminals(env=env, scratch=tmp_path / "scratch")
    yield kept
    kept.close_all()


def _open(terminals: Terminals, cwd: str, owner: object = "client") -> tuple[Opened, Screen, str]:
    screen, nonce = Screen(), secrets.token_hex(16)
    opened = terminals.open(
        owner, cols=100, rows=30, cwd=cwd, nonce=nonce, on_output=screen.output, on_exit=screen.exit
    )
    terminals.start(opened.id)
    return opened, screen, nonce


@needs_bash
def test_a_shell_opens_where_asked_writes_its_marks_and_runs_what_is_typed(
    terminals: Terminals, tmp_path: Path
) -> None:
    opened, screen, nonce = _open(terminals, str(tmp_path))
    assert opened.marks
    assert opened.shell in (("powershell", "pwsh") if WINDOWS else ("bash",))
    screen.until(lambda text: f"133;A;nonce={nonce}" in text)

    terminals.write("client", opened.id, "echo atlas-42\r")
    text = screen.until(lambda text: f"133;D;0;nonce={nonce}" in text)
    assert "atlas-42" in text
    # It started in the folder asked for.
    terminals.write("client", opened.id, "pwd\r")
    screen.until(lambda text: tmp_path.name in text.split(f"133;D;0;nonce={nonce}", 1)[1])

    terminals.write("client", opened.id, "exit 3\r")
    assert screen.ended.wait(20)
    # The shell's own code. It read as 1 on Windows while the program was named twice, which ran
    # a second PowerShell inside the first (24/10/2026).
    assert screen.code == 3
    assert not (tmp_path / "scratch" / nonce).exists(), "its start-up files go with it"


@needs_bash
def test_a_resized_shell_says_its_new_size(terminals: Terminals, tmp_path: Path) -> None:
    opened, screen, nonce = _open(terminals, str(tmp_path))
    screen.until(lambda text: f"133;A;nonce={nonce}" in text)
    terminals.resize("client", opened.id, 123, 45)
    asked = "$Host.UI.RawUI.WindowSize.Width" if WINDOWS else "tput cols"
    terminals.write("client", opened.id, f"{asked}\r")
    screen.until(lambda text: re.search(r"\b123\b", text.split(asked, 1)[-1]) is not None)


@needs_bash
def test_a_terminal_is_its_clients_alone_and_goes_with_it(
    terminals: Terminals, tmp_path: Path
) -> None:
    opened, screen, nonce = _open(terminals, "no such folder", owner="first")
    screen.until(lambda text: f"133;A;nonce={nonce}" in text)

    with pytest.raises(TerminalError, match="gone"):
        terminals.write("second", opened.id, "echo nope\r")
    with pytest.raises(TerminalError, match="gone"):
        terminals.resize("second", opened.id, 10, 10)
    terminals.close("second", opened.id)  # not its own: nothing
    assert not screen.ended.is_set()

    terminals.close_owner("first")
    assert screen.ended.wait(20), "its shell stopped with its client"
    with pytest.raises(TerminalError, match="gone"):
        terminals.write("first", opened.id, "echo gone\r")


def test_a_nonce_that_is_not_hex_opens_nothing(terminals: Terminals, tmp_path: Path) -> None:
    with pytest.raises(TerminalError, match="hexadecimal"):
        terminals.open(
            "client",
            cols=80,
            rows=24,
            cwd=str(tmp_path),
            nonce="'; whoami; '",
            on_output=lambda _: None,
            on_exit=lambda _: None,
        )


def test_one_client_keeps_a_bounded_number_of_terminals(tmp_path: Path) -> None:
    class Idle:
        pid = 1

        def read(self) -> str:
            time.sleep(0.01)
            return ""

        def write(self, data: str) -> None: ...

        def resize(self, cols: int, rows: int) -> None: ...

        def close(self) -> None: ...

        def exit_code(self) -> int | None:
            return 0

    def spawn(*_: object, **__: object) -> Idle:
        return Idle()

    terminals = Terminals(spawn=spawn, scratch=tmp_path)
    for _ in range(MOST_PER_CLIENT):
        terminals.open(
            "client",
            cols=80,
            rows=24,
            cwd="",
            nonce=secrets.token_hex(16),
            on_output=lambda _: None,
            on_exit=lambda _: None,
        )
    with pytest.raises(TerminalError, match="close one first"):
        terminals.open(
            "client",
            cols=80,
            rows=24,
            cwd="",
            nonce=secrets.token_hex(16),
            on_output=lambda _: None,
            on_exit=lambda _: None,
        )


def test_a_machine_without_its_pseudo_terminal_package_says_so(tmp_path: Path) -> None:
    terminals = Terminals(scratch=tmp_path, missing=lambda: "The terminal needs pywinpty.")
    assert terminals.unavailable() == "The terminal needs pywinpty."
