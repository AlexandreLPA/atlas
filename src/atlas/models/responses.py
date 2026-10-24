"""The Responses API: OpenAI's own wire, the one that takes function tools together with reasoning.

Since GPT-5.4, Chat Completions refuses tools when the effort is anything but `none` (HTTP 400, "use
/v1/responses or set reasoning_effort to 'none'"); Atlas V3 ran its OpenAI models with reasoning off
for that reason. Pure, like `atlas.models.chat`: it builds the body and reads the events.

**Stateless** (`store: false`): OpenAI keeps nothing, and the history travels whole on every call,
as it does on every other provider. Its rules:

- The reply's `output` items are kept in the message's provider fields and go back as they came,
  reasoning first, so the model continues its reasoning across tool calls. They go back without
  their `id`s: OpenAI kept no item, so an id is a 404 ("Items are not persisted when `store` is set
  to false").
- Encrypted reasoning goes back only to the model that wrote it (`replays_to`); any other model
  answers HTTP 400 `invalid_encrypted_content`. A reasoning item without its encrypted content is
  dropped, since it cannot be replayed.
- A `call_id` is at most 64 characters, so a longer one from another provider gets a stable
  surrogate, the same for the call and for its output.
- `include: ["reasoning.encrypted_content"]` and the effort go only to a model known to reason: the
  chat-era models (gpt-4o, gpt-4.1) refuse any reasoning field.

**Native tool search** (GPT-5.4 and later, `Provider.tool_search`), executed by Atlas: the tool
search goes as the provider's `tool_search` tool, the model asks with a `tool_search_call`, and the
answer goes back as a `tool_search_output` that carries the definitions of the tools found. They
land at the end of the context, so the cache of everything before them holds; a tool added to the
tool list instead changes the start of every later request. Any other model gets the tool search as
a function and the loaded tools in its tool list, and the same history serves both: a search call
in Atlas's history becomes a `tool_search_call` only for a native model.
"""

import hashlib
import json
from collections.abc import Mapping
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
    native_tool_search,
    replays_to,
    sees_images,
)

MAX_CALL_ID = 64
_REASONING_DELTAS = frozenset(
    {"response.reasoning_summary_text.delta", "response.reasoning_text.delta"}
)
_REQUEST_ERROR_CODES = frozenset({"invalid_prompt", "context_length_exceeded"})


def responses_request(
    provider: Provider,
    model: str,
    request: ModelRequest,
    info: ModelInfo | None,
    effort: str | None,
) -> dict[str, Any]:
    """The body of a streamed, stateless Responses API call."""
    native = native_tool_search(provider, model)
    searches = _search_calls(request.messages) if native else frozenset[str]()
    loaded = {tool.name: tool for tool in request.loaded_tools}
    items: list[JSON] = []
    for message in request.messages:
        items.extend(_items(message, provider, model, searches, loaded, sees_images(info)))
    body: dict[str, Any] = {"model": model, "input": items, "store": False, "stream": True}
    tools: list[JSON] = []
    for tool in request.tools:
        if native and tool.name == TOOL_SEARCH:
            tools.append(
                {
                    "type": "tool_search",
                    "execution": "client",
                    "description": tool.description,
                    "parameters": tool.parameters,
                }
            )
        else:
            tools.append(_function(tool))
    if not native:
        offered = {tool.name for tool in request.tools}
        tools.extend(_function(tool) for tool in request.loaded_tools if tool.name not in offered)
    if tools:
        body["tools"] = tools
    reasoning: dict[str, Any] = {}
    if info is not None and info.reasoning is True:
        body["include"] = ["reasoning.encrypted_content"]
        # The readable summary of the reasoning comes only when asked for; short reasoning may
        # still come without one.
        reasoning["summary"] = "auto"
    if effort is not None:
        reasoning["effort"] = effort
    if reasoning:
        body["reasoning"] = reasoning
    if request.max_output_tokens is not None:
        body["max_output_tokens"] = request.max_output_tokens
    return body


def _function(tool: ToolSpec, *, deferred: bool = False) -> JSONObject:
    function: JSONObject = {
        "type": "function",
        "name": tool.name,
        "description": tool.description,
        "parameters": tool.parameters,
        # Strict schemas demand every property required and no other; Atlas's tools are described
        # as they are, not bent to that shape.
        "strict": False,
    }
    if deferred:
        function["defer_loading"] = True
    return function


