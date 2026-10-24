"""A file of the server's machine for the side panel's preview (step 15, docs/ARCHITECTURE.md,
Several machines): the file is where the server is, so the server reads it, on this computer as on
another machine or in the container. It moved here from the app's `electron/preview-ipc.ts` (step
16f), whose rules it keeps: an absolute path, a file, at most 5 MB, shown by what it is (HTML in the
isolated frame, Markdown read, an image, SVG included, drawn as an image so its scripts never run,
or its text)."""

import base64
from pathlib import Path
from typing import Literal

#: Larger files are not shown: a preview is for a page, not a dump.
PREVIEW_BYTES = 5 * 1024 * 1024

_IMAGES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
}

type PreviewKind = Literal["html", "markdown", "text", "image", "error"]


def read_preview(file: str) -> tuple[PreviewKind, str]:
    """What the file is and what shows it: its text, an image's data URL, or why not."""
    path = Path(file)
    if not file or not path.is_absolute():
        return "error", f"{file} is not an absolute path."
    try:
        if not path.is_file():
            return "error", f"{file} is not a file."
        if path.stat().st_size > PREVIEW_BYTES:
            return "error", f"{file} is larger than 5 MB."
        data = path.read_bytes()
    except OSError as error:
        return "error", f"{file} could not be read: {error}"
    extension = path.suffix.lower()
    image = _IMAGES.get(extension)
    if image is not None:
        return "image", f"data:{image};base64,{base64.b64encode(data).decode('ascii')}"
    text = data.decode("utf-8", "replace")
    if extension in (".html", ".htm"):
        return "html", text
    return ("markdown" if extension in (".md", ".markdown") else "text"), text
