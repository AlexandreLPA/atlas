"""Chat Completions through the real SDK, against the fake model server over real HTTP.

Each built-in provider keeps its real quirks and catalog data; only its address points at the fake.
The fake behaves like the provider in question (strict schema, Gemini's thought signatures and its
refusal of a trailing assistant message, OpenRouter's cost), so a test passes only when what goes
over the wire is what that provider accepts. Ported from V3's test_model_factory.py,
test_provider_keys.py and test_trailing_model_turn.py, which patched `ModelFactory.get_model` and
`requests.get` instead.
"""

import asyncio
from contextlib import aclosing
from decimal import Decimal

import pytest

from atlas.home import ModelSettings
from atlas.models import (
    AssistantMessage,
    CostSource,
    Finish,
    ModelAuthError,
    ModelChoiceError,
    ModelEvent,
    ModelInfo,
    ModelKeyError,
    ModelOutOfCreditError,
    ModelRateLimitError,
    ModelReply,
    ModelRequest,
    ModelRequestError,
    ModelsError,
    ModelUnavailableError,
    ReasoningDelta,
    ReplyDone,
    SystemMessage,
    TextDelta,
    ToolResult,
    ToolSpec,
    UserMessage,
)
from atlas.models.chat import ChatReader, chat_request
from atlas.secrets import Vault
from fake_model import FakeCall, FakeError, FakeModelServer, FakeReply, FakeUsage
from model_support import Connect, Run

