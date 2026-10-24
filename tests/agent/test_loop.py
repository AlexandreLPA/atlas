"""The agent loop, run against the fake model server through the real model adapter.

The model side is real: the SDK, the wire, the call ledger. The tools are small async functions,
because the loop is the piece under proof here; the 13 real tools come in step 5.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from atlas.agent import (
    NO_RESULT,
    STOPPED,
    SUMMARY_MARK,
    AgentLoop,
    Compacted,
    SpillFolder,
    Steered,
    Tool,
    ToolFinished,
    ToolSetError,
    ToolStarted,
    TurnEnded,
    TurnEvent,
    TurnOutcome,
    estimate_tokens,
)
from atlas.agent.history import COMPACTION_INSTRUCTIONS, ROUND_CHARS, cut
from atlas.home import Database, Home, migrate
from atlas.models import (
    SCHEMA,
    AssistantMessage,
    CallLedger,
    Message,
    SystemMessage,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolStatus,
    UserMessage,
)
from fake_model import FakeCall, FakeError, FakeModelServer, FakeReply, FakeUsage
from model_support import Connect, Run

CHAT = "/v1/chat/completions"
SYSTEM = SystemMessage("You are Atlas.")


def _tool(name: str, run: Callable[[dict[str, Any]], Awaitable[str]], **flags: Any) -> Tool:
    return Tool(name, f"The {name} tool.", {"type": "object", "properties": {}}, run, **flags)


def _returns(text: str) -> Callable[[dict[str, Any]], Awaitable[str]]:
    async def run(arguments: dict[str, Any]) -> str:
        return text

    return run


async def _events(loop: AgentLoop, history: list[Message], text: str) -> list[TurnEvent]:
    return [event async for event in loop.run(history, text)]


def _small_model(home: Home, context: int = 12_000) -> None:
    """A local model with a small window, declared in the user's models.toml."""
    home.models_file.write_text(
        '[[models]]\nprovider = "vllm"\nid = "small"\n'
        f"limit = {{ context = {context}, output = 2000 }}\ntool_call = true\n\n",
        encoding="utf-8",
    )


def _conversation(turns: int, size: int) -> list[Message]:
    history: list[Message] = [SYSTEM]
    for i in range(turns):
        history += [
            UserMessage(f"question {i} " + "q" * size),
            AssistantMessage(f"answer {i} " + "a" * size),
        ]
    return history


# -- a turn --------------------------------------------------------------------------------------


def test_a_turn_answers_and_the_next_turn_sees_the_answer(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(text="Hello!"), FakeReply(text="Again!"))
    models = connect(fake, "openrouter")
    history: list[Message] = [SYSTEM]

    async def scenario() -> tuple[TurnEnded, TurnEnded]:
        loop = AgentLoop(models.client("openrouter"))
        return await loop.reply(history, "Hi"), await loop.reply(history, "Once more")

    first, second = run(models, scenario)

    assert (first.outcome, first.text, first.model_calls) == (TurnOutcome.ANSWERED, "Hello!", 1)
    assert second.text == "Again!"
    assert [type(message) for message in history] == [
        SystemMessage,
        UserMessage,
        AssistantMessage,
        UserMessage,
        AssistantMessage,
    ]
    sent = fake.bodies(CHAT)[1]["messages"]
    assert sent[2] == {"role": "assistant", "content": "Hello!"}
    assert sent[3] == {"role": "user", "content": "Once more"}


def test_a_tool_round_runs_the_tool_and_sends_its_result_back(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(calls=[FakeCall("read_file", '{"path": "today.md"}', id="c1")]),
        FakeReply(text="Buy bread."),
    )
    models = connect(fake, "openrouter")
    asked: list[dict[str, Any]] = []

    async def read_file(arguments: dict[str, Any]) -> str:
        asked.append(arguments)
        return "buy bread"

    history: list[Message] = [SYSTEM]

    async def scenario() -> list[TurnEvent]:
        loop = AgentLoop(models.client("openrouter"), [_tool("read_file", read_file)])
        return await _events(loop, history, "What is in today's notes?")

    events = run(models, scenario)

    kinds = [type(event) for event in events]
    assert kinds[:2] == [ToolStarted, ToolFinished]
    assert kinds[-1] is TurnEnded
    assert TextDelta in kinds
    assert asked == [{"path": "today.md"}]
    assert history[3] == ToolResult("c1", "buy bread")
    assert fake.bodies(CHAT)[1]["messages"][3] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": "buy bread",
    }
    end = events[-1]
    assert isinstance(end, TurnEnded)
    assert (end.outcome, end.text, end.model_calls) == (TurnOutcome.ANSWERED, "Buy bread.", 2)


