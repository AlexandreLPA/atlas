"""The call ledger: every call that goes out is one row, whatever happened to it.

Ported from V3's test_usage_ledger.py, whose ledger covered only the calls made outside a turn. The
database is a real SQLite file in a temporary home, migrated the way `atlas serve` migrates it.
"""

import logging
from contextlib import aclosing
from decimal import Decimal

import pytest

from atlas.home import Database, Home, migrate, parse_utc
from atlas.models import (
    SCHEMA,
    AssistantMessage,
    CallLedger,
    CallOutcome,
    CostSource,
    ModelRateLimitError,
    ModelRequest,
    ModelRequestError,
    ModelUnavailableError,
    SystemMessage,
    TextDelta,
    UserMessage,
)
from fake_model import FakeError, FakeModelServer, FakeReply, FakeUsage
from model_support import Connect, Run

QUESTION = (SystemMessage("You are Atlas."), UserMessage("What is in today's notes?"))


@pytest.fixture
def ledger(home: Home) -> CallLedger:
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    return CallLedger(database)


def test_the_ledger_is_a_migration_of_the_models_subject(home: Home) -> None:
    applied = migrate(Database(home.database_file), (SCHEMA,))
    assert [(step.subject, step.number, step.name) for step in applied] == [
        ("models", 1, "call ledger"),
        ("models", 2, "the hour-long cache write"),
    ]


def test_a_finished_call_is_recorded_with_its_usage_and_cost(
    fake: FakeModelServer, connect: Connect, run: Run, ledger: CallLedger
):
    fake.reply(FakeReply(text="ok", usage=FakeUsage(input=10_000, output=1_000, cached=8_000)))
    models = connect(fake, "gemini", ledger=ledger)
    request = ModelRequest(QUESTION, purpose="turn", scope="conversation-1")

    reply = run(models, lambda: models.client("gemini").reply(request))

    (call,) = ledger.recent()
    assert parse_utc(call.started_at) is not None
    assert (call.purpose, call.scope, call.provider, call.model) == (
        "turn",
        "conversation-1",
        "gemini",
        "gemini-3.8-flash",
    )
    assert (call.outcome, call.served_model) == (CallOutcome.OK, "gemini-3.8-flash")
    assert call.duration_ms >= 0
    assert call.usage == reply.usage
    assert call.cost == reply.cost
    assert call.cost.usd == Decimal("0.00585")  # 2k at 0.75 + 8k cached at 0.075 + 1k out at 3.75


def test_a_reported_cost_keeps_every_digit_and_its_source(
    connect: Connect, run: Run, ledger: CallLedger
):
    with FakeModelServer(usage_always=True) as openrouter:
        openrouter.reply(FakeReply(text="ok", usage=FakeUsage(input=10, output=2, cost=0.000042)))
        models = connect(openrouter, "openrouter", ledger=ledger)
        run(models, lambda: models.client("openrouter").reply(ModelRequest(QUESTION)))

    (call,) = ledger.recent()
    assert (call.cost.usd, call.cost.source) == (Decimal("0.000042"), CostSource.REPORTED)


def test_a_failed_call_is_recorded_without_made_up_usage(
    fake: FakeModelServer, connect: Connect, run: Run, ledger: CallLedger
):
    fake.reply(FakeError(429, "Slow down.", should_retry=False))
    models = connect(fake, "gemini", ledger=ledger)

    with pytest.raises(ModelRateLimitError):
        run(
            models,
            lambda: models.client("gemini").reply(ModelRequest(QUESTION, purpose="compaction")),
        )

    (call,) = ledger.recent()
    assert (call.outcome, call.purpose) == (CallOutcome.RATE_LIMITED, "compaction")
    assert call.usage is None
    assert (call.cost.usd, call.cost.source) == (None, CostSource.UNKNOWN)


def test_a_call_the_caller_stopped_is_recorded_as_cancelled(
    fake: FakeModelServer, connect: Connect, run: Run, ledger: CallLedger
):
    fake.reply(FakeReply(text="A long answer, word after word. " * 10, pause=0.01))
    models = connect(fake, "gemini", ledger=ledger)

    async def scenario() -> None:
        async with aclosing(models.client("gemini").stream(ModelRequest(QUESTION))) as events:
            async for event in events:
                if isinstance(event, TextDelta):
                    break

    run(models, scenario)

    (call,) = ledger.recent()
    assert call.outcome is CallOutcome.CANCELLED
    assert call.usage is None


def test_a_request_refused_before_sending_is_not_a_call(
    fake: FakeModelServer, connect: Connect, run: Run, ledger: CallLedger
):
    models = connect(fake, "gemini", ledger=ledger)
    request = ModelRequest((*QUESTION, AssistantMessage("note")))

    with pytest.raises(ModelRequestError):
        run(models, lambda: models.client("gemini").reply(request))

    assert ledger.recent() == []


def test_a_local_call_that_fails_still_costs_a_known_zero(
    connect: Connect, run: Run, ledger: CallLedger
):
    models = connect(None, "ollama", ledger=ledger)

    with pytest.raises(ModelUnavailableError):
        run(models, lambda: models.client("ollama", "llama3.3").reply(ModelRequest(QUESTION)))

    (call,) = ledger.recent()
    assert call.outcome is CallOutcome.UNAVAILABLE
    assert (call.cost.usd, call.cost.source) == (Decimal(0), CostSource.FREE)


def test_a_ledger_that_cannot_write_never_breaks_the_call(
    fake: FakeModelServer,
    connect: Connect,
    run: Run,
    home: Home,
    caplog: pytest.LogCaptureFixture,
):
    fake.reply(FakeReply(text="ok"))
    unmigrated = CallLedger(Database(home.database_file))  # no models_calls table
    models = connect(fake, "gemini", ledger=unmigrated)

    with caplog.at_level(logging.ERROR, logger="atlas.models"):
        reply = run(models, lambda: models.client("gemini").reply(ModelRequest(QUESTION)))

    assert reply.text == "ok"
    assert "The call ledger could not record a call" in caplog.text
