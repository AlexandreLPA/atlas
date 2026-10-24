"""Documents as Markdown, for read_file and web_fetch.

read_file turns PDF, Word, PowerPoint, Excel, OpenDocument, RTF and EPUB into Markdown with
`firecrawl-anydoc`, kept in the core because reading a PDF is a common first thing to ask (decided
on 09/10/2026). The conversion runs on this machine: a PDF whose pages are scanned images needs OCR,
which anydoc would do by sending the whole document to Firecrawl, so here it is refused with the
reason instead.
"""

from pathlib import Path

import anydoc

#: The formats read as documents. CSV stays text: read_file numbers its lines as they are.
DOCUMENTS = frozenset(
    {"pdf", "doc", "docx", "odt", "rtf", "epub", "ppt", "pptx", "odp", "xlsx", "ods"}
)


class DocumentError(Exception):
    """A document could not be turned into text; the message says why."""


def document_format(path: Path) -> str | None:
    """The document format of a file, by its content, or `None` for anything else."""
    try:
        found = anydoc.format_from_path(path)
    except (OSError, anydoc.ConvertError):
        return None
    return found if found in DOCUMENTS else None


def bytes_format(data: bytes) -> str | None:
    found = anydoc.format_from_bytes(data)
    return found if found in DOCUMENTS else None


def file_markdown(path: Path) -> str:
    """A document file as Markdown. Raises `DocumentError`."""
    try:
        return anydoc.to_markdown(path)
    except anydoc.ConvertError as error:
        raise DocumentError(_why(error)) from error


def bytes_markdown(data: bytes) -> str:
    """A downloaded document as Markdown. Raises `DocumentError`."""
    try:
        return anydoc.to_markdown_bytes(data)
    except anydoc.ConvertError as error:
        raise DocumentError(_why(error)) from error


def _why(error: Exception) -> str:
    if isinstance(error, anydoc.NeedsOcrError):
        return f"its pages are images that need OCR, which Atlas does not do: {error}"
    if isinstance(error, anydoc.EncryptedError):
        return "it is encrypted with a password"
    return f"{type(error).__name__}: {error}"
