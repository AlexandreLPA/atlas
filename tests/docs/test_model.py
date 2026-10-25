"""The documentation's pure rules: a page's header, the order, finding a page, the search, and the
pages written for a terminal."""

import pytest

from atlas.docs import (
    DocsError,
    Page,
    find,
    for_terminal,
    ordered,
    parse_page,
    render_index,
    render_page,
    render_search,
    search,
    sections,
)


def page(name: str, title: str, body: str = "", order: int | None = None) -> Page:
    return Page(name=name, title=title, description=f"About {title}.", order=order, body=body)


def test_a_header_gives_the_title_the_description_and_the_order() -> None:
    text = (
        "---\n"
        'title: "Bots"\n'
        "description: 'Make a bot and talk to it: what it holds.'\n"
        "sidebar:\n"
        "  order: 5\n"
        "---\n"
        "\n"
        "The body.\n"
    )

    parsed = parse_page("guides/bots", text)

    assert parsed == Page(
        name="guides/bots",
        title="Bots",
        description="Make a bot and talk to it: what it holds.",
        order=5,
        body="The body.\n",
    )
    assert parsed.group == "guides"


def test_a_header_reads_quotes_escaped_inside_and_a_leading_byte_order_mark() -> None:
    text = '﻿---\ntitle: Install\ndescription: "Say \\"hello\\"."\n---\nBody\n'

    parsed = parse_page("install", text)

    assert (parsed.title, parsed.description, parsed.order, parsed.body) == (
        "Install",
        'Say "hello".',
        None,
        "Body\n",
    )


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("No header at all.\n", "no header"),
        ("---\ntitle: Only a title\n---\n", "no description"),
        ("---\ndescription: Only a description\n---\n", "no title"),
        ("---\ntitle: A\ndescription: Make a bot: bare\n---\n", "needs quotes"),
        ("---\ntitle: `code` first\ndescription: Fine\n---\n", "needs quotes"),
    ],
)
def test_a_header_lacking_what_every_page_has_is_refused(text: str, said: str) -> None:
    with pytest.raises(DocsError, match=said):
        parse_page("install", text)


def test_a_page_outside_the_groups_is_refused() -> None:
    with pytest.raises(DocsError, match="not one of guides/"):
        parse_page("elsewhere/page", "---\ntitle: A\ndescription: B\n---\n")


def test_the_order_is_by_group_then_by_order_then_by_name() -> None:
    pages = [
        page("reference/tools", "Tools", order=3),
        page("guides/memory", "Memory", order=3),
        page("guides/coding-agents", "Coding agents", order=1),
        page("extending/plugins", "Plugins", order=1),
        page("install", "Install", order=2),
        page("index", "What Atlas is", order=1),
        page("guides/zz", "No order"),
    ]

    assert [p.name for p in ordered(pages)] == [
        "index",
        "install",
        "guides/coding-agents",
        "guides/memory",
        "guides/zz",
        "reference/tools",
        "extending/plugins",
    ]


PAGES = [
    page("index", "What Atlas is", order=1),
    page("install", "Install", order=2),
    page("guides/memory", "Memory", order=3),
    page("guides/bots", "Bots", order=5),
    page("reference/tools", "Tools", order=3),
]


@pytest.mark.parametrize(
    ("query", "name"),
    [
        ("guides/memory", "guides/memory"),
        ("memory", "guides/memory"),
        ("/docs/guides/memory/", "guides/memory"),
        ("guides/memory.md", "guides/memory"),
        ("Memory", "guides/memory"),
        ("tools", "reference/tools"),
        ("", "index"),
        ("docs", "index"),
    ],
)
def test_a_page_is_found_by_its_name_the_end_of_it_or_its_title(query: str, name: str) -> None:
    assert find(PAGES, query).name == name


def test_a_page_that_is_not_there_names_the_ones_that_are() -> None:
    with pytest.raises(DocsError) as caught:
        find(PAGES, "nowhere")

    said = str(caught.value)
    assert "There is no page 'nowhere'" in said
    assert "index, install, guides/memory, guides/bots, reference/tools" in said


def test_a_name_two_pages_end_with_is_not_guessed() -> None:
    pages = [page("guides/setup", "Setup A"), page("reference/setup", "Setup B")]

    with pytest.raises(DocsError):
        find(pages, "setup")


SEARCHED = [
    page(
        "guides/coding-agents",
        "Coding agents",
        "Intro about accounts.\n\n"
        "## Several accounts\n\n"
        "To add one, choose **Add an account** in the Executors tab.\n\n"
        "## Routes\n\n"
        "```bash\n# an account inside code is not a heading\n```\n"
        "A route of accounts.\n",
        order=1,
    ),
    page("guides/memory", "Memory", "## Limits\n\nThe limits are fixed.\n", order=3),
]


def test_the_sections_split_at_headings_and_not_at_comments_in_code() -> None:
    assert [heading for heading, _ in sections(SEARCHED[0])] == [
        "Coding agents",
        "Several accounts",
        "Routes",
    ]


def test_a_search_finds_the_sections_that_hold_every_word_the_closest_first() -> None:
    hits = search(SEARCHED, ["add", "account"])

    assert [(hit.page.name, hit.heading) for hit in hits] == [
        ("guides/coding-agents", "Several accounts")
    ]
    assert hits[0].line == "To add one, choose Add an account in the Executors tab."


def test_a_word_in_the_heading_counts_more_than_one_in_the_text() -> None:
    hits = search(SEARCHED, ["accounts"])

    assert hits[0].heading == "Several accounts"


def test_with_no_section_holding_every_word_the_search_gives_those_holding_the_most() -> None:
    hits = search(SEARCHED, ["second", "account"])

    assert hits
    assert all(hit.matched == 1 for hit in hits)
    assert render_search(hits, ["second", "account"]).startswith(
        "No section mentions every word of 'second account'; these mention the most of them:"
    )


def test_a_search_with_nothing_found_says_what_to_try() -> None:
    assert search(SEARCHED, ["quantum"]) == []
    assert "Try other words, or `atlas docs`" in render_search([], ["quantum"])


def test_a_search_needs_a_word() -> None:
    with pytest.raises(DocsError, match="at least one word"):
        search(SEARCHED, ["  "])


def test_a_search_keeps_to_its_limit() -> None:
    many = [page(f"guides/p{k}", f"Page {k}", "account\n") for k in range(20)]

    assert len(search(many, ["account"], limit=5)) == 5


def test_links_between_pages_become_the_command_that_prints_the_page() -> None:
    text = (
        "See [Install](/docs/install/), [Bots](/docs/guides/bots/#memory) and [the start](/docs/)."
    )

    assert for_terminal(text) == (
        "See Install (`atlas docs install`), Bots (`atlas docs guides/bots`) and "
        "the start (`atlas docs index`)."
    )


def test_a_page_for_a_terminal_starts_with_its_title_and_description() -> None:
    shown = render_page(page("install", "Install", "See [Bots](/docs/guides/bots/).\n"))

    assert shown == "# Install\n\n> About Install.\n\nSee Bots (`atlas docs guides/bots`).\n"


def test_the_index_lists_the_pages_by_group_with_how_to_read_them() -> None:
    index = render_index(PAGES)

    assert "`atlas docs PAGE`" in index
    assert index.index("## Start here") < index.index("## Guides") < index.index("## Reference")
    assert "- `guides/memory`: **Memory**. About Memory." in index
    assert "## Extending Atlas" not in index
