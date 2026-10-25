"""The documentation for whoever uses Atlas: its pages, which the site shows and `atlas docs`
reads in the installed version (docs/ARCHITECTURE.md, The documentation)."""

from atlas.docs.errors import DocsError
from atlas.docs.model import (
    COMMAND,
    GROUPS,
    Hit,
    Page,
    find,
    for_terminal,
    ordered,
    parse_page,
    render_all,
    render_index,
    render_page,
    render_search,
    search,
    sections,
)
from atlas.docs.store import PAGES_FOLDER, read_pages

__all__ = [
    "COMMAND",
    "GROUPS",
    "PAGES_FOLDER",
    "DocsError",
    "Hit",
    "Page",
    "find",
    "for_terminal",
    "ordered",
    "parse_page",
    "read_pages",
    "render_all",
    "render_index",
    "render_page",
    "render_search",
    "search",
    "sections",
]
