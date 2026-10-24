"""Anthropic's Messages API through its real SDK, against the fake model server over real HTTP.

The fake refuses as Anthropic does (step 3b): a thinking block whose past changed, a blank text
block, an output-only field, a tool reference to a tool it does not have, a cache point on a
deferred tool, more than four cache points, a call with no result right after it, clearing without
its beta. So a test passes only when the request is one Anthropic takes.
"""

import json
from decimal import Decimal
from typing import Any, cast

import pytest

from atlas.home import Database, Home, migrate
from atlas.models import (
    SCHEMA,
    AssistantMessage,
    CallLedger,
    CostSource,
    Finish,
    Image,
    ModelAuthError,
    ModelEvent,
    ModelOutOfCreditError,
    ModelRateLimitError,
    ModelReply,
    ModelRequest,
    ModelRequestError,
    Models,
    ModelUnavailableError,
    Origin,
    ReasoningDelta,
    ReplyDone,
    SystemMessage,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolSpec,
    UserMessage,
    load_catalog,
)
from atlas.models.messages import messages_request
from fake_model import (
    FakeCall,
    FakeError,
    FakeModelServer,
    FakeReply,
    FakeUsage,
    _anthropic_signature,  # pyright: ignore[reportPrivateUsage]
)
from model_support import Connect, Run

OPUS = "claude-opus-5-5"
READ_FILE = ToolSpec(
    name="read_file",
    description="Read a text file.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)
TOOL_SEARCH = ToolSpec(
    name="tool_search",
    description="Find deferred tools.",
    parameters={"type": "object", "properties": {"query": {"type": "string"}}},
)
CALENDAR = ToolSpec(
    name="calendar_list",
    description="List the calendar.",
    parameters={"type": "object", "properties": {}},
)
MAIL = ToolSpec(name="mail_send", description="Send mail.", parameters={"type": "object"})
QUESTION = (SystemMessage("You are Atlas."), UserMessage("What is in today's notes?"))
MESSAGES = "/v1/messages"


async def _call(models: Models, request: ModelRequest, model: str = OPUS) -> ModelReply:
    return await models.client("anthropic", model).reply(request)


def _bodies(fake: FakeModelServer) -> list[dict[str, Any]]:
    return fake.bodies(MESSAGES)


def _blocks(body: dict[str, Any], position: int) -> list[dict[str, Any]]:
    return cast("list[dict[str, Any]]", body["messages"][position]["content"])


# -- one reply -----------------------------------------------------------------------------------


def test_text_and_reasoning_arrive_in_pieces_and_the_reply_keeps_its_blocks(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(
        FakeReply(
            text="Buy bread.",
            reasoning="The user wants the notes.",
            usage=FakeUsage(input=1_000, output=200, cached=6_000, cache_write=3_000),
        )
    )
    models = connect(fake, "anthropic")

    async def scenario() -> list[ModelEvent]:
        client = models.client("anthropic", OPUS)
        return [event async for event in client.stream(ModelRequest(QUESTION))]

    events = run(models, scenario)

    assert "".join(e.text for e in events if isinstance(e, TextDelta)) == "Buy bread."
    thoughts = "".join(e.text for e in events if isinstance(e, ReasoningDelta))
    assert thoughts == "The user wants the notes."
    done = events[-1]
    assert isinstance(done, ReplyDone)
    reply = done.reply
    assert (reply.text, reply.message.reasoning, reply.finish) == (
        "Buy bread.",
        "The user wants the notes.",
        Finish.STOP,
    )
    assert reply.message.origin == Origin("anthropic", OPUS)
    content = cast("list[dict[str, Any]]", reply.message.provider_fields["content"])
    assert [block["type"] for block in content] == ["thinking", "text"]
    assert str(content[0]["signature"]).startswith("sig-")
    assert isinstance(reply.message.provider_fields["prefix"], str)
    # Anthropic's input is only what followed the last cache point; Atlas's includes the cache.
    assert reply.usage is not None
    assert (reply.usage.input_tokens, reply.usage.cache_read_tokens) == (10_000, 6_000)
    assert reply.usage.cache_write_tokens == 3_000
    body = _bodies(fake)[0]
    assert body["thinking"] == {"type": "adaptive", "display": "summarized"}
    # The provider's ceiling, 32,000, below the model's 128,000: one Opus call that
    # reasoned in a loop to the model's limit cost US$ 2.68 (14/10/2026).
    assert body["max_tokens"] == 32_000
    assert body["system"] == [
        {"type": "text", "text": "You are Atlas.", "cache_control": {"type": "ephemeral"}}
    ]
    assert "temperature" not in body


def test_the_key_goes_alone_even_with_anthropic_variables_in_the_environment(
    fake: FakeModelServer, connect: Connect, run: Run, monkeypatch: pytest.MonkeyPatch
):
    """The SDK can send ANTHROPIC_AUTH_TOKEN beside the key; the fake refuses a request with an
    Authorization header."""
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "a-token-of-claude-code")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "another-key")
    fake.reply(FakeReply(text="ok"))
    models = connect(fake, "anthropic")

    reply = run(models, lambda: _call(models, ModelRequest(QUESTION)))

    assert reply.text == "ok"
    headers = fake.requests[-1].headers
    assert headers["x-api-key"] == "fake-key"
    assert "authorization" not in headers


