"""plan and ask_user, and the plan kept across a compaction."""

import asyncio
import json
from typing import Any

import pytest

from atlas.agent import SUMMARY_MARK, AgentLoop, Tool
from atlas.home import Home
from atlas.models import AssistantMessage, Message, SystemMessage, ToolCall, ToolResult, UserMessage
from atlas.tools import (
    CARRIED_HEADER,
    RECOMMENDED,
    Answers,
    Outcome,
    Plan,
    PlanError,
    Question,
    ask_user,
)
from fake_model import FakeModelServer, FakeReply
from model_support import Connect, Run


def _call(tool: Tool, **arguments: Any) -> Any:
    async def call() -> object:
        return await tool.run(arguments)

    output = asyncio.run(call())
    assert isinstance(output, str)
    return json.loads(output) if output.startswith("{") else output


def _todo(item_id: str, content: str, status: str = "pending", **extra: str) -> dict[str, str]:
    return {"id": item_id, "content": content, "status": status, **extra}


# -- plan ----------------------------------------------------------------------------------------


def test_the_plan_is_written_whole_and_read_back() -> None:
    plan = Plan()
    tool = plan.tool()

    written = _call(tool, todos=[_todo("1", "Read the notes"), _todo("2", "Write the summary")])
    read = _call(tool)

    assert written == read
    assert [item["id"] for item in read["todos"]] == ["1", "2"]
    assert read["summary"] == {
        "total": 2,
        "pending": 2,
        "in_progress": 0,
        "completed": 0,
        "cancelled": 0,
    }


def test_merge_updates_by_id_and_adds_new_items() -> None:
    plan = Plan()
    plan.write([_todo("1", "Read"), _todo("2", "Write")])

    plan.write([{"id": "1", "status": "completed"}, _todo("3", "Send")], merge=True)

    assert [(i["id"], i["status"]) for i in plan.items] == [
        ("1", "completed"),
        ("2", "pending"),
        ("3", "pending"),
    ]


def test_a_refused_write_leaves_the_plan_as_it_was() -> None:
    plan = Plan()
    plan.write([_todo("1", "Read")])

    with pytest.raises(PlanError, match="has no content"):
        plan.write([_todo("1", "Read"), _todo("2", "  ")])
    said = _call(plan.tool(), todos=[_todo("9", "")])

    assert "has no content" in said
    assert [i["id"] for i in plan.items] == ["1"]
    assert "must be a list" in _call(plan.tool(), todos="read the notes")


def test_the_item_in_progress_goes_before_pending_ones_and_ids_are_unique() -> None:
    plan = Plan()

    plan.write([_todo("1", "a"), _todo("2", "b", "in_progress"), _todo("1", "a again")])

    assert [(i["id"], i["content"]) for i in plan.items] == [("2", "b"), ("1", "a again")]


def test_parents_that_do_not_exist_or_loop_are_dropped() -> None:
    plan = Plan()

    plan.write(
        [
            _todo("a", "A", parent="b"),
            _todo("b", "B", parent="a"),
            _todo("c", "C", parent="nobody"),
            _todo("d", "D", parent="d"),
        ]
    )

    parents = {item["id"]: item.get("parent") for item in plan.items}
    assert parents["c"] is None
    assert parents["d"] is None
    assert not (parents["a"] == "b" and parents["b"] == "a")


def test_a_plan_has_limits() -> None:
    plan = Plan()

    plan.write([_todo(str(i), "x" * 5_000) for i in range(300)])

    assert len(plan.items) == 256
    assert plan.items[0]["content"].endswith("… [cut]")
    assert len(plan.items[0]["content"]) == 4_000


def test_only_open_items_are_carried_with_their_open_subtasks_parents() -> None:
    plan = Plan()
    plan.write(
        [
            _todo("1", "Done", "completed"),
            _todo("2", "Release", "completed"),
            _todo("2.1", "Tag", "in_progress", parent="2"),
            _todo("3", "Announce"),
            _todo("4", "Dropped", "cancelled"),
        ]
    )

    carried = plan.carried()

    assert carried == (
        f"{CARRIED_HEADER}\n"
        "- [x] 2. Release (completed)\n"
        "  - [>] 2.1. Tag (in_progress)\n"
        "- [ ] 3. Announce (pending)"
    )
    plan.write([_todo("1", "Done", "completed")])
    assert plan.carried() is None


def test_a_resumed_conversation_gets_its_plan_from_the_history() -> None:
    earlier = Plan()
    earlier.write([_todo("1", "Old plan")])
    later = Plan()
    later.write([_todo("1", "Read", "completed"), _todo("2", "Write")])
    history: list[Message] = [
        UserMessage("go"),
        AssistantMessage(tool_calls=(ToolCall("p1", "plan", "{}"),)),
        ToolResult("p1", earlier.result()),
        AssistantMessage(tool_calls=(ToolCall("p2", "plan", "{}"), ToolCall("r1", "x", "{}"))),
        ToolResult("p2", later.result()),
        ToolResult("r1", '{"todos": [{"id": "z", "content": "not a plan", "status": "pending"}]}'),
    ]

    resumed = Plan()
    resumed.hydrate(history)

    assert resumed.items == later.items


