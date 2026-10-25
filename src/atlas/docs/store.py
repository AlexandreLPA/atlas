"""The documentation's pages, read from the installed package: a checkout, the container's image
and the packaged app all carry them, at the version each one runs."""

from pathlib import Path

from atlas.docs.errors import DocsError
from atlas.docs.model import Page, parse_page

#: Where the pages are, inside the package. The site builds its documentation from this folder too.
PAGES_FOLDER = Path(__file__).parent / "pages"


def read_pages(folder: Path = PAGES_FOLDER) -> list[Page]:
    """Every page under `folder`, named after its path there."""
    pages: list[Page] = []
    for path in sorted(folder.rglob("*.md")):
        name = path.relative_to(folder).with_suffix("").as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise DocsError(f"{path} cannot be read: {error}.") from error
        pages.append(parse_page(name, text))
    if not pages:
        raise DocsError(
            f"No documentation was found in {folder}: this installation lacks it, and "
            "`atlas update` brings it back."
        )
    return pages
