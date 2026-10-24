"""skill_manage's shapes and use_skill's usage, on real folders and a real database."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from atlas.agent import Tool
from atlas.home import Database, migrate
from atlas.skills import SCHEMA, SkillLibrary, SkillUsage, reviewing
from atlas.tools import SkillUse, skill_manage

NOTES = "---\nname: notes\ndescription: Use when writing notes.\n---\nWrite in bullets.\n"


@pytest.fixture
def usage(tmp_path: Path) -> SkillUsage:
    database = Database(tmp_path / "atlas.db")
    migrate(database, (SCHEMA,))
    return SkillUsage(database)


@pytest.fixture
def library(tmp_path: Path, usage: SkillUsage) -> SkillLibrary:
    return SkillLibrary(tmp_path / "skills", usage)


def _run(tool: Tool, arguments: dict[str, Any]) -> object:
    async def go() -> object:
        return await tool.run(arguments)

    return asyncio.run(go())


def _call(library: SkillLibrary, arguments: dict[str, Any]) -> str:
    result = _run(skill_manage(library), arguments)
    assert isinstance(result, str)
    return result


def test_each_action_takes_its_own_fields_only(library: SkillLibrary) -> None:
    for arguments, said in [
        ({}, "non-empty array"),
        ({"operations": []}, "non-empty array"),
        ({"operations": ["x"]}, "not an object"),
        ({"operations": [{"action": "rename", "name": "a"}]}, "Use: create"),
        ({"operations": [{"action": "create"}]}, "needs the skill's name"),
        (
            {"operations": [{"action": "create", "name": "a", "file_content": "x"}]},
            "does not take file_content; it takes content",
        ),
        ({"operations": [{"action": "create", "name": "a", "content": 3}]}, "must be text"),
    ]:
        assert said in _call(library, arguments), arguments


def test_a_call_answers_with_what_it_did(library: SkillLibrary) -> None:
    said = _call(library, {"operations": [{"action": "create", "name": "notes", "content": NOTES}]})

    assert "Skill 'notes' created" in said
    refused = _call(
        library, {"operations": [{"action": "create", "name": "notes", "content": NOTES}]}
    )
    assert refused.startswith("[Atlas:")
    assert "already exists" in refused


def test_a_load_is_a_use_and_a_review_load_always_answers_in_full(
    library: SkillLibrary, usage: SkillUsage
) -> None:
    _call(library, {"operations": [{"action": "create", "name": "notes", "content": NOTES}]})
    use = SkillUse(library.catalog, usage=usage)
    tool = use.tool()
    assert tool is not None

    first = _run(tool, {"name": "notes"})
    again = _run(tool, {"name": "notes"})

    async def in_review() -> object:
        with reviewing() as review:
            result = await tool.run({"name": "notes"})
            assert (library.folder / "notes" / "SKILL.md").resolve() in review.read
            return result

    reviewed = asyncio.run(in_review())
    assert "Write in bullets." in str(first)
    assert "already loaded" in str(again)
    assert "Write in bullets." in str(reviewed)
    record = usage.record("notes")
    assert record is not None
    assert record.use_count == 3
