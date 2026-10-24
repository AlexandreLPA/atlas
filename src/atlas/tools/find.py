"""find_files: files by name or by what is inside them, through ripgrep, the same on every system.

Decided on 09/10/2026. It runs on ripgrep, which is fast and skips what `.gitignore` ignores. It
is one tool for names and content, so it fits in the always-on set. ripgrep ships with Atlas (the
container image, the desktop app, the installer) and there is no second engine: a fallback to
`find` and `grep` would fail on Windows, which lacks them.

**Inside documents too** (decided on 09/10/2026, with the memory, in place of a library): ripgrep
reads files as they are on disk, and a PDF or a Word file keeps its text packed, so ripgrep alone
found a word in none of 31 real documents the converter read. Each document is converted once and
its text kept (`atlas.tools.texts`), and ripgrep searches the texts. Their matches come after the
files', under the documents' own paths, numbered as read_file numbers the document's lines. Texts
not ready within the search's time are left for the next search, which says so.

Two guards against the search that never ends:

- **Folders that are never the answer are skipped by name** (`node_modules`, `.git`, `dist`…, and
  Windows's `AppData`), unless the search starts inside one. On 05/08/2026 V3's searches of the
  Desktop took 263.7 seconds walking `node_modules`; with these exclusions the same search took
  17.3.
- **A search stops at 60 seconds** (V3's budget, three times its worst realistic case) with what
  it found and a note to narrow the path.

Results come in path order, so the same search gives the same answer and the repeat guard can
tell a loop from progress.
"""

import asyncio
import contextlib
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from atlas.agent import Tool
from atlas.tools.files import Workspace
from atlas.tools.texts import DOCUMENT_GLOBS, DocumentTexts

#: Folders skipped by name, Windows's AppData among them.
SKIPPED_FOLDERS = (
    ".git", ".hg", ".svn",
    "node_modules", "venv", ".venv", "site-packages", "dist-packages", "vendor", "third_party",
    "build", "dist", "target", "out", "coverage",
    ".next", ".turbo", ".parcel-cache", ".nuxt", ".svelte-kit",
    ".archive",
    "__pycache__", ".cache", ".Trash", ".tox", ".nox", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".npm", ".yarn", ".pnpm-store", ".gradle", ".m2", ".nuget",
    "backups", "backup", ".backups",
    "AppData",
)  # fmt: skip
#: Seconds before a search is stopped.
SEARCH_SECONDS = 60.0
#: Results shown when the model does not say, and the most it may ask for.
DEFAULT_RESULTS = 50
MAX_RESULTS = 500
#: The most output read from ripgrep; past it the search is stopped.
OUTPUT_BYTES = 5_000_000
#: A matching line longer than this is shown cut.
LINE_COLUMNS = 300
#: Characters of document texts named on one ripgrep command line; Windows takes 32,767.
_COMMAND_CHARS = 24_000

_INSTALL = (
    "ripgrep (rg) is not installed, and find_files runs on it. Install it: "
    "`winget install BurntSushi.ripgrep.MSVC` on Windows, `brew install ripgrep` on macOS, "
    "`apt install ripgrep` (or your distribution's package) on Linux."
)


@dataclass(frozen=True, slots=True)
class _Search:
    root: Path
    target: str | None
    pattern: str
    mode: str
    glob: str | None
    context: int
    ignore_case: bool


@dataclass
class _Found:
    lines: list[str] = field(default_factory=list[str])
    #: Why ripgrep was stopped before it finished, if it was.
    stopped: str | None = None
    #: What ripgrep said when it refused the search.
    refused: str | None = None


