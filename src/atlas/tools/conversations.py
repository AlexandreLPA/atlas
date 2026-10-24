"""conversation_search: a search over the conversations Atlas keeps (`atlas.conversations`).

Decided on 09/10/2026 (docs/ARCHITECTURE.md, Memory). One tool, four shapes told apart by their
arguments, and no model in between: every shape returns what was said.

1. **Discovery** (`query`): FTS5, all words required, one result per conversation. The best one
   comes in full (its first and last messages, and the messages around the match) and the others
   with the match alone; `detail: full` gives every one in full. A query that is a conversation's
   exact title finds it first. `after` and `before` bound when a conversation started;
   `exclude_conversation_ids` leaves out the ones already read.
2. **Scroll** (`conversation_id` and `around_message_id`): the messages around one.
3. **Read** (`conversation_id` alone): the whole conversation, or its first 20 and last 10 messages.
4. **Browse** (nothing): the latest conversations.

**Deferred**, found through tool search: it is used when the user asks about the past, not on
every turn, and models find it there as readily as when it is always visible.

**The teaching is in the responses**, so the description stays short: the search syntax comes with
an empty result, the scrolling with a scroll, the way to read further with a result. **Each message
is cut** to what a result needs: 1,200 characters at a conversation's start and end, 4,000 around a
match, 2,000 when reading a whole conversation (one read of 74,000 characters once took a call from
about 50,000 to 89,000 tokens); a scroll shows messages whole.

The user's and the agent's messages are searched, tool output only when asked (it is noise), and the
current conversation only where a compaction archived it.
"""

from dataclasses import dataclass
from typing import Any, cast

from atlas.agent import Tool
from atlas.conversations import (
    MAX_EXCLUDED,
    ConversationInfo,
    ConversationNotFoundError,
    ConversationStore,
    Hit,
    Kind,
    Role,
    StoredMessage,
    fts_query,
    time_bound,
)
from atlas.home import format_utc, utc_now

DEFAULT_RESULTS = 3
MAX_RESULTS = 10
DEFAULT_WINDOW = 5
MAX_WINDOW = 20
#: The read shape: the first 20 and the last 10 messages of a long conversation.
READ_HEAD, READ_TAIL = 20, 10
#: Conversations the browse shape lists.
RECENT = 10
#: First and last messages a full result shows (the bookends).
BOOKENDS = 3
#: How much of a message each shape shows; `None` is whole.
BOOKEND_CHARS = 1_200
WINDOW_CHARS = 4_000
READ_CHARS = 2_000

_DESCRIPTION = (
    "Look back at earlier conversations: an FTS5 search over them, a read of one, or a scroll "
    "inside one. The arguments pick the shape. `query`: discovery, the N conversations that match "
    "best, the first of them shown in full. `conversation_id` with `around_message_id`: scroll, "
    "the messages on both sides of that one. `conversation_id` alone: read the whole "
    "conversation. No arguments: browse the latest conversations. Every result is a stored "
    "message as it was written; no model sums it up. This covers conversation history ONLY: when "
    "the user points at a direct source (a URL, a file, a contact, a live system), look there "
    "first, and never decide that something is 'not found' from history alone. Reach for it when "
    "the user asks about the past: 'what did we do about X', 'where did we leave Y'."
)

_EMPTY = (
    "No matching conversation. By default FTS5 needs every term to appear. To widen the search, "
    "join terms with OR (`alpha OR beta`); quote a phrase to match it exactly, put NOT before a "
    "term to leave it out, or end a word with * to match its start (`deploy*`)."
)
_SCROLL_TIP = (
    "To read more around a compact result, scroll: conversation_search(conversation_id=..., "
    "around_message_id=<its match number>)."
)
_SCROLL_HINT = (
    "Scroll forward: re-call with around_message_id = the LAST message's number; backward: the "
    "FIRST message's number (the boundary message repeats as an orientation marker). Fewer than "
    "`window` messages on a side means you have hit that end of the conversation."
)


@dataclass(frozen=True, slots=True)
class _Opened:
    """A conversation the caller may read."""

    store: ConversationStore
    info: ConversationInfo