def _search_calls(messages: tuple[Message, ...]) -> frozenset[str]:
    """The call ids of the tool searches in the history, whose results go back as searches."""
    return frozenset(
        call.id
        for message in messages
        if isinstance(message, AssistantMessage)
        for call in message.tool_calls
        if call.name == TOOL_SEARCH
    )


def _items(
    message: Message,
    provider: Provider,
    model: str,
    searches: frozenset[str],
    loaded: Mapping[str, ToolSpec],
    sees: bool,
) -> list[JSON]:
    match message:
        case SystemMessage(text=text):
            return [{"role": "system", "content": text}]
        case UserMessage(text=text, images=images) if images and sees:
            # Images in the user's message, as `input_image` parts.
            parts: list[JSON] = [{"type": "input_text", "text": text}] if text.strip() else []
            parts.extend({"type": "input_image", "image_url": image.data_url()} for image in images)
            return [{"role": "user", "content": parts}]
        case UserMessage(text=text, images=images):
            return [{"role": "user", "content": text + NO_USER_IMAGES if images else text}]
        case ToolResult(call_id=call_id) if call_id in searches:
            found: list[JSON] = [
                _function(loaded[name], deferred=True) for name in message.loaded if name in loaded
            ]
            return [
                {
                    "type": "tool_search_output",
                    "execution": "client",
                    "call_id": call_id_for(call_id),
                    "status": "completed",
                    "tools": found,
                }
            ]
        case ToolResult(call_id=call_id, text=text, images=images) if images and sees:
            # Images inside the result, as `input_image` parts of its output.
            parts: list[JSON] = [{"type": "input_text", "text": text}]
            parts.extend({"type": "input_image", "image_url": image.data_url()} for image in images)
            return [
                {"type": "function_call_output", "call_id": call_id_for(call_id), "output": parts}
            ]
        case ToolResult(call_id=call_id, text=text, images=images):
            said = text + NO_IMAGES if images else text
            return [
                {"type": "function_call_output", "call_id": call_id_for(call_id), "output": said}
            ]
        case AssistantMessage():
            return _assistant(message, provider, model, searches)


def _assistant(
    message: AssistantMessage, provider: Provider, model: str, searches: frozenset[str]
) -> list[JSON]:
    output = message.provider_fields.get("output")
    if replays_to(message, provider, model) and isinstance(output, list):
        items: list[JSON] = []
        for entry in output:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "reasoning" and not entry.get("encrypted_content"):
                continue
            items.append(_replayed(entry))
        return items
    items = []
    if message.text:
        items.append({"role": "assistant", "content": message.text})
    for call in message.tool_calls:
        if call.id in searches:
            items.append(
                {
                    "type": "tool_search_call",
                    "execution": "client",
                    "call_id": call_id_for(call.id),
                    "status": "completed",
                    "arguments": _arguments(call.arguments),
                }
            )
        else:
            items.append(
                {
                    "type": "function_call",
                    "call_id": call_id_for(call.id),
                    "name": call.name,
                    "arguments": call.arguments,
                }
            )
    return items


def _arguments(text: str) -> JSON:
    """A call's arguments as the object a `tool_search_call` carries."""
    try:
        value: object = json.loads(text or "{}")
    except ValueError:
        return {}
    return cast("JSON", value) if isinstance(value, dict) else {}


def _replayed(item: JSONObject) -> JSONObject:
    """An output item ready to go back: no id, no empty field, and a `call_id` that fits."""
    clean: JSONObject = {}
    for key, value in item.items():
        if key == "id" or value is None:
            continue
        if key == "call_id" and isinstance(value, str):
            value = call_id_for(value)
        elif isinstance(value, list):
            value = [_replayed(part) if isinstance(part, dict) else part for part in value]
        clean[key] = value
    return clean


def call_id_for(call_id: str) -> str:
    """`call_id` as the Responses API takes it: as it is, or a stable surrogate when too long."""
    if len(call_id) <= MAX_CALL_ID:
        return call_id
    return f"call_{hashlib.sha256(call_id.encode()).hexdigest()[:32]}"


# -- reading the stream --------------------------------------------------------------------------


