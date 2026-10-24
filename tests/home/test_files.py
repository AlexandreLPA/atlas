"""Shared files: a lock another holder keeps busy, and a replacement that is whole or nothing.

Real files in a temporary folder; the lock is held through a second handle, as a second process
would hold it.
"""

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from atlas.home import FileBusyError, locked, replace_file


def test_a_held_lock_makes_the_next_holder_wait_and_give_up(tmp_path: Path) -> None:
    lock = tmp_path / "shared.lock"

    with locked(lock), pytest.raises(FileBusyError, match="stayed locked"), locked(lock, 0.1):
        pass


def test_a_released_lock_can_be_taken_again(tmp_path: Path) -> None:
    lock = tmp_path / "shared.lock"

    with locked(lock):
        pass
    with locked(lock, timeout=0.1):
        pass


def test_another_process_holding_the_lock_keeps_it_busy(tmp_path: Path) -> None:
    lock = tmp_path / "shared.lock"
    holder = textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        from atlas.home import locked
        with locked(Path({str(lock)!r})):
            print("held", flush=True)
            sys.stdin.readline()
        """
    )
    process = subprocess.Popen(  # noqa: S603 - this Python, running the test's own code
        [sys.executable, "-c", holder],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "held"
        with pytest.raises(FileBusyError), locked(lock, timeout=0.2):
            pass
    finally:
        assert process.stdin is not None
        process.stdin.write("\n")
        process.stdin.close()
        process.wait(timeout=10)
    with locked(lock, timeout=1):
        pass


def test_a_replacement_leaves_the_new_content_and_no_temporary_file(tmp_path: Path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("old", encoding="utf-8")

    replace_file(target, "new §\n".encode())

    assert target.read_bytes() == "new §\n".encode()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["notes.md"]
