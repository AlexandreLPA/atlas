"""A process Atlas waits on: the app that started a server, which leaves with it, and the app an
update waits to close before it touches the installation.

A parent-death watchdog, decided on 11/10/2026 (docs/ARCHITECTURE.md, Desktop app), moved here
from the server on 18/10/2026 so the update can wait on the app too:

- A check that cannot decide says alive: stopping a live user's server, or updating under an open
  app, is the worse mistake.
- Atlas keeps the PID, without the process's start time, which would need `psutil`, and guards it
  against a recycled PID another way: on Windows the process is opened once, when it is first
  looked at, and the handle held, so a PID the system hands to another process later cannot pass
  for it. On POSIX a zombie counts as gone (it answers a
  signal, but it is dead).
- Never `os.kill(pid, 0)` on Windows: there it terminates the process (bpo-14484).
"""

import os
import sys
from pathlib import Path


class Parent:
    """A process another one waits on, as that one can see it: the app, for its server and for
    the update it handed off."""

    def __init__(self, pid: int) -> None:
        if pid <= 0:
            raise ValueError("A parent PID is a positive number.")
        self.pid = pid
        self._handle: int | None = None
        if sys.platform == "win32":
            self._handle = _win32_open(pid)

    def alive(self) -> bool:
        """Whether the process is still there; `True` when that cannot be told."""
        if sys.platform == "win32":
            if self._handle is not None:
                return _win32_running(self._handle)
            return _win32_exists(self.pid)
        return _posix_exists(self.pid)

    def close(self) -> None:
        if sys.platform == "win32" and self._handle is not None:
            _win32_close(self._handle)
            self._handle = None


def _posix_exists(pid: int) -> bool:
    if _posix_zombie(pid):
        return False
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True  # it exists, owned by someone else
    except OSError:
        return False
    return True


def _posix_zombie(pid: int) -> bool:
    """A zombie per `/proc/<pid>/stat` field 3; `False` where there is no `/proc` (macOS)."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    except OSError:
        return False
    return len(fields) > 2 and fields[2] == "Z"


if sys.platform == "win32":
    import ctypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    # Pinned result types: the default c_int mangles handles and WAIT_* values.
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.OpenProcess.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    _kernel32.WaitForSingleObject.restype = ctypes.c_uint32
    _kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    _kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

    _QUERY_LIMITED_INFORMATION = 0x1000
    _SYNCHRONIZE = 0x100000  # needed to wait on the handle
    _WAIT_TIMEOUT = 0x102
    _ERROR_ACCESS_DENIED = 5

    def _win32_open(pid: int) -> int | None:
        handle: int | None = _kernel32.OpenProcess(
            _QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, 0, pid
        )
        return handle or None

    def _win32_running(handle: int) -> bool:
        """A held handle stays this process's, whatever its PID is given to later."""
        result: int = _kernel32.WaitForSingleObject(handle, 0)
        return result == _WAIT_TIMEOUT

    def _win32_exists(pid: int) -> bool:
        """Without a held handle (the app runs as another user): open it each time."""
        handle = _win32_open(pid)
        if handle is None:
            # Access denied: it exists. Anything else (an invalid PID): it is gone.
            return ctypes.get_last_error() == _ERROR_ACCESS_DENIED
        try:
            return _win32_running(handle)
        finally:
            _win32_close(handle)

    def _win32_close(handle: int) -> None:
        _kernel32.CloseHandle(handle)
