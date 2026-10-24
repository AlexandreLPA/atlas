"""Anthropic's Messages API (step 3b): the wire that keeps a model's reasoning across tool calls
and caches only where the request marks it. Pure, like `atlas.models.chat` and
`atlas.models.responses`: it builds the body and reads the events.

On Atlas's own message types, with the facts of Anthropic's documentation on 13 and 14/10/2026
(docs/ARCHITECTURE.md, Anthropic):

- **The reasoning goes back, sealed.** A reply's blocks are kept in its provider fields in the
  order they came (reasoning, text, call, reasoning...; rebuilt in another order the signature
  breaks), whitelisted field by field (the SDK's output-only fields are a 400 as input), and go
  back only to the model that wrote them.
- **A thinking block is valid only while everything before it is unchanged**: the system prompt,
  the tools and every earlier message, byte for byte; on an account made after 31/08/2026 an
  edited past is a 400. Atlas edits its past when it compacts and when the instructions change, so
  each reply keeps a fingerprint of the request it answered (`prefix`), and a block whose
  fingerprint no longer matches goes, with every later one: removing reasoning from the end is
  allowed.
- **Old tool results are cleared by Anthropic's server** (`clear_tool_uses_20250919`), which never
  invalidates the reasoning on the current models; Atlas's own clearing would, so the loop leaves
  it to the server. The same rule as the loop's (100,000 tokens, or half a smaller window; the 3
  latest results kept), with at least 20,000 tokens cleared each time, since each clearing
  rewrites the cache from there on. The tool search's results are never cleared: they load tools.
- **Four cache points**: the system prompt, the last tool that is not deferred, and the ends of the
  last two completed rounds (never a round still open). An hour when a person paces the
  conversation, five minutes otherwise.
- **Deferred tools by reference**: every deferred tool goes on every call with `defer_loading`, and
  the tool search's result names the ones it found in `tool_reference` blocks, which land at the end
  of the context: the tool list never changes, so neither the cache nor the reasoning breaks.
- **The conversion's rules**: consecutive results become one user message, and a message the user
  sent mid-turn follows them in it; same-role messages join by blocks, never by joining text; a
  blank text block never goes (400 anywhere); a history that starts with the assistant gets a user
  turn first; a tool id is sanitized to Anthropic's characters; a top-level `oneOf`/`anyOf`/`allOf`
  in a schema and a repeated tool name are 400s; `image/jpg` is `image/jpeg`, and an image type
  Anthropic does not read becomes a note.
- **The stream**: a reply without `message_stop` did not finish and is not kept; a call whose input
  did not arrive whole never runs.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from atlas.models.errors import (
    ModelOutOfCreditError,
    ModelRateLimitError,
    ModelRequestError,
    ModelsError,
    ModelUnavailableError,
)
from atlas.models.model import (
    JSON,
    NO_IMAGES,
    NO_USER_IMAGES,
    TOOL_SEARCH,
    AssistantMessage,
    Image,
    JSONObject,
    Message,
    ModelInfo,
    ModelRequest,
    Provider,
    ReasoningDelta,
    SystemMessage,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolSpec,
    Usage,
    UserMessage,
    WireResult,
    make_usage,
    replays_to,
    sees_images,
)

#: The beta that lets the server clear old tool results (`context_management`).
CLEARING_BETA = "context-management-2025-06-27"
#: The loop's clearing rule (atlas.agent's CLEAR_AT_TOKENS and KEEP_RECENT_RESULTS), sent to the
#: server instead of applied here.
CLEAR_AT_TOKENS = 100_000
KEEP_RESULTS = 3
#: The least a clearing removes, so each rewrite of the cache is worth what it cleared
#: (Anthropic's advice); Atlas's figure, to measure in the real proof.
CLEAR_AT_LEAST = 20_000
#: The output ceiling when neither the request nor the catalog names one: the current models' own
#: (future models are unlikely to have less). Anthropic requires one.
DEFAULT_MAX_TOKENS = 128_000
#: What stands in for an empty block, which Anthropic refuses anywhere.
EMPTY = "(empty)"
EMPTY_MESSAGE = "(empty message)"
NO_OUTPUT = "(no output)"
_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_TOOL_ID = re.compile(r"[^a-zA-Z0-9_-]")
_THINKING = frozenset({"thinking", "redacted_thinking"})
_SCHEMA_UNIONS = ("oneOf", "anyOf", "allOf")


@dataclass(frozen=True, slots=True)
class MessagesCall:
    """A request ready to send, and the fingerprint of everything it carries, which its reply
    keeps (`prefix`) so a later request can tell whether the past it reasoned on is unchanged."""

    body: dict[str, Any]
    prefix: str
    #: The betas the request needs, sent as a header.
    betas: tuple[str, ...] = ()


def messages_request(
    provider: Provider,
    model: str,
    request: ModelRequest,
    info: ModelInfo | None,
    effort: str | None,
) -> MessagesCall:
    """The body of a streamed Messages API call."""
    tools, by_reference = _tools(request)
    system = _system(request.messages)
    messages = _Conversion(provider, model, sees_images(info), by_reference, system, tools).convert(
        request.messages
    )
    prefix = fingerprint(system, tools, messages)
    marker: JSONObject = (
        {"type": "ephemeral", "ttl": "1h"} if request.long_cache else {"type": "ephemeral"}
    )
    body: dict[str, Any] = {
        "model": model,
        "max_tokens": _max_tokens(provider, request, info),
        "messages": _marked(messages, marker),
        "stream": True,
    }
    if system.strip():
        body["system"] = [{"type": "text", "text": system, "cache_control": marker}]
    if tools:
        body["tools"] = _marked_tools(tools, marker)
    if not request.reasoning:
        body["thinking"] = {"type": "disabled"}
    elif info is None or info.reasoning is not False:
        # On the current models the readable reasoning is empty unless asked for; it is billed
        # whole either way.
        body["thinking"] = {"type": "adaptive", "display": "summarized"}
    if effort is not None:
        body["output_config"] = {"effort": effort}
    betas: tuple[str, ...] = ()
    if any(not tool.get("defer_loading") for tool in tools):
        body["context_management"] = {"edits": [_clearing(info)]}
        betas = (CLEARING_BETA,)
    return MessagesCall(body, prefix, betas)


def _system(messages: Sequence[Message]) -> str:
    """The first message's text when it is the system prompt; a later system message goes as the
    user's text instead, since editing the system prompt would end every kept reasoning."""
    first = messages[0] if messages else None
    return first.text if isinstance(first, SystemMessage) else ""


