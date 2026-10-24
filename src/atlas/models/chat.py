"""Chat Completions: the wire every OpenAI-compatible provider speaks.

Pure: `chat_request` builds the body the SDK sends, and `ChatReader` reads the streamed chunks
(as plain dictionaries) into deltas and one `WireResult`. The SDK call is in `atlas.models.store`.

**What goes out is a copy.** The stored history keeps every provider field; the copy drops the ones
the destination does not take back (`Provider.replay`) or did not write (`replays_to`). Strict
providers answer HTTP 400 to a field they do not know.

**Streaming always, with usage asked for.** OpenAI sends usage on a stream only when
`stream_options.include_usage` asks for it (Atlas V3 lost the usage of every streamed turn on
non-OpenAI endpoints until it asked). Gemini, Ollama and vLLM honor it; OpenRouter always sends it.

**A stream that ends without a stop reason is a cut reply**, not a short one: the SDK ends quietly
when the connection closes, so the reader says so.
"""

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from atlas.models.errors import ModelUnavailableError
from atlas.models.model import (
    JSON,
    NO_IMAGES,
    NO_USER_IMAGES,
    AssistantMessage,
    EffortParameter,
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
from atlas.models.think_scrubber import ThinkScrubber

#: Keys of a streamed delta that Atlas reads itself. Any other key with a value is a provider field.
_DELTA_KEYS = frozenset(
    {
        "role",
        "content",
        "tool_calls",
        "function_call",
        "refusal",
        "audio",
        "reasoning_content",
        "reasoning",
        "reasoning_details",
    }
)
_TOOL_CALL_KEYS = frozenset({"index", "id", "type", "function"})
#: Streamed reasoning details of these types are fragments of one block, to be joined.
_JOINED_DETAILS = {"reasoning.text": "text", "reasoning.summary": "summary"}
_BACKFILLED_DETAIL_KEYS = ("signature", "id", "format", "index")


def chat_request(
    provider: Provider,
    model: str,
    request: ModelRequest,
    info: ModelInfo | None,
    effort: str | None,
) -> dict[str, Any]:
    """The body of a streamed Chat Completions call. A model known to reason is asked for its
    readable reasoning the way its provider wants it (`Provider.ask_reasoning`)."""
    body: dict[str, Any] = {
        "model": model,
        "messages": _messages(request.messages, provider, model, sees_images(info)),
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    # Tools a tool search loaded go after the others: Chat Completions has no place for them at
    # the end of the context, so the cache breaks once per load and holds again after.
    offered = {tool.name for tool in request.tools}
    tools = [*request.tools, *(t for t in request.loaded_tools if t.name not in offered)]
    if tools:
        body["tools"] = [_tool(tool) for tool in tools]
    if effort is not None:
        if provider.effort is EffortParameter.REASONING:
            body["reasoning"] = {"effort": effort}
        elif provider.effort is EffortParameter.THINKING_LEVEL:
            body["extra_body"] = {"google": {"thinking_config": {"thinking_level": effort}}}
        else:
            body["reasoning_effort"] = effort
    if request.max_output_tokens is not None:
        body["max_tokens"] = request.max_output_tokens
    if info is not None and info.reasoning is True:
        _merge(body, provider.ask_reasoning)
    return body


def _merge(into: dict[str, Any], fields: Mapping[str, Any]) -> None:
    """Put `fields` into `into`, joining nested tables rather than replacing them, so the effort
    and the request for readable reasoning share Gemini's one `thinking_config`."""
    for key, value in fields.items():
        current = into.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged = dict(cast("dict[str, Any]", current))
            _merge(merged, cast("Mapping[str, Any]", value))
            into[key] = merged
        else:
            into[key] = value


def _tool(tool: ToolSpec) -> JSONObject:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def _messages(
    messages: tuple[Message, ...], provider: Provider, model: str, sees: bool
) -> list[JSONObject]:
    """The messages, with the images of a round's tool results in a user message after them:
    a tool message takes only text, and nothing may come between a call and its result."""
    out: list[JSONObject] = []
    pending: list[JSON] = []
    for message in messages:
        if pending and not isinstance(message, ToolResult):
            out.append({"role": "user", "content": pending})
            pending = []
        if isinstance(message, ToolResult) and message.images:
            text = message.text
            if sees:
                pending.append(
                    {"type": "text", "text": f"[Atlas: the images of tool call {message.call_id}]"}
                )
                pending.extend(
                    {"type": "image_url", "image_url": {"url": image.data_url()}}
                    for image in message.images
                )
            else:
                text += NO_IMAGES
            out.append({"role": "tool", "tool_call_id": message.call_id, "content": text})
            continue
        if isinstance(message, UserMessage) and message.images:
            out.append(_user(message, sees))
            continue
        out.append(_message(message, provider, model))
    if pending:
        out.append({"role": "user", "content": pending})
    return out


def _user(message: UserMessage, sees: bool) -> JSONObject:
    """A user message with what it attached for the model to see: images as `image_url`, a video
    as `video_url`, for a model that takes video."""
    if not sees:
        return {"role": "user", "content": message.text + NO_USER_IMAGES}
    parts: list[JSON] = [{"type": "text", "text": message.text}] if message.text.strip() else []
    for image in message.images:
        if image.media_type.startswith("video/"):
            parts.append({"type": "video_url", "video_url": {"url": image.data_url()}})
        else:
            parts.append({"type": "image_url", "image_url": {"url": image.data_url()}})
    return {"role": "user", "content": parts}


def _message(message: Message, provider: Provider, model: str) -> JSONObject:
    match message:
        case SystemMessage(text=text):
            return {"role": "system", "content": text}
        case UserMessage(text=text):
            return {"role": "user", "content": text}
        case ToolResult(call_id=call_id, text=text):
            return {"role": "tool", "tool_call_id": call_id, "content": text}
        case AssistantMessage():
            return _assistant(message, provider, model)


def _assistant(message: AssistantMessage, provider: Provider, model: str) -> JSONObject:
    replay = provider.replay if replays_to(message, provider, model) else frozenset[str]()
    wire: JSONObject = {
        "role": "assistant",
        # The OpenAI schema allows no content beside tool calls; an empty answer is "".
        "content": message.text if message.text or not message.tool_calls else None,
    }
    if message.tool_calls:
        calls: list[JSON] = []
        for call in message.tool_calls:
            wire_call: JSONObject = {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments},
            }
            wire_call.update(
                {key: value for key, value in call.provider_fields.items() if key in replay}
            )
            calls.append(wire_call)
        wire["tool_calls"] = calls
    wire.update({key: value for key, value in message.provider_fields.items() if key in replay})
    return wire


# -- reading the stream --------------------------------------------------------------------------


@dataclass
class _CallSlot:
    id: str | None = None
    name: str = ""
    arguments: list[str] = field(default_factory=list[str])
    fields: JSONObject = field(default_factory=dict[str, JSON])


class ChatReader:
    """Reads streamed Chat Completions chunks, given as plain dictionaries.

    Reasoning a model writes inside its text, between tags such as `<think>` (local models) or
    `<thought>` (Gemini), leaves the text through the think scrubber and becomes reasoning,
    so the stored message is clean whichever provider wrote it.
    """

    def __init__(self, provider_name: str) -> None:
        self._name = provider_name
        self._scrubber = ThinkScrubber()
        # Whether the provider sent reasoning of its own in this reply.
        self._native = False
        self._text: list[str] = []
        self._reasoning: list[str] = []
        self._calls: dict[int, _CallSlot] = {}
        self._fields: JSONObject = {}
        self._details: list[JSONObject] = []
        self._stop_reason: str | None = None
        self._finished = False
        self._usage: Mapping[str, Any] | None = None
        self._model: str | None = None

    def read(self, chunk: Mapping[str, Any]) -> list[TextDelta | ReasoningDelta]:
        """Take one chunk; return the pieces of text and reasoning it carried, in order."""
        events: list[TextDelta | ReasoningDelta] = []
        model = chunk.get("model")
        if isinstance(model, str) and model:
            self._model = model
        usage = chunk.get("usage")
        if isinstance(usage, Mapping):
            self._usage = cast("Mapping[str, Any]", usage)
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return events
        choice = cast("Mapping[str, Any]", choices[0])
        delta = choice.get("delta")
        if isinstance(delta, Mapping):
            events.extend(self._delta(cast("Mapping[str, Any]", delta)))
        reason = choice.get("finish_reason")
        if isinstance(reason, str) and reason:
            self._stop_reason = reason
            self._finished = True
        return events

    def _delta(self, delta: Mapping[str, Any]) -> list[TextDelta | ReasoningDelta]:
        events: list[TextDelta | ReasoningDelta] = []
        # Providers name readable reasoning differently; one of them is the text, never both.
        reasoning = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning, str) and reasoning:
            self._native = True
            self._reasoning.append(reasoning)
            events.append(ReasoningDelta(reasoning))
        content = delta.get("content")
        if isinstance(content, str) and content:
            events.extend(self._content(content))
        details = delta.get("reasoning_details")
        if isinstance(details, list):
            for detail in cast("list[Any]", details):
                if isinstance(detail, Mapping):
                    _join_detail(self._details, cast("Mapping[str, JSON]", detail))
        calls = delta.get("tool_calls")
        if isinstance(calls, list):
            for position, call in enumerate(cast("list[Any]", calls)):
                if isinstance(call, Mapping):
                    self._call(cast("Mapping[str, Any]", call), position)
        for key, value in delta.items():
            if key not in _DELTA_KEYS and value is not None:
                self._fields[key] = cast("JSON", value)
        return events

    def _content(self, content: str) -> list[TextDelta | ReasoningDelta]:
        events: list[TextDelta | ReasoningDelta] = []
        visible = self._scrubber.feed(content)
        # What the scrubber took out is reasoning only while the provider sent none of its own
        # in this reply, so the same thought never shows twice.
        hidden = self._scrubber.hidden
        if hidden and not self._native:
            self._reasoning.append(hidden)
            events.append(ReasoningDelta(hidden))
        text = self._visible(visible)
        if text:
            events.append(TextDelta(text))
        return events

    def _visible(self, text: str) -> str:
        # Newlines before the first visible word are dropped: the line a model leaves after its
        # closing tag is no part of the answer.
        if not self._text:
            text = text.lstrip("\n")
        if text:
            self._text.append(text)
        return text

    def _call(self, call: Mapping[str, Any], position: int) -> None:
        index = call.get("index")
        call_id = call.get("id")
        if isinstance(index, int):
            place = index
        elif isinstance(call_id, str) and call_id:
            # Gemini sends each parallel call whole, in its own chunk, without an index (seen on
            # 09/10/2026): keyed by position, two calls became one, with both argument strings
            # glued together. A call with an id not seen before is a new call.
            known = next((k for k, s in self._calls.items() if s.id == call_id), None)
            place = known if known is not None else len(self._calls) + position
        else:
            place = max(self._calls, default=position)
        slot = self._calls.setdefault(place, _CallSlot())
        if isinstance(call_id, str) and call_id:
            slot.id = call_id
        function = call.get("function")
        if isinstance(function, Mapping):
            function = cast("Mapping[str, Any]", function)
            name, arguments = function.get("name"), function.get("arguments")
            if isinstance(name, str) and name:
                slot.name = name
            if isinstance(arguments, str):
                slot.arguments.append(arguments)
        for key, value in call.items():
            if key not in _TOOL_CALL_KEYS and value is not None:
                slot.fields[key] = cast("JSON", value)

    def result(self) -> WireResult:
        """The whole reply. A stream that ended without a stop reason was cut."""
        if not self._finished:
            raise ModelUnavailableError(
                f"{self._name} closed the reply before it finished. Nothing of it is kept; "
                "try again."
            )
        self._visible(self._scrubber.flush())
        fields = dict(self._fields)
        if self._details:
            fields["reasoning_details"] = list[JSON](self._details)
        calls: list[ToolCall] = []
        for index, slot in sorted(self._calls.items()):
            arguments = "".join(slot.arguments)
            calls.append(
                ToolCall(
                    id=slot.id or _call_id(index, slot.name, arguments),
                    name=slot.name,
                    arguments=arguments,
                    provider_fields=slot.fields,
                )
            )
        return WireResult(
            text="".join(self._text),
            reasoning="".join(self._reasoning),
            tool_calls=tuple(calls),
            provider_fields=fields,
            stop_reason=self._stop_reason,
            usage=None if self._usage is None else chat_usage(self._usage),
            reported_cost=None if self._usage is None else _reported_cost(self._usage),
            served_model=self._model,
        )


