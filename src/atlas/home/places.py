"""Whether a path leads into a place, however it is written.

`Path.resolve()` follows links but keeps how the volume is written. On Windows `\\\\?\\C:\\x`,
`\\\\?\\GLOBALROOT\\??\\C:\\x`, `\\\\localhost\\C$\\x`, `\\\\127.0.0.1\\C$\\x` and the computer's
own name as a share all lead to `C:\\x`, and a check that compared resolved paths as text let each
of them write the home's settings and read its vault (found on 22/10/2026). So a place is told by
what it is on the disk: the file system's identity of the place, or of its nearest folder that
exists and the names below it. Every way of writing a path that the system opens leads to the same
identity.

A hard link is a second name for a file, in any folder of the volume: a file with more than one
name is also compared with the files of the place.
"""

import sys
from pathlib import Path

type _Identity = tuple[int, int]


def inside(path: Path, place: Path) -> bool:
    """Whether `path` is `place` or inside it, `place` being a folder or a file, either of which
    may not exist yet."""
    if _resolved(path).is_relative_to(_resolved(place)):
        return True
    anchor = _anchor(place)
    if anchor is None:
        return False
    identity, below = anchor
    names: list[str] = []
    current = path
    while True:
        found = _identity(current)
        if found == identity and tuple(reversed(names))[: len(below)] == below:
            return True
        parent = current.parent
        if parent == current:
            break
        names.append(_name(current.name))
        current = parent
    return _linked(path, place)


def _anchor(place: Path) -> tuple[_Identity, tuple[str, ...]] | None:
    """The identity of `place`, or of its nearest folder that exists and the names below it."""
    names: list[str] = []
    current = place
    while True:
        found = _identity(current)
        if found is not None:
            return found, tuple(reversed(names))
        parent = current.parent
        if parent == current:
            return None
        names.append(_name(current.name))
        current = parent


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except (OSError, ValueError):
        return path


def _identity(path: Path) -> _Identity | None:
    try:
        stat = path.stat()
    except (OSError, ValueError):
        return None
    # A file system with no file numbers (FAT) gives 0 to every file: no identity to compare.
    return (stat.st_dev, stat.st_ino) if stat.st_ino else None


def _name(name: str) -> str:
    """A name as the system compares it: Windows ignores case, trailing dots and spaces, and a
    `:stream` after the name (`mcp.json::$DATA` is `mcp.json`)."""
    if sys.platform != "win32":
        return name
    return name.split(":", 1)[0].rstrip(". ").casefold()


def _linked(path: Path, place: Path) -> bool:
    """Whether `path` is a file with other names, one of them inside `place`."""
    try:
        stat = path.stat()
    except (OSError, ValueError):
        return False
    if stat.st_nlink < 2 or not place.is_dir():
        return False
    identity = (stat.st_dev, stat.st_ino)
    if not stat.st_ino:
        return False
    for folder, _, files in place.walk():
        for name in files:
            if _identity(folder / name) == identity:
                return True
    return False
