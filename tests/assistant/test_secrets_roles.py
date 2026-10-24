"""Who is given the secrets tool (step 16d): a conversation with someone to answer, and no one else.

A subagent, a routine's run and the background review are not given it, and a conversation whose
app is not attached is not given it either (no asker, no card). The review's copy of the tools
drops it and makes its tool search again over the copy.
"""

from pathlib import Path

from atlas.__main__ import SCHEMAS
from atlas.assistant import Assistant, ConversationRole
from atlas.assistant.service import _review_tools
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.models import CallLedger
from atlas.secrets import Vault
from atlas.tools import (
    Answers,
    Outcome,
    Question,
    SecretAnswer,
    SecretAsker,
    SecretOutcome,
    SecretRequest,
)
from fake_model import FakeModelServer
from model_support import Connect


async def _nobody(questions: tuple[Question, ...]) -> Answers:
    del questions
    return Answers(Outcome.UNDELIVERED)


async def _skipped(request: SecretRequest) -> SecretAnswer:
    del request
    return SecretAnswer(SecretOutcome.SKIPPED)


def _secret_asker(conversation: str, bot: str) -> SecretAsker:
    del conversation, bot
    return _skipped


def _no_secret_asker(conversation: str, bot: str) -> SecretAsker | None:
    del conversation, bot
    return None


def _assistant(
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    *,
    with_secret_asker: bool = True,
) -> Assistant:
    migrate(Database(home.database_file), SCHEMAS)
    chosen = ModelSettings(provider="openrouter")
    models = connect(
        fake, "openrouter", settings=chosen, ledger=CallLedger(Database(home.database_file))
    )
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(tmp_path))
    return Assistant(
        home,
        settings,
        models,
        vault,
        listener=lambda _c, _e: None,
        secret_asker=_secret_asker if with_secret_asker else _no_secret_asker,
    )


def test_a_conversation_with_someone_to_answer_is_given_the_deferred_tool(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    toolset, _, _ = assistant._shared.tools("c-1", [], _nobody, None)  # pyright: ignore[reportPrivateUsage]

    tool = toolset.get("secrets")
    assert tool is not None
    assert tool.deferred is True
    assert tool.source == "atlas.secrets"


def test_no_app_attached_means_no_secrets_tool(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    toolset, _, _ = assistant._shared.tools("c-1", [], None, None)  # pyright: ignore[reportPrivateUsage]

    assert toolset.get("secrets") is None


def test_a_subagent_and_a_routine_are_not_given_it_even_with_an_asker_for_the_secret(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    shared = assistant._shared  # pyright: ignore[reportPrivateUsage]
    # The open conversation passes no asker to a subagent or a routine: only the user's has one.
    for role in (ConversationRole.SUBAGENT, ConversationRole.ROUTINE):
        toolset, _, _ = shared.tools("c-1", [], None, None, role=role)
        assert toolset.get("secrets") is None, role


def test_without_a_secret_asker_the_tool_is_not_offered(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant = _assistant(home, vault, fake, connect, tmp_path, with_secret_asker=False)
    toolset, _, _ = assistant._shared.tools("c-1", [], _nobody, None)  # pyright: ignore[reportPrivateUsage]

    assert toolset.get("secrets") is None


def test_the_review_is_given_the_turns_tools_without_the_secrets_tool(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    shared = assistant._shared  # pyright: ignore[reportPrivateUsage]
    turn, _, _ = shared.tools("c-1", [], _nobody, None)

    review = _review_tools(turn, shared.mcp_sources(), None)

    assert review.get("secrets") is None
    assert review.get("ask_user") is not None
    # Its tool search no longer lists the secrets tool, so the review cannot load it.
    search = review.get("tool_search")
    assert search is not None
    assert "secrets" not in search.description.lower().split("tools to load:")[-1]


def test_a_turn_without_the_secrets_tool_hands_its_own_toolset_to_the_review(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    shared = assistant._shared  # pyright: ignore[reportPrivateUsage]
    turn, _, _ = shared.tools("c-1", [], None, None)

    assert _review_tools(turn, shared.mcp_sources(), None) is turn