def conversation_search(
    store: ConversationStore, current: str | None = None, *, bot: str | None = None
) -> Tool:
    """The tool over `store`; `current` is the conversation it runs in, searched only where a
    compaction archived it. A `bot` finds only its own conversations; the main agent finds every
    agent's (docs/ARCHITECTURE.md, Bots)."""

    async def run(arguments: dict[str, Any]) -> str:
        conversation = arguments.get("conversation_id")
        query = arguments.get("query")
        try:
            if isinstance(conversation, str) and conversation.strip():
                around = arguments.get("around_message_id")
                if isinstance(around, int) and not isinstance(around, bool):
                    window = _number(arguments.get("window"), DEFAULT_WINDOW, 1, MAX_WINDOW)
                    return _scroll(_mine(store, conversation, bot), around, window)
                return _read(_mine(store, conversation, bot))
        except ConversationNotFoundError as error:
            return f"[Atlas: {error}]"
        if isinstance(query, str) and query.strip():
            return _discover(store, query, arguments, current, bot)
        return _browse(store, current, bot)

    return Tool(
        name="conversation_search",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Discovery shape. What to look for in past conversations: "
                    "words, phrases or a boolean expression. Leave it out to browse the latest "
                    "conversations. It is ignored in the scroll shape, when conversation_id and "
                    "around_message_id are both given.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Discovery shape only. How many conversations to return, "
                    f"{DEFAULT_RESULTS} by default and {MAX_RESULTS} at most. Ask for 5-10 when "
                    "the subject probably runs across several conversations and you need to "
                    "choose which one to scroll into.",
                },
                "sort": {
                    "type": "string",
                    "enum": ["newest", "oldest"],
                    "description": "Discovery shape only. Tilts the FTS5 ranking by time. Without "
                    "it, results rank by relevance alone (open-ended recall). A question like "
                    '"where did we leave X" wants \'newest\'; one like "how did X start" wants '
                    "'oldest'.",
                },
                "detail": {
                    "type": "string",
                    "enum": ["adaptive", "full"],
                    "description": "Discovery shape only. 'adaptive', the default, shows the "
                    "best-ranked result in full and, for each one ranked below it, the single "
                    "message that matched. 'full' gives every result its opening and closing "
                    "messages and the whole window around its match.",
                },
                "after": {
                    "type": "string",
                    "description": "Discovery shape only. Conversations that started at or after "
                    "this moment. An ISO date or date and time (2026-06-01, for example), or a "
                    "duration back from now (7d, 24h, 2w: in the last N). Set it only when the "
                    "user gives a time frame; sort tilts the ranking and bounds nothing.",
                },
                "before": {
                    "type": "string",
                    "description": "Discovery shape only. Conversations that started strictly "
                    "before this moment. An ISO date or date and time (a bare date means midnight "
                    "UTC on that day), or a duration back from now (7d: more than a week ago). Set "
                    "it only when the user gives a time frame.",
                },
                "exclude_conversation_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Discovery shape only. Ids of the conversations already looked "
                    "at in this task. They are left out, so the next query finds something new "
                    f"instead of the same hit again. At most {MAX_EXCLUDED}.",
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Scroll or read shape. The conversation to read inside, as a "
                    "discovery or browse result shows it.",
                },
                "around_message_id": {
                    "type": "integer",
                    "description": "Scroll shape. The message the window is centred on: a match's "
                    "number from a discovery result, or any number seen in an earlier window.",
                },
                "window": {
                    "type": "integer",
                    "description": "Scroll shape only. How many messages to show before and after "
                    "the anchor, which is always shown as well. Kept between 1 and "
                    f"{MAX_WINDOW}; {DEFAULT_WINDOW} by default.",
                },
                "role_filter": {
                    "type": "string",
                    "description": "Optional. The roles to search, separated by commas. Discovery "
                    "uses 'user,assistant' unless told otherwise, since tool output is mostly "
                    "noise. 'user,assistant,tool' adds tool output (when debugging how a tool "
                    "behaved); 'tool' searches tool output alone.",
                },
            },
            "additionalProperties": False,
        },
        run=run,
        read_only=True,
        deferred=True,
    )


