"""The file tools: read_file, write_file and edit_file, native, so the core needs no Node.

Atlas V3 got its file tools from `@modelcontextprotocol/server-filesystem`, run through `npx`.

- **read_file** numbers lines as `cat -n` does and reads 2,000 lines from `offset`, within about
  100,000 characters, saying where to continue. A path that does not exist gets the names that
  look like it. It pages its own output, so it never spills. A document (PDF, Office,
  OpenDocument, RTF, EPUB) is read as Markdown (`atlas.tools.documents`); an image (PNG, JPEG,
  GIF, WebP, up to 5 MB) goes to the model to see.
- **write_file** replaces a whole file, atomically, and creates the folders it needs. An existing
  file must have been read in this conversation and not changed since, so a write never loses
  what the model did not see.
- **edit_file** replaces exact text (`old_string`, `new_string`, `replace_all`): no regex and no
  fuzzy matching. The text must appear once, or `replace_all` says to change every one. The only
  tolerance is line endings, since a Windows file ends lines in CRLF and the model writes LF. It
  answers with the diff. There is no patch tool: a patch grammar is something only some providers'
  APIs take.
- **The vault's folder is out of reach**: its key opens every secret, and a key read into the
  context would travel to the provider.
"""

import asyncio
import difflib
import os
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from atlas.agent import Tool, ToolOutput
from atlas.home import inside
from atlas.models import Image
from atlas.skills import mark_read
from atlas.tools.documents import DocumentError, document_format, file_markdown

#: Lines read_file returns when the model does not say.
READ_LINES = 2_000
#: The most characters one read returns; past it the read stops at a line and says where to go on.
READ_CHARS = 100_000
#: A line longer than this is cut.
LINE_CHARS = 2_000
#: The most characters of diff an edit answers with.
DIFF_CHARS = 4_000
_SNIFF_BYTES = 8_192
#: The largest image read_file sends: Claude's limit, the lowest of the providers'.
IMAGE_BYTES = 5_000_000
_IMAGES = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


@dataclass
class Workspace:
    """Where the file tools work: relative paths start at `root`, and `protected` folders (the
    vault's) are out of reach. One per conversation, since it remembers what was read."""

    root: Path
    protected: tuple[Path, ...] = ()
    #: What each file looked like when it was last read or written: (modified time, size).
    seen: dict[Path, tuple[int, int]] = field(default_factory=dict[Path, tuple[int, int]])
    #: Documents converted to Markdown, with what the file looked like then.
    documents: dict[Path, tuple[tuple[int, int], str]] = field(
        default_factory=dict[Path, tuple[tuple[int, int], str]]
    )

    def resolve(self, text: object) -> Path:
        """The absolute path for what the model wrote. Raises `ValueError` with what to say."""
        if not isinstance(text, str) or not text.strip():
            raise ValueError("A path is needed.")
        path = Path(text.strip()).expanduser()
        if not path.is_absolute():
            path = self.root / path
        path = path.resolve()
        for folder in self.protected:
            # By what the path leads to, not how it is written (`atlas.home.places`).
            if inside(path, folder):
                raise ValueError(
                    f"{path} is inside Atlas's vault, which the file tools do not open: its key "
                    "opens every secret."
                )
        return path

    def remember(self, path: Path) -> None:
        stat = path.stat()
        self.seen[path] = (stat.st_mtime_ns, stat.st_size)

    def document(self, path: Path) -> str:
        """A document as Markdown, converted once while the file stays the same, so paging
        through a long PDF does not convert it again for every page."""
        stat = path.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        cached = self.documents.get(path)
        if cached is not None and cached[0] == key:
            return cached[1]
        text = file_markdown(path)
        self.documents[path] = (key, text)
        return text

    def changed_since_seen(self, path: Path) -> bool | None:
        """Whether the file changed since it was last read or written; `None` if never seen."""
        before = self.seen.get(path)
        if before is None:
            return None
        stat = path.stat()
        return (stat.st_mtime_ns, stat.st_size) != before


def file_tools(workspace: Workspace) -> list[Tool]:
    """read_file, write_file and edit_file over one workspace."""
    return [_read_file(workspace), _write_file(workspace), _edit_file(workspace)]


def _path_property() -> dict[str, Any]:
    return {"type": "string", "description": "Absolute path, or relative to the working folder."}


# -- read_file -----------------------------------------------------------------------------------


