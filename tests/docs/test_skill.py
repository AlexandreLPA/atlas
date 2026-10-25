"""The `atlas` skill: shipped, listed whole, and every command it gives the agent works, run the way
the agent runs it, through `run_command`."""

import asyncio
import re
import shlex
from pathlib import Path

import pytest

from atlas.__main__ import main
from atlas.skills import BUNDLED_FOLDER, SkillLibrary, sync_bundled
from atlas.skills.model import DESCRIPTION_CHARS, listing
from atlas.tools import Commands, Workspace

SKILL = BUNDLED_FOLDER / "atlas" / "SKILL.md"


def test_it_ships_and_its_listing_line_is_never_cut(tmp_path: Path) -> None:
    library = SkillLibrary(tmp_path / "skills")

    assert "atlas" in sync_bundled(library).copied
    skill = library.catalog.get("atlas")
    assert skill is not None
    assert len(skill.description) <= DESCRIPTION_CHARS
    assert skill.description.startswith("Questions about Atlas itself")
    assert skill.description in listing([skill], 10_000)


def test_it_holds_no_documentation_of_its_own() -> None:
    folder = SKILL.parent

    assert [path.name for path in folder.rglob("*") if path.is_file()] == ["SKILL.md"]


def _commands_it_gives() -> list[list[str]]:
    """Every `atlas docs ...` command the skill writes, its placeholders filled in."""
    found: list[list[str]] = []
    for command in re.findall(r"`(atlas docs[^`]*)`", SKILL.read_text(encoding="utf-8")):
        words = shlex.split(command.replace("PAGE", "guides/memory").replace("WORDS", "limit"))
        found.append(words[1:])
    return found


def test_every_command_it_gives_works(capsys: pytest.CaptureFixture[str]) -> None:
    commands = _commands_it_gives()

    assert ["docs"] in commands
    assert ["docs", "--all"] in commands
    for argv in commands:
        assert main(argv) == 0, argv
        assert capsys.readouterr().out.strip(), argv


def test_the_agent_reads_a_page_through_run_command(tmp_path: Path) -> None:
    """The agent runs `atlas docs` as a command, with the environment run_command gives: the
    command is found, and the page arrives whole, accents and dashes included."""
    runner = Commands(Workspace(tmp_path), tmp_path / "commands")

    async def read() -> str:
        output = await runner.tool().run({"command": "atlas docs guides/coding-agents"})
        assert isinstance(output, str)
        return output

    said = asyncio.run(read())

    assert "# Coding agents and accounts" in said, said[-400:]
    assert "Claude Code · personal" in said
    assert "Anthropic's Agent SDK" in said
