"""Files that several processes share: a lock, and a replacement that is whole or nothing.

**One lock for every reader and writer** of a file that is read, changed and written whole (the
vault, the memory files), held on a separate lock file: `flock` on Linux and macOS,
`msvcrt.locking` on Windows, both released when the process dies. Without it two processes
writing at once lose one of the changes. Readers take it too, because Windows refuses to replace a
file another process has open.

**Atomic replacement:** the new content goes to a temporary file, flushed to disk, and is moved
over the old one, so a crash leaves the old file or the new one, never half of each.
"""

import os
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from atlas.home.errors import FileBusyError

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

#: How long a lock is waited for before giving up.
LOCK_TIMEOUT_SECONDS = 10.0


@contextmanager
def locked(lock_file: Path, timeout: float = LOCK_TIMEOUT_SECONDS) -> Generator[None]:
    """Hold the lock on `lock_file` (created if missing). Raises `FileBusyError` after
    `timeout` seconds of another process holding it."""
    deadline = time.monotonic() + timeout
    with lock_file.open("a+b") as handle:
        while not _try_lock(handle.fileno()):
            if time.monotonic() > deadline:
                raise FileBusyError(
                    f"{lock_file} stayed locked for {timeout:.0f} seconds. Another Atlas process "
                    "holds it; try again."
                )
            time.sleep(0.02)
        try:
            yield
        finally:
            _unlock(handle.fileno())


def replace_file(path: Path, data: bytes) -> None:
    """Put `data` in `path` whole, through a flushed temporary file moved over it."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    if sys.platform != "win32":
        # The rename itself must reach the disk too; Windows has no way to sync a folder.
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def _try_lock(descriptor: int) -> bool:
    if sys.platform == "win32":
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _unlock(descriptor: int) -> None:
    if sys.platform == "win32":
        os.lseek(descriptor, 0, os.SEEK_SET)
        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
