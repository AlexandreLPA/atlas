"""The background review's skill half against the fake model server: when it is due, what it may
change, and who owns what it makes.

The model side is real (the SDK, the wire, the call ledger); the skills are real folders, the
usage a real database, and the tools the real use_skill, skill_manage and read_file.
"""

import json
from pathlib import Path

import pytest

from atlas.agent import AgentLoop, Toolset
from atlas.home import Database, migrate
from atlas.memory import (
    COMBINED_REVIEW_PROMPT,
    BackgroundReview,
    MemoryFiles,
    ReviewEnded,
    skill_review_due,
)
from atlas.models import AssistantMessage, Message, ToolCall, ToolResult, UserMessage
from atlas.skills import (
    SCHEMA,
    SKILL_REVIEW_PROMPT,
    Origin,
    SkillLibrary,
    SkillOperation,
    SkillUsage,
)
from atlas.tools import SkillUse, Workspace, file_tools, memory, skill_manage
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect, Run

CHAT = "/v1/chat/completions"
NOTES = "---\nname: notes\ndescription: Use when writing notes.\n---\nWrite in bullets.\n"


@pytest.fixture
def usage(tmp_path: Path) -> SkillUsage:
    database = Database(tmp_path / "atlas.db")
    migrate(database, (SCHEMA,))
    return SkillUsage(database)


@pytest.fixture
def library(tmp_path: Path, usage: SkillUsage) -> SkillLibrary:
    library = SkillLibrary(tmp_path / "skills", usage)
    library.apply([SkillOperation("create", "notes", content=NOTES)], Origin.USER)
    library.apply(
        [SkillOperation("write_file", "notes", file_path="references/old.md", file_content="Old.")],
        Origin.USER,
    )
    return library


def _tools(library: SkillLibrary, usage: SkillUsage, tmp_path: Path) -> tuple[Toolset, SkillUse]:
    use = SkillUse(library.catalog, usage=usage)
    tool = use.tool()
    assert tool is not None
    return (
        Toolset(
            [
                tool,
                skill_manage(library),
                memory(MemoryFiles(tmp_path / "memory")),
                *file_tools(Workspace(tmp_path)),
            ]
        ),
        use,
    )


def _call(name: str, arguments: object) -> FakeCall:
    return FakeCall(name, json.dumps(arguments))


def _results(body: dict[str, object]) -> list[str]:
    messages = body["messages"]
    assert isinstance(messages, list)
    return [str(m["content"]) for m in messages if isinstance(m, dict) and m["role"] == "tool"]


# -- when it is due ------------------------------------------------------------------------------


def _turn(calls: int, *, changes_skill: bool = False) -> list[Message]:
    turn: list[Message] = [UserMessage("go")]
    for n in range(calls - 1):
        name = "skill_manage" if changes_skill and n == 0 else "read_file"
        call = ToolCall(f"c{n}", name, "{}")
        turn += [AssistantMessage(tool_calls=(call,)), ToolResult(call.id, "ok")]
    return [*turn, AssistantMessage("done")]


def test_a_skill_review_is_due_when_a_turn_carries_the_calls_past_ten() -> None:
    assert not skill_review_due([])
    assert not skill_review_due(_turn(9))
    assert skill_review_due(_turn(10))
    assert not skill_review_due([*_turn(10), *_turn(3)])  # 13: the review at 10 was done
    assert skill_review_due([*_turn(8), *_turn(4)])  # 12: this turn crossed 10
    assert skill_review_due([*_turn(4), *_turn(25)])  # 29: crossed 10 and 20 at once
    assert not skill_review_due([*_turn(8), *_turn(4, changes_skill=True)])  # 2 since the change


# -- what the review may do ----------------------------------------------------------------------


