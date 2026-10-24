"""The agent loop on Anthropic's Messages API (step 3b), through the real SDK, against the fake
model server, which refuses as Anthropic does: a thinking block whose past changed is a 400.

What the loop does differently there: it leaves the clearing of old results to Anthropic's server
(its own would edit the past the reasoning is sealed to), it gives the wire every deferred tool, and
it asks for the hour-long cache when a person paces the turns.
"""

import json
from collections.abc import Awaitable, Callable
from typing import Any

from atlas.agent import AgentLoop, Tool
from atlas.home import Home
from atlas.models import Message, SystemMessage
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect, Run

MESSAGES = "/v1/messages"
SMALL = "claude-small"


def _small_claude(home: Home) -> None:
    """An Anthropic model with a small window, declared in the user's models.toml."""
    home.models_file.write_text(
        f'[[models]]\nprovider = "anthropic"\nid = "{SMALL}"\n'
        "limit = { context = 12000, output = 2000 }\ntool_call = true\nreasoning = true\n\n",
        encoding="utf-8",
    )


def _tool(name: str, run: Callable[[dict[str, Any]], Awaitable[str]], **flags: Any) -> Tool:
    return Tool(name, f"The {name} tool.", {"type": "object", "properties": {}}, run, **flags)


def _returns(text: str) -> Callable[[dict[str, Any]], Awaitable[str]]:
    async def run(arguments: dict[str, Any]) -> str:
        return text

    return run


def test_the_reasoning_holds_through_rounds_and_old_results_are_left_to_the_server(
    fake: FakeModelServer, connect: Connect, run: Run, home: Home
) -> None:
    """Six rounds past the small window's trigger: the fake took every request, so the reasoning
    went back sealed each time, and the results went whole, Anthropic clearing them itself."""
    _small_claude(home)
    fake.reply(
        *(
            FakeReply(reasoning=f"Read page {i}.", calls=[FakeCall("read", f'{{"n": {i}}}')])
            for i in range(6)
        ),
        FakeReply(text="read them all", reasoning="Done reading."),
    )
    models = connect(fake, "anthropic")
    history: list[Message] = []

    async def scenario() -> None:
        async def read(arguments: dict[str, Any]) -> str:
            return f"page {arguments['n']} " + "w" * 4_000

        loop = AgentLoop(models.client("anthropic", SMALL), [_tool("read", read)])
        await loop.reply(history, "read six pages")

    run(models, scenario)

    bodies = fake.bodies(MESSAGES)
    assert len(bodies) == 7
    last = bodies[-1]
    results = [
        block["content"][0]["text"]
        for message in last["messages"]
        for block in message["content"]
        if block["type"] == "tool_result"
    ]
    assert len(results) == 6
    assert all(text.startswith("page") for text in results)  # no Atlas note: nothing cleared here
    thinking = [
        block
        for message in last["messages"]
        if message["role"] == "assistant"
        for block in message["content"]
        if block["type"] == "thinking"
    ]
    assert len(thinking) == 6
    edit = last["context_management"]["edits"][0]
    assert edit["trigger"] == {"type": "input_tokens", "value": 6_000}  # half the small window


def test_instructions_that_change_between_turns_drop_the_old_reasoning_not_the_turn(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    """A conversation that lasts forever reads its instructions again when they change: the past
    the earlier reasoning is sealed to changed, a 400 at Anthropic if it went back."""
    fake.reply(
        FakeReply(reasoning="Look.", calls=[FakeCall("look", "{}")]),
        FakeReply(text="First answer.", reasoning="Answer."),
        FakeReply(text="Second answer."),
    )
    models = connect(fake, "anthropic")
    instructions = ["You are Atlas."]
    history: list[Message] = []

    async def scenario() -> None:
        loop = AgentLoop(
            models.client("anthropic", "claude-opus-5-5"),
            [_tool("look", _returns("seen"))],
            instructions=lambda: instructions[-1],
            refresh_instructions=True,
        )
        await loop.reply(history, "first")
        instructions.append("You are Atlas. The memory changed.")
        await loop.reply(history, "second")

    run(models, scenario)

    last = fake.bodies(MESSAGES)[-1]
    assert last["system"][0]["text"] == "You are Atlas. The memory changed."
    assert history[0] == SystemMessage("You are Atlas. The memory changed.")
    kinds = [
        block["type"]
        for message in last["messages"]
        if message["role"] == "assistant"
        for block in message["content"]
    ]
    assert "thinking" not in kinds
    assert history[-1].text == "Second answer."  # type: ignore[union-attr]


def test_every_deferred_tool_travels_on_every_call(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(text="ok"), FakeReply(text="again"))
    models = connect(fake, "anthropic")
    tools = [
        _tool("tool_search", _returns("nothing found")),
        _tool("read", _returns("text")),
        _tool("calendar_list", _returns("free"), deferred=True),
    ]
    history: list[Message] = []

    async def scenario() -> None:
        loop = AgentLoop(models.client("anthropic", "claude-opus-5-5"), tools)
        await loop.reply(history, "hello")
        await loop.reply(history, "and again")

    run(models, scenario)

    first, second = fake.bodies(MESSAGES)
    assert first["tools"] == second["tools"]
    assert [(tool["name"], tool.get("defer_loading", False)) for tool in first["tools"]] == [
        ("tool_search", False),
        ("read", False),
        ("calendar_list", True),
    ]


def test_a_person_paced_conversation_caches_for_an_hour_and_a_routine_for_five_minutes(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(text="ok"), FakeReply(text="ok"))
    models = connect(fake, "anthropic")

    async def scenario() -> None:
        client = models.client("anthropic", "claude-opus-5-5")
        await AgentLoop(client, instructions=lambda: "You are Atlas.").reply([], "hi")
        routine = AgentLoop(client, instructions=lambda: "You are Atlas.", attended=False)
        await routine.reply([], "hi")

    run(models, scenario)

    person, machine = fake.bodies(MESSAGES)
    assert json.dumps(person["system"]).count('"ttl": "1h"') == 1
    assert '"ttl"' not in json.dumps(machine)
