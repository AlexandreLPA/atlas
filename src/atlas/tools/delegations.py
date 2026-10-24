"""delegation_search: the conversation search's four shapes over the delegation record (step 16c),
what the executor instances on the workbench did, Atlas's and the user's.

Decided with the user on 14/10/2026 (ROADMAP.md, Bancada de harnesses): Atlas reads what the
harnesses did when it needs to, never as they work: it keeps a short summary and a pointer and reads
the detail on demand. The shapes are `conversation_search`'s, and the text search reads the record
only: each turn's prompt, what it asked and who answered, its final text and the files it changed.
What the search does not find, Atlas finds by browsing and reading.

1. **Browse** (nothing, or only filters): the instances used last, with their last turn.
2. **Discovery** (`query`): FTS5, all words required, one result per instance; the best one with
   every turn, the others with the matching turn.
3. **Read** (`instance_id`): its record, every turn, and the start and the end of its harness's
   session, normalized by its plugin; when the harness deleted it (Claude Code does after 30
   days), the record and a word that the original is gone.
4. **Scroll** (`instance_id` and `around`): the session's entries around one.

**Deferred**, found through tool search, as conversation_search is. Each text is cut as that
tool cuts it, and a tool's input and output come only when asked (`include_tools`): it is noise.
"""

from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from atlas.agent import Tool
from atlas.conversations import fts_query, time_bound
from atlas.home import format_utc, utc_now
from atlas.jobs import InstanceRecord, InstanceStore, JobsError, TurnHit, TurnRecord
from atlas.plugins import Transcript, TranscriptEntry

DEFAULT_RESULTS = 3
MAX_RESULTS = 10
#: Instances the browse shape lists.
RECENT = 10
DEFAULT_WINDOW = 5
MAX_WINDOW = 20
#: The session's first and last entries a read shows.
READ_HEAD, READ_TAIL = 10, 10
#: How much of a text each shape shows (conversation_search's caps).
TURN_CHARS = 1_200
ENTRY_CHARS = 1_200
SNIPPET_CHARS = 300

#: Reads a session through its executor's plugin: the executor, the session and its folder;
#: `None` when the executor is not installed now.
type SessionReader = Callable[[str, str, str], Awaitable[Transcript | None]]

_DESCRIPTION = (
    "Recall what outside executors (Claude Code and the other harnesses on the workbench) did, "
    "for you or for the user: their prompts, what they asked and who answered, their final text "
    "and the files they changed. Four shapes, picked by args: `query` = discovery (FTS5 over that "
    "record, top result with every turn); `instance_id` alone = read (the record, then the start "
    "and end of the harness's own session); `instance_id` + `around` = scroll the session; no "
    "args = browse the latest. Filters: executor, folder, after, before. No LLM. Use for 'what "
    "did Claude Code do', 'what did it change in X'."
)
_EMPTY = (
    "No matching delegations found. FTS5 ANDs all terms by default — broaden with OR "
    "(`alpha OR beta`), exact-match with quoted phrases, or prefix-match with `deploy*`; or "
    "browse with no query, filtered by executor or folder."
)
_READ_TIP = "To read one whole, with its harness's session: delegation_search(instance_id=...)."
_SCROLL_HINT = (
    "Scroll forward: re-call with around = the LAST entry's number; backward: the FIRST entry's "
    "number. Pass include_tools=true to see the tools' input and output."
)


