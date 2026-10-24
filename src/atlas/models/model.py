"""What the agent loop sends to a model and what comes back, the catalog's records, and the rules
for usage and cost. Pure data, no I/O.

**One interface for every provider.** The loop builds a `ModelRequest` (messages and tools) and
gets a `ModelReply` (text, tool calls, readable reasoning, usage, cost), whatever provider and wire
answered. Messages are Atlas's own types, not a provider's dictionaries, so a conversation can move
between providers.

**Provider fields travel with the message, and go back only where they came from.** Some providers
return opaque data that they want back on the next call: Gemini's thought signature on a tool call,
OpenRouter's reasoning details, OpenAI's encrypted reasoning. The history keeps all of it with the
`Origin` that wrote it; the wire sends it back only to that same provider and model. Other
providers refuse fields they do not know (HTTP 400 "Extra inputs are not permitted"), and
encrypted reasoning is sealed to the model that wrote it.

**Usage, with two invariants** (from Atlas V3's `token_usage`): input tokens include the tokens read
from and written to the cache, and output tokens include the reasoning tokens. That is how
providers bill. When a provider reports parts larger than the whole, the whole is wrong and grows.

**Cost never invents a price** (from Atlas V3's `pricing`, whose earlier version priced any unknown
model as gpt-4o-mini and was 1000x off for o1-pro). A cost carries where it came from: the provider
reported it, it was computed from the price list, it is free (a local server), or it is unknown.
Unknown is `None`, never a plausible zero.
"""

import base64
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum

from atlas.models.errors import ModelChoiceError, ModelRequestError

type JSON = str | int | float | bool | list[JSON] | dict[str, JSON] | None
type JSONObject = dict[str, JSON]

MILLION = Decimal(1_000_000)


# -- the conversation ----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Origin:
    """The provider and model that wrote an assistant message."""

    provider: str
    model: str


@dataclass(frozen=True, slots=True)
class SystemMessage:
    text: str


class AttachmentKind(StrEnum):
    """What an attachment is, which decides how it reaches a model (docs/ARCHITECTURE.md,
    Attachments) and how the screen marks it."""

    IMAGE = "image"
    #: PDF, Office, OpenDocument, RTF, EPUB: read with read_file, which converts them.
    DOCUMENT = "document"
    #: Plain text a person reads (`.txt`, `.md`, `.csv`, `.json`…): whole in the message when small.
    TEXT = "text"
    VIDEO = "video"
    AUDIO = "audio"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Attachment:
    """A file the user attached to a message, kept in the home's `attachments/`. The message keeps
    it apart from its text; what a model reads of it is built for each call."""

    id: str
    name: str
    media_type: str
    size: int
    kind: AttachmentKind
    #: What the description model saw in an image, for a model that does not see images.
    description: str = ""