def test_read_only_tools_run_together_and_a_writer_runs_alone(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(calls=[FakeCall("read_a"), FakeCall("read_b"), FakeCall("write")]),
        FakeReply(text="done"),
    )
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> None:
        running = {"now": 0}
        a_in, b_in = asyncio.Event(), asyncio.Event()

        def reader(mine: asyncio.Event, other: asyncio.Event, name: str):
            async def run_(arguments: dict[str, Any]) -> str:
                running["now"] += 1
                mine.set()
                await asyncio.wait_for(other.wait(), 2)  # only if both run at once
                running["now"] -= 1
                return name

            return run_

        async def write(arguments: dict[str, Any]) -> str:
            return f"the writer saw {running['now']} running"

        tools = [
            _tool("read_a", reader(a_in, b_in, "A"), read_only=True),
            _tool("read_b", reader(b_in, a_in, "B"), read_only=True),
            _tool("write", write),
        ]
        await AgentLoop(models.client("openrouter"), tools).reply(history, "go")

    run(models, scenario)

    results = [message.text for message in history if isinstance(message, ToolResult)]
    assert results == ["A", "B", "the writer saw 0 running"]


def test_results_keep_the_order_of_the_calls(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(calls=[FakeCall("slow"), FakeCall("fast")]), FakeReply(text="ok"))
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def slow(arguments: dict[str, Any]) -> str:
        await asyncio.sleep(0.1)
        return "slow"

    async def scenario() -> list[TurnEvent]:
        tools = [
            _tool("slow", slow, read_only=True),
            _tool("fast", _returns("fast"), read_only=True),
        ]
        return await _events(AgentLoop(models.client("openrouter"), tools), history, "go")

    events = run(models, scenario)

    assert [m.text for m in history if isinstance(m, ToolResult)] == ["slow", "fast"]
    assert [e.result for e in events if isinstance(e, ToolFinished)] == ["slow", "fast"]


def test_a_failing_unknown_or_badly_called_tool_is_a_result_and_the_turn_goes_on(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(
            calls=[
                FakeCall("nonexistent"),
                FakeCall("boom"),
                FakeCall("read_file", "not json"),
                FakeCall("read_file", "[1, 2]"),
            ]
        ),
        FakeReply(text="I could not read it."),
    )
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def boom(arguments: dict[str, Any]) -> str:
        raise ValueError("the disk is full")

    async def scenario() -> list[TurnEvent]:
        tools = [_tool("boom", boom), _tool("read_file", _returns("never"))]
        return await _events(AgentLoop(models.client("openrouter"), tools), history, "go")

    events = run(models, scenario)

    finished = [event for event in events if isinstance(event, ToolFinished)]
    assert all(event.status is ToolStatus.FAILED for event in finished)
    texts = [event.result for event in finished]
    assert "there is no tool named nonexistent. The tools are: boom, read_file" in texts[0]
    assert texts[1] == "[Atlas: boom failed: ValueError: the disk is full]"
    assert "were not valid JSON" in texts[2]
    assert "must be a JSON object" in texts[3]
    end = events[-1]
    assert isinstance(end, TurnEnded)
    assert end.outcome is TurnOutcome.ANSWERED


def test_two_tools_with_one_name_are_refused(fake: FakeModelServer, connect: Connect) -> None:
    models = connect(fake, "openrouter")
    with pytest.raises(ToolSetError, match="Two tools are named read_file"):
        AgentLoop(
            models.client("openrouter"),
            [_tool("read_file", _returns("a")), _tool("read_file", _returns("b"))],
        )


# -- the loop guards -------------------------------------------------------------------------------


