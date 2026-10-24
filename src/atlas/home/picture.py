"""The home's picture (asked for by the user on 18/10/2026): beside its name at the foot of the
app's sidebar.

It is `picture.png` in the home, a `user` entry, so a backup carries it. The app crops and shrinks
it before it comes here, and only a PNG this small is kept: what reaches the home is never a file of
any size or kind.
"""

from atlas.home.errors import PictureError
from atlas.home.files import replace_file
from atlas.home.model import Home

#: What a PNG starts with.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
#: The most a picture may weigh: the app sends a square of 256 pixels, well under it.
MAX_PICTURE_BYTES = 512 * 1024


def read_picture(home: Home) -> bytes | None:
    """The home's picture, or `None` when it has none."""
    path = home.picture_file
    return path.read_bytes() if path.is_file() else None


def write_picture(home: Home, data: bytes | None) -> None:
    """Keep `data` as the home's picture; `None` takes it away. Raises `PictureError`."""
    path = home.picture_file
    if data is None:
        path.unlink(missing_ok=True)
        return
    if not data.startswith(PNG_SIGNATURE):
        raise PictureError("The picture is not a PNG. Choose an image again.")
    if len(data) > MAX_PICTURE_BYTES:
        raise PictureError(
            f"The picture weighs {len(data) // 1024} KB; a home's takes at most "
            f"{MAX_PICTURE_BYTES // 1024} KB."
        )
    replace_file(path, data)