def _join_detail(details: list[JSONObject], detail: Mapping[str, JSON]) -> None:
    """Add one streamed reasoning detail. Text fragments join the block before them.

    OpenRouter streams reasoning details word by word. Kept apart, a long thought would go back as
    hundreds of one-word entries, which providers that check the sequence refuse.
    Encrypted and other opaque entries stay whole and in place.
    """
    kind = detail.get("type")
    key = _JOINED_DETAILS.get(kind) if isinstance(kind, str) else None
    last = details[-1] if details else None
    piece = detail.get(key) if key else None
    if last is not None and key is not None and last.get("type") == kind and isinstance(piece, str):
        before = last.get(key)
        last[key] = (before if isinstance(before, str) else "") + piece
        for name in _BACKFILLED_DETAIL_KEYS:
            if last.get(name) in (None, "") and detail.get(name) not in (None, ""):
                last[name] = detail.get(name)
        return
    details.append(dict(detail))


def _call_id(index: int, name: str, arguments: str) -> str:
    """An id for a call the provider sent without one: the same call always gets the same id."""
    digest = hashlib.sha256(f"{index}\x00{name}\x00{arguments}".encode()).hexdigest()
    return f"call_{digest[:24]}"


def chat_usage(raw: Mapping[str, Any]) -> Usage:
    """Usage in the Chat Completions shape, with DeepSeek's own cache field as a fallback.

    Gemini counts its thinking only in `total_tokens`: on 08/10/2026 a reply reported 22 prompt
    and 47 completion tokens with a total of 428. The tokens the total holds beyond input and
    output are reasoning, billed as output; read as reported, the cost came out 8 times too low.
    """
    input_details = _mapping(raw.get("prompt_tokens_details"))
    output_details = _mapping(raw.get("completion_tokens_details"))
    cache_read = _count(input_details.get("cached_tokens")) or _count(
        raw.get("prompt_cache_hit_tokens")
    )
    prompt, completion = _count(raw.get("prompt_tokens")), _count(raw.get("completion_tokens"))
    reasoning = _count(output_details.get("reasoning_tokens"))
    unreported = _count(raw.get("total_tokens")) - prompt - completion
    if unreported > 0:
        completion += unreported
        reasoning = max(reasoning, unreported)
    return make_usage(
        input_tokens=prompt,
        output_tokens=completion,
        cache_read_tokens=cache_read,
        cache_write_tokens=_count(input_details.get("cache_write_tokens")),
        reasoning_tokens=reasoning,
    )


def _reported_cost(raw: Mapping[str, Any]) -> Decimal | None:
    value = raw.get("cost")
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        cost = Decimal(str(value))
    except InvalidOperation:
        return None
    return cost if cost.is_finite() and cost >= 0 else None


def _mapping(value: object) -> Mapping[str, Any]:
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else {}


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)
