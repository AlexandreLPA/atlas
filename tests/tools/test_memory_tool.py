"""The memory tool: its two shapes and its replies, over real memory files."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from atlas.agent import Tool
from atlas.memory import MemoryFiles, Target, proposing
from atlas.tools import MAX_CONSOLIDATION_FAILURES, memory

DONE = "Write saved. This update is complete — do not repeat it."


@pytest.fixture
def files(tmp_path: Path) -> MemoryFiles:
    return MemoryFiles(tmp_path / "memory", {Target.USER: 100, Target.MEMORY: 100})


def _call(tool: Tool, **arguments: Any) -> str:
    async def call() -> object:
        return await tool.run(arguments)

    result = asyncio.run(call())
    assert isinstance(result, str)
    return result


def test_a_single_change_is_saved_and_says_it_is_done(files: MemoryFiles) -> None:
    said = _call(memory(files), action="add", target="user", content="Name: Ana")

    assert said == f"Entry added. user: 9% — 9/100 chars, 1 entries.\n{DONE}"
    assert files.entries(Target.USER) == ["Name: Ana"]


def test_a_missing_target_is_the_memory_and_new_text_is_content(files: MemoryFiles) -> None:
    tool = memory(files)
    _call(tool, action="add", content="Uses tabs", target=None)

    said = _call(tool, action="replace", target="memory", old_text="tabs", new_text="Uses spaces")

    assert said.startswith("Entry replaced.")
    assert "Replaced, whole: Uses tabs" in said  # what was overwritten is never silent
    assert files.entries(Target.MEMORY) == ["Uses spaces"]


def test_operations_apply_together_and_say_what_they_took_out(files: MemoryFiles) -> None:
    tool = memory(files)
    _call(tool, action="add", target="memory", content="old fact")

    said = _call(
        tool,
        target="memory",
        operations=[
            {"action": "remove", "old_text": "old"},
            {"action": "add", "content": "new fact"},
            {"action": "add", "new_text": "other fact"},
        ],
    )

    assert said.startswith("Applied 3 operation(s).")
    assert "Removed by operation 1: old fact" in said
    assert files.entries(Target.MEMORY) == ["new fact", "other fact"]


def test_in_a_review_nobody_watches_an_add_still_applies(files: MemoryFiles) -> None:
    """Only a replace or a remove waits for the user; an add is all a review's
    prompt asks for."""
    with proposing("conv-1"):
        said = _call(memory(files), action="add", target="memory", content="Uses tabs")

    assert said.startswith("Entry added.")
    assert files.entries(Target.MEMORY) == ["Uses tabs"]


def test_the_same_entry_twice_says_nothing_changed(files: MemoryFiles) -> None:
    tool = memory(files)
    _call(tool, action="add", target="memory", content="fact")

    assert _call(tool, action="add", target="memory", content="fact").startswith(
        "Entry already exists (no duplicate added)."
    )


def test_a_single_change_over_the_limit_shows_the_entries_to_consolidate(
    files: MemoryFiles,
) -> None:
    tool = memory(files)
    _call(tool, action="add", target="memory", content="x" * 60)

    said = _call(tool, action="add", target="memory", content="y" * 60)

    assert said.startswith("[Atlas: Memory would be at 123/100 chars, over the limit by 23.")
    assert said.endswith(f"Current entries of memory (60% — 60/100 chars):\n- {'x' * 60}")


def test_a_failed_batch_shows_only_the_closest_entries(files: MemoryFiles) -> None:
    tool = memory(files)
    _call(tool, action="add", target="memory", content="Deploys only on Tuesdays")
    _call(tool, action="add", target="memory", content="Uses pnpm")

    said = _call(
        tool,
        target="memory",
        operations=[
            {"action": "add", "content": "Lives in Recife"},
            {"action": "remove", "old_text": "deploys on Fridays"},
        ],
    )

    assert "No operations were applied" in said
    assert "Closest entries:\n- Deploys only on Tuesdays" in said
    assert "Current entries" not in said
    assert "Uses pnpm" not in said


def test_an_ambiguous_piece_lists_the_entries_it_matched(files: MemoryFiles) -> None:
    tool = memory(files)
    _call(tool, target="memory", operations=[
        {"action": "add", "content": "uses tabs"}, {"action": "add", "content": "uses pnpm"},
    ])  # fmt: skip

    said = _call(tool, action="remove", target="memory", old_text="uses")

    assert "matched 2 different entries. Be more specific." in said
    assert "Entries it matched:\n- uses tabs\n- uses pnpm" in said


@pytest.mark.parametrize(
    ("arguments", "says"),
    [
        ({"target": "notes", "action": "add", "content": "x"}, "target must be 'user'"),
        ({"target": "memory", "action": "forget"}, "action must be add, replace or remove"),
        ({"target": "memory", "operations": "add x"}, "operations must be a list"),
        ({"target": "memory", "operations": [{"action": "add", "content": "x"}, 3]}, "operation 2"),
        ({"target": "memory", "action": "remove"}, "'remove' needs old_text"),
    ],
)
def test_arguments_it_cannot_read_are_explained(
    files: MemoryFiles, arguments: dict[str, Any], says: str
) -> None:
    assert says in _call(memory(files), **arguments)


def test_failing_to_consolidate_again_and_again_ends_with_stop_retrying(
    files: MemoryFiles,
) -> None:
    tool = memory(files)
    replies = [
        _call(tool, action="remove", target="memory", old_text="absent")
        for _ in range(MAX_CONSOLIDATION_FAILURES + 1)
    ]

    assert all(reply.startswith("[Atlas: no entry matched 'absent'.") for reply in replies[:-1])
    assert "Stop retrying" in replies[-1]
    # The count starts over: the next failure is an ordinary one again.
    assert _call(tool, action="remove", target="memory", old_text="absent").startswith(
        "[Atlas: no entry matched"
    )
