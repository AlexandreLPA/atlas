"""The `/` of a conversation of Atlas's or a bot's: Atlas's own commands and the skills the user
invokes there, and a skill loaded by its `/skill-name`.

The model is the fake model server through the real adapter; the skills, the bots and the
database are real, in a temporary home.
"""

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from atlas.__main__ import SCHEMAS
from atlas.agent import SKILL_MARK
from atlas.assistant import Assistant, SlashCommandView
from atlas.conversations import Kind
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.models import CallLedger
from atlas.secrets import Vault
from atlas.skills import SkillUsage
from fake_model import FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"


@pytest.fixture
def assistant(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> Assistant:
    migrate(Database(home.database_file), SCHEMAS)
    chosen = ModelSettings(provider="openrouter")
    ledger = CallLedger(Database(home.database_file))
    models = connect(fake, "openrouter", settings=chosen, ledger=ledger)
    work = tmp_path / "work"
    work.mkdir()
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    return Assistant(home, settings, models, vault, listener=lambda _c, _e: None)


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())  # pyright: ignore[reportArgumentType]


def _skill(folder: Path, name: str, body: str, extra: str = "") -> None:
    (folder / name).mkdir(parents=True)
    text = f"---\nname: {name}\ndescription: The {name} skill.\n{extra}---\n{body}\n"
    (folder / name / "SKILL.md").write_text(text, encoding="utf-8")


def _said(fake: FakeModelServer, call: int) -> str:
    return str(fake.bodies(CHAT)[call]["messages"][-1]["content"])


def test_the_slash_list_is_atlass_commands_then_the_skills_the_user_invokes(
    assistant: Assistant, home: Home
) -> None:
    _skill(home.skills_folder, "weekly-report", "Gather the numbers.")
    _skill(home.skills_folder, "background", "Known.", "user-invocable: false\n")
    _skill(home.skills_folder, "new", "A skill named as a command.")
    _skill(home.skills_folder, "deploy", "Ship it.")
    assistant.bots.create("finance", body="You are the finance bot.", skills=["deploy"])
    _skill(home.bots_folder / "finance" / "skills", "bills", "Pay them.")
    fixed = assistant.bot_conversation("finance")

    main = assistant.slash_commands(None)
    bot = assistant.slash_commands(fixed)

    assert main == [
        SlashCommandView("new", "Start a new conversation", group="command"),
        SlashCommandView("stop", "Stop the turn running", group="command"),
        SlashCommandView("deploy", "The deploy skill."),
        SlashCommandView("weekly-report", "The weekly-report skill."),
    ]
    # A bot's conversation is its one: no /new, and its skills.
    assert [(c.name, c.group) for c in bot] == [
        ("stop", "command"),
        ("bills", "skill"),
        ("deploy", "skill"),
    ]
    assert assistant.slash_commands(assistant.conversations.create().id) == main


def test_a_skill_typed_with_a_slash_is_loaded_into_the_turn(
    assistant: Assistant, fake: FakeModelServer, home: Home
) -> None:
    _skill(
        home.skills_folder,
        "weekly-report",
        "Gather the numbers of $ARGUMENTS.\nUse ${CLAUDE_SKILL_DIR}/template.md.",
    )
    (home.skills_folder / "weekly-report" / "template.md").write_text("T", encoding="utf-8")
    _skill(home.skills_folder, "background", "Known.", "user-invocable: false\n")
    fake.reply(FakeReply(text="Done."), FakeReply(text="Hidden."), FakeReply(text="Plain."))

    async def scenario() -> str:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        for text in ("/weekly-report  May and June", "/background", "/nope at all"):
            conversation.send(text)
            await conversation.wait()
        await assistant.close()
        return stored.id

    conversation = _run(scenario)
    folder = home.skills_folder / "weekly-report"

    loaded = _said(fake, 0)
    assert loaded.startswith(f'{SKILL_MARK}weekly-report" skill')
    assert "Gather the numbers of May and June." in loaded
    assert f"Use {folder}/template.md." in loaded
    assert f"[Skill directory: {folder}]" in loaded
    assert "Files: template.md." in loaded
    assert loaded.endswith(
        "The user has provided the following instruction alongside the skill invocation: "
        "May and June"
    )
    # A skill the menu does not offer, or no skill, goes as typed.
    assert _said(fake, 1) == "/background"
    assert _said(fake, 2) == "/nope at all"
    # The conversation is called by what the user typed, and the load counts as a use.
    assert assistant.conversations.info(conversation).title == "/weekly-report May and June"
    kinds = [m.kind for m in assistant.conversations.messages(conversation) if m.role == "user"]
    assert kinds == [Kind.SKILL, Kind.MESSAGE, Kind.MESSAGE]
    record = SkillUsage(Database(home.database_file)).record("weekly-report")
    assert record is not None
    assert record.use_count == 1


def test_a_bots_conversation_loads_its_own_skills_and_never_a_command(
    assistant: Assistant, fake: FakeModelServer, home: Home
) -> None:
    assistant.bots.create("finance", body="You are the finance bot.", skills=[])
    _skill(home.bots_folder / "finance" / "skills", "bills", "Pay them.")
    _skill(home.skills_folder, "weekly-report", "Gather the numbers.")
    _skill(home.skills_folder, "new", "A skill named as a command.")
    fake.reply(FakeReply(text="Paid."), FakeReply(text="Not here."), FakeReply(text="Hi."))

    async def scenario() -> None:
        finance = assistant.conversation(assistant.bot_conversation("finance"))
        # As a Telegram group sends it, the bot named after the command.
        for text in ("/bills@atlas_bot this month", "/weekly-report"):
            finance.send(text)
            await finance.wait()
        mine = assistant.conversation(assistant.conversations.create().id)
        mine.send("/new")
        await mine.wait()
        await assistant.close()

    _run(scenario)

    assert _said(fake, 0).startswith(f'{SKILL_MARK}bills" skill')
    assert _said(fake, 0).endswith("skill invocation: this month")
    assert _said(fake, 1) == "/weekly-report"
    assert _said(fake, 2) == "/new"
    record = SkillUsage(Database(home.database_file), "finance").record("bills")
    assert record is not None
    assert record.use_count == 1