def _mine(store: ConversationStore, conversation: str, bot: str | None) -> _Opened:
    """A conversation the caller may read. Raises `ConversationNotFoundError` for one that is
    not there, and for another agent's when a bot asks."""
    info = store.info(conversation.strip())
    if bot is not None and info.bot != bot:
        raise ConversationNotFoundError(
            f"There is no conversation {info.id!r} among yours. Copy the id exactly as a search "
            "or the list of recent conversations shows it."
        )
    return _Opened(store, info)


def _discover(
    store: ConversationStore,
    text: str,
    arguments: dict[str, Any],
    current: str | None,
    bot: str | None,
) -> str:
    query = fts_query(text)
    if query is None:
        return "[Atlas: the query has no words to search. Use words, not only symbols.]"
    bounds: dict[str, str | None] = {}
    for name in ("after", "before"):
        value = arguments.get(name)
        if isinstance(value, str) and value.strip():
            moment = time_bound(value, utc_now())
            if moment is None:
                return (
                    f"[Atlas: {name}={value!r} is not a time bound. Use an ISO date or date and "
                    "time (2026-06-01), or a duration such as 7d, 24h or 2w.]"
                )
            bounds[name] = format_utc(moment)
        else:
            bounds[name] = None
    filters: dict[str, Any] = {
        "roles": _roles(arguments.get("role_filter")),
        "current": current,
        "excluded": _excluded(arguments.get("exclude_conversation_ids")),
        "bot": bot,
        **bounds,
    }
    limit = _number(arguments.get("limit"), DEFAULT_RESULTS, 1, MAX_RESULTS)
    sort = arguments.get("sort")
    newest_first = {"newest": True, "oldest": False}.get(sort) if isinstance(sort, str) else None
    titled = store.titled(
        text,
        current=current,
        excluded=filters["excluded"],
        after=bounds["after"],
        before=bounds["before"],
        bot=bot,
    )
    hits = store.search(query, limit=limit, newest_first=newest_first, **filters)
    if titled is not None:
        hits = [hit for hit in hits if hit.conversation.id != titled.id][: limit - 1]
    if not hits and titled is None:
        missing = store.missing_terms(query, **filters)
        absent = f" No conversation contains {', '.join(missing)}; leave it out." if missing else ""
        return f"{_EMPTY}{absent}"
    full = arguments.get("detail") == "full"
    parts = [f"{len(hits) + (titled is not None)} conversation(s) found for {query}."]
    if titled is not None:
        parts.append(_by_title(store, titled))
    for rank, hit in enumerate(hits):
        whole = full or (rank == 0 and titled is None)
        parts.append(_full(store, hit) if whole else _compact(hit))
    parts.append(_SCROLL_TIP)
    return "\n\n".join(parts)


def _by_title(store: ConversationStore, info: ConversationInfo) -> str:
    said = _said(store.messages(info.id))
    lines = [_header(info), f"Match: the conversation's title is {info.title!r}."]
    lines += ["First messages:", *(_shown(m, BOOKEND_CHARS) for m in said[:BOOKENDS])]
    if len(said) > BOOKENDS:
        lines += ["Last messages:", *(_shown(m, BOOKEND_CHARS) for m in said[-BOOKENDS:])]
    return "\n".join(lines)


def _full(store: ConversationStore, hit: Hit) -> str:
    _, window = store.around(hit.match.number, DEFAULT_WINDOW)
    said = _said(store.messages(hit.conversation.id))
    shown = {m.number for m in window}
    first = [m for m in said[:BOOKENDS] if m.number not in shown]
    last = [m for m in said[-BOOKENDS:] if m.number not in shown and m not in first]
    lines = [_header(hit.conversation), f"Match: {hit.snippet}"]
    if first:
        lines += ["First messages:", *(_shown(m, BOOKEND_CHARS) for m in first)]
    lines += [f"Around the match (#{hit.match.number}):"]
    lines += [_shown(m, WINDOW_CHARS) for m in window]
    if last:
        lines += ["Last messages:", *(_shown(m, BOOKEND_CHARS) for m in last)]
    return "\n".join(lines)


def _compact(hit: Hit) -> str:
    return "\n".join(
        [
            _header(hit.conversation),
            f"Match #{hit.match.number} ({hit.match.role}, {_when(hit.match.created_at)}): "
            f"{hit.snippet}",
        ]
    )