def _max_tokens(provider: Provider, request: ModelRequest, info: ModelInfo | None) -> int:
    """The output the call may spend: what the request asks (a compaction's summary, the guardian),
    else the provider's ceiling (`max_output`, 32,000 for Anthropic), never more than the model
    takes. Anthropic requires one, and reasoning counts inside it."""
    limit = None if info is None else info.max_output_tokens
    limit = limit if limit is not None and limit > 0 else DEFAULT_MAX_TOKENS
    asked = request.max_output_tokens
    if asked is not None and asked > 0:
        return min(asked, limit)
    if provider.max_output is not None and provider.max_output > 0:
        return min(provider.max_output, limit)
    return limit


def _clearing(info: ModelInfo | None) -> JSONObject:
    window = None if info is None else (info.max_input_tokens or info.context_window)
    clear_at = CLEAR_AT_TOKENS if window is None else min(CLEAR_AT_TOKENS, window // 2)
    return {
        "type": "clear_tool_uses_20250919",
        "trigger": {"type": "input_tokens", "value": clear_at},
        "keep": {"type": "tool_uses", "value": KEEP_RESULTS},
        "clear_at_least": {"type": "input_tokens", "value": CLEAR_AT_LEAST},
        "exclude_tools": [TOOL_SEARCH],
    }


# -- tools ---------------------------------------------------------------------------------------


def _tools(request: ModelRequest) -> tuple[list[JSONObject], bool]:
    """The tool list, and whether deferred tools travel by reference: every deferred tool on every
    call with `defer_loading`, when the caller gave them and offers the tool search (at least one
    tool must stay loaded, and the search is it). Otherwise the loaded ones join the list."""
    offered = {tool.name for tool in request.tools}
    by_reference = bool(request.deferred_tools) and TOOL_SEARCH in offered
    seen: set[str] = set()
    tools: list[JSONObject] = []
    later = request.deferred_tools if by_reference else request.loaded_tools
    for tool, deferred in [
        *((tool, False) for tool in request.tools),
        *((tool, by_reference) for tool in later),
    ]:
        if tool.name in seen:
            continue  # a repeated name is a 400
        seen.add(tool.name)
        tools.append(_tool(tool, deferred=deferred))
    return tools, by_reference


def _tool(tool: ToolSpec, *, deferred: bool) -> JSONObject:
    definition: JSONObject = {
        "name": tool.name,
        "description": tool.description,
        "input_schema": _input_schema(tool.parameters),
    }
    if deferred:
        definition["defer_loading"] = True
    return definition


def _input_schema(parameters: JSONObject) -> JSONObject:
    """A schema Anthropic takes: a top-level `oneOf`, `anyOf` or `allOf` is a 400, so it goes, and
    an object gets its `properties` even when empty."""
    schema: JSONObject = {
        key: value for key, value in parameters.items() if key not in _SCHEMA_UNIONS
    }
    schema.setdefault("type", "object")
    if schema.get("type") == "object" and not isinstance(schema.get("properties"), dict):
        schema["properties"] = {}
    return schema


def _marked_tools(tools: list[JSONObject], marker: JSONObject) -> list[JSONObject]:
    """The cache point on the last tool that is not deferred: a deferred one with `cache_control`
    is a 400."""
    marked = [dict(tool) for tool in tools]
    for tool in reversed(marked):
        if not tool.get("defer_loading"):
            tool["cache_control"] = dict(marker)
            break
    return marked


# -- the conversation ----------------------------------------------------------------------------


def tool_id(call_id: str) -> str:
    """A call id as Anthropic takes it, `[a-zA-Z0-9_-]`, never empty; the same for the call and
    its result, so another provider's ids still pair."""
    return _TOOL_ID.sub("_", call_id) or "tool_0"


class _Conversion:
    """Atlas's messages as Anthropic's, and which kept reasoning still holds."""

    def __init__(
        self,
        provider: Provider,
        model: str,
        sees: bool,
        by_reference: bool,
        system: str,
        tools: list[JSONObject],
    ) -> None:
        self._provider = provider
        self._model = model
        self._sees = sees
        self._by_reference = by_reference
        self._system = system
        self._tools = tools
        self._searches: set[str] = set()
        self._out: list[JSONObject] = []
        #: Once a reasoning block is dropped, every later one goes too.
        self._dropped = False

    def convert(self, messages: Sequence[Message]) -> list[JSONObject]:
        for index, message in enumerate(messages):
            match message:
                case SystemMessage(text=text):
                    if index > 0:
                        self._add("user", [_text(text, EMPTY_MESSAGE)])
                case UserMessage(text=text, images=images) if images and self._sees:
                    blocks = [_text(text, EMPTY_MESSAGE)] if text.strip() else []
                    blocks.extend(cast("JSONObject", _image(image)) for image in images)
                    self._add("user", blocks)
                case UserMessage(text=text, images=images):
                    said = text + NO_USER_IMAGES if images else text
                    self._add("user", [_text(said, EMPTY_MESSAGE)])
                case ToolResult():
                    self._add("user", [self._result(message)])
                case AssistantMessage():
                    self._searches.update(
                        call.id for call in message.tool_calls if call.name == TOOL_SEARCH
                    )
                    self._add("assistant", self._assistant(message))
        return self._out

    def _add(self, role: str, blocks: list[JSONObject]) -> None:
        """Append a message, joining a same-role one by blocks: tool results first, then what the
        user sent meanwhile, as Anthropic wants them. A conversation that would start with the
        assistant (a compaction can leave one) gets a user turn first, as Anthropic requires."""
        if not self._out and role != "user":
            self._out.append({"role": "user", "content": [_text(EMPTY, EMPTY)]})
        last = self._out[-1] if self._out else None
        if last is not None and last["role"] == role:
            cast("list[JSON]", last["content"]).extend(blocks)
        else:
            self._out.append({"role": role, "content": list[JSON](blocks)})

    def _assistant(self, message: AssistantMessage) -> list[JSONObject]:
        kept = message.provider_fields.get("content")
        if replays_to(message, self._provider, self._model) and isinstance(kept, list):
            thinking = self._thinking_holds(message)
            arguments = {tool_id(call.id): call.arguments for call in message.tool_calls}
            blocks: list[JSONObject] = []
            for entry in kept:
                block = _replayed(entry, arguments)
                if block is None or (block["type"] in _THINKING and not thinking):
                    continue
                blocks.append(block)
            if any(block["type"] not in _THINKING for block in blocks):
                return blocks
        blocks = [] if not message.text.strip() else [{"type": "text", "text": message.text}]
        blocks.extend(
            {
                "type": "tool_use",
                "id": tool_id(call.id),
                "name": call.name,
                "input": _input(call.arguments),
            }
            for call in message.tool_calls
        )
        return blocks or [_text(EMPTY, EMPTY)]

    def _thinking_holds(self, message: AssistantMessage) -> bool:
        """Whether this reply's reasoning may go back: nothing before it changed since it was
        written, and no earlier reasoning was dropped."""
        if self._dropped:
            return False
        last = self._out[-1] if self._out else None
        holds = (
            last is not None
            and last["role"] == "user"
            and message.provider_fields.get("prefix")
            == fingerprint(self._system, self._tools, self._out)
        )
        if not holds:
            self._dropped = True
        return holds

    def _result(self, result: ToolResult) -> JSONObject:
        if self._by_reference and result.call_id in self._searches:
            deferred = {
                cast("str", tool["name"]) for tool in self._tools if tool.get("defer_loading")
            }
            references: list[JSON] = [
                {"type": "tool_reference", "tool_name": name}
                for name in result.loaded
                if name in deferred
            ]
            if references:
                # The references alone: Anthropic refuses them beside any other content ("Tool
                # definitions/code execution functions cannot be mixed with other content", found
                # by the real proof on 14/10/2026), and every later call of the conversation with
                # them. The search's text only repeats what the definitions say.
                return {
                    "type": "tool_result",
                    "tool_use_id": tool_id(result.call_id),
                    "content": references,
                }
        content: list[JSON] = []
        text = result.text + (NO_IMAGES if result.images and not self._sees else "")
        if text.strip():
            content.append({"type": "text", "text": text})
        if self._sees:
            content.extend(_image(image) for image in result.images)
        if not content:
            content.append({"type": "text", "text": NO_OUTPUT})
        return {"type": "tool_result", "tool_use_id": tool_id(result.call_id), "content": content}


def _text(text: str, empty: str) -> JSONObject:
    return {"type": "text", "text": text if text.strip() else empty}


def _image(image: Image) -> JSON:
    media_type = "image/jpeg" if image.media_type.lower() == "image/jpg" else image.media_type
    if media_type not in _IMAGE_TYPES:
        # Once in the history it would be a 400 on every later call.
        return {
            "type": "text",
            "text": f"[Atlas: an image in {media_type}, which this model does not read.]",
        }
    data = image.data_url().partition(",")[2]
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _input(arguments: str) -> JSON:
    try:
        value: object = json.loads(arguments or "{}")
    except ValueError:
        return {}
    return cast("JSON", value) if isinstance(value, dict) else {}


def _replayed(entry: JSON, arguments: Mapping[str, str]) -> JSONObject | None:
    """A kept block as input, whitelisted by type: the SDK's output-only fields are a 400 there.
    A call's input comes from the call as Atlas keeps it."""
    if not isinstance(entry, dict):
        return None
    match entry.get("type"):
        case "text":
            text = entry.get("text")
            if not isinstance(text, str) or not text.strip():
                return None
            block: JSONObject = {"type": "text", "text": text}
            citations = entry.get("citations")
            if isinstance(citations, list) and citations:
                block["citations"] = citations
            return block
        case "thinking":
            block = {"type": "thinking", "thinking": entry.get("thinking") or ""}
            if entry.get("signature"):
                block["signature"] = entry["signature"]
            return block
        case "redacted_thinking":
            data = entry.get("data")
            return {"type": "redacted_thinking", "data": data} if data else None
        case "tool_use":
            identifier = tool_id(str(entry.get("id") or ""))
            given = arguments.get(identifier)
            return {
                "type": "tool_use",
                "id": identifier,
                "name": entry.get("name") or "",
                "input": _input(given) if given is not None else entry.get("input") or {},
            }
        case _:
            return None


def _marked(messages: list[JSONObject], marker: JSONObject) -> list[JSONObject]:
    """The conversation with the cache points on the ends of the last two completed rounds: a user
    message carrying results, or the last message. Copies: the conversion's blocks stay unmarked."""
    ends = [
        index
        for index, message in enumerate(messages)
        if message["role"] == "user"
        and (
            index == len(messages) - 1
            or any(_kind(block) == "tool_result" for block in _blocks(message))
        )
    ][-2:]
    marked = list(messages)
    for index in ends:
        blocks = [dict(cast("JSONObject", block)) for block in _blocks(messages[index])]
        if blocks:
            blocks[-1]["cache_control"] = dict(marker)
        marked[index] = {**messages[index], "content": list[JSON](blocks)}
    return marked


def _blocks(message: JSONObject) -> list[JSON]:
    content = message.get("content")
    return cast("list[JSON]", content) if isinstance(content, list) else []


def _kind(block: JSON) -> JSON:
    return block.get("type") if isinstance(block, dict) else None


def fingerprint(system: str, tools: Sequence[JSONObject], messages: Sequence[JSONObject]) -> str:
    """What a request carried, as one hash: the system prompt, the tools and the messages, before
    any cache point (moving those changes nothing for the reasoning)."""
    document = json.dumps(
        {"system": system, "tools": list(tools), "messages": list(messages)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(document.encode()).hexdigest()


# -- reading the stream --------------------------------------------------------------------------


@dataclass(slots=True)
class _Block:
    kind: str
    fields: dict[str, Any] = field(default_factory=dict[str, Any])
    text: list[str] = field(default_factory=list[str])
    signature: list[str] = field(default_factory=list[str])
    json: list[str] = field(default_factory=list[str])


class MessagesReader:
    """Reads streamed Messages API events, given as plain dictionaries: the deltas as they arrive,
    and the reply once `message_stop` closed it."""

    def __init__(self, provider_name: str) -> None:
        self._name = provider_name
        self._blocks: dict[int, _Block] = {}
        self._usage: dict[str, Any] = {}
        self._stop_reason: str | None = None
        self._model: str | None = None
        self._stopped = False

    def read(self, event: Mapping[str, Any]) -> list[TextDelta | ReasoningDelta]:
        kind = event.get("type")
        if kind == "message_start":
            message = _mapping(event.get("message"))
            model = message.get("model")
            self._model = model if isinstance(model, str) else None
            self._usage.update(_mapping(message.get("usage")))
        elif kind == "content_block_start":
            block = dict(_mapping(event.get("content_block")))
            index = _count(event.get("index"))
            started = _Block(str(block.get("type") or ""), block)
            if started.kind == "text" and isinstance(block.get("text"), str):
                started.text.append(block["text"])
            elif started.kind == "thinking" and isinstance(block.get("thinking"), str):
                started.text.append(block["thinking"])
            self._blocks[index] = started
        elif kind == "content_block_delta":
            return self._delta(_count(event.get("index")), _mapping(event.get("delta")))
        elif kind == "message_delta":
            delta = _mapping(event.get("delta"))
            reason = delta.get("stop_reason")
            if isinstance(reason, str):
                self._stop_reason = reason
            # The delta's usage is cumulative: its numbers replace the start's.
            self._usage.update(
                {
                    key: value
                    for key, value in _mapping(event.get("usage")).items()
                    if value is not None
                }
            )
        elif kind == "message_stop":
            self._stopped = True
        elif kind == "error":
            raise _failure(self._name, _mapping(event.get("error")))
        return []

    def _delta(self, index: int, delta: Mapping[str, Any]) -> list[TextDelta | ReasoningDelta]:
        block = self._blocks.get(index)
        if block is None:
            return []
        match delta.get("type"):
            case "text_delta" if isinstance(delta.get("text"), str):
                block.text.append(delta["text"])
                return [TextDelta(delta["text"])] if delta["text"] else []
            case "thinking_delta" if isinstance(delta.get("thinking"), str):
                block.text.append(delta["thinking"])
                return [ReasoningDelta(delta["thinking"])] if delta["thinking"] else []
            case "signature_delta" if isinstance(delta.get("signature"), str):
                block.signature.append(delta["signature"])
            case "input_json_delta" if isinstance(delta.get("partial_json"), str):
                block.json.append(delta["partial_json"])
            case "citations_delta":
                citation = delta.get("citation")
                if citation is not None:
                    block.fields.setdefault("citations", [])
                    if isinstance(block.fields["citations"], list):
                        cast("list[Any]", block.fields["citations"]).append(citation)
            case _:
                pass
        return []

    def result(self) -> WireResult:
        if not self._stopped:
            raise ModelUnavailableError(
                f"{self._name} closed the reply before it finished. Nothing of it is kept; "
                "try again."
            )
        kept: list[JSON] = []
        text: list[str] = []
        reasoning: list[str] = []
        calls: list[ToolCall] = []
        for _, block in sorted(self._blocks.items()):
            match block.kind:
                case "text":
                    joined = "".join(block.text)
                    text.append(joined)
                    entry: JSONObject = {"type": "text", "text": joined}
                    citations = block.fields.get("citations")
                    if isinstance(citations, list) and citations:
                        entry["citations"] = cast("list[JSON]", citations)
                    kept.append(entry)
                case "thinking":
                    joined = "".join(block.text)
                    reasoning.append(joined)
                    signature = "".join(block.signature) or block.fields.get("signature") or ""
                    kept.append({"type": "thinking", "thinking": joined, "signature": signature})
                case "redacted_thinking":
                    data = block.fields.get("data")
                    if isinstance(data, str) and data:
                        kept.append({"type": "redacted_thinking", "data": data})
                case "tool_use":
                    call = _call(block)
                    if call is None:
                        continue  # its input did not arrive whole: it never runs
                    calls.append(call)
                    kept.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": cast("JSON", json.loads(call.arguments)),
                        }
                    )
                case _:
                    pass
        return WireResult(
            text="".join(text),
            reasoning="\n\n".join(part for part in reasoning if part),
            tool_calls=tuple(calls),
            provider_fields={"content": kept},
            stop_reason=self._stop_reason,
            usage=messages_usage(self._usage) if self._usage else None,
            reported_cost=None,
            served_model=self._model,
        )


def _call(block: _Block) -> ToolCall | None:
    raw = "".join(block.json)
    if raw:
        try:
            value: object = json.loads(raw)
        except ValueError:
            return None
    else:
        value = block.fields.get("input") or {}
    if not isinstance(value, dict):
        return None
    return ToolCall(
        id=str(block.fields.get("id") or ""),
        name=str(block.fields.get("name") or ""),
        arguments=json.dumps(value, ensure_ascii=False),
    )


def messages_usage(raw: Mapping[str, Any]) -> Usage:
    """Anthropic's usage in Atlas's terms. Its `input_tokens` is only what followed the last cache
    point; the cache it read and wrote are counted apart, and Atlas's input includes both."""
    created = _mapping(raw.get("cache_creation"))
    cache_read = _count(raw.get("cache_read_input_tokens"))
    cache_write = _count(raw.get("cache_creation_input_tokens"))
    return make_usage(
        input_tokens=_count(raw.get("input_tokens")) + cache_read + cache_write,
        output_tokens=_count(raw.get("output_tokens")),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        cache_write_long_tokens=_count(created.get("ephemeral_1h_input_tokens")),
    )


def _failure(name: str, error: Mapping[str, Any]) -> ModelsError:
    kind = error.get("type")
    message = error.get("message") or "no reason given"
    if kind == "rate_limit_error":
        return ModelRateLimitError(f"{name} is limiting requests: {message}. It passes; try again.")
    if kind == "billing_error":
        return ModelOutOfCreditError(
            f"{name} says the account has no credit left: {message}. Add credit at the provider."
        )
    if kind == "invalid_request_error":
        return ModelRequestError(f"{name} refused the request: {message}.")
    return ModelUnavailableError(f"{name} failed during the reply ({kind}): {message}. Try again.")


def _mapping(value: object) -> Mapping[str, Any]:
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else {}


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)