@dataclass(frozen=True, slots=True)
class UserMessage:
    text: str
    #: The files the user attached, as stored.
    attachments: tuple["Attachment", ...] = ()
    #: Images and videos for the model to see, filled for each call from the attachments and never
    #: stored. A model that does not see them gets a note instead.
    images: tuple["Image", ...] = ()


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One call the model asked for. `arguments` is the JSON text exactly as the model wrote it."""

    id: str
    name: str
    arguments: str
    #: Opaque data the provider attached to this call, such as Gemini's `extra_content`.
    provider_fields: JSONObject = field(default_factory=dict[str, JSON])


@dataclass(frozen=True, slots=True)
class AssistantMessage:
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    #: Readable reasoning, for display. Never sent back as text: what a provider wants back is in
    #: `provider_fields`.
    reasoning: str = ""
    origin: Origin | None = None
    #: Opaque data the provider attached to the message, such as OpenRouter's `reasoning_details`
    #: or the Responses API's `output` items.
    provider_fields: JSONObject = field(default_factory=dict[str, JSON])


@dataclass(frozen=True, slots=True)
class Image:
    """An image for the model to see: one a tool returned, such as a picture read_file opened, or
    one the user attached. A user's attached video travels the same way, to a model that takes it
    (`takes_video`)."""

    media_type: str
    data: bytes

    def data_url(self) -> str:
        return f"data:{self.media_type};base64,{base64.b64encode(self.data).decode()}"


class ToolStatus(StrEnum):
    """How a tool call ended, as the screen shows it and the stored result keeps it.

    The AI SDK's and ACP's states: `denied` is the AI SDK's `output-denied` (Codex's
    `declined`), `cancelled` ACP v2's. The model never sees it; it reads the result's text.
    """

    COMPLETED = "completed"
    #: The tool raised, was unknown, got arguments it could not take, or returned an error.
    FAILED = "failed"
    #: Refused at approval: by the user, the guardian or a rule.
    DENIED = "denied"
    #: The turn stopped before the call finished, or before it ran.
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ToolResult:
    """What a tool returned, for the call with id `call_id`."""

    call_id: str
    text: str
    #: The deferred tools this result made callable: a tool search's finds. They stay callable
    #: while this result is in the history.
    loaded: tuple[str, ...] = ()
    #: Images for the model to see. The Responses API takes them inside the result; Chat
    #: Completions takes only text there, so they follow the round's results in a user message. A
    #: model that does not see images gets a note instead.
    images: tuple[Image, ...] = ()
    status: ToolStatus = ToolStatus.COMPLETED


#: What a model that does not see images reads instead of them.
NO_IMAGES = " [Atlas: this result came with images, and this model does not see images.]"


def sees_images(info: "ModelInfo | None") -> bool:
    """Whether a model takes images, as the catalog says; unknown counts as yes, so a model the
    catalog does not know is not starved of what it may well see."""
    return info is None or not info.input_modalities or "image" in info.input_modalities


#: What a model that does not see images reads instead of the user's attached ones.
NO_USER_IMAGES = " [Atlas: images came with this message, and this model does not see images.]"


def takes_video(info: "ModelInfo | None", wire: "Wire") -> bool:
    """Whether a user's attached video may go to the model as itself: the catalog says it takes
    video, and the wire carries it, Chat Completions' `video_url`. Unknown
    counts as no, since a video the model cannot read is a refused request."""
    return wire is Wire.CHAT and info is not None and "video" in info.input_modalities


type Message = SystemMessage | UserMessage | AssistantMessage | ToolResult


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A tool the model may call. `parameters` is the JSON Schema of its arguments."""

    name: str
    description: str
    parameters: JSONObject


#: The name of the tool that finds deferred tools. On a model with native tool search (OpenAI's
#: Responses API, since GPT-5.4) the tool by this name goes as the provider's own `tool_search`.
TOOL_SEARCH = "tool_search"


@dataclass(frozen=True, slots=True)
class ModelRequest:
    messages: tuple[Message, ...]
    #: The tools always offered, the tool search among them when there is anything to find.
    tools: tuple[ToolSpec, ...] = ()
    #: The deferred tools the conversation has loaded through tool search. On a model with
    #: native tool search they travel inside the search's results, at the end of the context, so
    #: the provider's cache holds; elsewhere they join the tool list.
    loaded_tools: tuple[ToolSpec, ...] = ()
    #: Why the call is made; the call ledger groups by it.
    purpose: str = "turn"
    #: What the call belongs to (a conversation, a job), for the call ledger. Optional.
    scope: str | None = None
    #: How hard a reasoning model thinks. `None` leaves the model's own default.
    effort: str | None = None
    max_output_tokens: int | None = None
    #: Every deferred tool, loaded or not. A wire that loads them by reference (Anthropic's
    #: `defer_loading` and `tool_reference`) sends all of them on every call, so the tool list
    #: never changes; the others use `loaded_tools`. Empty when the caller has none to give.
    deferred_tools: tuple[ToolSpec, ...] = ()
    #: A person paces the conversation, so a cache that lasts an hour pays for its dearer write: a
    #: person often comes back after 5 to 60 minutes idle, when the short cache is gone. Calls a
    #: machine paces (a subagent, a routine, a
    #: review) keep the short cache, where the long one would cost about half again.
    long_cache: bool = False
    #: False asks the model not to reason at all, for a call too small to need it (a title):
    #: Anthropic's `thinking: disabled`, elsewhere the effort `none` when the model takes it.
    #: Reasoning is billed as output.
    reasoning: bool = True


