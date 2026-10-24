"""Tool search: the ranking, the tool itself, and the loop loading what it finds, on both wires.

The loop runs against the fake model server through the real adapter, so what is proven is what a
provider receives: the found tools in the tool list on Chat Completions, and inside a
`tool_search_output` at the end of the context on the Responses API.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from atlas.agent import AgentLoop, Tool, ToolFinished, Toolset, ToolSetError, TurnOutcome
from atlas.models import TOOL_SEARCH, Message, SystemMessage, ToolResult
from atlas.tools import Searchable, install_tool_search, rank
from atlas.tools.search import catalog_listing, listing_budget
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect, Run

CHAT = "/v1/chat/completions"
RESPONSES = "/v1/responses"
SYSTEM = SystemMessage("You are Atlas.")


def _returns(text: str) -> Callable[[dict[str, Any]], Awaitable[str]]:
    async def run(arguments: dict[str, Any]) -> str:
        return text

    return run


def _tool(
    name: str, description: str, *, deferred: bool = False, source: str = "atlas", text: str = "ok"
) -> Tool:
    parameters = {"type": "object", "properties": {"title": {"type": "string"}}}
    return Tool(name, description, parameters, _returns(text), deferred=deferred, source=source)


def _github() -> list[Tool]:
    github = {"deferred": True, "source": "mcp:github"}
    return [
        _tool("create_issue", "Create an issue in a repository.", **github),
        _tool("list_issues", "List the issues of a repository.", **github),
        _tool("merge_pull_request", "Merge a pull request.", **github),
        _tool("send_message", "Send a message to a channel.", deferred=True, source="mcp:slack"),
    ]


def _names(tools: list[Searchable]) -> list[str]:
    return [tool.name for tool in tools]


# -- the ranking ---------------------------------------------------------------------------------


def _searchable(name: str, description: str, source: str = "mcp:x") -> Searchable:
    return Searchable(name, description, source)


def test_a_word_finds_its_other_forms() -> None:
    tools = [_searchable("create_issue", "Create an issue."), _searchable("weather", "Weather.")]

    assert _names(rank(tools, "issues")) == ["create_issue"]


def test_the_rarest_word_of_the_query_admits_a_tool() -> None:
    # "send gmail email" must not return the five incident tools that share only "email".
    tools = [
        _searchable("send_gmail", "Send an email with Gmail.", "mcp:gmail"),
        *(
            _searchable(f"incident_{i}", f"Email the on-call about incident {i}.", "mcp:pager")
            for i in range(5)
        ),
    ]

    assert _names(rank(tools, "send gmail email")) == ["send_gmail"]


def test_a_long_query_needs_half_of_its_known_words() -> None:
    # "run shell command execute code python" must not be answered by a tool sharing only "run",
    # or the model searches again.
    tools = [
        _searchable("rerun_workflow", "Run a workflow again.", "mcp:ci"),
        _searchable("python_exec", "Execute Python code.", "mcp:code"),
        _searchable("shell", "A command line shell.", "mcp:term"),
    ]

    found = _names(rank(tools, "run shell command execute code python"))

    assert "rerun_workflow" not in found


def test_an_exact_name_comes_first_and_a_source_name_finds_its_tools() -> None:
    tools = [_searchable(t.name, t.description, t.source) for t in _github()]

    assert _names(rank(tools, "list_issues"))[0] == "list_issues"
    assert set(_names(rank(tools, "slack"))) == {"send_message"}


def test_a_query_with_no_known_word_finds_nothing() -> None:
    tools = [_searchable(t.name, t.description, t.source) for t in _github()]

    assert rank(tools, "weather forecast") == []
    assert rank(tools, "   ") == []
    assert rank(tools, "issue", limit=0) == []


# -- the tool ------------------------------------------------------------------------------------


def test_the_search_exists_only_when_there_is_something_to_find() -> None:
    toolset = Toolset([_tool("read_file", "Read a file.")])

    install_tool_search(toolset)
    assert toolset.get(TOOL_SEARCH) is None

    toolset.replace("mcp:github", _github()[:3])
    install_tool_search(toolset, {"mcp:github": "Issues and pull requests."})
    search = toolset.get(TOOL_SEARCH)
    assert search is not None
    listed = "github (3 tools): Issues and pull requests.\n- create_issue: Create an issue in a"
    assert listed in search.description
    assert search in toolset.always

    toolset.replace("mcp:github", [])
    install_tool_search(toolset)
    assert toolset.get(TOOL_SEARCH) is None


def test_the_search_answers_with_what_it_loaded() -> None:
    toolset = Toolset(_github())
    install_tool_search(toolset)
    search = toolset.get(TOOL_SEARCH)
    assert search is not None

    async def ask(arguments: dict[str, Any]) -> Any:
        return await search.run(arguments)

    found = asyncio.run(ask({"query": "create an issue", "limit": 1}))
    # A word no tool carries admits nothing: the model is told to try other words.
    missing = asyncio.run(ask({"query": "open an issue"}))
    empty = asyncio.run(ask({"query": ""}))

    assert found.loaded == ("create_issue",)
    assert "create_issue (github): Create an issue in a repository." in found.text
    assert missing.loaded == ()
    # The real gpt-6-luna searched "notes app add note" twice before it dropped "app" (09/10).
    assert missing.text == (
        'No tool matches "open an issue". No tool has the word(s) open; leave them out. Search '
        "again with a source's name (github, slack) plus an action, before deciding the tool "
        "does not exist."
    )
    assert "needs a query" in empty.text


def test_a_source_that_reloads_with_a_clashing_name_keeps_its_old_tools() -> None:
    toolset = Toolset([_tool("read_file", "Read a file."), *_github()[:1]])

    with pytest.raises(ToolSetError):
        toolset.replace("mcp:github", [_tool("read_file", "Another.", deferred=True)])

    assert toolset.get("create_issue") is not None


# -- the loop, Chat Completions ------------------------------------------------------------------


def test_on_chat_completions_a_found_tool_joins_the_tool_list_and_runs(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(calls=[FakeCall(TOOL_SEARCH, '{"query": "create an issue"}', id="s1")]),
        FakeReply(calls=[FakeCall("create_issue", '{"title": "Bug"}', id="c1")]),
        FakeReply(text="Done."),
        FakeReply(text="Still here."),
    )
    models = connect(fake, "openrouter")
    toolset = Toolset([_tool("read_file", "Read a file."), *_github()])
    install_tool_search(toolset)
    history: list[Message] = [SYSTEM]

    async def scenario() -> tuple[Any, Any]:
        loop = AgentLoop(models.client("openrouter"), toolset)
        first = await loop.reply(history, "File a bug")
        return first, await loop.reply(history, "Thanks")

    first, second = run(models, scenario)

    assert first.outcome is TurnOutcome.ANSWERED
    offered = [[t["function"]["name"] for t in body["tools"]] for body in fake.bodies(CHAT)]
    assert offered[0] == ["read_file", TOOL_SEARCH]
    assert offered[1][:2] == ["read_file", TOOL_SEARCH]
    assert offered[1][2] == "create_issue"
    assert "merge_pull_request" not in offered[1]
    # The tool stays loaded while the search's result is in the history.
    assert "create_issue" in offered[3]
    results = [m for m in history if isinstance(m, ToolResult)]
    assert results[0].loaded[0] == "create_issue"
    assert results[1].text == "ok"
    assert second.text == "Still here."


def test_a_deferred_tool_that_went_away_leaves_the_list(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(calls=[FakeCall(TOOL_SEARCH, '{"query": "issue"}', id="s1")]),
        FakeReply(text="Found it."),
        FakeReply(text="Gone."),
    )
    models = connect(fake, "openrouter")
    toolset = Toolset(_github())
    install_tool_search(toolset)
    history: list[Message] = [SYSTEM]

    async def scenario() -> None:
        loop = AgentLoop(models.client("openrouter"), toolset)
        await loop.reply(history, "Issues?")
        toolset.replace("mcp:github", [])
        install_tool_search(toolset)
        await loop.reply(history, "And now?")

    run(models, scenario)

    last = fake.bodies(CHAT)[-1]
    names = [t["function"]["name"] for t in last.get("tools", [])]
    assert [n for n in names if n != "send_message"] == [TOOL_SEARCH]


# -- the loop, the Responses API -----------------------------------------------------------------


def test_on_a_native_model_the_found_tools_travel_at_the_end_of_the_context(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(
            calls=[FakeCall(TOOL_SEARCH, '{"query": "create an issue"}', id="s1", search=True)]
        ),
        FakeReply(calls=[FakeCall("create_issue", '{"title": "Bug"}', id="c1")]),
        FakeReply(text="Filed."),
    )
    models = connect(fake, "openai")
    toolset = Toolset([_tool("read_file", "Read a file."), *_github()])
    install_tool_search(toolset)
    history: list[Message] = [SYSTEM]
    ran: list[str] = []

    async def scenario() -> Any:
        loop = AgentLoop(models.client("openai"), toolset)
        events = [event async for event in loop.run(history, "File a bug")]
        ran.extend(e.name for e in events if isinstance(e, ToolFinished))
        return events[-1]

    ended = run(models, scenario)

    assert ended.outcome is TurnOutcome.ANSWERED
    assert ran == [TOOL_SEARCH, "create_issue"]
    bodies = fake.bodies(RESPONSES)
    # The tool list never changes, so the provider's cache of the start holds.
    assert bodies[0]["tools"] == bodies[1]["tools"] == bodies[2]["tools"]
    assert [t.get("name", t["type"]) for t in bodies[0]["tools"]] == ["read_file", "tool_search"]
    assert bodies[0]["tools"][1]["execution"] == "client"
    # The search goes back as the provider's own items, the found tools inside the output.
    items = bodies[1]["input"]
    call = next(i for i in items if i.get("type") == "tool_search_call")
    output = next(i for i in items if i.get("type") == "tool_search_output")
    assert call["call_id"] == output["call_id"] == "s1"
    assert call["arguments"] == {"query": "create an issue"}
    assert output["tools"][0]["name"] == "create_issue"
    assert all(t["defer_loading"] is True for t in output["tools"])
    assert any(i.get("type") == "function_call_output" for i in bodies[2]["input"])


def test_a_conversation_moving_to_a_model_without_native_search_keeps_its_tools(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(calls=[FakeCall(TOOL_SEARCH, '{"query": "issue"}', id="s1", search=True)]),
        FakeReply(text="Loaded."),
        FakeReply(text="Older model here."),
    )
    models = connect(fake, "openai")
    toolset = Toolset(_github())
    install_tool_search(toolset)
    history: list[Message] = [SYSTEM]

    async def scenario() -> None:
        await AgentLoop(models.client("openai"), toolset).reply(history, "Issues")
        await AgentLoop(models.client("openai", "gpt-5"), toolset).reply(history, "Again")

    run(models, scenario)

    last = fake.bodies(RESPONSES)[-1]
    assert last["model"] == "gpt-5"
    names = [t["name"] for t in last["tools"]]
    assert names[0] == TOOL_SEARCH
    assert "create_issue" in names
    assert all(t["type"] == "function" for t in last["tools"])
    kinds = [i.get("type") for i in last["input"]]
    assert "tool_search_call" not in kinds
    assert "function_call_output" in kinds


# -- the gate ------------------------------------------------------------------------------------


def test_every_call_passes_the_gate_a_loaded_one_too(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(
        FakeReply(calls=[FakeCall(TOOL_SEARCH, '{"query": "merge pull request"}', id="s1")]),
        FakeReply(calls=[FakeCall("merge_pull_request", '{"title": "#4"}', id="m1")]),
        FakeReply(text="Not merged."),
    )
    models = connect(fake, "openrouter")
    merged: list[str] = []

    async def merge(arguments: dict[str, Any]) -> str:
        merged.append(json.dumps(arguments))
        return "merged"

    toolset = Toolset(
        [Tool("merge_pull_request", "Merge a pull request.", {}, merge, deferred=True)]
    )
    install_tool_search(toolset)
    seen: list[tuple[str, dict[str, Any]]] = []

    async def gate(tool: Tool, arguments: dict[str, Any]) -> str | None:
        seen.append((tool.name, arguments))
        return "[Atlas: the user said no.]" if tool.name == "merge_pull_request" else None

    async def scenario() -> Any:
        return await AgentLoop(models.client("openrouter"), toolset, gate=gate).reply(
            [SYSTEM], "Merge #4"
        )

    ended = run(models, scenario)

    assert ended.text == "Not merged."
    assert merged == []
    assert seen == [
        (TOOL_SEARCH, {"query": "merge pull request"}),
        ("merge_pull_request", {"title": "#4"}),
    ]
    sent = fake.bodies(CHAT)[2]["messages"]
    assert sent[-1]["content"] == "[Atlas: the user said no.]"


def test_an_unknown_tool_points_to_the_search(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    fake.reply(FakeReply(calls=[FakeCall("create_issues", "{}", id="x")]), FakeReply(text="Ok."))
    models = connect(fake, "openrouter")
    toolset = Toolset([_tool("read_file", "Read a file."), *_github()])
    install_tool_search(toolset)

    async def scenario() -> Any:
        return await AgentLoop(models.client("openrouter"), toolset).reply([SYSTEM], "Go")

    run(models, scenario)

    said = fake.bodies(CHAT)[1]["messages"][-1]["content"]
    assert "The tools are: read_file, tool_search." in said
    assert "Find others with tool_search." in said


def test_a_deferred_tool_called_before_it_is_loaded_does_not_run(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    """A deferred name called directly is refused, and the model is asked for its tool search
    first: it never saw the schema. Once loaded, the same call runs."""
    fake.reply(
        FakeReply(calls=[FakeCall("merge_pull_request", '{"title": "#4"}', id="m0")]),
        FakeReply(calls=[FakeCall(TOOL_SEARCH, '{"query": "merge_pull_request"}', id="s1")]),
        FakeReply(calls=[FakeCall("merge_pull_request", '{"title": "#4"}', id="m1")]),
        FakeReply(text="Merged."),
    )
    models = connect(fake, "openrouter")
    merged: list[str] = []

    async def merge(arguments: dict[str, Any]) -> str:
        merged.append(json.dumps(arguments))
        return "merged"

    toolset = Toolset(
        [Tool("merge_pull_request", "Merge a pull request.", {}, merge, deferred=True)]
    )
    install_tool_search(toolset)

    async def scenario() -> Any:
        return await AgentLoop(models.client("openrouter"), toolset).reply([SYSTEM], "Merge #4")

    ended = run(models, scenario)

    assert ended.text == "Merged."
    assert merged == ['{"title": "#4"}']
    refused = fake.bodies(CHAT)[1]["messages"][-1]["content"]
    assert "merge_pull_request is not loaded, so it did not run." in refused
    assert 'Load it with tool_search (query "merge_pull_request")' in refused


def test_the_listing_falls_to_names_then_sums_up_the_largest_source_first() -> None:
    big = [
        _searchable(f"tool_{i:03}", "Does one thing for the big server.", "mcp:big")
        for i in range(60)
    ]
    small = [_searchable("ping", "Check the small server.", "mcp:small")]
    about = {"mcp:small": "A small server."}

    full = catalog_listing(small, about, max_tokens=4_000)
    names = catalog_listing([*big, *small], about, max_tokens=420)
    summed = catalog_listing([*big, *small], about, max_tokens=60)

    assert full == "small (1 tools): A small server.\n- ping: Check the small server."
    assert names is not None
    assert "tool_000, tool_001" in names
    assert "Does one thing" not in names
    assert summed is not None
    assert summed.startswith("big (60 tools) - names not listed; search for them")
    assert "small (1 tools): A small server.\nping" in summed
    assert catalog_listing([*big, *small], about, max_tokens=5) is None
    assert listing_budget(None) == 4_000
    assert listing_budget(40_000) == 2_000
