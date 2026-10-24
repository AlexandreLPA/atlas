"""The text of the user's documents, converted once and kept for search: the home's `documents/`.

ripgrep-all's design (`rga`): a document is converted to text by read_file's converter
(`atlas.tools.documents`), the text is kept under a name made from the document's path, and
ripgrep searches the text. A document is converted again when its modification time changes, as
`rga` keys its cache by path and time. A document that cannot be converted (scanned pages that
need OCR, a password, a damaged file) is kept as an empty text, so it is not tried on every search.

The conversion runs inside Atlas and not through ripgrep's `--pre`, which starts a process per
file on every search: measured on 09/10/2026, a Python process with the converter takes 0.8 s to
start, while converting a document takes 0.06 to 0.12 s. The folder is private, as the spill
folder is: it holds what the documents say.
"""

import asyncio
import hashlib
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from atlas.home import HomeError, private_folder, replace_file
from atlas.tools.documents import DOCUMENTS, DocumentError, file_markdown

#: The file name patterns searched as documents.
DOCUMENT_GLOBS = tuple(f"*.{extension}" for extension in sorted(DOCUMENTS))

_logger = logging.getLogger("atlas.tools")


@dataclass(frozen=True, slots=True)
class Prepared:
    """The texts ready for a search, and the documents left for a later one."""

    #: Each text, relative to the folder, with the document's path as the search shows it.
    texts: dict[str, Path]
    #: Documents whose text was not ready in time.
    waiting: int


class DocumentTexts:
    """The kept texts of one home."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder

    def name(self, document: Path) -> str:
        """Where a document's text is kept, relative to the folder: the same length for every
        document, so a search's output can be read back by position."""
        key = str(document.resolve())
        if sys.platform == "win32":
            key = key.lower()
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:40]
        return f"{digest[:2]}/{digest}.md"

    async def prepare(self, documents: list[Path], deadline: float) -> Prepared:
        """The texts of `documents`, converting what is missing or stale until `deadline` (a
        `time.monotonic()` value)."""
        try:
            private_folder(self.folder)
        except HomeError as error:
            _logger.warning("The documents' texts cannot be kept in %s: %s", self.folder, error)
            return Prepared({}, len(documents))
        texts: dict[str, Path] = {}
        waiting = 0
        for document in documents:
            try:
                changed = document.stat().st_mtime_ns
            except OSError:
                continue
            name = self.name(document)
            kept = self.folder / name
            if not _fresh(kept, changed):
                if time.monotonic() >= deadline:
                    waiting += 1
                    continue
                await asyncio.to_thread(self._convert, document, kept, changed)
            if _size(kept) > 0:
                texts[name] = document
        return Prepared(texts, waiting)

    @staticmethod
    def _convert(document: Path, kept: Path, changed: int) -> None:
        try:
            text = file_markdown(document)
        except DocumentError as error:
            text = _plain_text(document)
            if not text:
                _logger.info("A document has no text to search (%s): %s", error, document)
        except OSError as error:
            _logger.warning("A document could not be read: %s", error)
            return
        try:
            kept.parent.mkdir(exist_ok=True)
            replace_file(kept, text.encode("utf-8"))
            os.utime(kept, ns=(changed, changed))
        except OSError as error:
            _logger.warning("A document's text could not be kept: %s", error)


def _plain_text(document: Path) -> str:
    """A file named as a document that is plain text inside (seen on a user's disk: hundreds of
    `.pdf` files that were text), read as it is; empty for anything else."""
    try:
        data = document.read_bytes()
        return "" if b"\x00" in data else data.decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _fresh(kept: Path, changed: int) -> bool:
    try:
        return kept.stat().st_mtime_ns == changed
    except OSError:
        return False


def _size(kept: Path) -> int:
    try:
        return kept.stat().st_size
    except OSError:
        return 0