class Finish(StrEnum):
    """Why the model stopped."""

    STOP = "stop"
    TOOL_CALLS = "tool_calls"
    #: The output limit cut the reply short.
    LENGTH = "length"
    #: The provider's content filter stopped the reply.
    FILTERED = "content_filter"
    OTHER = "other"


def finish_from(reason: str | None, *, has_tool_calls: bool) -> Finish:
    """A provider's stop reason, in Atlas's words. Tool calls win: some providers say `stop`."""
    if has_tool_calls:
        return Finish.TOOL_CALLS
    match reason:
        case "stop" | "completed" | "end_turn" | "stop_sequence" | None:
            return Finish.STOP
        case "tool_calls" | "function_call" | "tool_use":
            return Finish.TOOL_CALLS
        case "length" | "max_output_tokens" | "max_tokens" | "model_context_window_exceeded":
            return Finish.LENGTH
        case "content_filter" | "refusal":
            return Finish.FILTERED
        case _:
            return Finish.OTHER


# -- usage and cost ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Usage:
    """The tokens one call spent, as the provider reported them."""

    #: Every input token, the ones read from and written to the cache included.
    input_tokens: int
    #: Every output token, the reasoning tokens included.
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    #: The part of `cache_write_tokens` written to a cache that lasts an hour, which Anthropic
    #: bills at 2x the input where the five-minute one is 1.25x.
    cache_write_long_tokens: int = 0

    @property
    def new_input_tokens(self) -> int:
        """Input tokens neither read from the cache nor written to it: the full-price ones."""
        return self.input_tokens - self.cache_read_tokens - self.cache_write_tokens


def make_usage(
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    reasoning_tokens: int = 0,
    cache_write_long_tokens: int = 0,
) -> Usage:
    """A `Usage` that keeps the invariants, whatever the provider reported.

    An input smaller than its cache can only be an input reported without the cache, which would
    make the new input negative and the call free: the cache is added back. Reasoning is already
    part of the output, so an output smaller than its reasoning grows to it, and nothing is added.
    The hour-long write is part of the cache write, which grows to it the same way.
    """
    counts = [
        max(0, value)
        for value in (
            input_tokens,
            output_tokens,
            cache_read_tokens,
            cache_write_tokens,
            reasoning_tokens,
            cache_write_long_tokens,
        )
    ]
    total_in, total_out, cache_read, cache_write, reasoning, long_write = counts
    cache_write = max(cache_write, long_write)
    cached = cache_read + cache_write
    return Usage(
        input_tokens=total_in if total_in >= cached else total_in + cached,
        output_tokens=max(total_out, reasoning),
        cache_read_tokens=cache_read,
        cache_write_tokens=cache_write,
        reasoning_tokens=reasoning,
        cache_write_long_tokens=long_write,
    )


@dataclass(frozen=True, slots=True)
class PriceTier:
    """Prices that replace the base ones for a call whose input exceeds `above_input_tokens`.

    Providers reprice the whole request past a context size: OpenAI doubles the input of GPT-5.6
    past 272,000 tokens, and Atlas V3 measured its expensive turns on that side of the line.
    """

    above_input_tokens: int
    input: Decimal
    output: Decimal
    cache_read: Decimal | None = None
    cache_write: Decimal | None = None


@dataclass(frozen=True, slots=True)
class Price:
    """US dollars per million tokens.

    A missing cache price means no discount: those tokens cost the input price. That is the higher
    figure, never a guess at a discount nobody declared.
    """

    input: Decimal
    output: Decimal
    cache_read: Decimal | None = None
    cache_write: Decimal | None = None
    #: Some models bill reasoning at its own price; otherwise it is output.
    reasoning: Decimal | None = None
    tiers: tuple[PriceTier, ...] = ()
    #: The write to a cache that lasts an hour (Anthropic: 2x the input). Missing, it costs what
    #: `cache_write` costs.
    cache_write_long: Decimal | None = None

    def for_input(self, input_tokens: int) -> "Price":
        """The prices that apply to a call with this many input tokens."""
        applicable = [tier for tier in self.tiers if input_tokens > tier.above_input_tokens]
        if not applicable:
            return self
        tier = max(applicable, key=lambda tier: tier.above_input_tokens)
        return Price(
            input=tier.input,
            output=tier.output,
            cache_read=tier.cache_read if tier.cache_read is not None else self.cache_read,
            cache_write=tier.cache_write if tier.cache_write is not None else self.cache_write,
            reasoning=self.reasoning,
            cache_write_long=self.cache_write_long,
        )


