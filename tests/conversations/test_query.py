"""The cleaning of a search query: what the user or the model types becomes a query FTS5 accepts.

Every cleaned query is run against a real FTS5 table, so "accepted" means SQLite accepted it.
"""

import sqlite3
from datetime import UTC, datetime

import pytest

from atlas.conversations import fts_query, query_terms, time_bound, title_from

NASTY = [
    "it's",
    "gateway/run.py",
    "user@host",
    "a,b",
    "50%",
    "fim.",
    "-flag",
    "TODO: fix",
    '"unbalanced',
    "(parens)",
    "x AND",
    "OR y",
    "a AND OR b",
    "***",
    "chat-send*",
    "C:\\Users\\name",
    "{braces} [brackets] <angle>",
    "a ^ b ~ c | d & e = f ? g ! h $ i # j",
    "`backticks`",
    "emoji 🎉 ok",
]


def _accepts(query: str) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute(
            "CREATE VIRTUAL TABLE t USING fts5(text, tokenize='unicode61 remove_diacritics 2')"
        )
        connection.execute("INSERT INTO t (text) VALUES ('memória do gateway run py')")
        connection.execute("SELECT * FROM t WHERE t MATCH ?", (query,)).fetchall()
    finally:
        connection.close()


@pytest.mark.parametrize("text", NASTY)
def test_whatever_is_typed_becomes_a_query_fts5_accepts_or_none(text: str) -> None:
    query = fts_query(text)

    if query is not None:
        _accepts(query)


def test_plain_words_stay_and_all_of_them_are_required() -> None:
    assert fts_query("limite de gasto") == "limite de gasto"


def test_balanced_phrases_stay_whole() -> None:
    assert fts_query('the "docker networking" issue') == 'the "docker networking" issue'


def test_joined_words_become_phrases_so_fts5_does_not_split_them() -> None:
    assert fts_query("chat-send P2.2 my_app.config") == '"chat-send" "P2.2" "my_app.config"'


def test_operators_stay_between_words_and_leave_the_edges() -> None:
    assert fts_query("AND alpha OR beta NOT") == "alpha OR beta"
    assert fts_query("alpha AND OR beta") == "alpha AND beta"


def test_lowercase_operators_are_plain_words() -> None:
    assert fts_query("rock and roll") == "rock and roll"


def test_a_trailing_star_stays_a_prefix_search() -> None:
    assert fts_query("deploy* *x") == "deploy* x"


def test_nothing_searchable_gives_none() -> None:
    assert fts_query("  ::  ()  ") is None
    assert fts_query("") is None


def test_query_terms_leave_operators_out_and_keep_phrases() -> None:
    assert query_terms('alpha OR "beta gamma" NOT delta') == ["alpha", '"beta gamma"', "delta"]


def test_a_title_is_the_first_line_cut() -> None:
    assert title_from("\n  Plan the trip  \nmore") == "Plan the trip"
    assert title_from("x" * 200) == "x" * 79 + "…"


NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "bound"),
    [
        ("7d", datetime(2026, 10, 2, 12, 0, tzinfo=UTC)),
        ("24h", datetime(2026, 10, 8, 12, 0, tzinfo=UTC)),
        ("2W", datetime(2026, 9, 25, 12, 0, tzinfo=UTC)),
        ("2026-06-01", datetime(2026, 6, 1, tzinfo=UTC)),  # a date alone is midnight UTC
        ("2026-06-01T09:30:00-03:00", datetime(2026, 6, 1, 12, 30, tzinfo=UTC)),
        ("last week", None),
    ],
)
def test_time_bounds_are_read_as_written(text: str, bound: datetime | None) -> None:
    assert time_bound(text, NOW) == bound