def delegation_search(store: InstanceStore, read_session: SessionReader) -> Tool:
    """The tool over the delegation record; `read_session` reads a harness's own session."""

    async def run(arguments: dict[str, Any]) -> str:
        instance_id = arguments.get("instance_id")
        tools = arguments.get("include_tools") is True
        if isinstance(instance_id, str) and instance_id.strip():
            record = store.get(instance_id.strip())
            if record is None:
                return (
                    f"[Atlas: There is no delegation {instance_id.strip()!r}. Copy the id "
                    "exactly as a search or the browse shows it.]"
                )
            around = arguments.get("around")
            if isinstance(around, int) and not isinstance(around, bool):
                window = _number(arguments.get("window"), DEFAULT_WINDOW, 1, MAX_WINDOW)
                return await _scroll(record, read_session, around, window, tools)
            return await _read(store, record, read_session, tools)
        filters = _filters(arguments)
        if isinstance(filters, str):
            return filters
        query = arguments.get("query")
        if isinstance(query, str) and query.strip():
            return _discover(store, query, filters, arguments)
        return _browse(store, filters)

    return Tool(
        name="delegation_search",
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Discovery shape. Words to find in the record: prompts, "
                    "questions and answers, final texts, files changed.",
                },
                "limit": {
                    "type": "integer",
                    "description": f"Discovery shape only. Max delegations (default "
                    f"{DEFAULT_RESULTS}, max {MAX_RESULTS}).",
                },
                "executor": {
                    "type": "string",
                    "description": "Only this executor's (`claude-code`).",
                },
                "folder": {
                    "type": "string",
                    "description": "Only those whose folder holds this text (a project's name).",
                },
                "after": {
                    "type": "string",
                    "description": "Used since: an ISO date, or 7d, 24h, 2w for the last N.",
                },
                "before": {
                    "type": "string",
                    "description": "Started before: an ISO date, or 7d for older than a week.",
                },
                "instance_id": {
                    "type": "string",
                    "description": "Read or scroll shape: the delegation's id, as results show it.",
                },
                "around": {
                    "type": "integer",
                    "description": "Scroll shape: the session entry's number to center on.",
                },
                "window": {
                    "type": "integer",
                    "description": f"Scroll shape only. Entries on each side (default "
                    f"{DEFAULT_WINDOW}, max {MAX_WINDOW}).",
                },
                "include_tools": {
                    "type": "boolean",
                    "description": "Read and scroll shapes: also the tools' input and output.",
                },
            },
            "additionalProperties": False,
        },
        run=run,
        read_only=True,
        deferred=True,
    )


def _filters(arguments: dict[str, Any]) -> dict[str, str | None] | str:
    """The filters as the store takes them, or what is wrong with one."""
    found: dict[str, str | None] = {}
    for name in ("executor", "folder"):
        value = arguments.get(name)
        found[name] = value.strip() if isinstance(value, str) and value.strip() else None
    for name in ("after", "before"):
        value = arguments.get(name)
        if isinstance(value, str) and value.strip():
            moment = time_bound(value, utc_now())
            if moment is None:
                return (
                    f"[Atlas: {name}={value!r} is not a time bound. Use an ISO date (2026-06-01), "
                    "or a duration such as 7d, 24h or 2w.]"
                )
            found[name] = format_utc(moment)
        else:
            found[name] = None
    return found


def _discover(
    store: InstanceStore, text: str, filters: dict[str, str | None], arguments: dict[str, Any]
) -> str:
    query = fts_query(text)
    if query is None:
        return "[Atlas: the query has no words to search. Use words, not only symbols.]"
    limit = _number(arguments.get("limit"), DEFAULT_RESULTS, 1, MAX_RESULTS)
    try:
        hits = store.search(query, limit, **filters)
    except JobsError as error:
        return f"[Atlas: {error}]"
    if not hits:
        return _EMPTY
    parts = [f"{len(hits)} delegation(s) found for {query}."]
    for rank, hit in enumerate(hits):
        parts.append(_full(store, hit) if rank == 0 else _compact(hit))
    parts.append(_READ_TIP)
    return "\n\n".join(parts)


def _full(store: InstanceStore, hit: TurnHit) -> str:
    lines = [_header(hit.instance), f"Match (turn {hit.turn.number}): {hit.snippet}"]
    lines += [_turn(turn) for turn in store.turns(hit.instance.id)]
    return "\n".join(lines)


def _compact(hit: TurnHit) -> str:
    return "\n".join(
        [_header(hit.instance), f"Match (turn {hit.turn.number}): {hit.snippet}", _turn(hit.turn)]
    )


def _browse(store: InstanceStore, filters: dict[str, str | None]) -> str:
    recent = store.recent(RECENT, **filters)
    if not recent:
        return "There are no delegations yet: no executor instance has worked."
    lines = [f"Showing {len(recent)} most recent delegations:"]
    for record in recent:
        turns = store.turns(record.id)
        last = turns[-1] if turns else None
        said = f" Last turn: {_cut(last.prompt, SNIPPET_CHARS)!r}" if last is not None else ""
        if last is not None and last.text:
            said += f" → {_cut(last.text, SNIPPET_CHARS)!r}"
        lines.append(f"- {_label(record)}, {len(turns)} turn(s).{said}")
    lines.append("Pass query= to search, or instance_id= to read one.")
    return "\n".join(lines)