class CostSource(StrEnum):
    #: The provider said what it charged (OpenRouter's `usage.cost`).
    REPORTED = "reported"
    #: Computed from the catalog's prices.
    PRICE_LIST = "price_list"
    #: A server on the user's machine. The zero is known, not assumed.
    FREE = "free"
    #: No usage was reported, or the model has no price. Never shown as zero.
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Cost:
    usd: Decimal | None
    source: CostSource


UNKNOWN_COST = Cost(None, CostSource.UNKNOWN)


def price_call(
    usage: Usage | None, price: Price | None, *, reported: Decimal | None, free: bool
) -> Cost:
    """What one call cost, and where the number came from. Unknown is never made up.

    The tier is chosen per call, from that call's input: exact, where Atlas V3 had to average the
    input over a whole turn.
    """
    if reported is not None:
        return Cost(reported, CostSource.REPORTED)
    if free:
        return Cost(Decimal(0), CostSource.FREE)
    if usage is None or price is None:
        return UNKNOWN_COST
    rates = price.for_input(usage.input_tokens)
    cache_read = rates.input if rates.cache_read is None else rates.cache_read
    cache_write = rates.input if rates.cache_write is None else rates.cache_write
    long_write = cache_write if rates.cache_write_long is None else rates.cache_write_long
    reasoning = rates.output if rates.reasoning is None else rates.reasoning
    total = (
        usage.new_input_tokens * rates.input
        + usage.cache_read_tokens * cache_read
        + (usage.cache_write_tokens - usage.cache_write_long_tokens) * cache_write
        + usage.cache_write_long_tokens * long_write
        + (usage.output_tokens - usage.reasoning_tokens) * rates.output
        + usage.reasoning_tokens * reasoning
    ) / MILLION
    return Cost(total, CostSource.PRICE_LIST)


# -- the reply -----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelReply:
    """What one model call returned."""

    #: The assistant message to append to the history, provider fields and origin included.
    message: AssistantMessage
    finish: Finish
    #: `None` when the provider reported no usage: unmeasured, not zero.
    usage: Usage | None
    cost: Cost
    #: The model the provider says answered, which may name a dated snapshot of the one asked for.
    served_model: str | None = None

    @property
    def text(self) -> str:
        return self.message.text

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return self.message.tool_calls


@dataclass(frozen=True, slots=True)
class WireResult:
    """What a wire read from one reply, before the service adds the origin and the cost."""

    text: str
    reasoning: str
    tool_calls: tuple[ToolCall, ...]
    provider_fields: JSONObject
    #: The provider's own stop reason, such as `stop`, `length` or `max_output_tokens`.
    stop_reason: str | None
    usage: Usage | None
    #: What the provider said the call cost, when it says (OpenRouter).
    reported_cost: Decimal | None
    served_model: str | None


@dataclass(frozen=True, slots=True)
class TextDelta:
    """A piece of the reply's text, as it arrives."""

    text: str


@dataclass(frozen=True, slots=True)
class ReasoningDelta:
    """A piece of the readable reasoning, as it arrives."""

    text: str


@dataclass(frozen=True, slots=True)
class ReplyDone:
    """The last event of a call: the whole reply."""

    reply: ModelReply


type ModelEvent = TextDelta | ReasoningDelta | ReplyDone


# -- the catalog ---------------------------------------------------------------------------------


class Wire(StrEnum):
    """The request format a provider speaks."""

    CHAT = "chat"
    RESPONSES = "responses"
    #: Anthropic's Messages API, through its own SDK (step 3b).
    MESSAGES = "messages"


