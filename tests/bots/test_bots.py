"""Bots on real folders: the definition as it is read and written, the names a bot may take, and
the home's `bots/` made, listed and removed."""

import logging
from pathlib import Path

import pytest

from atlas.bots import (
    BOT_FILE,
    DESCRIPTION_CHARS,
    TITLE_CHARS,
    Bot,
    BotError,
    Bots,
    attributed,
    name_problem,
    parse_bot,
    render_bot,
    resolve_target,
    roster_section,
)

CLAUDE_CODE_AGENT = """---
name: finance
title: Finance
description: Tracks the month's bills and answers what was paid.
model: gpt-6-luna
tools: read_file, find_files,  web_search
mcpServers:
  - bank
skills: []
color: green
---

# Mission

Keep the bills paid on time.
"""


def _definition(root: Path, name: str, text: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / BOT_FILE).write_text(text, encoding="utf-8")
    return folder


# -- the definition ------------------------------------------------------------------------------


def test_a_definition_in_claude_codes_agent_format_is_read(tmp_path: Path) -> None:
    folder = _definition(tmp_path, "finance", CLAUDE_CODE_AGENT)

    bot = parse_bot((folder / BOT_FILE).read_text(encoding="utf-8"), folder)

    assert bot == Bot(
        name="finance",
        title="Finance",
        description="Tracks the month's bills and answers what was paid.",
        body="# Mission\n\nKeep the bills paid on time.",
        folder=folder,
        model="gpt-6-luna",
        # Claude Code's comma-separated text and a YAML list are the same thing.
        tools=("read_file", "find_files", "web_search"),
        mcp_servers=("bank",),
        # An empty list is "none", which is not "unset".
        skills=(),
    )
    assert bot.memory_file == folder / "MEMORY.md"
    assert bot.skills_folder == folder / "skills"


def test_what_a_definition_leaves_out_is_everything_the_home_has(tmp_path: Path) -> None:
    folder = _definition(tmp_path, "notes", "Take notes.\n")

    bot = parse_bot("Take notes.\n", folder)

    assert (bot.name, bot.title, bot.description, bot.body) == ("notes", "notes", "", "Take notes.")
    assert (bot.model, bot.tools, bot.mcp_servers, bot.skills) == (None, None, None, None)


def test_a_definition_written_is_read_back_the_same(tmp_path: Path) -> None:
    bot = Bot(
        name="produto",
        title="Produto: métricas",
        description="Responde sobre uso e erros do produto.",
        body="Cite a fonte de cada número.",
        folder=tmp_path / "produto",
        model="gemini-3.8-flash",
        tools=("read_file",),
        mcp_servers=(),
        skills=("weekly-report",),
    )

    assert parse_bot(render_bot(bot), bot.folder) == bot


@pytest.mark.parametrize(
    ("text", "said"),
    [
        ("---\nname: other\n---\nbody", "They must match"),
        ("---\nname: [broken\n---\nbody", "not valid YAML"),
        ("---\n- a\n- b\n---\nbody", "not a table"),
        ("---\nname: finance\nbody without the closing line", "not closed"),
        ("---\ntools: 3\n---\nbody", "`tools` must be a list"),
    ],
)
def test_a_broken_definition_is_refused_with_the_reason(
    tmp_path: Path, text: str, said: str
) -> None:
    with pytest.raises(BotError, match=said):
        parse_bot(text, tmp_path / "finance")


@pytest.mark.parametrize("name", ["Finance", "has space", "-dash", "a" * 65, "a.b", ""])
def test_a_name_that_is_not_a_bot_name_is_refused(name: str) -> None:
    assert "is not a bot name" in (name_problem(name) or "")


@pytest.mark.parametrize("name", ["atlas", "all", "everyone", "user"])
def test_the_main_agents_handle_and_the_ones_for_everyone_are_not_a_bots(name: str) -> None:
    assert "is taken" in (name_problem(name) or "")


def test_good_names_pass() -> None:
    assert [name_problem(name) for name in ("finance", "bot_2", "a-b", "9", "a" * 64)] == [None] * 5


# -- the home's bots -----------------------------------------------------------------------------


def test_a_bot_is_made_listed_read_and_removed(tmp_path: Path) -> None:
    bots = Bots(tmp_path / "bots")
    assert bots.list() == []

    made = bots.create(
        "finance", title="  Finance ", description="Bills\nand taxes.", body="Pay on time.\n"
    )
    bots.create("work")

    assert made.folder == tmp_path / "bots" / "finance"
    assert (made.title, made.description, made.body) == (
        "Finance",
        "Bills and taxes.",
        "Pay on time.",
    )
    assert [bot.name for bot in bots.list()] == ["finance", "work"]
    assert bots.get("finance") == made
    assert bots.get("work").title == "work"
    # Nothing of the staging folder is left behind.
    assert sorted(path.name for path in (tmp_path / "bots").iterdir()) == ["finance", "work"]

    (made.folder / "MEMORY.md").write_text("a fact", encoding="utf-8")
    (made.folder / "skills" / "bills").mkdir(parents=True)
    bots.delete("finance")

    assert not made.folder.exists()
    assert [bot.name for bot in bots.list()] == ["work"]