def _read_file(workspace: Workspace) -> Tool:
    async def run(arguments: dict[str, Any]) -> str | ToolOutput:
        try:
            path = workspace.resolve(arguments.get("path"))
        except ValueError as error:
            return f"[Atlas: {error}]"
        offset = _whole(arguments.get("offset"), 1, minimum=1)
        limit = _whole(arguments.get("limit"), READ_LINES, minimum=1, maximum=READ_LINES)
        return await asyncio.to_thread(_read, workspace, path, offset, limit)

    return Tool(
        name="read_file",
        description=(
            "Reads a text file, or a document (PDF, Word, PowerPoint, Excel, OpenDocument, RTF, "
            "EPUB) as Markdown, and returns its lines numbered as `cat -n` does; an image (PNG, "
            "JPEG, GIF, WebP) you see. Reads up to 2,000 "
            "lines from `offset`; a longer file says where to continue. Read a file before "
            "changing it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": _path_property(),
                "offset": {"type": "integer", "description": "First line to read, from 1."},
                "limit": {"type": "integer", "description": "How many lines, up to 2,000."},
            },
            "required": ["path"],
            "additionalProperties": False,
        },
        run=run,
        read_only=True,
        spills=False,
    )


def _read(workspace: Workspace, path: Path, offset: int, limit: int) -> str | ToolOutput:
    if path.is_dir():
        return f"[Atlas: {path} is a folder. List it with find_files (target: files).]"
    if not path.exists():
        return f"[Atlas: {path} does not exist.{_lookalikes(path)}]"
    image = _image_type(path)
    if image is not None:
        size = path.stat().st_size
        if size > IMAGE_BYTES:
            return (
                f"[Atlas: {path} is a {size:,}-byte image, past the {IMAGE_BYTES:,} bytes a model "
                "takes. Make a smaller copy with run_command and read that.]"
            )
        workspace.remember(path)
        return ToolOutput(
            f"Image {path}: {image}, {size:,} bytes.", images=(Image(image, path.read_bytes()),)
        )
    kind = document_format(path)
    if kind is not None:
        try:
            text = workspace.document(path)
        except DocumentError as error:
            return f"[Atlas: {path} is a {kind.upper()} file that could not be read: {error}.]"
        numbered = _numbered(iter(text.splitlines()), offset, limit)
    else:
        with path.open("rb") as raw:
            if b"\x00" in raw.read(_SNIFF_BYTES):
                return f"[Atlas: {path} is not a text file or a document read_file can read.]"
        with path.open(encoding="utf-8-sig", errors="replace", newline=None) as text:
            numbered = _numbered(text, offset, limit)
    workspace.remember(path)
    mark_read(path)  # a background review may now change it, if it is a skill's file
    lines, more = numbered
    if not lines:
        if offset > 1:
            return f"[Atlas: {path} has fewer than {offset:,} lines.]"
        return f"[Atlas: {path} is empty.]"
    if more:
        last = offset + len(lines) - 1
        lines.append(f"[Atlas: the file goes on; continue with offset={last + 1}.]")
    return "\n".join(lines)


def _image_type(path: Path) -> str | None:
    """The media type of an image the model can see, by the file's first bytes."""
    with path.open("rb") as raw:
        start = raw.read(16)
    for signature, media_type in _IMAGES:
        if start.startswith(signature):
            return media_type
    if start[:4] == b"RIFF" and start[8:12] == b"WEBP":
        return "image/webp"
    return None


def _numbered(source: Iterator[str], offset: int, limit: int) -> tuple[list[str], bool]:
    """Lines from `offset`, numbered as `cat -n` does, within the line and character budgets;
    and whether the file goes on past them."""
    lines: list[str] = []
    used = 0
    for number, line in enumerate(source, start=1):
        if number < offset:
            continue
        if len(lines) == limit:
            return lines, True
        line = line.rstrip("\n")
        if len(line) > LINE_CHARS:
            line = line[:LINE_CHARS] + f"… [line cut at {LINE_CHARS:,} characters]"
        entry = f"{number:>6}\t{line}"
        if lines and used + len(entry) > READ_CHARS:
            return lines, True
        lines.append(entry)
        used += len(entry) + 1
    return lines, False


def _lookalikes(path: Path) -> str:
    folder = path.parent
    if not folder.is_dir():
        return f" Neither does its folder, {folder}."
    try:
        names = [entry.name for entry in folder.iterdir()]
    except OSError:
        return ""
    close = difflib.get_close_matches(path.name, names, n=3, cutoff=0.6)
    return f" Did you mean: {', '.join(close)}?" if close else ""


# -- write_file ----------------------------------------------------------------------------------