class EffortParameter(StrEnum):
    """How Chat Completions sends the effort."""

    #: OpenAI's top-level field, also read by Gemini, Ollama and vLLM.
    REASONING_EFFORT = "reasoning_effort"
    #: OpenRouter's `{"reasoning": {"effort": ...}}`.
    REASONING = "reasoning"
    #: Gemini's `thinking_level`, inside the `thinking_config` that also asks for readable
    #: reasoning: Gemini refuses `reasoning_effort` beside a `thinking_config` (HTTP 400, "Expected
    #: one of either `reasoning_effort` or custom `thinking_config`; found both", seen 10/10/2026).
    THINKING_LEVEL = "thinking_level"


@dataclass(frozen=True, slots=True)
class Provider:
    id: str
    name: str
    base_url: str
    #: The vault secret that holds the API key. `None` for a server that takes none.
    key: str | None
    wire: Wire
    #: The models.dev provider whose capability and price data apply, if any.
    catalog: str | None = None
    #: Provider fields this provider takes back on the next call.
    replay: frozenset[str] = frozenset()
    effort: EffortParameter = EffortParameter.REASONING_EFFORT
    #: Whether a request may end with an assistant message (Gemini refuses it).
    trailing_assistant: bool = True
    #: A server on the user's machine: every call costs zero.
    free: bool = False
    #: The path, under `base_url`, that answers only to a valid key and spends nothing.
    key_check: str = "/models"
    #: The page where the user makes a key, which the key screen links to.
    key_page: str | None = None
    default_model: str | None = None
    #: The models the app's picker offers, in order: a dated choice, like `default_model`.
    models: tuple[str, ...] = ()
    #: Body fields that ask a model that reasons for its readable reasoning (Chat Completions).
    ask_reasoning: JSONObject = field(default_factory=dict[str, JSON])
    #: Model ids, or their beginnings (`gpt-5.4` covers `gpt-5.4-mini`), with the provider's own
    #: tool search. Only the Responses API has one.
    tool_search: tuple[str, ...] = ()
    #: The write to a cache that lasts an hour, as a multiple of the input price, for a catalog
    #: that prices only the short one (models.dev gives Anthropic's five-minute write alone).
    cache_write_long: Decimal | None = None
    #: The most output a call may ask for, whatever the model allows: 32,000 for a model not
    #: listed otherwise. On 14/10/2026 one call of Opus 5.5 reasoned in a loop
    #: up to the model's 128,000 and cost US$ 2.68 alone. `None` sends the model's own limit.
    max_output: int | None = None


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """A provider, one of its models, and an effort (`None`: Atlas's default, `DEFAULT_EFFORT`,
    where the model takes efforts): what the settings choose, or what a conversation picked in the
    app."""

    provider: str
    model: str
    effort: str | None = None


@dataclass(frozen=True, slots=True)
class OfferedModel:
    """One model of the picker, with what the catalog says it takes."""

    id: str
    name: str
    #: Whether it reasons; `None` when the catalog does not know.
    reasoning: bool | None
    #: The effort levels it takes, in the catalog's order; `None` when unknown.
    efforts: tuple[str, ...] | None


@dataclass(frozen=True, slots=True)
class OfferedProvider:
    """A provider with a key in the vault, and the models the picker offers from it."""

    id: str
    name: str
    models: tuple[OfferedModel, ...]


def check_choice(choice: ModelChoice, info: "ModelInfo | None") -> None:
    """Refuse a pick the catalog knows will fail: a model that calls no tools (every turn offers
    some), or an effort the model does not take. What the catalog does not know is let through."""
    if info is None:
        return
    if info.tool_call is False:
        raise ModelChoiceError(f"{choice.model} cannot call tools. Choose a model that can.")
    effort = choice.effort
    if effort is not None and info.reasoning is False:
        raise ModelChoiceError(f"{choice.model} does not reason, so it takes no effort.")
    if effort is not None and info.efforts is not None and effort not in info.efforts:
        raise ModelChoiceError(
            f"{choice.model} does not accept the effort {effort!r}. It accepts: "
            f"{', '.join(info.efforts)}."
        )


