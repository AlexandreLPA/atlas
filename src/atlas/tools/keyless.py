"""The keyless web search: `ddgs`, a metasearch over DuckDuckGo, Bing and others that needs no key.

The last rung of web_search, after Tavily and Brave; free, and the first to stop answering. It lives
in its own module because `ddgs` is an extra (`atlas-agent[ddgs]`): nothing in the core imports it,
and `atlas.tools.web` loads this module only when the extra is installed.
"""

import asyncio
from typing import Any, cast

import ddgs
from ddgs.exceptions import DDGSException

from atlas.tools.web import WEB_SECONDS, SearchError, SearchResult


class KeylessSearch:
    name = "the keyless search (ddgs)"
    keyless = True

    async def search(self, query: str, limit: int) -> list[SearchResult]:
        def run() -> list[dict[str, Any]]:
            engine: Any = ddgs.DDGS(timeout=int(WEB_SECONDS))  # pyright: ignore[reportUnknownMemberType]
            return cast("list[dict[str, Any]]", engine.text(query, max_results=limit))

        try:
            found = await asyncio.to_thread(run)
        except DDGSException as error:
            raise SearchError(f"{self.name} failed: {error}") from error
        return [
            SearchResult(str(r.get("title", "")), str(r.get("href", "")), str(r.get("body", "")))
            for r in found
        ]