class ResponsesReader:
    """Reads streamed Responses API events, given as plain dictionaries.

    The deltas are passed on as they arrive; the reply itself is taken whole from the final
    `response.completed` (or `response.incomplete`) event, which carries every output item and the
    usage, so nothing has to be rebuilt from fragments.
    """

    def __init__(self, provider_name: str) -> None:
        self._name = provider_name
        self._final: Mapping[str, Any] | None = None

    def read(self, event: Mapping[str, Any]) -> list[TextDelta | ReasoningDelta]:
        kind = event.get("type")
        delta = event.get("delta")
        if kind == "response.output_text.delta" and isinstance(delta, str) and delta:
            return [TextDelta(delta)]
        if kind in _REASONING_DELTAS and isinstance(delta, str) and delta:
            return [ReasoningDelta(delta)]
        if kind in ("response.completed", "response.incomplete"):
            response = event.get("response")
            if isinstance(response, Mapping):
                self._final = cast("Mapping[str, Any]", response)
        elif kind == "response.failed":
            response = _mapping(event.get("response"))
            raise _failure(self._name, _mapping(response.get("error")))
        elif kind == "error":
            raise _failure(self._name, event)
        return []

    def result(self) -> WireResult:
        final = self._final
        if final is None:
            raise ModelUnavailableError(
                f"{self._name} closed the reply before it finished. Nothing of it is kept; "
                "try again."
            )
        output = [
            cast("JSONObject", item)
            for item in cast("list[Any]", final.get("output") or [])
            if isinstance(item, dict)
        ]
        text: list[str] = []
        reasoning: list[str] = []
        calls: list[ToolCall] = []
        for item in output:
            kind = item.get("type")
            if kind == "message":
                text.extend(_texts(item.get("content"), ("output_text", "refusal")))
            elif kind == "reasoning":
                reasoning.extend(_texts(item.get("summary"), ("summary_text",)))
            elif kind == "function_call":
                calls.append(
                    ToolCall(
                        id=str(item.get("call_id") or item.get("id") or ""),
                        name=str(item.get("name") or ""),
                        arguments=str(item.get("arguments") or ""),
                    )
                )
            elif kind == "tool_search_call" and item.get("execution") == "client":
                calls.append(
                    ToolCall(
                        id=str(item.get("call_id") or item.get("id") or ""),
                        name=TOOL_SEARCH,
                        arguments=json.dumps(item.get("arguments") or {}, ensure_ascii=False),
                    )
                )
        details = _mapping(final.get("incomplete_details"))
        reason = details.get("reason")
        status = final.get("status")
        usage = final.get("usage")
        model = final.get("model")
        return WireResult(
            text="".join(text),
            reasoning="\n\n".join(reasoning),
            tool_calls=tuple(calls),
            provider_fields={"output": list[JSON](output)},
            stop_reason=reason if isinstance(reason, str) else str(status or "completed"),
            usage=responses_usage(cast("Mapping[str, Any]", usage))
            if isinstance(usage, Mapping)
            else None,
            reported_cost=None,
            served_model=model if isinstance(model, str) else None,
        )


def _texts(parts: object, kinds: tuple[str, ...]) -> list[str]:
    found: list[str] = []
    for part in cast("list[Any]", parts) if isinstance(parts, list) else []:
        fields = _mapping(part)
        kind = fields.get("type")
        if kind in kinds:
            value = fields.get("refusal" if kind == "refusal" else "text")
            if isinstance(value, str):
                found.append(value)
    return found


def _failure(name: str, error: Mapping[str, Any]) -> ModelsError:
    code = error.get("code")
    message = error.get("message") or "no reason given"
    if code == "rate_limit_exceeded":
        return ModelRateLimitError(f"{name} is limiting requests: {message}. It passes; try again.")
    if code == "insufficient_quota":
        return ModelOutOfCreditError(
            f"{name} says the account has no credit left: {message}. Add credit at the provider."
        )
    if code in _REQUEST_ERROR_CODES:
        return ModelRequestError(f"{name} refused the request ({code}): {message}.")
    return ModelUnavailableError(f"{name} failed during the reply ({code}): {message}. Try again.")


def responses_usage(raw: Mapping[str, Any]) -> Usage:
    input_details = _mapping(raw.get("input_tokens_details"))
    output_details = _mapping(raw.get("output_tokens_details"))
    return make_usage(
        input_tokens=_count(raw.get("input_tokens")),
        output_tokens=_count(raw.get("output_tokens")),
        cache_read_tokens=_count(input_details.get("cached_tokens")),
        cache_write_tokens=_count(input_details.get("cache_write_tokens")),
        reasoning_tokens=_count(output_details.get("reasoning_tokens")),
    )


def _mapping(value: object) -> Mapping[str, Any]:
    return cast("Mapping[str, Any]", value) if isinstance(value, Mapping) else {}


def _count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)