def _scroll(opened: _Opened, around: int, window: int) -> str:
    store, info = opened.store, opened.info
    anchor, messages = store.around(around, window)
    if anchor is None or anchor.conversation_id != info.id:
        return f"[Atlas: message #{around} is not in conversation {info.id}.]"
    return "\n".join([_header(info), *(_shown(m, None) for m in messages), _SCROLL_HINT])


def _read(opened: _Opened) -> str:
    info = opened.info
    messages = opened.store.messages(info.id)
    lines = [_header(info)]
    if len(messages) > READ_HEAD + READ_TAIL:
        lines += [_shown(m, READ_CHARS) for m in messages[:READ_HEAD]]
        lines += [_shown(m, READ_CHARS) for m in messages[-READ_TAIL:]]
        lines.append(
            f"Conversation has {len(messages)} messages; showing first {READ_HEAD} + last "
            f"{READ_TAIL}. Pass around_message_id (any number above) to scroll the middle."
        )
    else:
        lines += [_shown(m, READ_CHARS) for m in messages]
    return "\n".join(lines)


def _browse(store: ConversationStore, current: str | None, bot: str | None) -> str:
    recent = store.recent(RECENT, exclude=current, bot=bot, with_bots=bot is None)
    if not recent:
        return "There are no past conversations yet."
    listed = [
        f"- {info.title}{_whose(info)} — conversation {info.id}, last active "
        f"{_when(info.updated_at)}, "
        f"{info.messages} messages"
        for info in recent
    ]
    return "\n".join(
        [
            f"Showing {len(recent)} most recent conversations:",
            *listed,
            "Pass a query= to search, or conversation_id+around_message_id to scroll.",
        ]
    )


def _whose(info: ConversationInfo) -> str:
    """Which bot a conversation is with, said to the main agent, who reads every agent's."""
    return "" if info.bot is None else f" (with the bot {info.bot})"


def _said(messages: list[StoredMessage]) -> list[StoredMessage]:
    """The user's and the agent's words, without compaction summaries (for the bookends)."""
    return [
        m
        for m in messages
        if m.role in (Role.USER, Role.ASSISTANT) and m.kind is Kind.MESSAGE and m.text.strip()
    ]


def _header(info: ConversationInfo) -> str:
    return (
        f"## {info.title or '(untitled)'}{_whose(info)} — conversation {info.id}, "
        f"{_when(info.started_at)} to {_when(info.updated_at)}, {info.messages} messages"
    )


def _shown(message: StoredMessage, limit: int | None) -> str:
    who = message.role.value
    if message.kind is Kind.SUMMARY:
        who = "summary of earlier messages"
    elif message.role is Role.TOOL:
        who = f"tool {message.tool_name or ''}".strip()
    text = message.text.strip()
    if limit is not None and len(text) > limit:
        text = (
            f"{text[:limit]}… [cut: {len(message.text.strip()):,} chars; scroll to read it whole]"
        )
    if not text and message.tool_name:
        text = f"(called {message.tool_name})"
    archived = "" if message.active else ", archived"
    return f"#{message.number} {who} ({_when(message.created_at)}{archived}): {text}"


def _roles(value: object) -> tuple[Role, ...]:
    if not isinstance(value, str) or not value.strip():
        return (Role.USER, Role.ASSISTANT)
    named = [part.strip().lower() for part in value.split(",")]
    roles = tuple(role for role in (Role.USER, Role.ASSISTANT, Role.TOOL) if role.value in named)
    return roles or (Role.USER, Role.ASSISTANT)


def _excluded(value: object) -> list[str]:
    if isinstance(value, str):
        items: list[object] = [value]
    elif isinstance(value, list):
        items = list(cast("list[object]", value))
    else:
        items = []
    found: list[str] = []
    for item in items:
        if isinstance(item, str) and item.strip() and item.strip() not in found:
            found.append(item.strip())
        if len(found) >= MAX_EXCLUDED:
            break
    return found


def _when(stamp: str) -> str:
    """`2026-10-09T14:03:12.345Z` as `2026-10-09 14:03 UTC`."""
    return f"{stamp[:10]} {stamp[11:16]} UTC"


def _number(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return max(minimum, min(maximum, int(value)))