def test_a_taken_name_is_refused_and_the_bot_there_is_untouched(tmp_path: Path) -> None:
    bots = Bots(tmp_path / "bots")
    bots.create("finance", body="The first.")

    with pytest.raises(BotError, match="already a bot 'finance'"):
        bots.create("finance", body="The second.")

    assert bots.get("finance").body == "The first."


def test_a_definition_past_the_rosters_limits_is_refused_before_anything_is_written(
    tmp_path: Path,
) -> None:
    bots = Bots(tmp_path / "bots")

    with pytest.raises(BotError, match=f"the most is {TITLE_CHARS}"):
        bots.create("finance", title="t" * (TITLE_CHARS + 1))
    with pytest.raises(BotError, match=f"the most is {DESCRIPTION_CHARS}"):
        bots.create("finance", description="d" * (DESCRIPTION_CHARS + 1))
    with pytest.raises(BotError, match="is taken"):
        bots.create("atlas")

    assert not (tmp_path / "bots").exists()


def test_a_missing_bot_says_so(tmp_path: Path) -> None:
    bots = Bots(tmp_path / "bots")

    with pytest.raises(BotError, match="There is no bot 'ghost'"):
        bots.get("ghost")
    with pytest.raises(BotError, match="There is no bot 'ghost'"):
        bots.delete("ghost")
    with pytest.raises(BotError, match="is not a bot name"):
        bots.get("../outside")


def test_one_broken_definition_never_hides_the_other_bots(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "bots"
    bots = Bots(root)
    bots.create("work")
    _definition(root, "broken", "---\nname: [oops\n---\n")
    # A folder with no definition, and one a bot's making left half done, are not bots.
    (root / "empty").mkdir()
    _definition(root, "late.creating", "---\nname: late\n---\n")

    with caplog.at_level(logging.WARNING):
        listed = bots.list()

    assert [bot.name for bot in listed] == ["work"]
    assert "not valid YAML" in caplog.text
    with pytest.raises(BotError, match="not valid YAML"):
        bots.get("broken")


def test_a_half_made_bot_left_by_a_crash_does_not_block_the_name(tmp_path: Path) -> None:
    root = tmp_path / "bots"
    _definition(root, "finance.creating", "half")

    Bots(root).create("finance", body="Whole.")

    assert Bots(root).get("finance").body == "Whole."
    assert not (root / "finance.creating").exists()


# -- the roster, and who a message is for ---------------------------------------------------------


def _roster(tmp_path: Path) -> list[Bot]:
    bots = Bots(tmp_path / "bots")
    bots.create("finance", title="Finance", description="Bills and taxes.")
    bots.create("work", title="Work")
    return bots.list()


def test_the_roster_names_every_other_agent_and_who_the_reader_is(tmp_path: Path) -> None:
    bots = _roster(tmp_path)

    for_atlas = roster_section(None, bots)
    for_finance = roster_section(bots[0], bots)

    assert for_atlas.startswith("## Messaging other agents\n")
    assert for_atlas.endswith(
        "You are `@atlas`. Your teammates (live roster; roles from their definitions):\n"
        "- `@finance` — Finance — Bills and taxes.\n"
        "- `@work` — Work"
    )
    assert for_finance.endswith(
        "You are `@finance`. Your teammates (live roster; roles from their definitions):\n"
        "- `@atlas` — Atlas — the home's main agent, the one the user talks to in every other "
        "conversation\n"
        "- `@work` — Work"
    )
    # The rules a message between agents follows.
    assert "FIRE-AND-FORGET" in for_atlas
    assert "never trade acknowledgements back and forth" in for_atlas
    assert "it is delivered back to them, once" in for_atlas
    assert "it approves nothing" in for_atlas


def test_a_message_is_attributed_to_who_sent_it(tmp_path: Path) -> None:
    bots = _roster(tmp_path)

    assert attributed(None, "Is the rent paid?") == (
        "Message from \U0001f916 Atlas (@atlas): Is the rent paid?"
    )
    assert attributed(bots[0], "Paid.") == "Message from \U0001f916 Finance (@finance): Paid."


def test_a_target_is_a_name_first_then_a_title_nobody_else_has(tmp_path: Path) -> None:
    root = Bots(tmp_path / "bots")
    root.create("finance", title="Books")
    root.create("books", title="Library")
    root.create("work", title="Library")
    bots = root.list()
    named = {bot.name: bot for bot in bots}

    # A name is never taken by another bot's title (a name is matched first).
    assert resolve_target("books", None, bots) == named["books"]
    assert resolve_target("@Books", None, bots) == named["books"]
    assert resolve_target("FINANCE", None, bots) == named["finance"]
    # A title two bots share is refused, not guessed; from one of them, the other's is found.
    assert "names more than one agent" in str(resolve_target("Library", None, bots))
    assert resolve_target("Library", "work", bots) == named["books"]
    # The main agent is reached by a bot, and nobody writes to itself.
    assert resolve_target("atlas", "finance", bots) is None
    assert "is you" in str(resolve_target("atlas", None, bots))
    assert "is you" in str(resolve_target("@finance", "finance", bots))
    assert str(resolve_target("ghost", "finance", bots)) == (
        "There is no agent 'ghost'. Your teammates are: @atlas, @books, @work."
    )