READ_FILE = ToolSpec(
    name="read_file",
    description="Read a text file.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)
QUESTION = (SystemMessage("You are Atlas."), UserMessage("What is in today's notes?"))


def test_the_text_arrives_in_pieces_and_whole(fake: FakeModelServer, connect: Connect, run: Run):
    fake.reply(FakeReply(text="Your notes say: buy bread, call the plumber."))
    models = connect(fake, "gemini")

    async def scenario() -> list[ModelEvent]:
        client = models.client("gemini")
        return [event async for event in client.stream(ModelRequest(QUESTION))]

    events = run(models, scenario)

    deltas = [event.text for event in events if isinstance(event, TextDelta)]
    assert len(deltas) > 1
    assert "".join(deltas) == "Your notes say: buy bread, call the plumber."
    done = events[-1]
    assert isinstance(done, ReplyDone)
    assert done.reply.text == "Your notes say: buy bread, call the plumber."
    assert done.reply.finish is Finish.STOP
    assert done.reply.served_model == "gemini-3.8-flash"
    assert done.reply.usage is not None
    (request,) = fake.requests
    assert request.headers["authorization"] == "Bearer fake-key"
    assert request.body is not None
    assert request.body["model"] == "gemini-3.8-flash"
    assert request.body["stream"] is True
    assert request.body["stream_options"] == {"include_usage": True}
    assert request.body["messages"] == [
        {"role": "system", "content": "You are Atlas."},
        {"role": "user", "content": "What is in today's notes?"},
    ]


def test_a_tool_call_streamed_in_fragments_is_whole_again(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(FakeReply(calls=[FakeCall("read_file", '{"path": "notes/today.md"}', id="c1")]))
    models = connect(fake, "openrouter")

    reply = run(
        models, lambda: models.client("openrouter").reply(ModelRequest(QUESTION, (READ_FILE,)))
    )

    assert reply.finish is Finish.TOOL_CALLS
    (call,) = reply.tool_calls
    assert (call.id, call.name, call.arguments) == ("c1", "read_file", '{"path": "notes/today.md"}')
    (body,) = fake.bodies("/v1/chat/completions")
    assert body["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file.",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]


def test_parallel_calls_sent_whole_without_an_index_stay_apart(connect: Connect, run: Run):
    # Gemini sends each parallel call whole in its own chunk with no index (09/10/2026); read by
    # position, the two became one call with both argument strings glued together.
    with FakeModelServer(signatures=True, whole_calls=True) as gemini:
        gemini.reply(
            FakeReply(
                calls=[
                    FakeCall("today", "{}", id="call_1"),
                    FakeCall("tool_search", '{"query": "notes"}', id="call_2"),
                ]
            )
        )
        models = connect(gemini, "gemini")

        reply = run(
            models, lambda: models.client("gemini").reply(ModelRequest(QUESTION, (READ_FILE,)))
        )

    assert [(c.id, c.name, c.arguments) for c in reply.tool_calls] == [
        ("call_1", "today", "{}"),
        ("call_2", "tool_search", '{"query": "notes"}'),
    ]
    assert [c.provider_fields for c in reply.tool_calls] == [
        {"extra_content": {"google": {"thought_signature": "sig-1-0"}}},
        {"extra_content": {"google": {"thought_signature": "sig-1-1"}}},
    ]


def _after_the_tool(reply: ModelReply) -> ModelRequest:
    return ModelRequest(
        (*QUESTION, reply.message, ToolResult(reply.tool_calls[0].id, "buy bread")), (READ_FILE,)
    )


def test_gemini_gets_its_thought_signature_back_and_a_strict_provider_never_sees_it(
    connect: Connect, run: Run
):
    with (
        FakeModelServer(signatures=True, strict_fields=frozenset({"extra_content"})) as gemini,
        FakeModelServer(strict_fields=frozenset(), key=None) as strict,
    ):
        gemini.reply(FakeReply(calls=[FakeCall("read_file", '{"path": "today.md"}')]))
        gemini.reply(FakeReply(text="Buy bread."))
        strict.reply(FakeReply(text="Buy bread."))
        connect(gemini, "gemini")
        models = connect(strict, "vllm")  # the catalog now points both at their fakes

        async def scenario() -> tuple[ModelReply, ModelReply, ModelReply]:
            first = await models.client("gemini").reply(ModelRequest(QUESTION, (READ_FILE,)))
            second = await models.client("gemini").reply(_after_the_tool(first))
            elsewhere = await models.client("vllm", "qwen3").reply(_after_the_tool(first))
            return first, second, elsewhere

        first, second, elsewhere = run(models, scenario)

    signature = {"google": {"thought_signature": "sig-1-0"}}
    assert first.tool_calls[0].provider_fields == {"extra_content": signature}
    assert second.text == "Buy bread."
    sent = gemini.bodies("/v1/chat/completions")[1]["messages"][2]
    assert sent["tool_calls"][0]["extra_content"] == signature
    assert elsewhere.text == "Buy bread."
    stripped = strict.bodies("/v1/chat/completions")[0]["messages"][2]
    assert "extra_content" not in stripped["tool_calls"][0]
    # The stored history still has it, for a return to Gemini.
    assert first.message.tool_calls[0].provider_fields == {"extra_content": signature}


def test_gemini_refuses_a_history_that_lost_the_signature(connect: Connect, run: Run):
    # The fake's check is real: without the field, Gemini's HTTP 400 comes back.
    with FakeModelServer(signatures=True) as gemini:
        gemini.reply(FakeReply(calls=[FakeCall("read_file", "{}")]))
        models = connect(gemini, "gemini")

        async def scenario() -> None:
            first = await models.client("gemini").reply(ModelRequest(QUESTION, (READ_FILE,)))
            call = first.tool_calls[0]
            bare = AssistantMessage(tool_calls=(type(call)(call.id, call.name, call.arguments),))
            request = ModelRequest((*QUESTION, bare, ToolResult(call.id, "x")), (READ_FILE,))
            await models.client("gemini").reply(request)

        with pytest.raises(ModelRequestError, match="missing a thought_signature"):
            run(models, scenario)


def test_openrouter_reasoning_details_are_joined_and_go_back_only_to_their_model(
    connect: Connect, run: Run
):
    fragments = [
        {"type": "reasoning.text", "text": "The user ", "index": 0, "format": "anthropic"},
        {"type": "reasoning.text", "text": "wants notes.", "index": 0, "signature": "s1"},
        {"type": "reasoning.encrypted", "data": "opaque", "index": 1},
    ]
    with FakeModelServer(usage_always=True) as openrouter:
        openrouter.reply(
            FakeReply(
                reasoning="The user wants notes.",
                detail_fragments=fragments,
                calls=[FakeCall("read_file", "{}")],
                usage=FakeUsage(input=1200, output=80, cost=0.00042),
            ),
            FakeReply(text="Buy bread."),
            FakeReply(text="Buy bread."),
        )
        models = connect(openrouter, "openrouter")

        async def scenario() -> tuple[list[ModelEvent], ModelReply]:
            client = models.client("openrouter", "anthropic/claude-sonnet-5")
            events = [event async for event in client.stream(ModelRequest(QUESTION, (READ_FILE,)))]
            done = events[-1]
            assert isinstance(done, ReplyDone)
            await client.reply(_after_the_tool(done.reply))
            other = models.client("openrouter", "openai/gpt-6-luna")
            await other.reply(_after_the_tool(done.reply))
            return events, done.reply

        events, first = run(models, scenario)

    joined = [
        {
            "type": "reasoning.text",
            "text": "The user wants notes.",
            "index": 0,
            "format": "anthropic",
            "signature": "s1",
        },
        {"type": "reasoning.encrypted", "data": "opaque", "index": 1},
    ]
    assert first.message.provider_fields["reasoning_details"] == joined
    assert first.message.reasoning == "The user wants notes."
    assert (
        "".join(e.text for e in events if isinstance(e, ReasoningDelta)) == first.message.reasoning
    )
    assert first.cost.source is CostSource.REPORTED
    assert first.cost.usd == Decimal("0.00042")
    same, other = openrouter.bodies("/v1/chat/completions")[1:]
    assert same["messages"][2]["reasoning_details"] == joined
    assert "reasoning_details" not in other["messages"][2]


@pytest.mark.parametrize(
    ("provider", "model", "field", "value"),
    [
        ("openrouter", "openai/gpt-6-luna", "reasoning", {"effort": "high"}),
        # Inside the thinking_config that asks for readable reasoning: Gemini refuses
        # reasoning_effort beside it (seen on 10/10/2026, and the fake refuses it too).
        (
            "gemini",
            "gemini-3.8-flash",
            "extra_body",
            {"google": {"thinking_config": {"thinking_level": "high", "include_thoughts": True}}},
        ),
    ],
)
def test_the_effort_goes_as_each_provider_reads_it(
    fake: FakeModelServer,
    connect: Connect,
    run: Run,
    provider: str,
    model: str,
    field: str,
    value: object,
):
    fake.reply(FakeReply(text="ok"))
    models = connect(fake, provider)

    run(models, lambda: models.client(provider, model, effort="high").reply(ModelRequest(QUESTION)))

    (body,) = fake.bodies("/v1/chat/completions")
    assert body[field] == value


@pytest.mark.parametrize(
    ("model", "effort", "tools", "message"),
    [
        ("gpt-6-astra", "none", (), r"does not accept the effort 'none'\. It accepts: low"),
        ("gpt-4.1", "low", (), "does not reason, so it takes no effort"),
        ("text-embedding-3-small", None, (READ_FILE,), "cannot call tools"),
    ],
)
def test_what_a_model_refuses_is_refused_before_sending(
    fake: FakeModelServer,
    connect: Connect,
    run: Run,
    model: str,
    effort: str | None,
    tools: tuple[ToolSpec, ...],
    message: str,
):
    # V3 kept such models out of its catalog by hand, because each one failed on the first turn.
    models = connect(fake, "openai")
    request = ModelRequest(QUESTION, tools)

    with pytest.raises(ModelChoiceError, match=message):
        run(models, lambda: models.client("openai", model, effort=effort).reply(request))
    assert fake.requests == []


def test_gemini_never_gets_a_request_that_ends_with_an_assistant_message(
    fake: FakeModelServer, connect: Connect, run: Run
):
    # Atlas V3 on 07/10/2026: notes appended as model messages made Gemini answer 400 twice.
    models = connect(fake, "gemini")
    request = ModelRequest((*QUESTION, AssistantMessage("A note for the model.")))

    with pytest.raises(ModelRequestError, match="ends with an assistant message"):
        run(models, lambda: models.client("gemini").reply(request))
    assert fake.requests == []


def test_the_usage_is_read_and_priced_from_the_catalog(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(
        FakeReply(
            text="ok", usage=FakeUsage(input=10_000, output=1_000, cached=8_000, reasoning=200)
        )
    )
    models = connect(fake, "gemini")

    reply = run(models, lambda: models.client("gemini").reply(ModelRequest(QUESTION)))

    assert reply.usage is not None
    assert (reply.usage.input_tokens, reply.usage.cache_read_tokens) == (10_000, 8_000)
    assert (reply.usage.output_tokens, reply.usage.reasoning_tokens) == (1_000, 200)
    # gemini-3.8-flash: 2k new at 0.75, 8k cached at 0.075, 1k out at 3.75, per million.
    assert reply.cost.source is CostSource.PRICE_LIST
    assert reply.cost.usd == Decimal("0.00585")


def test_a_local_server_takes_no_key_and_costs_a_known_zero(connect: Connect, run: Run):
    with FakeModelServer(key=None) as local:
        local.reply(FakeReply(text="ok"))
        models = connect(local, "ollama")
        reply = run(
            models, lambda: models.client("ollama", "llama3.3").reply(ModelRequest(QUESTION))
        )

    assert reply.cost.source is CostSource.FREE
    assert reply.cost.usd == Decimal(0)
    assert local.requests[0].headers["authorization"] == "Bearer no-key"


def test_a_missing_key_names_the_command_that_stores_it(fake: FakeModelServer, connect: Connect):
    models = connect(fake, "openrouter", key=None)
    with pytest.raises(ModelKeyError, match="atlas secrets set OPENROUTER_API_KEY"):
        models.client("openrouter")


def test_a_refused_key_says_how_to_replace_it(fake: FakeModelServer, connect: Connect, run: Run):
    models = connect(fake, "openrouter", key="a-wrong-key")
    with pytest.raises(ModelAuthError, match="atlas secrets set OPENROUTER_API_KEY"):
        run(models, lambda: models.client("openrouter").reply(ModelRequest(QUESTION)))


@pytest.mark.parametrize(
    ("error", "kind", "message"),
    [
        (FakeError(402, "Insufficient credits."), ModelOutOfCreditError, "no credit left"),
        (
            FakeError(
                429, "You exceeded your quota.", code="insufficient_quota", should_retry=False
            ),
            ModelOutOfCreditError,
            "waiting does not help",
        ),
        (
            # OpenAI's empty account, as Atlas V3 saw it: the code says it, not the status.
            FakeError(
                429,
                "Your credit balance is exhausted.",
                code="credit_balance_exhausted",
                type="insufficient_quota",
                should_retry=False,
            ),
            ModelOutOfCreditError,
            "waiting does not help",
        ),
        (FakeError(429, "Slow down.", should_retry=False), ModelRateLimitError, "It passes"),
        (FakeError(400, "Bad schema."), ModelRequestError, r"refused the request \(HTTP 400\)"),
        (FakeError(503, "Overloaded.", should_retry=False), ModelUnavailableError, "HTTP 503"),
    ],
)
def test_each_failure_says_what_fixes_it(
    fake: FakeModelServer,
    connect: Connect,
    run: Run,
    error: FakeError,
    kind: type[ModelsError],
    message: str,
):
    fake.reply(error)
    models = connect(fake, "openrouter")
    with pytest.raises(kind, match=message):
        run(models, lambda: models.client("openrouter").reply(ModelRequest(QUESTION)))


#: Gemini's answer when the free tier's daily quota ran out, as it came on 08/10/2026.
GEMINI_QUOTA = (
    "You exceeded your current quota, please check your plan and billing details.\n"
    "* Quota exceeded for metric: generate_content_free_tier_requests, limit: 20\n"
    "Please retry in 22h27m2.87387338s."
)


@pytest.mark.parametrize(
    ("delay", "kind", "message"),
    [
        ("80822s", ModelOutOfCreditError, r"quota is used up.*resets in about 22 hours"),
        ("20s", ModelRateLimitError, "It passes"),
    ],
)
def test_gemini_says_how_long_its_limit_lasts_and_that_decides_the_kind(
    fake: FakeModelServer,
    connect: Connect,
    run: Run,
    delay: str,
    kind: type[ModelsError],
    message: str,
):
    # The same HTTP 429 RESOURCE_EXHAUSTED and the same text for a per-minute limit and a daily
    # quota; only the stated wait tells them apart. The body is a list, not OpenAI's object.
    fake.reply(
        FakeError(
            429, GEMINI_QUOTA, google="RESOURCE_EXHAUSTED", retry_delay=delay, should_retry=False
        )
    )
    models = connect(fake, "gemini")

    with pytest.raises(kind, match=message) as raised:
        run(models, lambda: models.client("gemini").reply(ModelRequest(QUESTION)))

    assert str(raised.value).startswith(
        "Google Gemini " + ("says the quota" if delay == "80822s" else "is limiting")
    )
    assert "(HTTP 429): You exceeded your current quota" in str(raised.value)


def test_a_long_retry_after_is_a_used_up_quota(fake: FakeModelServer, connect: Connect, run: Run):
    fake.reply(FakeError(429, "Try later.", retry_after_ms=3_600_000, should_retry=False))
    models = connect(fake, "openrouter")
    with pytest.raises(ModelOutOfCreditError, match="resets in about 60 minutes"):
        run(models, lambda: models.client("openrouter").reply(ModelRequest(QUESTION)))


def test_a_server_error_is_retried_and_the_reply_still_comes(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(
        FakeError(500, "Try again.", type="server_error", retry_after_ms=10), FakeReply(text="ok")
    )
    models = connect(fake, "openrouter", retries=2)

    reply = run(models, lambda: models.client("openrouter").reply(ModelRequest(QUESTION)))

    assert reply.text == "ok"
    assert len(fake.requests) == 2


def test_stopping_mid_reply_closes_the_providers_stream_at_once(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(FakeReply(text="A long answer, word after word, " * 20, pause=0.02))
    models = connect(fake, "openrouter")

    async def scenario() -> str:
        client = models.client("openrouter")
        first = ""
        async with aclosing(client.stream(ModelRequest(QUESTION))) as events:
            async for event in events:
                if isinstance(event, TextDelta):
                    first = event.text
                    break
        for _ in range(100):  # the server notices the closed connection within moments
            if fake.abandoned:
                break
            await asyncio.sleep(0.02)
        return first

    first = run(models, scenario)

    assert first == "A lo"
    assert fake.abandoned == 1


def test_a_stream_cut_before_the_end_is_no_reply(fake: FakeModelServer, connect: Connect, run: Run):
    fake.reply(FakeReply(text="A long answer that never ends", cut_after=3))
    models = connect(fake, "openrouter")
    with pytest.raises(ModelUnavailableError, match="closed the reply before it finished"):
        run(models, lambda: models.client("openrouter").reply(ModelRequest(QUESTION)))


def test_an_error_inside_the_stream_is_reported(fake: FakeModelServer, connect: Connect, run: Run):
    fake.reply(FakeReply(text="Partial", error_in_stream={"message": "Overloaded.", "code": 502}))
    models = connect(fake, "openrouter")
    with pytest.raises(ModelUnavailableError, match="during the reply"):
        run(models, lambda: models.client("openrouter").reply(ModelRequest(QUESTION)))


@pytest.mark.parametrize(
    "reply",
    [
        # Gemini on 07/10/2026: the reply opened, then nothing came for 10 minutes.
        FakeReply(text="It starts, and then the provider goes quiet.", pause=2.0),
        FakeReply(text="Nothing comes back, not even the status.", wait_before=2.0),
    ],
)
def test_a_provider_that_goes_silent_is_given_up(
    fake: FakeModelServer, connect: Connect, run: Run, reply: FakeReply
):
    fake.reply(reply)
    models = connect(fake, "gemini", silence=0.3)
    with pytest.raises(ModelUnavailableError, match=r"sent nothing for 0\.3 seconds"):
        run(models, lambda: models.client("gemini").reply(ModelRequest(QUESTION)))


def test_an_unreachable_provider_says_where_it_looked(connect: Connect, run: Run):
    models = connect(None, "openrouter")  # an address where nothing listens
    with pytest.raises(
        ModelUnavailableError, match=r"could not be reached at http://127\.0\.0\.1:9"
    ):
        run(models, lambda: models.client("openrouter").reply(ModelRequest(QUESTION)))


def test_without_a_choice_the_settings_decide(fake: FakeModelServer, connect: Connect):
    models = connect(fake, "gemini", settings=ModelSettings(provider="gemini", effort="low"))
    client = models.client()
    assert (client.provider.id, client.model, client.effort) == (
        "gemini",
        "gemini-3.8-flash",
        "low",
    )
    assert client.info is not None
    assert client.info.context_window == 1_048_576


def test_no_model_chosen_and_no_key_says_how_to_store_one(fake: FakeModelServer, connect: Connect):
    # A local server needs no key, and is never chosen without being named.
    models = connect(fake, "ollama")
    with pytest.raises(
        ModelChoiceError,
        match=r"no provider's key\. Store one with `atlas secrets set OPENAI_API_KEY` \(or "
        r"OPENROUTER_API_KEY, GEMINI_API_KEY, ANTHROPIC_API_KEY\), or set \[model\] "
        r"provider.*anthropic, gemini, ollama",
    ):
        models.client()
    with pytest.raises(ModelChoiceError, match="Ollama has no default model"):
        models.client("ollama")


def test_no_model_chosen_takes_the_first_provider_with_a_key(
    fake: FakeModelServer, connect: Connect, caplog: pytest.LogCaptureFixture
):
    # The provider chosen automatically. Gemini alone has a key here; then OpenAI's comes first, in
    # the catalog's order.
    models = connect(fake, "gemini", settings=ModelSettings(effort="low"))
    caplog.set_level("INFO", logger="atlas.models")
    client = models.client()
    assert (client.provider.id, client.model, client.effort) == (
        "gemini",
        "gemini-3.8-flash",
        "low",
    )
    models.client()
    said = [r.getMessage() for r in caplog.records if "first provider with a key" in r.getMessage()]
    assert said == [
        "No model is chosen in the settings, so Google Gemini is used: the first provider with a "
        "key in the vault. [model] provider chooses another."
    ]
    models = connect(fake, "openai")
    assert (models.client().provider.id, models.client().model) == ("openai", "gpt-6-luna")


def test_a_key_stored_while_atlas_runs_is_chosen_at_the_next_call(
    fake: FakeModelServer, connect: Connect, vault: Vault
):
    models = connect(fake, "gemini", key=None)
    with pytest.raises(ModelChoiceError, match="no provider's key"):
        models.client()
    vault.set("GEMINI_API_KEY", "fake-key")
    assert models.client().provider.id == "gemini"


def test_a_model_named_without_its_provider_is_refused(fake: FakeModelServer, connect: Connect):
    models = connect(fake, "gemini", settings=ModelSettings(name="gemini-3.8-flash"))
    with pytest.raises(ModelChoiceError, match=r"\[model\] name is set without \[model\] provider"):
        models.client()


# -- readable reasoning --------------------------------------------------------------------------


def test_gemini_is_asked_for_its_thoughts_and_they_are_split_from_the_answer(
    connect: Connect, run: Run
) -> None:
    # Gemini on 08/10/2026: "<thought>...</thought>" at the start of the content, the closing tag
    # in the same chunk as the answer's first words. Chunks of 3 cut the tags themselves.
    with FakeModelServer(thought_tag="thought", chunk_size=3) as gemini:
        gemini.reply(FakeReply(reasoning="1001 = 7 x 11 x 13.", text="No, 1001 is not prime."))
        models = connect(gemini, "gemini")

        async def scenario() -> list[ModelEvent]:
            client = models.client("gemini")
            return [event async for event in client.stream(ModelRequest(QUESTION))]

        events = run(models, scenario)

    reasoning = "".join(e.text for e in events if isinstance(e, ReasoningDelta))
    text = "".join(e.text for e in events if isinstance(e, TextDelta))
    assert (reasoning, text) == ("1001 = 7 x 11 x 13.", "No, 1001 is not prime.")
    done = events[-1]
    assert isinstance(done, ReplyDone)
    assert (done.reply.text, done.reply.message.reasoning) == (text, reasoning)
    (body,) = gemini.bodies("/v1/chat/completions")
    # The effort is Atlas's default, as nobody chose one.
    assert body["extra_body"] == {
        "google": {"thinking_config": {"thinking_level": "medium", "include_thoughts": True}}
    }


def test_a_model_that_does_not_reason_is_not_asked_for_thoughts(connect: Connect) -> None:
    models = connect(None, "gemini")
    gemini = models.catalog.provider("gemini")
    plain = ModelInfo("gemini-flat", "Flat", reasoning=False)
    body = chat_request(gemini, "gemini-flat", ModelRequest(QUESTION), plain, None)
    assert "extra_body" not in body


@pytest.mark.parametrize(
    ("chunks", "reasoning", "text"),
    [
        (["<thought>a", "b</thought>c"], "ab", "c"),
        (["<th", "ought>", "x", "</tho", "ught>", "y"], "x", "y"),
        # The newline a model leaves after its closing tag is no part of the answer.
        (["<think>x</think>\n\n", "y"], "x", "y"),
        # An answer that writes the tag itself, after its first words, stays text; a lone
        # closing tag goes.
        (["Use <thought> like this", " </thought>"], "", "Use <thought> like this "),
        (["<tho"], "", "<tho"),  # a stream that ends on half a tag keeps it as text
        (["<thought>never closed"], "never closed", ""),
        # Every chat provider, every tag: a local model's <think> between tool rounds.
        (["Checking.\n<think>", "plan", "</think>\nDone."], "plan", "Checking.\n\nDone."),
    ],
)
def test_reasoning_tags_leave_the_text_wherever_they_are_cut(
    chunks: list[str], reasoning: str, text: str
) -> None:
    reader = ChatReader("Ollama")
    for piece in chunks:
        reader.read({"choices": [{"index": 0, "delta": {"content": piece}}]})
    reader.read({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    result = reader.result()
    assert (result.reasoning, result.text) == (reasoning, text)


def test_a_tag_beside_the_providers_own_reasoning_is_not_shown_twice() -> None:
    # Once the provider sent reasoning of its own, what the scrubber takes out of
    # the text is not reasoning again.
    reader = ChatReader("Ollama")
    chunks: list[dict[str, str]] = [
        {"reasoning": "native"},
        {"content": "<think>inline</think>"},
        {"content": "answer"},
    ]
    for delta in chunks:
        reader.read({"choices": [{"index": 0, "delta": delta}]})
    reader.read({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    result = reader.result()
    assert (result.reasoning, result.text) == ("native", "answer")


def test_thinking_counted_only_in_the_total_is_billed_as_output(
    fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    # Gemini on 08/10/2026: 22 in, 47 out, 428 in total. The 359 between are thinking.
    fake.reply(FakeReply(text="ok", usage=FakeUsage(input=22, output=47, hidden_reasoning=359)))
    models = connect(fake, "gemini")

    reply = run(models, lambda: models.client("gemini").reply(ModelRequest(QUESTION)))

    assert reply.usage is not None
    assert (reply.usage.output_tokens, reply.usage.reasoning_tokens) == (406, 359)
    # gemini-3.8-flash: 22 in at 0.75 and 406 out at 3.75 per million.
    assert reply.cost.usd == Decimal("0.0015390")