#: The effort levels from the weakest to the strongest; no `ultra`, which no wire takes.
EFFORT_LADDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")
#: The effort of whatever Atlas calls when nobody chose one: its conversations, bots, routines,
#: subagents and the executors it starts (decided by the user on 17/10/2026). Atlas sends what it
#: shows, so the picker never says one level while the provider runs another.
DEFAULT_EFFORT = "medium"


def clamp_effort(effort: str, supported: Sequence[str]) -> str:
    """`effort` onto the levels a model takes: as it is when taken, when the levels are unknown or
    when it is no level of the ladder; else the nearest weaker level, so a clamp never costs more;
    and only when nothing is weaker, the weakest. `none` is
    never the answer to an effort that reasons: it would turn the reasoning off."""
    if not supported or effort in supported or effort not in EFFORT_LADDER:
        return effort
    candidates = [level for level in supported if level in EFFORT_LADDER and level != "none"]
    if not candidates:
        return effort
    asked = EFFORT_LADDER.index(effort)
    below = [level for level in candidates if EFFORT_LADDER.index(level) < asked]
    if below:
        return max(below, key=EFFORT_LADDER.index)
    return min(candidates, key=EFFORT_LADDER.index)


def default_effort(reasoning: bool | None, efforts: Sequence[str] | None) -> str | None:
    """The effort a model runs on when nobody chose one: `DEFAULT_EFFORT` onto its levels; `None`
    for a model that does not reason, or whose levels are not known, as sending one it may refuse
    would fail the turn."""
    if reasoning is False or not efforts:
        return None
    return clamp_effort(DEFAULT_EFFORT, efforts)


def native_tool_search(provider: Provider, model: str) -> bool:
    """Whether this model searches deferred tools with the provider's own tool search."""
    return provider.wire is Wire.RESPONSES and any(
        model == prefix or model.startswith((f"{prefix}-", f"{prefix}."))
        for prefix in provider.tool_search
    )


@dataclass(frozen=True, slots=True)
class ModelInfo:
    """What a model can do and what it costs. `None` means unknown, never a guess."""

    id: str
    name: str
    context_window: int | None = None
    max_input_tokens: int | None = None
    max_output_tokens: int | None = None
    tool_call: bool | None = None
    reasoning: bool | None = None
    #: The effort levels the model accepts, when known.
    efforts: tuple[str, ...] | None = None
    input_modalities: tuple[str, ...] = ()
    price: Price | None = None
    #: Where the record came from: models.dev, or the user's models.toml over it.
    source: str = ""


# -- before a call -------------------------------------------------------------------------------


def replays_to(message: AssistantMessage, provider: Provider, model: str) -> bool:
    """Whether the message's provider fields may go back on a call to this provider and model.

    Only to the provider and model that wrote them: another provider refuses fields it does not
    know, and encrypted reasoning is sealed to the model that wrote it.
    """
    return message.origin == Origin(provider.id, model)


def check_request(
    provider: Provider,
    model: str,
    info: ModelInfo | None,
    request: ModelRequest,
    effort: str | None,
) -> None:
    """Refuse, before anything is sent, a request this provider or model is known to refuse.

    A refusal here costs nothing and names the fix; the same refusal from the provider costs a
    round trip and arrives as an HTTP 400 in the middle of a turn.
    """
    if not request.messages:
        raise ModelRequestError("A model request needs at least one message.")
    if not provider.trailing_assistant and isinstance(request.messages[-1], AssistantMessage):
        raise ModelRequestError(
            f"{provider.name} refuses a request that ends with an assistant message. End it with "
            "the user's message or a tool result, and put notes for the model on the user side."
        )
    if info is None:
        return
    if request.tools and info.tool_call is False:
        raise ModelChoiceError(
            f"{model} at {provider.name} cannot call tools, and this request offers "
            f"{len(request.tools)}. Choose a model that can."
        )
    if effort is not None and info.reasoning is False:
        raise ModelChoiceError(
            f"{model} does not reason, so it takes no effort. Leave the effort unset for it."
        )
    if effort is not None and info.efforts is not None and effort not in info.efforts:
        raise ModelChoiceError(
            f"{model} does not accept the effort {effort!r}. It accepts: {', '.join(info.efforts)}."
        )


