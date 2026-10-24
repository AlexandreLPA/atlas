"""The secrets tool (step 16d): a request shows a masked card in the app, a list reads the
vault's names and dates; the value never reaches the tool, so it cannot be logged, replayed or
echoed to a frame the client sees."""

import asyncio
import json
from typing import Any

import pytest

from atlas.tools import (
    SECRET_REQUEST_SECONDS,
    SecretAnswer,
    SecretOutcome,
    SecretRequest,
    SecretView,
    make_secrets_tool,
)
from atlas.tools.secrets_tool import (
    MAX_NAME_CHARS,
    MAX_REASON_CHARS,
    NAME_PATTERN,
    parse_arguments,
    parse_name,
)


async def _skipped(_request: SecretRequest) -> SecretAnswer:
    return SecretAnswer(SecretOutcome.SKIPPED)


def _call(**arguments: Any) -> Any:
    """A convenient run of the tool: builds askers that return a fixed answer."""
    requested: list[SecretRequest] = []

    async def asker(request: SecretRequest) -> SecretAnswer:
        requested.append(request)
        return SecretAnswer(SecretOutcome.STORED)

    def lister() -> list[SecretView]:
        return [SecretView("OPENAI_API_KEY", "2026-10-15T12:00:00Z")]

    tool = make_secrets_tool(asker, lister)

    async def go() -> Any:
        text = await tool.run(arguments)
        assert isinstance(text, str)
        return json.loads(text) if text.startswith("{") else text, requested

    return asyncio.run(go())


def test_request_asks_the_server_and_returns_stored() -> None:
    body, requested = _call(action="request", name="STRIPE_API_KEY", reason="to list my charges")

    assert body == {"status": "stored", "name": "STRIPE_API_KEY"}
    assert requested == [SecretRequest("STRIPE_API_KEY", "to list my charges")]


def test_list_returns_names_and_dates_without_values() -> None:
    body, _ = _call(action="list")

    assert body == {"secrets": [{"name": "OPENAI_API_KEY", "updated_at": "2026-10-15T12:00:00Z"}]}


def test_an_unknown_action_is_refused_before_the_asker_runs() -> None:
    body, requested = _call(action="delete", name="STRIPE_API_KEY", reason="to clean up")

    assert isinstance(body, str)
    assert body.startswith("[Atlas:")
    assert requested == []


def test_a_lowercase_name_is_refused_so_the_env_variable_form_is_kept() -> None:
    body, requested = _call(action="request", name="stripe_key", reason="to list my charges")

    assert isinstance(body, str)
    assert "not a secret name" in body
    assert requested == []


def test_an_empty_name_or_reason_is_refused() -> None:
    body_a, _ = _call(action="request", name="", reason="any")
    body_b, _ = _call(action="request", name="OPENAI_API_KEY", reason="")

    assert isinstance(body_a, str)
    assert "`name`" in body_a
    assert isinstance(body_b, str)
    assert "`reason`" in body_b


def test_a_long_reason_is_refused_by_character_count() -> None:
    body, _ = _call(action="request", name="OPENAI_API_KEY", reason="x" * (MAX_REASON_CHARS + 1))
    assert isinstance(body, str)
    assert "past" in body


def test_a_long_name_is_refused_by_character_count() -> None:
    name = "A" + "B" * MAX_NAME_CHARS
    body, _ = _call(action="request", name=name, reason="to list my charges")
    assert isinstance(body, str)
    assert "not a secret name" in body


def test_a_no_answer_outcome_carries_the_hint_to_the_terminal() -> None:
    async def asker(request: SecretRequest) -> SecretAnswer:
        return SecretAnswer(SecretOutcome.NO_ANSWER, detail="No app attached.")

    tool = make_secrets_tool(asker, list)

    async def go() -> Any:
        text = await tool.run({"action": "request", "name": "STRIPE_API_KEY", "reason": "charges"})
        assert isinstance(text, str)
        return json.loads(text)

    body = asyncio.run(go())
    assert body == {
        "status": "no_answer",
        "name": "STRIPE_API_KEY",
        "hint": "atlas secrets set STRIPE_API_KEY",
        "detail": "No app attached.",
    }


def test_a_failed_outcome_carries_the_error_detail() -> None:
    async def asker(request: SecretRequest) -> SecretAnswer:
        return SecretAnswer(SecretOutcome.FAILED, detail="The vault is locked.")

    tool = make_secrets_tool(asker, list)

    async def go() -> Any:
        text = await tool.run({"action": "request", "name": "STRIPE_API_KEY", "reason": "charges"})
        assert isinstance(text, str)
        return json.loads(text)

    body = asyncio.run(go())
    assert body == {
        "status": "failed",
        "name": "STRIPE_API_KEY",
        "detail": "The vault is locked.",
    }


def test_the_tool_is_deferred_and_sourced_as_atlas_secrets() -> None:
    tool = make_secrets_tool(_skipped, list)

    assert tool.deferred is True
    assert tool.source == "atlas.secrets"
    assert tool.name == "secrets"


def test_description_tells_the_model_not_to_ask_in_the_chat() -> None:
    tool = make_secrets_tool(_skipped, list)

    assert (
        "do not ask" in tool.description.lower()
        or "never ask" in tool.description.lower()
        or "do not" in tool.description.lower()
    )


@pytest.mark.parametrize(
    "name",
    ["OPENAI_API_KEY", "A", "A_B", "A1_2", "A" * MAX_NAME_CHARS],
)
def test_name_pattern_accepts_env_variable_names(name: str) -> None:
    assert NAME_PATTERN.match(name) is not None
    assert parse_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["1KEY", "key", "A-B", "A.B", "A B", "", "A" * (MAX_NAME_CHARS + 1)],
)
def test_name_pattern_refuses_everything_else(name: str) -> None:
    assert parse_name(name) is None


def test_parse_arguments_matches_the_tools_validation() -> None:
    assert parse_arguments({"action": "list"}) == ("list", "", "")
    assert parse_arguments(
        {"action": "request", "name": "OPENAI_API_KEY", "reason": "to call OpenAI"}
    ) == ("request", "OPENAI_API_KEY", "to call OpenAI")
    assert isinstance(parse_arguments({"action": "nope"}), str)
    assert isinstance(parse_arguments({"action": "request", "name": "k", "reason": ""}), str)


def test_a_request_waits_fifteen_minutes() -> None:
    assert SECRET_REQUEST_SECONDS == 15 * 60
