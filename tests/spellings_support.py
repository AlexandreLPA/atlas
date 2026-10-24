"""Other ways of writing a path that Windows opens as the same file: device paths, the drive's
administrative share on this computer, and `\\\\?\\GLOBALROOT`. Empty elsewhere."""

import sys
from pathlib import Path

SLASH = "\\"


def spellings(path: Path) -> list[str]:
    """`path` written other ways, those that open on this computer."""
    if sys.platform != "win32":
        return []
    text = str(path.resolve())
    drive, rest = text[0], text[2:]
    share = f"{drive}$"
    written = [
        SLASH * 2 + "?" + SLASH + text,
        SLASH * 2 + "." + SLASH + text,
        SLASH * 2 + "?" + SLASH + "GLOBALROOT" + SLASH + "??" + SLASH + text,
        SLASH * 2 + "localhost" + SLASH + share + rest,
        SLASH * 2 + "127.0.0.1" + SLASH + share + rest,
        SLASH * 2 + "?" + SLASH + "UNC" + SLASH + "localhost" + SLASH + share + rest,
    ]
    return [spelled for spelled in written if _opens(spelled, path)]


def _opens(spelled: str, path: Path) -> bool:
    """Whether this computer opens the spelling (its administrative shares may be off)."""
    folder = path if path.exists() else path.parent
    tail = len(path.parts) - len(folder.parts)
    target = Path(spelled)
    for _ in range(tail):
        target = target.parent
    try:
        return target.samefile(folder)
    except OSError:
        return False
