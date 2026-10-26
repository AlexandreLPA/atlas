"""Conversation titles: how the reply is read, the models in the order the user chose,
and the title a conversation takes as its first turn starts.

The models are the fake model server through the real adapters, Anthropic's and OpenAI's.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.agent import TurnEnded
from atlas.assistant import Assistant, ConversationEvent, Titled
from atlas.assistant.titles import (
    MAX_TITLE_CHARS,
    QUARANTINE,
    TITLE_MAX_TOKENS,
    TITLE_PROMPT,
    TITLE_PURPOSE,
    TitleQuarantine,
    provisional,
    read_title,
    write_title,
)
from atlas.conversations import TitleSource
from atlas.home import (
    Database,
    Home,
    ModelSettings,
    Settings,
    WorkspaceSettings,
    migrate,
    utc_now,
)
from atlas.models import CallLedger, Models
from atlas.secrets import Vault
from fake_model import FakeError, FakeModelServer, FakeReply
from model_support import Connect

MESSAGES = "/v1/messages"
RESPONSES = "/v1/responses"
CHAT = "/v1/chat/completions"
#: Taken only by the title's call: its system prompt.
TITLING = "Your job is naming chat sessions."
NO_CREDIT = "Your credit balance is too low to access the Anthropic API."


class _Clock:
    def __init__(self) -> None:
        self.now = utc_now()

    def __call__(self) -> datetime:
        return self.now


def _fresh() -> TitleQuarantine:
    return TitleQuarantine(utc_now)


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())  # pyright: ignore[reportArgumentType]


# -- the reply ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "title"),
    [
        ('{"title": "Configurar nginx com HTTPS"}', "Configurar nginx com HTTPS"),
        ('```json\n{"title": "Fix flaky CI job"}\n```', "Fix flaky CI job"),
        ('Sure: "title": "Plan a trip to Lisbon"', "Plan a trip to Lisbon"),
        ("Title: Budget spreadsheet for March.", "Budget spreadsheet for March"),
        ('"Rename the photos"\nmore text', "Rename the photos"),
        ('{"title": "  Spaces   inside\\nthe title  "}', "Spaces inside the title"),
    ],
)
def test_a_title_is_read_from_json_a_fence_or_prose(reply: str, title: str) -> None:
    assert read_title(reply) == title


@pytest.mark.parametrize(
    "reply",
    [
        "",
        '{"title": ""}',
        # The model answered instead of naming (refused, not cut).
        '{"title": "I do not have context on that, but here is what I would try first today"}',
        # One of the prompt's own examples, in any wrapper.
        '{"title": "Rename columns in sales CSV"}',
        "[Server problem]",
        # JSON the output limit cut short: its fragment is no title.
        '{"title": "Half of a ti',
        "```json",
    ],
)
def test_an_answer_an_echo_or_a_cut_reply_is_no_title(reply: str) -> None:
    assert read_title(reply) is None


def test_a_long_title_is_cut_and_the_greeting_is_a_placeholder() -> None:
    long = read_title('{"title": "' + "Supercalifragilistic " * 6 + '"}')
    assert long is not None
    assert len(long) == MAX_TITLE_CHARS
    assert long.endswith("…")
    assert provisional("Friendly greeting")
    assert provisional('"Friendly greeting in chat"')
    assert not provisional("Greeting card design")


def test_the_prompt_asks_a_short_title_and_the_greeting_in_english() -> None:
    assert "3 to 7 words" in TITLE_PROMPT
    assert "same language as the user's message" in TITLE_PROMPT
    assert 'titled exactly "Friendly greeting", in English' in TITLE_PROMPT
    assert TITLE_PROMPT.endswith('Reply with JSON only: {"title": "..."}')


# -- the models -----------------------------------------------------------------------------------


def _models(connect: Connect, fake: FakeModelServer, home: Home, *providers: str) -> Models:
    migrate(Database(home.database_file), SCHEMAS)
    return connect(fake, *providers, ledger=CallLedger(Database(home.database_file)))


def test_haiku_writes_the_title_with_its_reasoning_off(
    connect: Connect, fake: FakeModelServer, home: Home
) -> None:
    models = _models(connect, fake, home, "anthropic", "openai")
    fake.reply(FakeReply(text='{"title": "Configurar nginx com HTTPS no Ubuntu"}'))

    async def scenario() -> str | None:
        try:
            return await write_title(
                models, "me ajuda a configurar o nginx com https", "c1", quarantine=_fresh()
            )
        finally:
            await models.close()

    assert _run(scenario) == "Configurar nginx com HTTPS no Ubuntu"
    [body] = fake.bodies(MESSAGES)
    assert body["model"] == "claude-haiku-5-5"
    assert body["thinking"] == {"type": "disabled"}
    assert "output_config" not in body
    assert body["max_tokens"] == TITLE_MAX_TOKENS
    assert body["system"][0]["text"] == TITLE_PROMPT
    assert body["messages"][0]["content"][0]["text"] == "me ajuda a configurar o nginx com https"
    assert "tools" not in body
    assert fake.bodies(RESPONSES) == []
    [call] = CallLedger(Database(home.database_file)).recent(5)
    assert (call.purpose, call.scope, call.model) == (TITLE_PURPOSE, "c1", "claude-haiku-5-5")


def test_with_no_credit_at_anthropic_luna_writes_it_with_the_effort_none(
    connect: Connect, fake: FakeModelServer, home: Home
) -> None:
    models = _models(connect, fake, home, "anthropic", "openai")
    fake.reply(
        FakeError(400, NO_CREDIT),
        FakeReply(text='{"title": "Plan a trip to Lisbon"}'),
    )

    async def scenario() -> str | None:
        try:
            return await write_title(
                models, "help me plan a week in Lisbon " * 100, quarantine=_fresh()
            )
        finally:
            await models.close()

    assert _run(scenario) == "Plan a trip to Lisbon"
    [body] = fake.bodies(RESPONSES)
    assert body["model"] == "gpt-6-luna"
    assert body["reasoning"]["effort"] == "none"
    assert body["max_output_tokens"] == TITLE_MAX_TOKENS
    said = body["input"][-1]["content"]
    said = said if isinstance(said, str) else said[0]["text"]
    assert len(said) == 1000  # the message, cut


def test_with_only_an_openai_key_luna_is_asked(
    connect: Connect, fake: FakeModelServer, home: Home
) -> None:
    models = _models(connect, fake, home, "openai", "openrouter")
    fake.reply(FakeReply(text='{"title": "Rename the photos"}'))

    async def scenario() -> str | None:
        try:
            return await write_title(models, "rename my photos by date", quarantine=_fresh())
        finally:
            await models.close()

    assert _run(scenario) == "Rename the photos"
    assert len(fake.bodies(RESPONSES)) == 1
    assert fake.bodies(MESSAGES) == []


def test_with_no_title_model_keyed_no_call_is_made(
    connect: Connect, fake: FakeModelServer, home: Home
) -> None:
    models = _models(connect, fake, home, "openrouter")

    async def scenario() -> str | None:
        try:
            return await write_title(models, "anything at all", quarantine=_fresh())
        finally:
            await models.close()

    assert _run(scenario) is None
    assert fake.requests == []


def test_a_greeting_s_placeholder_or_an_answer_keeps_the_instant_title(
    connect: Connect, fake: FakeModelServer, home: Home
) -> None:
    models = _models(connect, fake, home, "anthropic", "openai")
    fake.reply(
        FakeReply(text='{"title": "Friendly greeting"}'),
        FakeReply(text="Hello! I am doing well, thanks for asking, how can I help you today?"),
    )

    async def scenario() -> list[str | None]:
        try:
            held = _fresh()
            return [
                await write_title(models, "oi", quarantine=held),
                await write_title(models, "how are you?", quarantine=held),
            ]
        finally:
            await models.close()

    # Neither falls back to Luna: the model answered, it just gave no title.
    assert _run(scenario) == [None, None]
    assert fake.bodies(RESPONSES) == []


# -- the quarantine -------------------------------------------------------------------------------


def test_a_provider_with_no_credit_is_skipped_for_ten_minutes_then_asked_again(
    connect: Connect, fake: FakeModelServer, home: Home
) -> None:
    models = _models(connect, fake, home, "anthropic", "openai")
    clock = _Clock()
    held = TitleQuarantine(clock)
    fake.reply(
        FakeError(400, NO_CREDIT),
        FakeReply(text='{"title": "First of the day"}'),  # Luna
        FakeReply(text='{"title": "Second of the day"}'),  # Luna, Haiku skipped
        FakeReply(text='{"title": "Third of the day"}'),  # Haiku again, ten minutes later
    )

    async def scenario() -> list[str | None]:
        try:
            first = await write_title(models, "one", quarantine=held)
            clock.now += QUARANTINE - timedelta(seconds=1)
            second = await write_title(models, "two", quarantine=held)
            clock.now += timedelta(seconds=1)
            third = await write_title(models, "three", quarantine=held)
            return [first, second, third]
        finally:
            await models.close()

    assert _run(scenario) == ["First of the day", "Second of the day", "Third of the day"]
    assert len(fake.bodies(MESSAGES)) == 2  # the refused call, and the one after ten minutes
    assert len(fake.bodies(RESPONSES)) == 2
    assert timedelta(minutes=10) == QUARANTINE


def test_a_refused_key_is_skipped_too_and_a_passing_failure_is_not(
    connect: Connect, fake: FakeModelServer, home: Home
) -> None:
    models = _models(connect, fake, home, "anthropic", "openai")
    refused, overloaded = _fresh(), _fresh()
    fake.reply(
        FakeError(401, "invalid x-api-key", type="authentication_error"),
        FakeReply(text='{"title": "Luna after a refused key"}'),
        FakeReply(text='{"title": "Luna again"}'),
        FakeError(529, "Overloaded", type="overloaded_error"),
        FakeReply(text='{"title": "Luna after an overload"}'),
        FakeReply(text='{"title": "Haiku is back"}'),
    )

    async def scenario() -> list[str | None]:
        try:
            return [
                await write_title(models, "one", quarantine=refused),
                await write_title(models, "two", quarantine=refused),
                await write_title(models, "three", quarantine=overloaded),
                await write_title(models, "four", quarantine=overloaded),
            ]
        finally:
            await models.close()

    assert _run(scenario) == [
        "Luna after a refused key",
        "Luna again",
        "Luna after an overload",
        "Haiku is back",
    ]
    assert len(fake.bodies(MESSAGES)) == 3  # 401, then 529, then the answer: never the skipped one


# -- a conversation's title -----------------------------------------------------------------------


type Make = Callable[..., tuple[Assistant, list[tuple[str, ConversationEvent]]]]


@pytest.fixture
def make(home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path) -> Make:
    migrate(Database(home.database_file), SCHEMAS)

    def make(
        *titled_by: str, **options: Any
    ) -> tuple[Assistant, list[tuple[str, ConversationEvent]]]:
        chosen = ModelSettings(provider="openrouter")
        ledger = CallLedger(Database(home.database_file))
        models = connect(fake, "openrouter", *titled_by, settings=chosen, ledger=ledger)
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
        heard: list[tuple[str, ConversationEvent]] = []
        assistant = Assistant(
            home,
            settings,
            models,
            vault,
            listener=lambda conversation, event: heard.append((conversation, event)),
            **options,
        )
        return assistant, heard

    return make


def test_the_first_turn_titles_the_conversation_and_the_screen_hears_it(
    make: Make, fake: FakeModelServer
) -> None:
    assistant, heard = make("anthropic")
    fake.reply(
        FakeReply(text='{"title": "Configurar nginx com HTTPS"}', when=TITLING),
        FakeReply(text="Vamos lá.", when="You are Atlas"),
        FakeReply(text="Pronto.", when="You are Atlas"),
    )

    async def scenario() -> str:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("me ajuda a configurar o nginx com https no meu servidor")
        await conversation.wait()
        conversation.send("e o certificado?")
        await conversation.wait()
        await assistant.close()
        return stored.id

    conversation_id = _run(scenario)
    info = assistant.conversations.info(conversation_id)
    assert info.title == "Configurar nginx com HTTPS"
    assert assistant.conversations.title_source(conversation_id) is TitleSource.LLM
    assert (conversation_id, Titled("Configurar nginx com HTTPS")) in heard
    # One title call: the second turn found the title a model's.
    assert len(fake.bodies(MESSAGES)) == 1
    assert len(fake.bodies(CHAT)) == 2
    assert [e for _, e in heard if isinstance(e, TurnEnded)][-1].text == "Pronto."


def test_a_greeting_tries_again_on_the_next_turns_and_stops_after_the_third(
    make: Make, fake: FakeModelServer
) -> None:
    assistant, heard = make("anthropic")
    greeting = '{"title": "Friendly greeting"}'
    fake.reply(
        *(FakeReply(text=greeting, when=TITLING) for _ in range(3)),
        *(FakeReply(text="Oi!", when="You are Atlas") for _ in range(4)),
    )

    async def scenario() -> str:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        for said in ("oi", "tudo bem?", "e aí", "quero ajuda com uma planilha"):
            conversation.send(said)
            await conversation.wait()
        await assistant.close()
        return stored.id

    conversation_id = _run(scenario)
    # Three tries, then none: the instant title, the user's own words, stays.
    assert len(fake.bodies(MESSAGES)) == 3
    assert assistant.conversations.info(conversation_id).title == "oi"
    assert assistant.conversations.title_source(conversation_id) is TitleSource.DERIVED
    assert not any(isinstance(event, Titled) for _, event in heard)


def test_a_bot_s_or_a_routine_s_named_conversation_is_never_titled(
    make: Make, fake: FakeModelServer
) -> None:
    assistant, _ = make("anthropic")
    fake.reply(FakeReply(text="Done.", when="You are Atlas"))

    async def scenario() -> str:
        stored = assistant.conversations.create(title="Morning digest")
        conversation = assistant.conversation(stored.id)
        conversation.send("run the digest")
        await conversation.wait()
        await assistant.close()
        return stored.id

    conversation_id = _run(scenario)
    assert fake.bodies(MESSAGES) == []
    assert assistant.conversations.info(conversation_id).title == "Morning digest"
    assert assistant.conversations.title_source(conversation_id) is TitleSource.USER


def test_a_failed_title_leaves_the_instant_one_and_the_turn_untouched(
    make: Make, fake: FakeModelServer
) -> None:
    assistant, heard = make("anthropic")
    fake.reply(
        FakeError(529, "Overloaded", type="overloaded_error", when=TITLING),
        FakeReply(text="Sure.", when="You are Atlas"),
    )

    async def scenario() -> str:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Plan my week")
        await conversation.wait()
        await assistant.close()
        return stored.id

    conversation_id = _run(scenario)
    assert assistant.conversations.info(conversation_id).title == "Plan my week"
    assert [e for _, e in heard if isinstance(e, TurnEnded)][-1].text == "Sure."


def test_every_conversation_s_title_shares_one_quarantine(
    make: Make, fake: FakeModelServer
) -> None:
    assistant, _ = make("anthropic", "openai")
    fake.reply(
        FakeError(400, NO_CREDIT, when=TITLING),
        FakeReply(text='{"title": "Plan my week"}', when=TITLING),
        FakeReply(text='{"title": "Book a dentist"}', when=TITLING),
        FakeReply(text="Sure.", when="You are Atlas"),
        FakeReply(text="Done.", when="You are Atlas"),
    )

    async def scenario() -> None:
        first = assistant.conversations.create()
        assistant.conversation(first.id).send("Plan my week")
        deadline = time.monotonic() + 10
        # The model's title, past Haiku's refusal: the first message's own, the same words, is
        # written as the turn starts.
        while assistant.conversations.title_source(first.id) is not TitleSource.LLM:
            assert time.monotonic() < deadline, "the first title never came"
            await asyncio.sleep(0.02)
        assert assistant.conversations.info(first.id).title == "Plan my week"
        second = assistant.conversation(assistant.conversations.create().id)
        second.send("Book a dentist for Tuesday")
        await second.wait()
        await assistant.close()

    _run(scenario)
    # Haiku was asked once, for the first title: the second went straight to Luna.
    assert len(fake.bodies(MESSAGES)) == 1
    assert len(fake.bodies(RESPONSES)) == 2
