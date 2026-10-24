"""The home folder on disk: create it, and keep the folders that hold secrets private.

**Private means only the owner.** On Linux and macOS that is mode 0700. On Windows it is an access
list with a single entry, the owner's (see `atlas.home.windows`).

**Close the empty folder first, write afterwards.** A file written before its folder is closed
keeps the access it was created with. So a private folder is closed while it is still empty, and an
existing one is checked, never assumed.
"""

import os
import sys
from pathlib import Path

from atlas.home.errors import HomeFolderError
from atlas.home.model import Home

if sys.platform == "win32":
    from atlas.home import windows


def open_home(root: Path) -> Home:
    """The home at `root`, created (private) if it does not exist yet."""
    if root.exists() and not root.is_dir():
        raise HomeFolderError(
            f"{root} is a file, but the home must be a folder. "
            "Point ATLAS_HOME at a folder, or move the file away."
        )
    if root.is_dir():
        return Home(root)
    try:
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir()
    except OSError as error:
        if root.is_dir():  # another process created it first, and closed it
            return Home(root)
        raise HomeFolderError(f"Could not create the home folder {root}: {error}.") from error
    _close(root)
    return Home(root)


def private_folder(folder: Path) -> Path:
    """`folder`, created private if missing. An existing one must already be private."""
    if folder.exists() and not folder.is_dir():
        raise HomeFolderError(f"{folder} must be a folder, but it is a file.")
    if folder.is_dir():
        if not is_private(folder):
            raise HomeFolderError(
                f"{folder} can be opened by other accounts ({', '.join(_who(folder))}), and it "
                "holds secrets. Make it private "
                f"(chmod 700 on Linux and macOS; on Windows, remove inherited access and keep only "
                f"your account) or move its content to a new folder."
            )
        return folder
    try:
        folder.mkdir(parents=True)
    except OSError as error:
        if folder.is_dir():  # another process created it first, and closed it
            return folder
        raise HomeFolderError(f"Could not create {folder}: {error}.") from error
    _close(folder)
    return folder


def is_private(path: Path) -> bool:
    """Whether only the owner can open `path`."""
    if sys.platform == "win32":
        return len(windows.principals(path)) == 1
    status = path.stat()
    return status.st_uid == os.getuid() and status.st_mode & 0o077 == 0


def _who(path: Path) -> list[str]:
    """Who can open `path`, for the message that refuses it."""
    if sys.platform == "win32":
        return windows.principals(path)
    status = path.stat()
    return [f"mode {status.st_mode & 0o777:o}, owner uid {status.st_uid}"]


def _close(folder: Path) -> None:
    if sys.platform == "win32":
        windows.close_folder(folder)
    else:
        folder.chmod(0o700)