def test_the_effort_goes_in_output_config(fake: FakeModelServer, connect: Connect, run: Run):
    fake.reply(FakeReply(text="ok"))
    models = connect(fake, "anthropic")

    async def scenario() -> ModelReply:
        client = models.client("anthropic", OPUS, effort="high")
        return await client.reply(ModelRequest(QUESTION))

    run(models, scenario)

    assert _bodies(fake)[0]["output_config"] == {"effort": "high"}


def test_the_output_ceiling_is_the_providers_raised_in_models_toml_never_past_the_models(
    home: Home,
) -> None:
    home.models_file.write_text("[providers.anthropic]\nmax_output = 64000\n", encoding="utf-8")
    raised = load_catalog(home.models_file)
    shipped = load_catalog(home.models_file.with_name("absent.toml"))
    opus = shipped.model(shipped.provider("anthropic"), OPUS)

    def ceiling(catalog: Any, request: ModelRequest, info: Any = opus) -> int:
        call = messages_request(catalog.provider("anthropic"), OPUS, request, info, None)
        return int(call.body["max_tokens"])

    assert ceiling(shipped, ModelRequest(QUESTION)) == 32_000
    assert ceiling(raised, ModelRequest(QUESTION)) == 64_000
    # A request's own ceiling (a compaction's summary) wins, under the model's limit.
    assert ceiling(shipped, ModelRequest(QUESTION, max_output_tokens=2_500)) == 2_500
    assert ceiling(shipped, ModelRequest(QUESTION, max_output_tokens=500_000)) == 128_000
    home.models_file.write_text("[providers.anthropic]\nmax_output = 900000\n", encoding="utf-8")
    assert ceiling(load_catalog(home.models_file), ModelRequest(QUESTION)) == 128_000


# -- the reasoning, sealed -----------------------------------------------------------------------


def test_the_reasoning_goes_back_sealed_through_a_round_of_tools(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(
        FakeReply(
            reasoning="Read the notes first.",
            text="Let me read them.",
            calls=[FakeCall("read_file", '{"path": "notes.md"}')],
        ),
        FakeReply(text="Buy bread.", reasoning="Now answer."),
    )
    models = connect(fake, "anthropic")
    first = run(models, lambda: _call(models, ModelRequest(QUESTION, (READ_FILE,))))
    after = ModelRequest(
        (*QUESTION, first.message, ToolResult(first.tool_calls[0].id, "buy bread")), (READ_FILE,)
    )

    second = run(models, lambda: _call(models, after))

    assert second.text == "Buy bread."
    replayed = _blocks(_bodies(fake)[1], 1)
    # In the order they came, with the signature the fake checks against the unchanged past.
    assert [block["type"] for block in replayed] == ["thinking", "text", "tool_use"]
    assert replayed[0]["thinking"] == "Read the notes first."
    assert replayed[2]["input"] == {"path": "notes.md"}
    assert _blocks(_bodies(fake)[1], 2)[0]["type"] == "tool_result"


def test_an_edited_past_drops_the_reasoning_from_there_on_and_the_request_is_taken(
    fake: FakeModelServer, connect: Connect, run: Run
):
    """A compaction or new instructions change the past; the reasoning written on the old one is
    a 400 on an account made after 31/08/2026. Atlas drops it from the first changed point on."""
    fake.reply(
        FakeReply(reasoning="Think.", text="Reading.", calls=[FakeCall("read_file", "{}")]),
        FakeReply(text="Done."),
    )
    models = connect(fake, "anthropic")
    first = run(models, lambda: _call(models, ModelRequest(QUESTION, (READ_FILE,))))
    edited = (
        SystemMessage("You are Atlas. The memory changed."),
        QUESTION[1],
        first.message,
        ToolResult(first.tool_calls[0].id, "buy bread"),
    )

    second = run(models, lambda: _call(models, ModelRequest(edited, (READ_FILE,))))

    assert second.text == "Done."
    assert [block["type"] for block in _blocks(_bodies(fake)[1], 1)] == ["text", "tool_use"]


def test_the_fake_refuses_a_sealed_block_whose_past_changed(fake: FakeModelServer):
    """The control of the test above: the fake is as strict as Anthropic."""
    first = {
        "model": OPUS,
        "max_tokens": 10,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Hi"}]}],
    }
    signature = _anthropic_signature(first, 1)
    changed = {
        **first,
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "t", "signature": signature},
                    {"type": "text", "text": "x"},
                ],
            },
            {"role": "user", "content": [{"type": "text", "text": "More"}]},
        ],
    }
    refused = fake._messages_refusal(changed, "")  # pyright: ignore[reportPrivateUsage]

    assert refused is not None
    assert b"Invalid `signature`" in refused.body