def find_files(
    workspace: Workspace,
    ripgrep: str | None = None,
    *,
    seconds: float = SEARCH_SECONDS,
    texts: DocumentTexts | None = None,
) -> Tool:
    """The find_files tool. `ripgrep` is the program's path; by default it is found on the PATH.
    A search stops after `seconds`. With `texts`, content searches read inside documents."""

    async def run(arguments: dict[str, Any]) -> str:
        pattern = arguments.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            return "[Atlas: find_files needs a pattern.]"
        try:
            where = workspace.resolve(arguments.get("path") or str(workspace.root))
        except ValueError as error:
            return f"[Atlas: {error}]"
        if not where.exists():
            return f"[Atlas: {where} does not exist.]"
        program = ripgrep or shutil.which("rg")
        if program is None:
            return f"[Atlas: {_INSTALL}]"
        names = arguments.get("target") == "files"
        mode = arguments.get("output_mode")
        glob = arguments.get("file_glob")
        search = _Search(
            root=where if where.is_dir() else where.parent,
            target=None if where.is_dir() else where.name,
            pattern=pattern,
            mode="files" if names else (mode if mode in ("files_only", "count") else "content"),
            glob=glob if isinstance(glob, str) and glob and not names else None,
            context=_number(arguments.get("context"), 0, 0, 10),
            ignore_case=arguments.get("ignore_case") is True,
        )
        limit = _number(arguments.get("limit"), DEFAULT_RESULTS, 1, MAX_RESULTS)
        offset = _number(arguments.get("offset"), 0, 0, None)
        deadline = time.monotonic() + seconds
        reading = texts is not None and search.mode != "files"
        found = await _ripgrep(
            program, _arguments(search, documents_apart=reading), search.root, deadline
        )
        if found.refused is not None:
            return f"[Atlas: ripgrep refused the search: {found.refused}]"
        notes: list[str] = []
        if texts is not None and reading and found.stopped is None:
            notes = await _in_documents(program, search, texts, found, deadline)
        return _shown(search, found, limit, offset, seconds, notes)

    return Tool(
        name="find_files",
        description=(
            "Searches files with ripgrep. `target: content` (the default) finds the lines that "
            "match the regex `pattern`, inside PDF and Office documents too; `target: files` "
            "lists the files whose path matches the glob `pattern` (`*.md`, `src/**/*.py`). Skips "
            "hidden and .gitignore'd files, and folders like node_modules unless `path` is in one."
        ),
        parameters={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "A regex to find in files, or a glob of file names.",
                },
                "target": {"type": "string", "enum": ["content", "files"]},
                "path": {
                    "type": "string",
                    "description": "Folder or file to search. Defaults to the working folder.",
                },
                "file_glob": {
                    "type": "string",
                    "description": "Search only files whose name matches this glob (content).",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_only", "count"],
                    "description": "Lines (default), file names, or counts per file (content).",
                },
                "context": {"type": "integer", "description": "Lines around each match, to 10."},
                "ignore_case": {"type": "boolean"},
                "limit": {"type": "integer", "description": "Results to show. Defaults to 50."},
                "offset": {"type": "integer", "description": "Results to skip, to page on."},
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
        run=run,
        read_only=True,
    )


def _skips() -> list[str]:
    arguments: list[str] = []
    for folder in SKIPPED_FOLDERS:
        arguments += ["--glob", f"!{folder}"]
    return arguments


def _matching(search: _Search) -> list[str]:
    """How ripgrep should match and show lines, whatever files it reads."""
    arguments = ["--line-number", "--with-filename", "--no-heading"]
    arguments += [f"--max-columns={LINE_COLUMNS}", "--max-columns-preview"]
    if search.mode == "files_only":
        arguments.append("--files-with-matches")
    elif search.mode == "count":
        arguments.append("--count")
    elif search.context:
        arguments.append(f"--context={search.context}")
    if search.ignore_case:
        arguments.append("--ignore-case")
    return [*arguments, "--regexp", search.pattern]


def _arguments(search: _Search, *, documents_apart: bool = False) -> list[str]:
    """The search over the files as they are. `documents_apart` leaves documents out, because
    their kept texts are searched instead: a PDF whose text is not packed would match twice."""
    arguments = ["--color=never", "--sort=path", "--no-messages", *_skips()]
    if search.mode == "files":
        flag = "--iglob" if search.ignore_case else "--glob"
        arguments += ["--files", flag, search.pattern]
    else:
        if search.glob:
            arguments += ["--glob", search.glob]
        if documents_apart:
            for glob in DOCUMENT_GLOBS:
                arguments += ["--iglob", f"!{glob}"]
        arguments += _matching(search)
    if search.target is not None:
        arguments.append(search.target)
    return arguments