def test_the_review_reads_before_it_writes_never_deletes_and_owns_what_it_creates(
    connect: Connect, run: Run, tmp_path: Path, library: SkillLibrary, usage: SkillUsage
) -> None:
    toolset, use = _tools(library, usage, tmp_path)
    patch = {
        "operations": [
            {
                "action": "patch",
                "name": "notes",
                "old_string": "Write in bullets.",
                "new_string": "Write in bullets, at most five.",
            }
        ]
    }
    remove = {
        "operations": [{"action": "remove_file", "name": "notes", "file_path": "references/old.md"}]
    }
    create = {
        "operations": [
            {
                "action": "create",
                "name": "summaries",
                "content": "---\nname: summaries\ndescription: Use when summing up.\n---\nLead "
                "with the answer.\n",
            }
        ]
    }
    old = library.folder / "notes" / "references" / "old.md"
    with FakeModelServer() as fake:
        fake.reply(
            FakeReply(calls=[_call("skill_manage", patch)]),  # refused: not loaded yet
            FakeReply(calls=[_call("use_skill", {"name": "notes"})]),
            FakeReply(calls=[_call("skill_manage", patch)]),
            FakeReply(calls=[_call("skill_manage", remove)]),  # refused: not read yet
            FakeReply(calls=[_call("read_file", {"path": str(old)})]),
            FakeReply(calls=[_call("skill_manage", remove)]),
            FakeReply(calls=[_call("skill_manage", {"action": "delete", "name": "notes"})]),
            FakeReply(calls=[_call("skill_manage", create)]),
            FakeReply(text="Patched notes; created summaries."),
        )
        models = connect(fake, "openrouter")

        async def scenario() -> ReviewEnded:
            review = BackgroundReview(models.client("openrouter", "openai/gpt-4o-mini"), toolset)
            return await review.run(
                [UserMessage("Too long; five bullets at most.")], memory=False, skills=True
            )

        ended = run(models, scenario)
        bodies = fake.bodies(CHAT)

    # What its summary says: only the calls that applied, refusals left out.
    assert (ended.created, ended.updated) == (("summaries",), ("notes",))
    # Its skill diffs: one per skill, from before its first write to after its last.
    assert ended.diffs["notes"].splitlines() == [
        "--- a/SKILL.md",
        "+++ b/SKILL.md",
        "@@ -4,2 +4,2 @@",
        " ---",
        "-Write in bullets.",
        "+Write in bullets, at most five.",
        "--- a/references/old.md",
        "+++ /dev/null",
        "@@ -1 +0,0 @@",
        "-Old.",
    ]
    assert ended.diffs["summaries"].splitlines()[:2] == ["--- /dev/null", "+++ b/SKILL.md"]
    assert "+Lead with the answer." in ended.diffs["summaries"]
    results = _results(bodies[-1])
    assert "has not been loaded in this review" in results[0]
    assert "Patched SKILL.md in skill 'notes'" in results[2]
    assert "has not been loaded in this review" in results[3]
    assert "removed from skill 'notes'" in results[5]
    assert "may not delete skills" in results[6]
    assert "created" in results[7]
    assert bodies[0]["messages"][-1]["content"].startswith(SKILL_REVIEW_PROMPT)
    assert "at most five" in (library.folder / "notes" / "SKILL.md").read_text(encoding="utf-8")
    assert not old.exists()
    assert library.catalog.get("notes") is not None  # not deleted
    assert len(library.versions("notes")) == 3  # the support file, the patch, the removal
    made, mine = usage.record("summaries"), usage.record("notes")
    assert made is not None
    assert made.origin is Origin.REVIEW
    assert mine is not None
    assert mine.origin is Origin.USER  # improved, still the user's
    assert use.carried() is None  # the review's load left the conversation's skills alone


def test_outside_a_review_a_new_skill_is_the_users_and_no_read_is_asked(
    connect: Connect, run: Run, tmp_path: Path, library: SkillLibrary, usage: SkillUsage
) -> None:
    toolset, _ = _tools(library, usage, tmp_path)
    create = {
        "action": "create",  # the flat legacy shape, still accepted
        "name": "mine",
        "content": "---\nname: mine\ndescription: Use when asked.\n---\nDo it.\n",
    }
    patch = {
        "operations": [
            {"action": "patch", "name": "notes", "old_string": "bullets", "new_string": "lists"}
        ]
    }
    with FakeModelServer() as fake:
        fake.reply(
            FakeReply(calls=[_call("skill_manage", create)]),
            FakeReply(calls=[_call("skill_manage", patch)]),
            FakeReply(text="Done."),
        )
        models = connect(fake, "openrouter")

        async def scenario() -> None:
            loop = AgentLoop(models.client("openrouter", "openai/gpt-4o-mini"), toolset)
            await loop.reply([], "Make me a skill, and fix notes.")

        run(models, scenario)

    record = usage.record("mine")
    assert record is not None
    assert record.origin is Origin.USER
    assert "lists" in (library.folder / "notes" / "SKILL.md").read_text(encoding="utf-8")


def test_both_halves_due_run_one_combined_review(
    connect: Connect, run: Run, tmp_path: Path, library: SkillLibrary, usage: SkillUsage
) -> None:
    toolset, _ = _tools(library, usage, tmp_path)
    with FakeModelServer() as fake:
        fake.reply(FakeReply(text="Nothing to save."))
        models = connect(fake, "openrouter")

        async def scenario() -> None:
            review = BackgroundReview(models.client("openrouter", "openai/gpt-4o-mini"), toolset)
            await review.run([UserMessage("hi")], memory=True, skills=True)

        run(models, scenario)
        (body,) = fake.bodies(CHAT)

    prompt = body["messages"][-1]["content"]
    assert prompt.startswith(COMBINED_REVIEW_PROMPT)
    assert "**Memory**: TWO separate stores" in prompt
    assert "You can only call memory and skill management tools" in prompt