def test_attended_the_same_call_is_warned_and_the_turn_goes_on(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    """Attended: someone watches a conversation and can stop it, so the guards only warn."""
    fake.reply(
        *(FakeReply(calls=[FakeCall("status", '{"id": 7}')]) for _ in range(6)),
        FakeReply(text="Still down."),
    )
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> TurnEnded:
        loop = AgentLoop(models.client("openrouter"), [_tool("status", _returns("down"))])
        return await loop.reply(history, "Is it up?")

    end = run(models, scenario)

    assert end.outcome is TurnOutcome.ANSWERED
    results = [m.text for m in history if isinstance(m, ToolResult)]
    assert results[:2] == ["down", "down"]
    assert "[Atlas: this is the 3rd call in a row to status" in results[2]
    assert "6th call in a row" in results[5]


def test_unattended_the_same_call_halts_the_turn_at_the_fifth(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    """A routine nobody watches: the guard ends the turn at the fifth identical call."""
    fake.reply(*(FakeReply(calls=[FakeCall("status", '{"id": 7}')]) for _ in range(5)))
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> TurnEnded:
        loop = AgentLoop(
            models.client("openrouter"), [_tool("status", _returns("down"))], attended=False
        )
        return await loop.reply(history, "Is it up?")

    end = run(models, scenario)

    assert end.outcome is TurnOutcome.HALTED
    assert "returned the same result 5 times in a row" in end.text
    assert "Nobody was watching this run" in end.text
    assert history[-1] == AssistantMessage(end.text)
    assert len(fake.bodies(CHAT)) == 5  # no sixth call after the halt


def test_unattended_after_the_halt_the_rest_of_the_round_does_not_run(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(calls=[FakeCall("status", "{}") for _ in range(7)]))
    models = connect(fake, "openrouter")
    history: list[Message] = []
    ran: list[int] = []

    async def status(arguments: dict[str, Any]) -> str:
        ran.append(1)
        return "down"

    async def scenario() -> TurnEnded:
        loop = AgentLoop(models.client("openrouter"), [_tool("status", status)], attended=False)
        return await loop.reply(history, "go")

    end = run(models, scenario)

    assert end.outcome is TurnOutcome.HALTED
    assert len(ran) == 5
    results = [m.text for m in history if isinstance(m, ToolResult)]
    assert len(results) == 7  # every call still has its result
    assert all("not run" in text for text in results[5:])


def test_unattended_a_call_that_keeps_failing_is_blocked_without_running(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(*(FakeReply(calls=[FakeCall("lookup", '{"key": "x"}')]) for _ in range(6)))
    models = connect(fake, "openrouter")
    history: list[Message] = []
    ran: list[int] = []

    async def lookup(arguments: dict[str, Any]) -> str:
        ran.append(1)
        # The error changes each time, so the identical-result streak never forms: what stops
        # the 6th call is the same call failing 5 times.
        return f"[Atlas: there is no key x (try {len(ran)}).]"

    async def scenario() -> TurnEnded:
        tools = [_tool("lookup", lookup)]
        return await AgentLoop(models.client("openrouter"), tools, attended=False).reply(
            history, "go"
        )

    end = run(models, scenario)

    assert end.outcome is TurnOutcome.HALTED
    assert len(ran) == 5
    results = [m.text for m in history if isinstance(m, ToolResult)]
    assert results[-1].startswith("[Atlas: Blocked lookup: the same call failed 5 times")
    assert "Tool loop warning: repeated_exact_failure_warning" in results[1]


def test_a_repeated_long_result_reaches_the_model_as_a_pointer(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    page = "line\n" * 200
    fake.reply(
        FakeReply(calls=[FakeCall("page", "{}", id="p1")]),
        FakeReply(calls=[FakeCall("page", "{}", id="p2")]),
        FakeReply(text="Same page."),
    )
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> TurnEnded:
        return await AgentLoop(models.client("openrouter"), [_tool("page", _returns(page))]).reply(
            history, "Read it twice"
        )

    run(models, scenario)

    results = [m.text for m in history if isinstance(m, ToolResult)]
    assert results[0] == page
    assert results[1].startswith("[Atlas: this result is byte-identical to the page result")
    assert "(call p1)" in results[1]


# -- stopping ------------------------------------------------------------------------------------


def test_stopping_as_a_tool_starts_leaves_every_call_with_a_result(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(calls=[FakeCall("slow", id="c1"), FakeCall("slow", id="c2")]),
        FakeReply(text="Fine."),
    )
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> TurnEnded:
        loop = AgentLoop(models.client("openrouter"), [_tool("slow", _returns("late"))])
        async with aclosing(loop.run(history, "go")) as events:  # how a caller stops a turn
            async for event in events:
                if isinstance(event, ToolStarted):
                    break  # the user pressed stop
        return await loop.reply(history, "Never mind.")

    end = run(models, scenario)

    cancelled = ToolStatus.CANCELLED
    assert history[2:4] == [
        ToolResult("c1", STOPPED, status=cancelled),
        ToolResult("c2", STOPPED, status=cancelled),
    ]
    assert end.text == "Fine."
    tool_messages = [m for m in fake.bodies(CHAT)[1]["messages"] if m["role"] == "tool"]
    assert [m["content"] for m in tool_messages] == [STOPPED, STOPPED]


def test_cancelling_a_running_tool_leaves_its_result_as_stopped(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(calls=[FakeCall("slow", id="c1")]))
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> None:
        started = asyncio.Event()

        async def slow(arguments: dict[str, Any]) -> str:
            started.set()
            await asyncio.sleep(30)
            return "late"

        loop = AgentLoop(models.client("openrouter"), [_tool("slow", slow)])
        task = asyncio.create_task(_events(loop, history, "go"))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(models, scenario)

    assert history[-1] == ToolResult("c1", STOPPED, status=ToolStatus.CANCELLED)
    assert STOPPED != NO_RESULT


# -- oversized results ---------------------------------------------------------------------------


def test_an_oversized_result_is_spilled_whole_and_the_model_gets_its_ends(
    fake: FakeModelServer, connect: Connect, run: Run, home: Home
) -> None:
    whole = "S" * 2_000 + "M" * 60_000 + "E" * 2_000
    fake.reply(
        FakeReply(calls=[FakeCall("dump", id="c1"), FakeCall("pager", id="c2")]),
        FakeReply(text="ok"),
    )
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> list[TurnEvent]:
        tools = [_tool("dump", _returns(whole)), _tool("pager", _returns(whole), spills=False)]
        loop = AgentLoop(models.client("openrouter"), tools, spill=SpillFolder(home.spill_folder))
        return await _events(loop, history, "go")

    events = run(models, scenario)

    dump, pager = [event for event in events if isinstance(event, ToolFinished)]
    assert dump.spill_path is not None
    assert dump.result == cut(whole, dump.spill_path)
    assert Path(dump.spill_path).read_text(encoding="utf-8") == whole
    assert (pager.spill_path, pager.result) == (None, cut(whole, None))
    sent = [m for m in fake.bodies(CHAT)[1]["messages"] if m["role"] == "tool"]
    assert sent[0]["content"] == dump.result


def test_a_round_too_large_together_spills_the_results_past_its_budget(
    fake: FakeModelServer, connect: Connect, run: Run, home: Home
) -> None:
    fake.reply(FakeReply(calls=[FakeCall("page") for _ in range(5)]), FakeReply(text="ok"))
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> list[TurnEvent]:
        tool = _tool("page", _returns("p" * 45_000), read_only=True)
        loop = AgentLoop(models.client("openrouter"), [tool], spill=SpillFolder(home.spill_folder))
        return await _events(loop, history, "go")

    events = run(models, scenario)

    finished = [event for event in events if isinstance(event, ToolFinished)]
    assert [event.spill_path is None for event in finished] == [True, True, True, True, False]
    assert sum(len(event.result) for event in finished) <= ROUND_CHARS


# -- the context ---------------------------------------------------------------------------------


def test_old_results_are_cleared_in_the_copy_once_it_grows(
    connect: Connect, run: Run, home: Home
) -> None:
    _small_model(home)
    with FakeModelServer(key=None) as local:
        local.reply(
            *(FakeReply(calls=[FakeCall("read", f'{{"n": {i}}}')]) for i in range(6)),
            FakeReply(text="read them all"),
        )
        models = connect(local, "vllm")
        history: list[Message] = []

        async def scenario() -> None:
            async def read(arguments: dict[str, Any]) -> str:
                return f"page {arguments['n']} " + "w" * 4_000

            await AgentLoop(models.client("vllm", "small"), [_tool("read", read)]).reply(
                history, "read six pages"
            )

        run(models, scenario)
        last = local.bodies(CHAT)[-1]["messages"]

    sent = [message["content"] for message in last if message["role"] == "tool"]
    assert sent[0].startswith("[Atlas: an old result of read")
    assert [text.startswith("page") for text in sent[-3:]] == [True, True, True]
    stored = [m.text for m in history if isinstance(m, ToolResult)]
    assert all(text.startswith("page") for text in stored)


def test_a_conversation_near_the_window_is_compacted_before_the_call(
    connect: Connect, run: Run, home: Home
) -> None:
    _small_model(home)
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    ledger = CallLedger(database)
    history = _conversation(12, size=2_000)
    with FakeModelServer(key=None) as local:
        local.reply(FakeReply(text="## Goal\nAnswer the questions."), FakeReply(text="Answer."))
        models = connect(local, "vllm", ledger=ledger)

        async def scenario() -> list[TurnEvent]:
            loop = AgentLoop(models.client("vllm", "small"))
            return [event async for event in loop.run(history, "and now?", scope="conv-1")]

        events = run(models, scenario)
        summary_call, turn_call = local.bodies(CHAT)

    assert isinstance(events[0], Compacted)
    assert events[0].tokens_after < events[0].tokens_before
    instructions = summary_call["messages"][0]
    assert instructions["role"] == "system"
    assert instructions["content"].startswith(COMPACTION_INSTRUCTIONS)
    # Its length is asked, a quarter of the small window, and never set as the output limit: a
    # reasoning model spends that limit thinking, and the summary comes back cut.
    assert instructions["content"].endswith("Keep the summary under about 2,500 tokens.")
    assert "max_tokens" not in summary_call
    assert "tools" not in summary_call
    assert history[0] == SYSTEM
    summary = history[1]
    assert isinstance(summary, AssistantMessage)
    assert summary.text.startswith(SUMMARY_MARK)
    assert "## Goal\nAnswer the questions." in summary.text
    assert "> question 0 " in summary.text  # the user's words, verbatim
    assert isinstance(history[2], UserMessage)
    assert history[-2] == UserMessage("and now?")
    answer = history[-1]
    assert isinstance(answer, AssistantMessage)
    assert answer.text == "Answer."
    assert turn_call["messages"][1]["content"] == summary.text
    calls = list(reversed(ledger.recent()))
    assert [(c.purpose, c.scope) for c in calls] == [("compaction", "conv-1"), ("turn", "conv-1")]


def test_a_failed_compaction_keeps_the_history_whole_and_stops_after_three(
    connect: Connect, run: Run, home: Home
) -> None:
    # V3, 07/10/2026: a compaction without a working summarizer kept 14 of 126 messages.
    _small_model(home)
    history = _conversation(12, size=2_000)
    whole = list(history)
    with FakeModelServer(key=None) as local:
        for _ in range(3):
            local.reply(FakeError(400, "summarizer refused"), FakeReply(text="ok"))
        local.reply(FakeReply(text="ok"))  # the fourth turn does not try again
        models = connect(local, "vllm")

        async def scenario() -> None:
            loop = AgentLoop(models.client("vllm", "small"))
            for i in range(4):
                end = await loop.reply(history, f"turn {i}")
                assert end.outcome is TurnOutcome.ANSWERED

        run(models, scenario)
        posts = len(local.bodies(CHAT))

    assert history[: len(whole)] == whole
    assert posts == 3 * 2 + 1


def test_a_summary_cut_short_keeps_the_history_whole(
    connect: Connect, run: Run, home: Home
) -> None:
    # A summary that stopped at the output limit ends where it was cut, and kept, everything past
    # the cut would leave the conversation for good.
    _small_model(home)
    history = _conversation(12, size=2_000)
    whole = list(history)
    with FakeModelServer(key=None) as local:
        local.reply(
            FakeReply(
                text="## Goal\nAnswer the questions.\n\n## Pending\n1. Rename the", finish="length"
            ),
            FakeReply(text="Answer."),
        )
        models = connect(local, "vllm")

        async def scenario() -> list[TurnEvent]:
            loop = AgentLoop(models.client("vllm", "small"))
            return [event async for event in loop.run(history, "and now?")]

        events = run(models, scenario)
        posts = len(local.bodies(CHAT))

    assert not any(isinstance(event, Compacted) for event in events)
    assert history[: len(whole)] == whole
    assert not any(
        m.text.startswith(SUMMARY_MARK) for m in history if isinstance(m, AssistantMessage)
    )
    answer = history[-1]
    assert isinstance(answer, AssistantMessage)
    assert answer.text == "Answer."
    assert posts == 2


def test_a_conversation_the_provider_says_does_not_fit_is_compacted_and_sent_again(
    connect: Connect, run: Run
) -> None:
    history = _conversation(4, size=100)
    with FakeModelServer(key=None) as local:
        local.reply(
            FakeError(
                400,
                "This model's maximum context length is 8192 tokens.",
                code="context_length_exceeded",
            ),
            FakeReply(text="## Goal\nKeep going."),
            FakeReply(text="Answer."),
        )
        models = connect(local, "vllm")  # an unknown model: no window, so no early compaction

        async def scenario() -> list[TurnEvent]:
            return await _events(AgentLoop(models.client("vllm", "unknown")), history, "go on")

        events = run(models, scenario)

    assert [type(e) for e in events if not isinstance(e, TextDelta)] == [Compacted, TurnEnded]
    end = events[-1]
    assert isinstance(end, TurnEnded)
    assert (end.outcome, end.text, end.model_calls) == (TurnOutcome.ANSWERED, "Answer.", 2)


def test_a_conversation_that_cannot_be_compacted_fails_with_the_reason(
    connect: Connect, run: Run
) -> None:
    with FakeModelServer(key=None) as local:
        local.reply(FakeError(400, "prompt is too long: 9000 tokens > 8192 maximum"))
        models = connect(local, "vllm")

        async def scenario() -> TurnEnded:
            return await AgentLoop(models.client("vllm", "unknown")).reply([], "x" * 100)

        end = run(models, scenario)

    assert end.outcome is TurnOutcome.FAILED
    assert end.text.startswith("The conversation no longer fits in unknown's context")


def test_an_unknown_window_is_said_once_with_the_fix(
    connect: Connect, caplog: pytest.LogCaptureFixture
) -> None:
    with FakeModelServer(key=None) as local:
        models = connect(local, "vllm")
        with caplog.at_level(logging.WARNING, logger="atlas.agent"):
            AgentLoop(models.client("vllm", "unknown"))
    assert "Declare limit.context for it in models.toml" in caplog.text


# -- failures and cost ---------------------------------------------------------------------------


def test_a_model_failure_ends_the_turn_with_its_reason_and_raises_nothing(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeError(402, "Insufficient credits."))
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> TurnEnded:
        return await AgentLoop(models.client("openrouter")).reply(history, "Hi")

    end = run(models, scenario)

    assert (end.outcome, end.error, end.model_calls) == (
        TurnOutcome.FAILED,
        "ModelOutOfCreditError",
        0,
    )
    assert "no credit left" in end.text
    assert history == [UserMessage("Hi")]


def test_an_empty_reply_is_asked_again_and_three_in_a_row_fail(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(), FakeReply(text="Here."), FakeReply(), FakeReply(), FakeReply())
    models = connect(fake, "openrouter")

    async def scenario() -> tuple[TurnEnded, TurnEnded]:
        loop = AgentLoop(models.client("openrouter"))
        return await loop.reply([], "one"), await loop.reply([], "two")

    first, second = run(models, scenario)

    assert (first.outcome, first.text, first.model_calls) == (TurnOutcome.ANSWERED, "Here.", 2)
    assert second.outcome is TurnOutcome.FAILED
    assert "returned an empty reply 3 times" in second.text


def test_the_turn_adds_up_what_its_calls_cost(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    usage = FakeUsage(input=10_000, output=1_000)
    fake.reply(FakeReply(calls=[FakeCall("t")], usage=usage), FakeReply(text="ok", usage=usage))
    models = connect(fake, "gemini")

    async def scenario() -> TurnEnded:
        return await AgentLoop(models.client("gemini"), [_tool("t", _returns("r"))]).reply([], "go")

    end = run(models, scenario)

    # gemini-3.8-flash: 10k in at 0.75 and 1k out at 3.75 per million, twice.
    assert end.cost_usd == Decimal("0.0225")


def test_a_call_of_unknown_price_makes_the_turns_cost_unknown(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(text="ok"))
    models = connect(fake, "openrouter")

    async def scenario() -> TurnEnded:
        return await AgentLoop(models.client("openrouter", "nobody/unpriced")).reply([], "go")

    end = run(models, scenario)

    assert end.cost_usd is None


def test_tool_calls_keep_their_ids_through_the_loop(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(calls=[FakeCall("t", id="call_9")]), FakeReply(text="ok"))
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> TurnEnded:
        return await AgentLoop(models.client("openrouter"), [_tool("t", _returns("r"))]).reply(
            history, "go"
        )

    run(models, scenario)

    asked = history[1]
    assert isinstance(asked, AssistantMessage)
    assert asked.tool_calls == (ToolCall("call_9", "t", "{}"),)
    assert history[2] == ToolResult("call_9", "r")


def test_the_providers_count_of_the_last_call_decides_compaction(
    connect: Connect, run: Run, home: Home
) -> None:
    # On 09/10/2026 the estimate alone missed a compaction OpenAI's count called for. The
    # provider's count of the last call is the anchor.
    _small_model(home)
    history: list[Message] = [SYSTEM]
    with FakeModelServer(key=None) as local:
        local.reply(
            FakeReply(text="Hello.", usage=FakeUsage(input=9_000, output=5)),
            FakeReply(text="## Goal\nGreet."),
            FakeReply(text="Still here."),
        )
        models = connect(local, "vllm")

        async def scenario() -> list[TurnEvent]:
            loop = AgentLoop(models.client("vllm", "small"))
            await loop.reply(history, "Hi")  # small to the estimate, 9,000 to the provider
            return await _events(loop, history, "And now?")

        events = run(models, scenario)

    assert isinstance(events[0], Compacted)
    assert events[0].tokens_before > 9_000  # past 85% of the 10,000-token window


def test_provider_fields_are_not_estimated(fake: FakeModelServer) -> None:
    plain = AssistantMessage("ok")
    sealed = AssistantMessage(
        "ok", provider_fields={"output": [{"encrypted_content": "x" * 9_000}]}
    )
    assert estimate_tokens([sealed]) == estimate_tokens([plain])


# -- steering ------------------------------------------------------------------------------------


def test_a_message_sent_mid_turn_enters_after_the_round_of_tools(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(calls=[FakeCall("read_file", '{"path": "a.md"}', id="c1")]),
        FakeReply(text="Done, in Portuguese."),
    )
    models = connect(fake, "openrouter")
    inbox: list[str] = []

    async def read_file(arguments: dict[str, Any]) -> str:
        inbox.append("Answer in Portuguese.")  # the user types while the tool runs
        return "notes"

    def steering() -> list[str]:
        taken = list(inbox)
        inbox.clear()
        return taken

    history: list[Message] = [SYSTEM]

    async def scenario() -> list[TurnEvent]:
        loop = AgentLoop(models.client("openrouter"), [_tool("read_file", read_file)])
        return [event async for event in loop.run(history, "Read a.md", steering=steering)]

    events = run(models, scenario)

    assert [type(event) for event in events if not isinstance(event, TextDelta)] == [
        ToolStarted,
        ToolFinished,
        Steered,
        TurnEnded,
    ]
    assert [type(message) for message in history] == [
        SystemMessage,
        UserMessage,
        AssistantMessage,
        ToolResult,
        UserMessage,
        AssistantMessage,
    ]
    sent = fake.bodies(CHAT)[1]["messages"]
    assert sent[-1] == {"role": "user", "content": "Answer in Portuguese."}
    assert sent[-2]["role"] == "tool"


def test_steering_is_not_asked_before_the_first_call_or_after_the_answer(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(text="Hi."))
    models = connect(fake, "openrouter")
    asked: list[int] = []

    def steering() -> list[str]:
        asked.append(1)
        return ["late"]

    history: list[Message] = [SYSTEM]

    async def scenario() -> list[TurnEvent]:
        loop = AgentLoop(models.client("openrouter"))
        return [event async for event in loop.run(history, "Hello", steering=steering)]

    events = run(models, scenario)

    assert asked == []
    assert isinstance(events[-1], TurnEnded)
    assert [type(message) for message in history] == [SystemMessage, UserMessage, AssistantMessage]
