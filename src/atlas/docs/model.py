"""The documentation's pages, and what `atlas docs` does with them: read a page's header, put the
pages in order, find one by name, search their sections, and write them for a terminal.

The pages are Markdown with a header between `---` lines, the site's own format:

- `title` and `description`, one line each, both required;
- an optional `sidebar` block whose `order` places the page within its group.

A page's name is its path under the pages' folder, without `.md`: `install`, `guides/memory`.
Links between pages are the site's paths (`/docs/install/`); written for a terminal, they become the
`atlas docs` command that prints the page.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from atlas.docs.errors import DocsError

#: The groups of pages, in the order the site's sidebar and `atlas docs` list them: the pages at the
#: top first, then one folder each. A page in any other folder is refused, so a new group is added
#: here and in the site's sidebar (`site/site.config.mjs`) together.
GROUPS: tuple[tuple[str, str], ...] = (
    ("", "Start here"),
    ("guides", "Guides"),
    ("reference", "Reference"),
    ("extending", "Extending Atlas"),
)
#: The command a reader runs to read a page, as the pages' links are written for a terminal.
COMMAND = "atlas docs"
#: How many sections a search returns, the best first.
SEARCH_LIMIT = 12
_UNORDERED = 10_000

_HEADER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
_DOC_LINK = re.compile(r"\[([^\]\n]+)\]\(/docs(/[^)#\s]*)?(#[^)\s]*)?\)")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
#: What a bare YAML value may not hold: `: ` or ` #` inside, or a reserved character first.
_BARE_TROUBLE = re.compile(r": | #|^(?:[`@&*!|>%#\[\]{},?]|- )")


@dataclass(frozen=True, slots=True)
class Page:
    """One page: its name, what its header says, and its Markdown body."""

    name: str
    title: str
    description: str
    #: Where the page sits within its group; `None` puts it after the ordered ones.
    order: int | None
    body: str

    @property
    def group(self) -> str:
        """The folder the page is in, `""` for the pages at the top."""
        return self.name.rpartition("/")[0]


@dataclass(frozen=True, slots=True)
class Hit:
    """One section a search found."""

    page: Page
    #: The section's heading; the page's title for the text before its first heading.
    heading: str
    #: The first line of the section that holds a word searched for, shortened.
    line: str
    #: How many of the words searched for the section holds.
    matched: int
    score: int


def parse_page(name: str, text: str) -> Page:
    """Read a page's header and body. Refuses a page whose header lacks its title or description,
    or which sits outside the documentation's groups."""
    text = text.removeprefix("﻿")
    match = _HEADER.match(text)
    if match is None:
        raise DocsError(f"The page {name} has no header between `---` lines.")
    fields = _header_fields(match.group(1))
    title = fields.get("title", "")
    description = fields.get("description", "")
    missing = [key for key, value in (("title", title), ("description", description)) if not value]
    if missing:
        raise DocsError(f"The page {name}'s header has no {' and no '.join(missing)}.")
    group = name.rpartition("/")[0]
    if group not in {folder for folder, _ in GROUPS}:
        known = ", ".join(f"{folder}/" for folder, _ in GROUPS if folder)
        raise DocsError(f"The page {name} is in {group}/, which is not one of {known}.")
    order = fields.get("sidebar.order", "")
    return Page(
        name=name,
        title=title,
        description=description,
        order=int(order) if re.fullmatch(r"-?\d+", order) else None,
        body=text[match.end() :].strip() + "\n",
    )


def _header_fields(header: str) -> dict[str, str]:
    """`key: value` lines, and a block's indented lines as `block.key`. A bare value YAML would
    refuse, or read otherwise, is refused here too: the site reads the same header as YAML."""
    fields: dict[str, str] = {}
    block = ""
    for line in header.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, colon, value = line.strip().partition(":")
        if not colon:
            continue
        value = value.strip()
        if _BARE_TROUBLE.search(value) and value[:1] not in "\"'":
            raise DocsError(
                f"The header's {key.strip()} needs quotes: {value[:60]!r} holds what YAML reads as "
                "syntax (`: `, ` #`, or a leading reserved character)."
            )
        value = _unquote(value)
        if line[0] in " \t":
            if block:
                fields[f"{block}.{key.strip()}"] = value
            continue
        block = "" if value else key.strip()
        if value:
            fields[key.strip()] = value
    return fields


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        inner = value[1:-1]
        return inner.replace('\\"', '"') if value[0] == '"' else inner.replace("''", "'")
    return value


def ordered(pages: Sequence[Page]) -> list[Page]:
    """The pages in the documentation's order: by group, then by `order`, then by name."""
    rank = {folder: position for position, (folder, _) in enumerate(GROUPS)}
    return sorted(
        pages,
        key=lambda page: (
            rank.get(page.group, len(GROUPS)),
            _UNORDERED if page.order is None else page.order,
            page.name,
        ),
    )


def find(pages: Sequence[Page], query: str) -> Page:
    """The page a reader named: by its name (`guides/memory`), by the last part of it when only
    one page has it (`memory`), or by its title (`Several machines`)."""
    wanted = query.strip().strip("/").lower().removesuffix(".md").removeprefix("docs/")
    if wanted in ("", "docs"):
        wanted = "index"
    by_name = {page.name.lower(): page for page in pages}
    if wanted in by_name:
        return by_name[wanted]
    for matches in (
        [page for page in pages if page.name.lower().rpartition("/")[2] == wanted],
        [page for page in pages if page.title.lower() == wanted],
    ):
        if len(matches) == 1:
            return matches[0]
    names = ", ".join(page.name for page in ordered(pages))
    raise DocsError(
        f"There is no page {query.strip()!r}. The pages are: {names}. `{COMMAND}` lists them "
        f"with what each covers, and `{COMMAND} --search WORDS` finds a subject."
    )


