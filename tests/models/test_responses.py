"""The Responses API through the real SDK, against the fake model server over real HTTP.

OpenAI's own provider speaks it, because Chat Completions refuses function tools together with
reasoning since GPT-5.4. The fake enforces the stateless rules (no item ids, encrypted reasoning
only back to the model that wrote it), so a test passes only when the replay is one OpenAI takes.
"""

from decimal import Decimal

import pytest

from atlas.models import (
    AssistantMessage,
    CostSource,
    Finish,
    ModelAuthError,
    ModelEvent,
    ModelReply,
    ModelRequest,
    ModelRequestError,
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
)
from fake_model import FakeCall, FakeModelServer, FakeReply, FakeUsage
from model_support import Connect, Run

READ_FILE = ToolSpec(
    name="read_file",
    description="Read a text file.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)
QUESTION = (SystemMessage("You are Atlas."), UserMessage("What is in today's notes?"))


def _after_the_tool(reply: ModelReply) -> ModelRequest:
    return ModelRequest(
        (*QUESTION, reply.message, ToolResult(reply.tool_calls[0].id, "buy bread")), (READ_FILE,)
    )


def test_text_and_reasoning_arrive_in_pieces_and_whole(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(
        FakeReply(
            text="Buy bread and call the plumber.",
            reasoning="The user wants the notes.",
            usage=FakeUsage(input=100_000, output=2_000, cached=60_000, reasoning=500),
        )
    )
    models = connect(fake, "openai")

    async def scenario() -> list[ModelEvent]:
        client = models.client("openai", "gpt-6-luna")
        return [event async for event in client.stream(ModelRequest(QUESTION))]

    events = run(models, scenario)

    texts = [event.text for event in events if isinstance(event, TextDelta)]
    thoughts = [event.text for event in events if isinstance(event, ReasoningDelta)]
    assert "".join(texts) == "Buy bread and call the plumber."
    assert "".join(thoughts) == "The user wants the notes."
    done = events[-1]
    assert isinstance(done, ReplyDone)
    reply = done.reply
    assert (reply.text, reply.message.reasoning) == (
        "Buy bread and call the plumber.",
        "The user wants the notes.",
    )
    assert reply.finish is Finish.STOP
    assert reply.message.origin == Origin("openai", "gpt-6-luna")
    assert [item["type"] for item in reply.message.provider_fields["output"]] == [  # type: ignore[index]
        "reasoning",
        "message",
    ]
    # gpt-6-luna: 40k new at 0.10, 60k cached at 0.01, 2k out at 0.50, per million.
    assert reply.cost.source is CostSource.PRICE_LIST
    assert reply.cost.usd == Decimal("0.0056")
    (body,) = fake.bodies("/v1/responses")
    assert (body["store"], body["stream"]) == (False, True)
    assert body["include"] == ["reasoning.encrypted_content"]
    assert body["input"] == [
        {"role": "system", "content": "You are Atlas."},
        {"role": "user", "content": "What is in today's notes?"},
    ]


def test_tools_with_reasoning_and_the_reasoning_goes_back_without_ids(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(
        FakeReply(
            reasoning="Read the notes first.", calls=[FakeCall("read_file", '{"path": "a"}')]
        ),
        FakeReply(text="Buy bread."),
    )
    models = connect(fake, "openai")

    async def scenario() -> tuple[ModelReply, ModelReply]:
        client = models.client("openai", "gpt-6-luna", effort="high")
        first = await client.reply(ModelRequest(QUESTION, (READ_FILE,)))
        return first, await client.reply(_after_the_tool(first))

    first, second = run(models, scenario)

    assert first.finish is Finish.TOOL_CALLS
    (call,) = first.tool_calls
    assert (call.id, call.name, call.arguments) == ("call_1_0", "read_file", '{"path": "a"}')
    assert second.text == "Buy bread."
    opening, follow_up = fake.bodies("/v1/responses")
    # A model that reasons is asked for the readable summary of its reasoning.
    assert opening["reasoning"] == {"summary": "auto", "effort": "high"}
    assert opening["tools"] == [
        {
            "type": "function",
            "name": "read_file",
            "description": "Read a text file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            "strict": False,
        }
    ]
    reasoning, function_call, output = follow_up["input"][2:]
    assert reasoning["type"] == "reasoning"
    assert reasoning["encrypted_content"] == "enc:gpt-6-luna:1"
    assert "id" not in reasoning
    assert function_call == {
        "type": "function_call",
        "call_id": "call_1_0",
        "name": "read_file",
        "arguments": '{"path": "a"}',
        "status": "completed",
    }
    assert output == {"type": "function_call_output", "call_id": "call_1_0", "output": "buy bread"}


def test_another_model_gets_the_turn_without_the_reasoning_it_cannot_open(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(
        FakeReply(reasoning="Read the notes.", calls=[FakeCall("read_file", "{}")]),
        FakeReply(text="Buy bread."),
    )
    models = connect(fake, "openai")

    async def scenario() -> ModelReply:
        first = await models.client("openai", "gpt-6-luna").reply(
            ModelRequest(QUESTION, (READ_FILE,))
        )
        return await models.client("openai", "gpt-6-sol").reply(_after_the_tool(first))

    reply = run(models, scenario)

    assert reply.text == "Buy bread."
    sent = fake.bodies("/v1/responses")[1]["input"][2:]
    assert [item.get("type") for item in sent] == ["function_call", "function_call_output"]


def test_encrypted_reasoning_sent_to_another_model_is_refused(
    fake: FakeModelServer, connect: Connect, run: Run
):
    # The fake's rule is real: encrypted reasoning replayed to a model that did not write it fails.
    fake.reply(FakeReply(reasoning="Think.", calls=[FakeCall("read_file", "{}")]))
    models = connect(fake, "openai")

    async def scenario() -> None:
        first = await models.client("openai", "gpt-6-luna").reply(
            ModelRequest(QUESTION, (READ_FILE,))
        )
        forged = AssistantMessage(
            tool_calls=first.tool_calls,
            origin=Origin("openai", "gpt-6-sol"),
            provider_fields=first.message.provider_fields,
        )
        request = ModelRequest((*QUESTION, forged, ToolResult("call_1_0", "x")), (READ_FILE,))
        await models.client("openai", "gpt-6-sol").reply(request)

    with pytest.raises(ModelRequestError, match="encrypted content could not be verified"):
        run(models, scenario)


def test_a_history_from_another_provider_gets_call_ids_that_fit(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(FakeReply(text="Done."))
    models = connect(fake, "openai")
    long_id = "function-call-" + "7" * 70
    from_gemini = AssistantMessage(
        tool_calls=(ToolCall(long_id, "read_file", "{}"),),
        origin=Origin("gemini", "gemini-3.8-flash"),
        provider_fields={"extra_content": {"google": {"thought_signature": "sig"}}},
    )
    request = ModelRequest((*QUESTION, from_gemini, ToolResult(long_id, "x")), (READ_FILE,))

    run(models, lambda: models.client("openai", "gpt-6-luna").reply(request))

    call, output = fake.bodies("/v1/responses")[0]["input"][2:]
    assert call["call_id"] == output["call_id"]
    assert len(call["call_id"]) <= 64
    assert "extra_content" not in call


def test_a_model_that_does_not_reason_gets_no_reasoning_field(
    fake: FakeModelServer, connect: Connect, run: Run
):
    # The chat-era models refuse any reasoning field with HTTP 400.
    fake.reply(FakeReply(text="ok"))
    models = connect(fake, "openai")

    run(models, lambda: models.client("openai", "gpt-4.1").reply(ModelRequest(QUESTION)))

    (body,) = fake.bodies("/v1/responses")
    assert "include" not in body
    assert "reasoning" not in body


def test_an_answer_cut_by_the_output_limit_says_so(
    fake: FakeModelServer, connect: Connect, run: Run
):
    fake.reply(FakeReply(text="The answer goes on and", finish="max_output_tokens"))
    models = connect(fake, "openai")
    request = ModelRequest(QUESTION, max_output_tokens=16)

    reply = run(models, lambda: models.client("openai", "gpt-6-luna").reply(request))

    assert reply.finish is Finish.LENGTH
    assert reply.text == "The answer goes on and"
    assert fake.bodies("/v1/responses")[0]["max_output_tokens"] == 16


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (FakeReply(text="x", finish="failed"), r"failed during the reply \(server_error\)"),
        (FakeReply(text="A long answer", cut_after=3), "closed the reply before it finished"),
    ],
)
def test_a_failed_or_cut_reply_is_no_reply(
    fake: FakeModelServer, connect: Connect, run: Run, reply: FakeReply, message: str
):
    fake.reply(reply)
    models = connect(fake, "openai")
    with pytest.raises(ModelUnavailableError, match=message):
        run(models, lambda: models.client("openai", "gpt-6-luna").reply(ModelRequest(QUESTION)))


def test_a_refused_key_on_the_responses_api(fake: FakeModelServer, connect: Connect, run: Run):
    models = connect(fake, "openai", key="wrong")
    with pytest.raises(ModelAuthError, match="atlas secrets set OPENAI_API_KEY"):
        run(models, lambda: models.client("openai").reply(ModelRequest(QUESTION)))