def test_the_reasoning_of_another_model_never_goes_back(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(FakeReply(reasoning="Opus thinks.", text="Hi."), FakeReply(text="Sonnet here."))
    models = connect(fake, "anthropic")
    first = run(models, lambda: _call(models, ModelRequest(QUESTION)))
    later = ModelRequest((*QUESTION, first.message, UserMessage("And now?")))

    run(models, lambda: _call(models, later, "claude-sonnet-5-5"))

    assert _blocks(_bodies(fake)[1], 1) == [{"type": "text", "text": "Hi."}]


# -- the cache and the clearing ------------------------------------------------------------------


def test_a_conversation_paced_by_a_person_caches_for_an_hour_and_reads_it_back(
    fake: FakeModelServer, connect: Connect, run: Run, home: Home
):
    """Four cache points at most: the system prompt, the last tool, the ends of the last two
    completed rounds. The second call reads what the first wrote, and the hour-long write is
    priced at 2x the input (Opus 5.5: US$ 8 a million)."""
    fake.reply(
        FakeReply(text="Reading.", calls=[FakeCall("read_file", "{}")]), FakeReply(text="Done.")
    )
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    models = connect(fake, "anthropic", ledger=CallLedger(database))
    first = run(
        models, lambda: _call(models, ModelRequest(QUESTION, (READ_FILE,), long_cache=True))
    )
    after = ModelRequest(
        (*QUESTION, first.message, ToolResult(first.tool_calls[0].id, "x" * 4_000)),
        (READ_FILE,),
        long_cache=True,
    )

    second = run(models, lambda: _call(models, after))

    body = _bodies(fake)[1]
    # The system prompt, the last tool and the round that just ended: the user's question opened
    # a round, it did not end one.
    marks = json.dumps(body).count('"cache_control"')
    assert marks == 3
    assert body["tools"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert first.usage is not None
    assert second.usage is not None
    assert first.usage.cache_write_long_tokens == first.usage.cache_write_tokens > 0
    assert second.usage.cache_read_tokens >= first.usage.cache_write_tokens
    record = CallLedger(database).recent()[0]
    assert record.usage is not None
    assert record.usage.cache_write_long_tokens == second.usage.cache_write_long_tokens
    assert record.cost.source is CostSource.PRICE_LIST
    usage = second.usage
    expected = (
        Decimal(usage.new_input_tokens) * 4
        + Decimal(usage.cache_read_tokens) * Decimal("0.2")
        + Decimal(usage.cache_write_long_tokens) * 8
        + Decimal(usage.output_tokens) * 20
    ) / 1_000_000
    assert second.cost.usd == expected


def test_a_call_a_machine_paces_keeps_the_five_minute_cache(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(FakeReply(text="ok"))
    models = connect(fake, "anthropic")

    run(models, lambda: _call(models, ModelRequest(QUESTION, (READ_FILE,))))

    assert _bodies(fake)[0]["tools"][-1]["cache_control"] == {"type": "ephemeral"}


def test_old_results_are_cleared_by_anthropic_with_the_loops_rule(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(FakeReply(text="ok"))
    models = connect(fake, "anthropic")

    run(models, lambda: _call(models, ModelRequest(QUESTION, (READ_FILE,))))

    body = _bodies(fake)[0]
    assert body["context_management"] == {
        "edits": [
            {
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "input_tokens", "value": 100_000},
                "keep": {"type": "tool_uses", "value": 3},
                "clear_at_least": {"type": "input_tokens", "value": 20_000},
                "exclude_tools": ["tool_search"],
            }
        ]
    }
    assert "context-management-2025-06-27" in fake.requests[-1].headers["anthropic-beta"]


# -- deferred tools by reference -----------------------------------------------------------------


def test_deferred_tools_travel_on_every_call_and_the_search_loads_them_by_reference(
    fake: FakeModelServer, connect: Connect, run: Run
):
    """The tool list never changes, so neither the cache nor the reasoning breaks; the fake refuses
    a reference to a tool the request does not carry deferred."""
    fake.reply(
        FakeReply(reasoning="Search.", calls=[FakeCall("tool_search", '{"query": "calendar"}')]),
        FakeReply(text="You are free today."),
    )
    models = connect(fake, "anthropic")
    deferred = (CALENDAR, MAIL)
    first = run(
        models,
        lambda: _call(
            models, ModelRequest(QUESTION, (READ_FILE, TOOL_SEARCH), deferred_tools=deferred)
        ),
    )
    found = ToolResult(first.tool_calls[0].id, "Loaded calendar_list.", loaded=("calendar_list",))
    after = ModelRequest(
        (*QUESTION, first.message, found),
        (READ_FILE, TOOL_SEARCH),
        loaded_tools=(CALENDAR,),
        deferred_tools=deferred,
    )

    run(models, lambda: _call(models, after))

    first_body, second_body = _bodies(fake)
    assert first_body["tools"] == second_body["tools"]
    assert [(t["name"], t.get("defer_loading", False)) for t in second_body["tools"]] == [
        ("read_file", False),
        ("tool_search", False),
        ("calendar_list", True),
        ("mail_send", True),
    ]
    result = _blocks(second_body, 2)[0]
    # The references alone: Anthropic refuses them beside any other content (the real proof).
    assert result["content"] == [{"type": "tool_reference", "tool_name": "calendar_list"}]
    # The same tools on both calls: the reasoning of the first went back sealed.
    assert _blocks(second_body, 1)[0]["type"] == "thinking"


def test_without_the_deferred_tools_the_loaded_ones_join_the_list(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(FakeReply(text="ok"))
    models = connect(fake, "anthropic")

    run(
        models,
        lambda: _call(models, ModelRequest(QUESTION, (READ_FILE,), loaded_tools=(CALENDAR,))),
    )

    tools = _bodies(fake)[0]["tools"]
    assert [tool["name"] for tool in tools] == ["read_file", "calendar_list"]
    assert not any(tool.get("defer_loading") for tool in tools)


# -- the conversion's rules ----------------------------------------------------------------------


def test_the_conversion_follows_what_anthropic_takes(
    fake: FakeModelServer, connect: Connect, run: Run
):
    """The rules: results join one user message with what the user sent meanwhile after them;
    blank blocks never go; a history starting with the assistant gets a user turn; ids are
    sanitized; a schema loses its top-level union; a repeated tool goes; jpg is jpeg and an image
    Anthropic does not read becomes a note; a later system message is the user's text."""
    fake.reply(FakeReply(text="ok"))
    models = connect(fake, "anthropic")
    union = ToolSpec("pick", "Pick one.", {"anyOf": [{"type": "object"}], "type": "object"})
    calls = (ToolCall("call.1:x", "read_file", "{}"), ToolCall("call.2", "read_file", "not json"))
    history = (
        SystemMessage("You are Atlas."),
        AssistantMessage(text="A summary of earlier."),
        UserMessage("   "),
        AssistantMessage(text="", tool_calls=calls),
        ToolResult("call.1:x", "", images=(Image("image/jpg", b"\xff\xd8"),)),
        ToolResult("call.2", "two", images=(Image("image/bmp", b"BM"),)),
        UserMessage("Also check the mail."),
        SystemMessage("Atlas: the model changed."),
    )

    run(models, lambda: _call(models, ModelRequest(history, (READ_FILE, union, READ_FILE))))

    body = _bodies(fake)[0]
    roles = [message["role"] for message in body["messages"]]
    assert roles == ["user", "assistant", "user", "assistant", "user"]
    assert _blocks(body, 0)[0]["text"] == "(empty)"
    assert _blocks(body, 2)[0]["text"] == "(empty message)"
    assert [block["id"] for block in _blocks(body, 3)] == ["call_1_x", "call_2"]
    assert _blocks(body, 3)[1]["input"] == {}
    results = _blocks(body, 4)
    assert [block["type"] for block in results] == ["tool_result", "tool_result", "text", "text"]
    assert results[0]["tool_use_id"] == "call_1_x"
    assert results[0]["content"][0]["source"]["media_type"] == "image/jpeg"
    assert "image/bmp" in results[1]["content"][1]["text"]
    assert [block["text"] for block in results[2:]] == [
        "Also check the mail.",
        "Atlas: the model changed.",
    ]
    assert [tool["name"] for tool in body["tools"]] == ["read_file", "pick"]
    assert "anyOf" not in body["tools"][1]["input_schema"]


# -- the stream ----------------------------------------------------------------------------------


def test_a_reply_without_its_end_is_not_kept(fake: FakeModelServer, connect: Connect, run: Run):
    fake.reply(FakeReply(text="Half of an answer", cut_after=3))
    models = connect(fake, "anthropic")

    with pytest.raises(ModelUnavailableError, match="closed the reply before it finished"):
        run(models, lambda: _call(models, ModelRequest(QUESTION)))


def test_a_call_whose_input_did_not_arrive_whole_never_runs(
    fake: FakeModelServer, connect: Connect, run: Run
):
    """A call cut by the output limit never runs with `{}`."""
    fake.reply(
        FakeReply(
            text="Let me look.",
            calls=[FakeCall("read_file", '{"path": "no')],
            finish="max_tokens",
        )
    )
    models = connect(fake, "anthropic")

    reply = run(models, lambda: _call(models, ModelRequest(QUESTION, (READ_FILE,))))

    assert (reply.tool_calls, reply.finish, reply.text) == ((), Finish.LENGTH, "Let me look.")


def test_a_refusal_is_a_filtered_finish(fake: FakeModelServer, connect: Connect, run: Run):
    fake.reply(FakeReply(text="", finish="refusal"))
    models = connect(fake, "anthropic")

    reply = run(models, lambda: _call(models, ModelRequest(QUESTION)))

    assert reply.finish is Finish.FILTERED


# -- errors and the key --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("error", "kind", "words"),
    [
        (
            FakeError(401, "invalid x-api-key"),
            ModelAuthError,
            "atlas secrets set ANTHROPIC_API_KEY",
        ),
        (FakeError(402, "Payment required."), ModelOutOfCreditError, "cannot pay"),
        (
            FakeError(400, "Your credit balance is too low to access the Anthropic API."),
            ModelOutOfCreditError,
            "cannot pay",
        ),
        (
            FakeError(
                429, "You have reached your specified API usage limits.", retry_after_ms=None
            ),
            ModelRateLimitError,
            "It passes",
        ),
        (
            FakeError(429, "This request would exceed your organization's monthly spend limit."),
            ModelOutOfCreditError,
            "waiting does not help",
        ),
        (FakeError(529, "Overloaded"), ModelUnavailableError, "overloaded"),
        (
            FakeError(400, "prompt is too long: 1200000 tokens > 1000000 maximum"),
            ModelRequestError,
            "prompt is too long",
        ),
    ],
)
def test_each_error_says_what_happened_and_what_fixes_it(
    fake: FakeModelServer,
    connect: Connect,
    run: Run,
    error: FakeError,
    kind: type[Exception],
    words: str,
):
    fake.reply(error)
    models = connect(fake, "anthropic", retries=0)

    with pytest.raises(kind, match=words):
        run(models, lambda: _call(models, ModelRequest(QUESTION)))


def test_an_error_inside_the_stream_ends_the_call(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(
        FakeReply(
            text="Starting", error_in_stream={"type": "overloaded_error", "message": "Overloaded"}
        )
    )
    models = connect(fake, "anthropic")

    with pytest.raises(ModelUnavailableError, match=r"overloaded \(during the reply\)"):
        run(models, lambda: _call(models, ModelRequest(QUESTION)))


def test_the_key_is_checked_on_the_models_list_where_it_costs_nothing(
    fake: FakeModelServer, connect: Connect, run: Run
):
    models = connect(fake, "anthropic")

    good = run(models, lambda: models.check_key("anthropic"))
    fake.refuse_check(FakeError(401, "invalid x-api-key"))
    bad = run(models, lambda: models.check_key("anthropic"))

    assert (good.status.value, good.detail) == ("valid", "The key opens Anthropic.")
    assert bad.status.value == "refused"
    assert [request.path for request in fake.requests] == ["/v1/models", "/v1/models"]
    assert fake.bodies(MESSAGES) == []