def test_after_a_compaction_the_open_plan_follows_the_summary(
    connect: Connect, run: Run, home: Home
) -> None:
    # The plan waits behind tool search; what it carries still follows.
    assert Plan().tool().deferred
    home.models_file.write_text(
        '[[models]]\nprovider = "vllm"\nid = "small"\n'
        "limit = { context = 12000, output = 2000 }\ntool_call = true\n\n",
        encoding="utf-8",
    )
    history: list[Message] = [SystemMessage("You are Atlas.")]
    for i in range(12):
        history += [UserMessage(f"q{i} " + "q" * 2_000), AssistantMessage(f"a{i} " + "a" * 2_000)]
    plan = Plan()
    plan.write([_todo("1", "Collect the numbers", "in_progress"), _todo("2", "Chart them")])
    with FakeModelServer(key=None) as local:
        local.reply(FakeReply(text="## Goal\nNumbers."), FakeReply(text="Charting."))
        models = connect(local, "vllm")

        async def scenario() -> None:
            await AgentLoop(models.client("vllm", "small"), [plan.tool()]).reply(history, "go on")

        run(models, scenario)

    summary = history[1]
    assert isinstance(summary, AssistantMessage)
    carried = "- [>] 1. Collect the numbers (in_progress)\n- [ ] 2. Chart them (pending)"
    assert summary.text.endswith(f"{CARRIED_HEADER}\n{carried}")


def test_a_tool_that_fails_to_say_what_it_carries_does_not_stop_a_compaction(
    connect: Connect, run: Run, home: Home
) -> None:
    home.models_file.write_text(
        '[[models]]\nprovider = "vllm"\nid = "small"\n'
        "limit = { context = 12000, output = 2000 }\ntool_call = true\n\n",
        encoding="utf-8",
    )
    history: list[Message] = [SystemMessage("You are Atlas.")]
    for i in range(12):
        history += [UserMessage(f"q{i} " + "q" * 2_000), AssistantMessage(f"a{i} " + "a" * 2_000)]

    def broken() -> str | None:
        raise RuntimeError("lost its notes")

    async def nothing(arguments: dict[str, Any]) -> str:
        return ""

    tool = Tool("notes", "Notes.", {"type": "object"}, nothing, carried=broken)
    with FakeModelServer(key=None) as local:
        local.reply(FakeReply(text="## Goal\nNumbers."), FakeReply(text="Ok."))
        models = connect(local, "vllm")

        async def scenario() -> None:
            await AgentLoop(models.client("vllm", "small"), [tool]).reply(history, "go on")

        run(models, scenario)

    summary = history[1]
    assert isinstance(summary, AssistantMessage)
    assert summary.text.startswith(SUMMARY_MARK)
    assert "## Goal\nNumbers." in summary.text
    assert CARRIED_HEADER not in summary.text


# -- ask_user ------------------------------------------------------------------------------------


def _asking(answers: Answers) -> tuple[Tool, list[tuple[Question, ...]]]:
    asked: list[tuple[Question, ...]] = []

    async def asker(questions: tuple[Question, ...]) -> Answers:
        asked.append(questions)
        return answers

    return ask_user(asker), asked


def test_questions_reach_the_user_with_the_recommended_choice_marked() -> None:
    tool, asked = _asking(
        Answers(Outcome.SUBMITTED, {"q0": f"Postgres {RECOMMENDED}", "q1": ["Logs", "Metrics"]})
    )

    said = _call(
        tool,
        questions=[
            {"question": "Which database?", "choices": ["Postgres", "SQLite", " "]},
            {"question": "What to keep?", "choices": ["Logs", "Metrics"], "multi_select": True},
            {"question": "Anything else?"},
        ],
    )

    assert asked[0] == (
        Question("q0", "Which database?", (f"Postgres {RECOMMENDED}", "SQLite")),
        Question("q1", "What to keep?", (f"Logs {RECOMMENDED}", "Metrics"), multi_select=True),
        Question("q2", "Anything else?"),
    )
    assert said["outcome"] == "submitted"
    first, second, third = said["responses"]
    assert first == {
        "question": "Which database?",
        "choices_offered": ["Postgres", "SQLite"],
        "status": "answered",
        "user_response": "Postgres",
    }
    assert second["user_response"] == ["Logs", "Metrics"]
    assert (third["status"], third["user_response"], third["choices_offered"]) == (
        "unanswered",
        None,
        None,
    )


def test_a_wait_that_ended_without_answers_says_why() -> None:
    tool, _ = _asking(Answers(Outcome.TIMED_OUT, {"q0": None}, notice="Nobody answered in 1 h."))

    said = _call(tool, questions=[{"question": "Deploy now?", "choices": ["Yes", "No"]}])

    assert said["outcome"] == "timed_out"
    assert said["notice"] == "Nobody answered in 1 h."
    assert said["responses"][0]["status"] == "skipped"


@pytest.mark.parametrize(
    ("questions", "message"),
    [
        (None, "must be a non-empty list"),
        ([], "must be a non-empty list"),
        ([{"question": str(i)} for i in range(6)], "at most 5 questions"),
        (["Which?"], "must be an object"),
        ([{"question": " "}], "question is empty"),
        ([{"question": "Q", "choices": "a, b"}], "must be a list of texts"),
        ([{"question": "Q", "choices": [" ", ""]}], "are all blank"),
        ([{"question": "Q", "choices": ["x" * 8_001]}], "past the 8,000 allowed"),
    ],
)
def test_malformed_questions_are_refused_before_anyone_is_asked(
    questions: object, message: str
) -> None:
    tool, asked = _asking(Answers(Outcome.SUBMITTED))

    said = _call(tool, questions=questions)

    assert message in said
    assert asked == []