# -- the call ledger and the key check -----------------------------------------------------------


class CallOutcome(StrEnum):
    """How a model call ended."""

    OK = "ok"
    #: The caller stopped reading before the reply ended (the user pressed stop).
    CANCELLED = "cancelled"
    AUTH = "auth"
    OUT_OF_CREDIT = "out_of_credit"
    RATE_LIMITED = "rate_limited"
    REFUSED = "refused"
    UNAVAILABLE = "unavailable"
    #: Anything else: a defect in Atlas rather than a provider's answer.
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CallRecord:
    """One row of the call ledger. Usage and cost are `None` when nobody measured them."""

    started_at: str
    purpose: str
    scope: str | None
    provider: str
    model: str
    served_model: str | None
    outcome: CallOutcome
    duration_ms: int
    usage: Usage | None
    cost: Cost


@dataclass(slots=True)
class UsageLine:
    """The calls of one day, model or purpose, summed. `usd` sums the costs that are known;
    `unknown` counts the calls whose cost nobody knows, which are never counted as zero."""

    calls: int = 0
    input_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    usd: Decimal = Decimal(0)
    unknown: int = 0

    def add(self, record: CallRecord) -> None:
        self.calls += 1
        if record.usage is not None:
            self.input_tokens += record.usage.input_tokens
            self.cache_read_tokens += record.usage.cache_read_tokens
            self.output_tokens += record.usage.output_tokens
        if record.cost.usd is None:
            self.unknown += 1
        else:
            self.usd += record.cost.usd


@dataclass(slots=True)
class UsageReport:
    """The usage report, from the call ledger: the calls summed in all, by
    day, by model (`provider model`) and by purpose, and how many costs came from each source."""

    total: UsageLine = field(default_factory=UsageLine)
    days: dict[str, UsageLine] = field(default_factory=dict[str, UsageLine])
    models: dict[str, UsageLine] = field(default_factory=dict[str, UsageLine])
    purposes: dict[str, UsageLine] = field(default_factory=dict[str, UsageLine])
    sources: dict[CostSource, int] = field(default_factory=dict[CostSource, int])


def usage_report(records: Iterable[tuple[str, CallRecord]]) -> UsageReport:
    """Sum the calls, each given with the day it counts for (the caller turns UTC into the user's
    local day, at the edge). Days come out in order; models and purposes, the costliest first."""
    report = UsageReport()
    for day, record in records:
        report.total.add(record)
        for key, lines in (
            (day, report.days),
            (f"{record.provider} {record.model}", report.models),
            (record.purpose, report.purposes),
        ):
            lines.setdefault(key, UsageLine()).add(record)
        report.sources[record.cost.source] = report.sources.get(record.cost.source, 0) + 1
    report.days = dict(sorted(report.days.items()))
    report.models = _costliest(report.models)
    report.purposes = _costliest(report.purposes)
    return report


def _costliest(lines: dict[str, UsageLine]) -> dict[str, UsageLine]:
    return dict(sorted(lines.items(), key=lambda item: (-item[1].usd, -item[1].calls, item[0])))


class KeyStatus(StrEnum):
    """What a provider said about a key, asked where it costs nothing."""

    VALID = "valid"
    #: There is no key in the vault for this provider; nothing was asked.
    MISSING = "missing"
    #: The provider refused the key (HTTP 401 or 403).
    REFUSED = "refused"
    #: The key is valid, and the account is out of credit (HTTP 402).
    NO_CREDIT = "no_credit"
    #: The key is valid, and the provider is limiting requests for now (HTTP 429).
    LIMITED = "limited"
    #: The provider could not be reached. This says nothing about the key.
    UNREACHABLE = "unreachable"
    #: An answer Atlas does not know how to read.
    UNEXPECTED = "unexpected"


@dataclass(frozen=True, slots=True)
class KeyCheck:
    provider: str
    status: KeyStatus
    detail: str

    @property
    def usable(self) -> bool:
        """Whether calls with this key can work: valid, or valid and only limited for now."""
        return self.status in (KeyStatus.VALID, KeyStatus.LIMITED)
