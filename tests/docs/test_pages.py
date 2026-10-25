"""The documentation Atlas ships: every page reads, sits in a group, and links only to pages that
are there."""

import re
from pathlib import Path

import pytest

from atlas.docs import GROUPS, PAGES_FOLDER, DocsError, find, read_pages, render_all, render_page

_LINK = re.compile(r"\]\(/docs(/[^)#\s]*)?(?:#[^)\s]*)?\)")


def test_every_page_ships_and_reads() -> None:
    pages = read_pages()

    assert {page.name for page in pages} >= {
        "index",
        "install",
        "guides/coding-agents",
        "guides/machines",
        "guides/memory",
        "guides/background-review",
        "guides/bots",
        "guides/approval-gate",
        "reference/commands",
        "reference/settings",
        "reference/tools",
        "extending/plugins",
    }
    for page in pages:
        assert page.title
        assert page.description.endswith("."), page.name
        assert page.group in {folder for folder, _ in GROUPS}
        assert find(pages, page.name) == page


def test_every_link_between_pages_lands_on_a_page() -> None:
    pages = read_pages()
    names = {page.name for page in pages}

    for page in pages:
        for path in _LINK.findall(page.body):
            target = path.strip("/") or "index"
            assert target in names, f"{page.name} links to /docs{path}, which is no page"


def test_every_page_is_written_for_a_terminal_without_a_site_link_left() -> None:
    pages = read_pages()

    for page in pages:
        assert "](/docs" not in render_page(page), page.name
    everything = render_all(pages)
    assert everything.startswith("# What Atlas is\n")
    assert all(f"\n# {page.title}\n" in f"\n{everything}" for page in pages)


def test_a_folder_without_pages_says_the_installation_lacks_them(tmp_path: Path) -> None:
    with pytest.raises(DocsError, match="this installation lacks it"):
        read_pages(tmp_path)


def test_a_page_that_cannot_be_read_says_which(tmp_path: Path) -> None:
    (tmp_path / "install.md").write_bytes(b"---\ntitle: \xff\n---\n")

    with pytest.raises(DocsError, match=r"install\.md cannot be read"):
        read_pages(tmp_path)


def test_the_pages_live_in_the_package() -> None:
    assert Path(__file__).resolve().parents[2] / "src" / "atlas" / "docs" / "pages" == PAGES_FOLDER
