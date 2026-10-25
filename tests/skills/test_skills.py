"""Skills on real folders: the catalog, the listing, and use_skill.

Ported from Atlas V3's test_skills_catalog.py where its rules still hold here; the slash command
that expands a skill in the user's message belongs to the server (step 8).
"""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

import pytest

from atlas.agent import Tool
from atlas.skills import (
    Needs,
    Skill,
    SkillCatalog,
    listing,
    listing_budget,
    parse_skill,
    rendered,
    support_files,
)
from atlas.tools import SkillUse
from atlas.tools.skill import CARRIED_HEADER


def _skill(root: Path, folder: str, text: str, file: str = "SKILL.md") -> Path:
    path = root / folder
    path.mkdir(parents=True, exist_ok=True)
    (path / file).write_text(text, encoding="utf-8")
    return path


@pytest.fixture
def skills(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    _skill(
        root,
        "weekly-report",
        "---\nname: weekly-report\ndescription: Write the weekly report.\n---\n"
        "Gather the numbers.\n",
    )
    _skill(
        root,
        "Commit",
        "---\ndescription: >-\n  Stage and commit\n  the changes.\nwhen_to_use: After an edit.\n"
        "allowed-tools: Bash(git *)\n---\n# Commit\nRun git commit for $ARGUMENTS.\n",
    )
    _skill(root, "notes", "# Take notes\n\nWrite them down.\n")
    return root


def _use(tool: Tool, **arguments: Any) -> str:
    async def call() -> object:
        return await tool.run(arguments)

    output = asyncio.run(call())
    assert isinstance(output, str)
    return output


# -- the catalog ---------------------------------------------------------------------------------


def test_skills_are_folders_with_a_skill_file(skills: Path) -> None:
    (skills / "loose.md").write_text("---\nname: loose\n---\nNot a skill.\n", encoding="utf-8")
    (skills / "empty").mkdir()
    _skill(skills, ".versions", "---\nname: old\n---\nA hidden folder.\n")

    names = sorted(skill.name for skill in SkillCatalog([skills]).skills())

    assert names == ["Commit", "notes", "weekly-report"]


def test_the_frontmatter_is_yaml_and_unknown_fields_are_ignored(skills: Path) -> None:
    commit = SkillCatalog([skills]).get("Commit")

    assert commit is not None
    assert commit.description == "Stage and commit the changes. After an edit."
    assert commit.body.startswith("# Commit")


def test_a_description_falls_back_to_the_first_line_of_prose(skills: Path) -> None:
    notes = SkillCatalog([skills]).get("notes")

    assert notes is not None
    assert notes.description == "Take notes"


def test_the_skill_file_is_found_whatever_its_case(tmp_path: Path) -> None:
    _skill(tmp_path, "lower", "---\nname: lower\n---\nBody.\n", file="skill.md")

    assert SkillCatalog([tmp_path]).get("lower") is not None


def test_a_broken_skill_is_skipped_and_said(skills: Path, caplog: pytest.LogCaptureFixture) -> None:
    _skill(skills, "broken", "---\nname: [unclosed\n---\nBody.\n")
    _skill(skills, "badname", "---\nname: ../escape\n---\nBody.\n")
    _skill(skills, "list", "---\n- a\n- b\n---\nBody.\n")

    with caplog.at_level(logging.WARNING, logger="atlas.skills"):
        names = {skill.name for skill in SkillCatalog([skills]).skills()}

    assert names == {"Commit", "notes", "weekly-report"}
    said = caplog.text
    assert "not valid YAML" in said
    assert "cannot be a folder's name" in said
    assert "is not a table" in said


def test_a_name_that_would_leave_the_folder_is_not_a_skill(skills: Path) -> None:
    catalog = SkillCatalog([skills])

    assert catalog.get("../weekly-report") is None
    assert catalog.get("") is None


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_a_link_out_of_the_skills_folder_is_skipped(skills: Path, tmp_path: Path) -> None:
    outside = _skill(tmp_path / "elsewhere", "secret", "---\nname: secret\n---\nOutside.\n")
    (skills / "secret").symlink_to(outside, target_is_directory=True)

    assert SkillCatalog([skills]).get("secret") is None


def test_the_first_folder_wins_a_shared_name(tmp_path: Path) -> None:
    mine = _skill(tmp_path / "home", "report", "---\nname: report\n---\nMine.\n")
    _skill(tmp_path / "plugin", "report", "---\nname: report\n---\nThe plugin's.\n")

    found = SkillCatalog([tmp_path / "home", tmp_path / "plugin", tmp_path / "missing"])

    report = found.get("report")
    assert report is not None
    assert report.folder == mine


# -- the listing and rendering -------------------------------------------------------------------


def _made(name: str, description: str = "", *, invocable: bool = True) -> Skill:
    return Skill(name, description, "Body.", Path(name), model_invocable=invocable)


def test_the_listing_names_every_skill_and_describes_while_the_budget_lasts() -> None:
    skills = [
        _made("b-skill", "b" * 60),
        _made("a-skill", "a" * 60),
        _made("hidden", "secret", invocable=False),
        _made("c-skill", "c" * 60),
    ]

    full = listing(skills, budget=10_000)
    tight = listing(skills, budget=150)

    assert full.splitlines() == [f"- {x}-skill: {x * 60}" for x in "abc"]
    assert tight.splitlines()[1:] == ["- b-skill: " + "b" * 60, "- c-skill"]
    assert "hidden" not in full


def test_a_description_is_cut_to_sixty_characters_in_the_skills_index() -> None:
    line = listing([_made("long", "x" * 3_000)], budget=10_000)
    fits = listing([_made("short", "y" * 60)], budget=10_000)

    assert line == "- long: " + "x" * 57 + "..."
    assert fits == "- short: " + "y" * 60


def test_the_budget_is_one_percent_of_the_window() -> None:
    assert listing_budget(400_000) == 16_000
    assert listing_budget(None) == 8_000
    assert listing_budget(10_000) == 1_000


def test_rendering_fills_the_folder_and_the_arguments(tmp_path: Path) -> None:
    folder = tmp_path / "s"
    skill = parse_skill("---\nname: s\n---\nRun ${CLAUDE_SKILL_DIR}/go.py on $ARGUMENTS.\n", folder)
    plain = parse_skill("Do it.\n", folder)

    assert rendered(skill, "x.csv") == f"Run {folder}/go.py on x.csv."
    assert rendered(plain, "now") == "Do it.\n\nARGUMENTS: now"
    assert rendered(plain, " ") == "Do it."


# -- use_skill -----------------------------------------------------------------------------------


def test_use_skill_lists_the_skills_and_loads_one_with_its_files(skills: Path) -> None:
    (skills / "weekly-report" / "template.md").write_text("x", encoding="utf-8")
    (skills / "weekly-report" / "scripts").mkdir()
    (skills / "weekly-report" / "scripts" / "sum.py").write_text("x", encoding="utf-8")
    use = SkillUse(SkillCatalog([skills]))
    tool = use.tool()
    assert tool is not None

    said = _use(tool, name="weekly-report")
    again = _use(tool, name="weekly-report")

    assert "- weekly-report: Write the weekly report." in tool.description
    assert said.startswith(f"[Skill weekly-report, from {skills / 'weekly-report'}]")
    assert "Files: scripts/sum.py, template.md." in said
    assert said.endswith("Gather the numbers.")
    assert again == "[Atlas: the skill weekly-report is already loaded above; follow it.]"


def test_new_arguments_load_the_skill_again(skills: Path) -> None:
    tool = SkillUse(SkillCatalog([skills])).tool()
    assert tool is not None

    first = _use(tool, name="Commit", arguments="the fix")
    second = _use(tool, name="Commit", arguments="the docs")

    assert first.endswith("Run git commit for the fix.")
    assert second.endswith("Run git commit for the docs.")


def test_an_unknown_or_hidden_skill_is_refused_with_the_known_ones(skills: Path) -> None:
    _skill(skills, "manual", "---\nname: manual\ndisable-model-invocation: true\n---\nBody.\n")
    tool = SkillUse(SkillCatalog([skills])).tool()
    assert tool is not None

    for name in ("nope", "manual"):
        said = _use(tool, name=name)
        assert said == (
            f"[Atlas: there is no skill named {name!r}. The skills are: Commit, notes, "
            "weekly-report.]"
        )
    assert "manual" not in tool.description


def test_no_skill_no_tool(tmp_path: Path) -> None:
    assert SkillUse(SkillCatalog([tmp_path / "none"])).tool() is None


def test_skills_in_use_come_back_after_a_compaction_most_recent_first(skills: Path) -> None:
    _skill(skills, "huge", "---\nname: huge\n---\n" + "h" * 30_000 + "\n")
    use = SkillUse(SkillCatalog([skills]))
    tool = use.tool()
    assert tool is not None
    assert use.carried() is None

    _use(tool, name="notes")
    _use(tool, name="huge")
    _use(tool, name="weekly-report")
    carried = use.carried()

    assert carried is not None
    assert carried.startswith(CARRIED_HEADER)
    order = [line[3:] for line in carried.splitlines() if line.startswith("## ")]
    assert order == ["weekly-report", "huge", "notes"]
    assert "the rest of huge was left out" in carried
    assert carried.count("h" * 100) < 30_000 // 100


# -- the user's `/` ------------------------------------------------------------------------------


def test_a_skill_the_user_does_not_invoke_says_so_as_claude_code_does(skills: Path) -> None:
    _skill(skills, "background", "---\nname: background\nuser-invocable: false\n---\nKnown.\n")
    _skill(skills, "shown", "---\nname: shown\nuser-invocable: yes\n---\nKept.\n")
    catalog = SkillCatalog([skills])

    found = {s.name: s.user_invocable for s in catalog.skills()}

    assert found == {
        "Commit": True,
        "background": False,
        "notes": True,
        "shown": True,
        "weekly-report": True,
    }
    # The model still loads it: the flag hides it from the user's menu only.
    background = catalog.get("background")
    assert background is not None
    assert background.model_invocable


def test_a_skills_support_files_are_named_once_for_both_ways_in(skills: Path) -> None:
    folder = skills / "weekly-report"
    (folder / "scripts").mkdir()
    for name in ("template.md", "scripts/fetch.py", "data.csv"):
        (folder / name).write_text("x", encoding="utf-8")

    assert support_files(folder, 2) == "Files: data.csv, scripts/fetch.py and 1 more."
    assert support_files(folder, 5) == "Files: data.csv, scripts/fetch.py, template.md."
    assert support_files(skills / "notes", 5) == ""
    assert support_files(skills / "missing", 5) == ""


def test_what_a_skill_needs_is_read_from_its_frontmatter(tmp_path: Path) -> None:
    """Step 16s: `metadata.requires`, as the skills Atlas ships for gws already write it."""
    text = (
        "---\nname: video\ndescription: Cut videos.\nmetadata:\n  requires:\n"
        "    bins: [ffmpeg, ffmpeg, ' magick ']\n    mcp: docs\n    os: [Darwin, linux, win32]\n"
        "---\nCut it.\n"
    )
    needs = parse_skill(text, tmp_path / "video").needs
    assert needs.programs == ("ffmpeg", "magick")
    assert needs.servers == ("docs",)
    assert needs.systems == ("macos", "linux", "windows")
    assert parse_skill("---\nname: plain\n---\nBody.\n", tmp_path / "plain").needs == Needs()
    odd = "---\nname: odd\nmetadata: just text\n---\nBody.\n"
    assert parse_skill(odd, tmp_path / "odd").needs == Needs()