async def _in_documents(
    program: str, search: _Search, texts: DocumentTexts, found: _Found, deadline: float
) -> list[str]:
    """Search the documents under the search's root through their kept texts; their lines join
    `found`. Returns the notes for the model."""
    listing = ["--color=never", "--sort=path", "--no-messages", "--files", *_skips()]
    for glob in DOCUMENT_GLOBS:
        listing += ["--iglob", glob]
    if search.target is not None:
        listing.append(search.target)
    listed = await _ripgrep(program, listing, search.root, deadline)
    documents = [search.root / line for line in listed.lines]
    if search.glob:
        documents = [d for d in documents if PurePath(d.name).match(search.glob)]
    if not documents:
        return []
    prepared = await texts.prepare(documents, deadline)
    names = sorted(prepared.texts)
    for batch in _batches(names):
        result = await _ripgrep(
            program, ["--color=never", "--no-messages", *_matching(search), *batch],
            texts.folder, deadline,
        )  # fmt: skip
        for line in sorted(result.lines, key=lambda text: _order(text, prepared.texts, search)):
            name = line[: len(batch[0])]
            document = prepared.texts.get(name.replace("\\", "/"))
            if document is not None:
                found.lines.append(_shown_path(document, search) + line[len(name) :])
        if result.stopped is not None:
            found.stopped = result.stopped
            break
    if prepared.waiting:
        return [
            f"[Atlas: {prepared.waiting} document(s) here were not read yet: reading takes about "
            "0.1 s each, and this search ran out of time. Search again to include them.]"
        ]
    return []


def _batches(names: list[str]) -> list[list[str]]:
    batches: list[list[str]] = []
    size = 0
    for name in names:
        if not batches or size + len(name) + 1 > _COMMAND_CHARS:
            batches.append([])
            size = 0
        batches[-1].append(name)
        size += len(name) + 1
    return batches


def _order(line: str, texts: dict[str, Path], search: _Search) -> tuple[str, int]:
    """Documents in path order, and each one's lines in their order."""
    name = next(iter(texts))  # every name has the same length
    document = texts.get(line[: len(name)].replace("\\", "/"))
    rest = line[len(name) + 1 :]
    digits = "".join(c for c in rest.split(":", 1)[0].split("-", 1)[0] if c.isdigit())
    shown = "" if document is None else _shown_path(document, search)
    return shown, int(digits) if digits else 0


def _shown_path(document: Path, search: _Search) -> str:
    try:
        return str(document.relative_to(search.root))
    except ValueError:
        return str(document)


async def _ripgrep(program: str, arguments: list[str], cwd: Path, deadline: float) -> _Found:
    process = await asyncio.create_subprocess_exec(
        program,
        *arguments,
        cwd=cwd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    stdout, stderr = process.stdout, process.stderr
    if stdout is None or stderr is None:  # pragma: no cover - both were asked for with PIPE
        raise RuntimeError("ripgrep started without its output pipes.")
    chunks: list[bytes] = []
    size = 0
    found = _Found()

    async def collect() -> None:
        nonlocal size
        while chunk := await stdout.read(65_536):
            chunks.append(chunk)
            size += len(chunk)
            if size >= OUTPUT_BYTES:
                found.stopped = "it found more than Atlas reads at once"
                return

    seconds = max(0.0, deadline - time.monotonic())
    try:
        await asyncio.wait_for(collect(), seconds)
    except TimeoutError:
        found.stopped = "it ran out of time"
    if found.stopped is not None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    errors = (await stderr.read()).decode("utf-8", "replace").strip()
    code = await process.wait()
    found.lines = b"".join(chunks).decode("utf-8", "replace").splitlines()
    # Exit code 2 with no message is "no file was searched" (a glob that left nothing, its
    # message muted by --no-messages); a real refusal, such as a broken regex, says why.
    if found.stopped is None and code == 2 and not found.lines and errors:
        found.refused = errors
    return found


def _shown(
    search: _Search, found: _Found, limit: int, offset: int, seconds: float, notes: list[str]
) -> str:
    lines = found.lines
    stopped = found.stopped
    if stopped == "it ran out of time":
        stopped = f"it ran past {seconds:g} seconds"
    if not lines and stopped is not None:
        return (
            f"[Atlas: the search was stopped because {stopped}, before it found anything. Point "
            "`path` at a narrower folder.]"
        )
    if not lines:
        what = "No file name matches" if search.mode == "files" else "Nothing matches"
        return "\n".join([f"{what} {search.pattern!r} in {search.root}.", *notes])
    shown = lines[offset : offset + limit]
    parts = [f"In {search.root}:", *shown]
    if offset + limit < len(lines):
        parts.append(
            f"[Atlas: {len(lines):,} results; showing {offset + 1:,} to {offset + len(shown):,}. "
            f"Continue with offset={offset + limit}, or narrow the search.]"
        )
    elif not shown:
        parts.append(f"[Atlas: there are only {len(lines):,} results.]")
    if stopped is not None:
        parts.append(
            f"[Atlas: the search was stopped because {stopped}, so these are partial. Point "
            "`path` at a narrower folder.]"
        )
    return "\n".join([*parts, *notes])


def _number(value: object, default: int, minimum: int, maximum: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    number = max(minimum, int(value))
    return number if maximum is None else min(maximum, number)
