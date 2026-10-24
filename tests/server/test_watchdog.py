"""The parent watchdog tells a live process from a gone one, with real processes."""

import os
import subprocess
import sys

import pytest

from atlas.server import Parent


def _sleeper() -> subprocess.Popen[bytes]:
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])


def test_a_running_process_is_alive() -> None:
    parent = Parent(os.getpid())
    try:
        assert parent.alive()
    finally:
        parent.close()


def test_a_process_that_ended_is_gone() -> None:
    child = _sleeper()
    parent = Parent(child.pid)
    try:
        assert parent.alive()
        child.kill()
        child.wait(timeout=30)
        assert not parent.alive()
    finally:
        parent.close()


def test_a_process_gone_before_the_server_started_is_gone() -> None:
    child = _sleeper()
    child.kill()
    child.wait(timeout=30)
    parent = Parent(child.pid)
    try:
        assert not parent.alive()
    finally:
        parent.close()


@pytest.mark.parametrize("pid", [0, -1])
def test_a_pid_is_positive(pid: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        Parent(pid)