def search(pages: Sequence[Page], words: Sequence[str], limit: int = SEARCH_LIMIT) -> list[Hit]:
    """The sections that mention every word, case aside, the ones that mention them most first; a
    word in a section's heading counts more than one in its text. When no section mentions every
    word, the sections that mention the most of them: a reader asks in their own words, and one of
    them may not be the documentation's."""
    terms = list(dict.fromkeys(word.lower() for word in " ".join(words).split()))
    if not terms:
        raise DocsError("Give at least one word to search for.")
    hits: list[Hit] = []
    for page in ordered(pages):
        for heading, text in sections(page):
            heading_text = heading.lower()
            body = text.lower()
            matched = sum(1 for term in terms if term in heading_text or term in body)
            if not matched:
                continue
            score = sum(body.count(term) + 5 * heading_text.count(term) for term in terms)
            hits.append(Hit(page, heading, _first_line(text, terms), matched, score))
    best = max((hit.matched for hit in hits), default=0)
    hits = [hit for hit in hits if hit.matched == best]
    hits.sort(key=lambda hit: -hit.score)
    return hits[:limit]


def sections(page: Page) -> list[tuple[str, str]]:
    """A page's sections: each heading with the text under it, and the page's title with the text
    before its first heading. A `#` line inside a code block is code, not a heading."""
    found: list[tuple[str, list[str]]] = [(page.title, [])]
    in_code = False
    for line in page.body.splitlines():
        if _FENCE.match(line):
            in_code = not in_code
        heading = None if in_code else _HEADING.match(line)
        if heading:
            found.append((_plain(heading.group(2)), []))
        else:
            found[-1][1].append(line)
    return [(heading, "\n".join(lines).strip()) for heading, lines in found if heading]


def _plain(text: str) -> str:
    return text.replace("`", "").replace("**", "").strip()


def _first_line(text: str, terms: Sequence[str]) -> str:
    """The first line holding a word, without its Markdown marks, cut to 160 characters around
    the word."""
    for raw in text.splitlines():
        line = raw.replace("**", "").strip().lstrip("-*>|# ").strip()
        lowered = line.lower()
        found = [lowered.find(term) for term in terms if term in lowered]
        if not found:
            continue
        start = max(0, min(found) - 60) if len(line) > 160 else 0
        piece = line[start : start + 160]
        return ("…" if start else "") + piece + ("…" if start + 160 < len(line) else "")
    return ""


def for_terminal(markdown: str) -> str:
    """The pages' links to each other written as the command that prints the linked page:
    `[Install](/docs/install/)` reads "Install (`atlas docs install`)"."""

    def command(match: re.Match[str]) -> str:
        path = (match.group(2) or "").strip("/")
        return f"{match.group(1)} (`{COMMAND} {path or 'index'}`)"

    return _DOC_LINK.sub(command, markdown)


def render_page(page: Page) -> str:
    """One page, as Markdown for a terminal."""
    return f"# {page.title}\n\n> {page.description}\n\n{for_terminal(page.body)}"


def render_all(pages: Sequence[Page]) -> str:
    """Every page, in the documentation's order."""
    return "\n---\n\n".join(render_page(page) for page in ordered(pages))


def render_index(pages: Sequence[Page]) -> str:
    """The list of pages by group, each with its title and what it covers."""
    lines = [
        "# Atlas's documentation",
        "",
        "The documentation of the Atlas installed here, as this version ships it. Read a page with "
        f"`{COMMAND} PAGE`, find a subject with `{COMMAND} --search WORDS`, or read every page "
        f"with `{COMMAND} --all`.",
        "",
    ]
    listed = ordered(pages)
    for folder, label in GROUPS:
        members = [page for page in listed if page.group == folder]
        if not members:
            continue
        lines += [f"## {label}", ""]
        lines += [f"- `{page.name}`: **{page.title}**. {page.description}" for page in members]
        lines.append("")
    return "\n".join(lines)


def render_search(hits: Sequence[Hit], words: Sequence[str]) -> str:
    """What a search found, each section with the command that prints its page."""
    asked = " ".join(words).strip()
    if not hits:
        return (
            f"No section of the documentation mentions any word of {asked!r}. Try other words, "
            f"or `{COMMAND}` for the list of pages.\n"
        )
    terms = len(dict.fromkeys(word.lower() for word in asked.split()))
    if hits[0].matched < terms:
        lead = f"No section mentions every word of {asked!r}; these mention the most of them:"
    else:
        lead = f"Sections that mention {asked!r}, the closest first:"
    lines = [lead, ""]
    for hit in hits:
        where = (
            hit.page.title if hit.heading == hit.page.title else f"{hit.page.title}, {hit.heading}"
        )
        lines.append(f"- `{COMMAND} {hit.page.name}`: {where}")
        if hit.line:
            lines.append(f"  {hit.line}")
    lines.append("")
    return "\n".join(lines)
