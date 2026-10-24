"""What the loop tells the screen of a turn (step 9c): the interim text, and how each call ended.

Through the real SDK against the fake model, as every loop test.
"""

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import aclosing
from typing import Any

from atlas.agent import (
    STOPPED,
    AgentLoop,
    InterimText,
    Tool,
    ToolFinished,
    ToolStarted,
    TurnEvent,
)
from atlas.models import Message, TextDelta, ToolResult, ToolStatus
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect, Run


def _tool(name: str, run: Callable[[dict[str, Any]], Awaitable[str]], **flags: Any) -> Tool:
    return Tool(name, f"The {name} tool.", {"type": "object", "properties": {}}, run, **flags)


def _returns(text: str) -> Callable[[dict[str, Any]], Awaitable[str]]:
    async def run(arguments: dict[str, Any]) -> str:
        return text

    return run


async def _events(loop: AgentLoop, history: list[Message], text: str) -> list[TurnEvent]:
    return [event async for event in loop.run(history, text)]


def test_the_text_of_a_call_that_asks_for_tools_is_said_interim_before_its_first_tool(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(text="Let me look.", calls=[FakeCall("look", id="c1")]),
        FakeReply(calls=[FakeCall("look", id="c2")]),  # a call with no text says nothing
        FakeReply(text="Found it."),
    )
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> list[TurnEvent]:
        loop = AgentLoop(models.client("openrouter"), [_tool("look", _returns("here"))])
        return await _events(loop, history, "Where is it?")

    events = run(models, scenario)

    interim = [event for event in events if isinstance(event, InterimText)]
    assert interim == [InterimText("Let me look.")]
    at = events.index(interim[0])
    assert isinstance(events[at + 1], ToolStarted)
    assert all(not isinstance(event, TextDelta) for event in events[at : at + 3])
    assert "".join(e.text for e in events[:at] if isinstance(e, TextDelta)) == "Let me look."


def test_each_call_says_how_it_ended_and_its_result_keeps_it(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(
            calls=[
                FakeCall("good", id="ok"),
                FakeCall("errs", id="bad"),
                FakeCall("guarded", id="no"),
                FakeCall("missing", id="gone"),
            ]
        ),
        FakeReply(text="Done."),
    )
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def gate(tool: Tool, arguments: dict[str, Any]) -> str | None:
        return "[Atlas: the user denied this call.]" if tool.name == "guarded" else None

    async def scenario() -> list[TurnEvent]:
        tools = [
            _tool("good", _returns("fine")),
            # An error said in the result, not raised: it is read as a failure.
            _tool("errs", _returns("make: *** [test] Error 1\n[exit code 2]")),
            _tool("guarded", _returns("never")),
        ]
        return await _events(
            AgentLoop(models.client("openrouter"), tools, gate=gate), history, "go"
        )

    events = run(models, scenario)

    finished = {e.call_id: e for e in events if isinstance(e, ToolFinished)}
    assert {key: done.status for key, done in finished.items()} == {
        "ok": ToolStatus.COMPLETED,
        "bad": ToolStatus.FAILED,
        "no": ToolStatus.DENIED,
        "gone": ToolStatus.FAILED,
    }
    assert all(done.duration_s >= 0 for done in finished.values())
    stored = {m.call_id: m.status for m in history if isinstance(m, ToolResult)}
    assert stored == {key: done.status for key, done in finished.items()}


def test_a_call_takes_its_time(fake: FakeModelServer, connect: Connect, run: Run) -> None:
    fake.reply(FakeReply(calls=[FakeCall("slow", id="c1")]), FakeReply(text="ok"))
    models = connect(fake, "openrouter")

    async def slow(arguments: dict[str, Any]) -> str:
        await asyncio.sleep(0.2)
        return "late"

    async def scenario() -> list[TurnEvent]:
        return await _events(
            AgentLoop(models.client("openrouter"), [_tool("slow", slow)]), [], "go"
        )

    (done,) = [e for e in run(models, scenario) if isinstance(e, ToolFinished)]
    assert 0.2 <= done.duration_s < 5


def test_calls_the_halt_kept_from_running_show_as_cancelled(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(calls=[FakeCall("status", "{}", id=f"s{n}") for n in range(7)]))
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> list[TurnEvent]:
        loop = AgentLoop(
            models.client("openrouter"), [_tool("status", _returns("down"))], attended=False
        )
        return await _events(loop, history, "go")

    events = run(models, scenario)

    started = [e.call_id for e in events if isinstance(e, ToolStarted)]
    finished = [e for e in events if isinstance(e, ToolFinished)]
    assert started == [f"s{n}" for n in range(7)]  # every call the stored reply holds
    assert [e.status for e in finished[5:]] == [ToolStatus.CANCELLED] * 2
    stored = [m.status for m in history if isinstance(m, ToolResult)]
    assert stored[5:] == [ToolStatus.CANCELLED] * 2


def test_a_stop_leaves_the_cut_calls_cancelled_in_the_history(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(calls=[FakeCall("slow", id="c1"), FakeCall("slow", id="c2")]))
    models = connect(fake, "openrouter")
    history: list[Message] = []

    async def scenario() -> None:
        loop = AgentLoop(models.client("openrouter"), [_tool("slow", _returns("late"))])
        async with aclosing(loop.run(history, "go")) as events:
            async for event in events:
                if isinstance(event, ToolStarted):
                    break

    run(models, scenario)

    assert history[-2:] == [
        ToolResult("c1", STOPPED, status=ToolStatus.CANCELLED),
        ToolResult("c2", STOPPED, status=ToolStatus.CANCELLED),
    ]
