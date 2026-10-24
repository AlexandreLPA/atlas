"""Tool search: the always-on tool that finds deferred tools and loads them into the conversation.

Decided on 09/10/2026, from Atlas V3:

- **Few tools always on, the rest found by search.** Every always-on tool is paid on every model
  call: Atlas V3's 52 tools cost about 10,800 tokens per call. Anthropic measured tool search
  lifting Opus 4 from 49% to 74% on tool choice, with 85% fewer tokens spent on definitions.
- **The model searches by intent or by name**, and up to five tools come back, ranked by
  `atlas.tools.search`.
- **A found tool becomes a real tool of the conversation**, called with its own schema, and not
  through a generic `tool_call(name, arguments)` bridge. On OpenAI's Responses API (GPT-5.4 and
  later) the search is the provider's own, and the found tools land at the end of the context so the
  cache holds; elsewhere they join the tool list (`atlas.models`). The loop keeps them loaded while
  the search's result is in the history.
- **The tool exists only when there is something to find**: a tool with nothing to offer is paid
  on every call for nothing (V3 learned it with its delegation tool).
- **It names its sources** (an MCP server, a plugin) in its description, so the model knows a search
  is worth trying.
"""

from collections.abc import Mapping
from typing import Any, cast

from atlas.agent import Tool, ToolOutput, Toolset
from atlas.models import TOOL_SEARCH
from atlas.tools.search import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    Searchable,
    catalog_listing,
    first_sentence,
    listing_budget,
    rank,
    source_label,
    unknown_words,
)

_DESCRIPTION = (
    "Finds tools that are not loaded yet and loads them, so you can call them next. Search with "
    "keywords, not a question: the service's name, an action and an object (\"github create "
    "issue\"), or a tool's exact name. A word no tool contains makes the search find nothing."
)


def install_tool_search(
    toolset: Toolset,
    sources: Mapping[str, str] | None = None,
    window_tokens: int | None = None,
) -> None:
    """Put a fresh tool search in `toolset`, or take it out when there is nothing to find. Call
    it again whenever the deferred tools change, as when an MCP server connects.

    `sources` describes each source in one line (`{"mcp:github": "Issues and pull requests"}`);
    `window_tokens`, the model's window, sizes the listing of the deferred tools.
    """
    toolset.remove(TOOL_SEARCH)
    if toolset.deferred:
        toolset.add(tool_search(toolset, sources or {}, window_tokens))


def tool_search(
    toolset: Toolset, sources: Mapping[str, str], window_tokens: int | None = None
) -> Tool:
    """The tool search over the deferred tools `toolset` holds when it runs. Its description lists
    their names and first sentences, within a budget (`catalog_listing`)."""
    listed = catalog_listing(
        [_searchable(tool) for tool in toolset.deferred], sources, listing_budget(window_tokens)
    )
    description = f"{_DESCRIPTION}\n\nTools to load:\n{listed}" if listed else _DESCRIPTION

    async def run(arguments: dict[str, Any]) -> ToolOutput:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolOutput(f"[Atlas: {TOOL_SEARCH} needs a query: what the tool should do.]")
        limit = _limit(arguments.get("limit"))
        deferred = {tool.name: tool for tool in toolset.deferred}
        searchable = [_searchable(t) for t in deferred.values()]
        found = rank(searchable, query, limit)
        if not found:
            labels = ", ".join(sorted({source_label(t.source) for t in deferred.values()}))
            missing = ", ".join(unknown_words(searchable, query))
            drop = f" No tool has the word(s) {missing}; leave them out." if missing else ""
            return ToolOutput(
                f'No tool matches "{query.strip()}".{drop} Search again with a source\'s name '
                f"({labels}) plus an action, before deciding the tool does not exist."
            )
        listed = "\n".join(
            f"- {tool.name} ({source_label(tool.source)}): {first_sentence(tool.description)}"
            for tool in found
        )
        return ToolOutput(
            f"Loaded {len(found)} tool(s); call them directly:\n{listed}",
            loaded=tuple(tool.name for tool in found),
        )

    return Tool(
        name=TOOL_SEARCH,
        description=description,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What the tool should do, in a few words, or its exact name.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"How many tools to load, 1 to {MAX_LIMIT}. "
                    f"Defaults to {DEFAULT_LIMIT}.",
                },
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        run=run,
        read_only=True,
    )


def _searchable(tool: Tool) -> Searchable:
    return Searchable(tool.name, tool.description, tool.source, tuple(_properties(tool.parameters)))


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return DEFAULT_LIMIT
    return max(1, min(MAX_LIMIT, int(value)))


def _properties(schema: Mapping[str, Any]) -> list[str]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return []
    return [str(name) for name in cast("Mapping[str, Any]", properties)]