def _write_file(workspace: Workspace) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        try:
            path = workspace.resolve(arguments.get("path"))
        except ValueError as error:
            return f"[Atlas: {error}]"
        content = arguments.get("content")
        if not isinstance(content, str):
            return "[Atlas: write_file needs `content`, the whole text of the file.]"
        return await asyncio.to_thread(_write, workspace, path, content)

    return Tool(
        name="write_file",
        description=(
            "Creates a file, or replaces all of an existing one, with `content`; folders are "
            "created as needed. An existing file must be read with read_file first. To change "
            "part of a file, use edit_file."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": _path_property(),
                "content": {"type": "string", "description": "The whole text of the file."},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
        run=run,
    )


def _write(workspace: Workspace, path: Path, content: str) -> str:
    if path.is_dir():
        return f"[Atlas: {path} is a folder.]"
    if path.exists():
        changed = workspace.changed_since_seen(path)
        if changed is None:
            return (
                f"[Atlas: {path} already exists and was not read in this conversation. Read it "
                "with read_file first, so nothing in it is lost, then write it.]"
            )
        if changed:
            return f"[Atlas: {path} changed since you read it. Read it again, then write it.]"
    path.parent.mkdir(parents=True, exist_ok=True)
    _replace(path, content)
    workspace.remember(path)
    lines = content.count("\n") + (0 if content.endswith("\n") or not content else 1)
    return f"Wrote {lines:,} lines to {path}."


def _replace(path: Path, content: str) -> None:
    """Write `content` to `path` whole or not at all: a crash midway leaves the old file."""
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        if path.exists():
            Path(temporary).chmod(path.stat().st_mode)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# -- edit_file -----------------------------------------------------------------------------------


def _edit_file(workspace: Workspace) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        try:
            path = workspace.resolve(arguments.get("path"))
        except ValueError as error:
            return f"[Atlas: {error}]"
        old, new = arguments.get("old_string"), arguments.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            return "[Atlas: edit_file needs `old_string` and `new_string`.]"
        every = arguments.get("replace_all") is True
        return await asyncio.to_thread(_edit, workspace, path, old, new, every)

    return Tool(
        name="edit_file",
        description=(
            "Replaces exact text in a file. `old_string` must appear exactly once, whitespace "
            "and indentation included, unless `replace_all` is true; include enough surrounding "
            "lines to make it unique. Answers with the diff."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": _path_property(),
                "old_string": {"type": "string", "description": "The exact text to replace."},
                "new_string": {"type": "string", "description": "The text to put in its place."},
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every occurrence. Defaults to false.",
                },
            },
            "required": ["path", "old_string", "new_string"],
            "additionalProperties": False,
        },
        run=run,
    )


def _edit(workspace: Workspace, path: Path, old: str, new: str, every: bool) -> str:
    if not path.is_file():
        return f"[Atlas: {path} is not a file.{_lookalikes(path)} To create one, use write_file.]"
    if not old:
        return "[Atlas: old_string is empty. To create or replace a whole file, use write_file.]"
    if old == new:
        return "[Atlas: old_string and new_string are the same, so there is nothing to change.]"
    try:
        content = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        return f"[Atlas: {path} is not UTF-8 text, so edit_file will not change it.]"
    changed = workspace.changed_since_seen(path)
    if "\r\n" in content and "\r\n" not in old:
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    count = content.count(old)
    if count == 0:
        hint = " It changed since you read it." if changed else ""
        return (
            f"[Atlas: old_string was not found in {path}.{hint} Read the file again: the text "
            "must match exactly, whitespace and indentation included.]"
        )
    if count > 1 and not every:
        return (
            f"[Atlas: old_string appears {count} times in {path}. Include more surrounding lines "
            "to make it unique, or set replace_all to change every one.]"
        )
    updated = content.replace(old, new) if every else content.replace(old, new, 1)
    _replace(path, updated)
    workspace.remember(path)
    said = f"Edited {path}: {count if every else 1} replacement(s)."
    if changed:
        said += (
            " The file had changed since you last read it; read it again before edits that "
            "depend on the rest of it."
        )
    return f"{said}\n{_diff(content, updated)}"


def _diff(before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(), "before", "after", n=2, lineterm=""
    )
    text = "\n".join(list(lines)[2:])
    if len(text) > DIFF_CHARS:
        text = text[:DIFF_CHARS] + "\n[Atlas: the diff goes on; read the file to see the rest.]"
    return text


def _whole(value: object, default: int, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    number = max(minimum, int(value))
    return number if maximum is None else min(maximum, number)