async def _read(
    store: InstanceStore, record: InstanceRecord, read_session: SessionReader, tools: bool
) -> str:
    turns = store.turns(record.id)
    lines = [_header(record)]
    lines += [_turn(turn) for turn in turns] or ["No turn yet."]
    session = await _session(record, read_session)
    if isinstance(session, str):
        return "\n".join([*lines, session])
    entries = _numbered(session.entries, tools)
    lines.append(f"Its session ({len(entries)} entries, as {record.title} keeps it):")
    if len(entries) > READ_HEAD + READ_TAIL:
        lines += [_entry(n, e) for n, e in entries[:READ_HEAD]]
        lines.append(
            f"… {len(entries) - READ_HEAD - READ_TAIL} entries more; scroll with around=<number>."
        )
        lines += [_entry(n, e) for n, e in entries[-READ_TAIL:]]
    else:
        lines += [_entry(n, e) for n, e in entries]
    return "\n".join(lines)


async def _scroll(
    record: InstanceRecord, read_session: SessionReader, around: int, window: int, tools: bool
) -> str:
    session = await _session(record, read_session)
    if isinstance(session, str):
        return "\n".join([_header(record), session])
    entries = _numbered(session.entries, tools)
    shown = [(n, e) for n, e in entries if around - window <= n <= around + window]
    if not shown:
        return f"[Atlas: entry #{around} is not in {record.title}'s session.]"
    return "\n".join([_header(record), *(_entry(n, e, None) for n, e in shown), _SCROLL_HINT])


async def _session(record: InstanceRecord, read_session: SessionReader) -> Transcript | str:
    """Its harness's session, or what to say instead."""
    if not record.session_id:
        return "Its harness never started a session."
    try:
        found = await read_session(record.executor, record.session_id, record.folder)
    except Exception as error:  # noqa: BLE001 - a plugin's own code: the record still answers
        return f"Its session could not be read ({type(error).__name__}: {error})."
    if found is None:
        return f"{record.title} is not installed now, so its session cannot be read."
    if found.expired:
        return (
            f"{record.title} deleted this session (it keeps them for a while only); the record "
            "above is what remains."
        )
    return found


def _numbered(entries: Sequence[TranscriptEntry], tools: bool) -> list[tuple[int, TranscriptEntry]]:
    """The entries with their numbers, the tools' left out unless asked for (their numbers kept,
    so a scroll lands where it should)."""
    return [
        (number, entry)
        for number, entry in enumerate(entries, start=1)
        if tools or entry.kind not in ("tool", "result")
    ]


def _header(record: InstanceRecord) -> str:
    return f"## {_label(record)}"


def _label(record: InstanceRecord) -> str:
    whose = "the user's" if record.owner == "user" else "yours (delegated)"
    account = f", on {record.account}" if record.account else ""
    return (
        f"{record.title} in {record.folder}{account} — delegation {record.id}, {whose}, "
        f"{record.state}, {_when(format_utc(record.started_at))} to "
        f"{_when(format_utc(record.updated_at))}"
    )


def _turn(turn: TurnRecord) -> str:
    started = _when(format_utc(turn.started_at))
    lines = [f"Turn {turn.number} ({started}): {_cut(turn.prompt, TURN_CHARS)}"]
    for exchange in turn.exchanges:
        if exchange.kind == "steer":
            lines.append(f"  steered: {_cut(exchange.asked, TURN_CHARS)}")
        else:
            lines.append(
                f"  {exchange.kind}: {_cut(exchange.asked, TURN_CHARS)} → "
                f"{_cut(exchange.answer, TURN_CHARS)} (by {exchange.by.value})"
            )
    if turn.ended_at is None:
        lines.append("  still running")
    else:
        ended = "failed" if turn.failed else "ended"
        lines.append(f"  {ended} ({turn.reason}): {_cut(turn.text, TURN_CHARS)}")
    if turn.files:
        lines.append(f"  files changed: {', '.join(turn.files)}")
    return "\n".join(lines)


def _entry(number: int, entry: TranscriptEntry, limit: int | None = ENTRY_CHARS) -> str:
    who = entry.kind if entry.kind != "text" else entry.by
    if entry.tool:
        who = f"{who} {entry.tool}"
    text = entry.text if limit is None else _cut(entry.text, limit)
    return f"#{number} {who}: {text}"


def _cut(text: str, limit: int) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… [cut: {len(text):,} chars]"


def _when(stamp: str) -> str:
    """`2026-10-09T14:03:12.345Z` as `2026-10-09 14:03 UTC`."""
    return f"{stamp[:10]} {stamp[11:16]} UTC"


def _number(value: object, default: int, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    return max(minimum, min(maximum, int(value)))
