"""A bot's conversation in the assistant: who it runs as, the memory it reads and writes, the tools
it is given, and its fixed conversation that lasts forever.

The model is the fake model server through the real adapter; the bots, the memory and the database
are real, in a temporary home.
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.agent import TurnEnded, TurnOutcome
from atlas.assistant import Assistant, ConversationEvent, ConversationRole
from atlas.bots import BotError
from atlas.conversations import BOT, SUBAGENT
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.memory import Action, MemoryFiles, Operation, Target
from atlas.models import CallLedger
from atlas.secrets import Vault
from atlas.skills import Origin, SkillUsage
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"
MISSION = "You are the finance bot. Keep the bills paid on time."

type Heard = list[tuple[str, ConversationEvent]]


@pytest.fixture
def assistant(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> tuple[Assistant, Heard]:
    migrate(Database(home.database_file), SCHEMAS)
    chosen = ModelSettings(provider="openrouter")
    ledger = CallLedger(Database(home.database_file))
    models = connect(fake, "openrouter", settings=chosen, ledger=ledger)
    work = tmp_path / "work"
    work.mkdir()
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    heard: Heard = []
    made = Assistant(home, settings, models, vault, listener=lambda c, e: heard.append((c, e)))
    return made, heard


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())  # pyright: ignore[reportArgumentType]


def _system(body: dict[str, Any]) -> str:
    return str(body["messages"][0]["content"])


def _tools(body: dict[str, Any]) -> set[str]:
    return {tool["function"]["name"] for tool in body.get("tools", [])}


def _save(arguments: dict[str, str]) -> FakeReply:
    return FakeReply(calls=[FakeCall("memory", json.dumps(arguments), id="m1")])


def test_a_bots_conversation_runs_as_the_bot(
    assistant: tuple[Assistant, Heard], fake: FakeModelServer, home: Home
) -> None:
    atlas, _ = assistant
    atlas.bots.create("finance", title="Finance", body=MISSION, tools=["read_file", "memory"])
    files = MemoryFiles(home.memory_folder)
    assert files.change(Target.USER, [Operation(Action.ADD, "Name: Ana")]).entries
    assert files.change(Target.MEMORY, [Operation(Action.ADD, "Deploys via make ship")]).entries
    fake.reply(FakeReply(text="Paid."), FakeReply(text="Hi."))

    async def scenario() -> tuple[str, str]:
        fixed = atlas.bot_conversation("finance")
        conversation = atlas.conversation(fixed)
        conversation.send("Is the rent paid?")
        await conversation.wait()
        mine = atlas.conversations.create().id
        atlas.conversation(mine).send("Hello")
        await atlas.conversation(mine).wait()
        await atlas.close()
        return fixed, mine

    fixed, mine = _run(scenario)
    as_bot, as_atlas = fake.bodies(CHAT)

    # Its mission in the identity's place, and no start date in a conversation that never ends.
    assert _system(as_bot).startswith(MISSION)
    assert "You are Atlas" not in _system(as_bot)
    assert "This conversation started" not in _system(as_bot)
    # The home's profile, and not the main agent's memory.
    assert "Name: Ana" in _system(as_bot)
    assert "make ship" not in _system(as_bot)
    # Only what its definition allows.
    assert _tools(as_bot) == {"read_file", "memory"}
    # The main agent is as it was.
    assert _system(as_atlas).startswith("You are Atlas, a personal agent.")
    assert "make ship" in _system(as_atlas)
    assert {"run_command", "ask_user", "skill_manage"} <= _tools(as_atlas)
    # One fixed conversation per bot, in the bots' list and not among the conversations.
    assert atlas.bot_conversation("finance") == fixed
    info = atlas.conversations.info(fixed)
    assert (info.source, info.bot, info.title) == (BOT, "finance", "Finance")
    assert atlas.auto_mode(fixed), "a bot's conversation starts in Auto"
    assert not atlas.auto_mode(mine), "one made outside the app's screen does not"
    assert [found.id for found in atlas.conversations.recent(10)] == [mine]


def test_what_one_agent_saves_about_the_user_reaches_a_bots_open_conversation(
    assistant: tuple[Assistant, Heard], fake: FakeModelServer, home: Home
) -> None:
    # The defect the one profile exists to end: tell the finance bot of the new job, and the
    # product bot still believes the old one. A fixed conversation never starts anew, so its
    # instructions are read again at the turn that finds them changed.
    atlas, _ = assistant
    atlas.bots.create("finance", body=MISSION)
    atlas.bots.create("product", body="You are the product bot.")
    fake.reply(
        FakeReply(text="Hello."),  # product, before
        _save({"action": "add", "target": "user", "content": "Works at the new job"}),
        FakeReply(text="Noted."),  # finance
        _save({"action": "add", "target": "memory", "content": "Rent is due on the 5th"}),
        FakeReply(text="Kept."),  # finance, its own notes
        FakeReply(text="At the new job."),  # product, after
    )

    async def scenario() -> None:
        product = atlas.conversation(atlas.bot_conversation("product"))
        finance = atlas.conversation(atlas.bot_conversation("finance"))
        product.send("Hi")
        await product.wait()
        finance.send("I changed jobs.")
        await finance.wait()
        finance.send("Rent is due on the 5th.")
        await finance.wait()
        product.send("Where do I work?")
        await product.wait()
        await atlas.close()

    _run(scenario)
    bodies = fake.bodies(CHAT)

    assert "new job" not in _system(bodies[0])
    assert "Works at the new job" in _system(bodies[-1])
    # The profile is the home's; each bot's notes are its own.
    assert MemoryFiles(home.memory_folder).entries(Target.USER) == ["Works at the new job"]
    assert MemoryFiles(home.memory_folder).entries(Target.MEMORY) == []
    finance_memory = home.bots_folder / "finance" / "MEMORY.md"
    assert "Rent is due on the 5th" in finance_memory.read_text(encoding="utf-8")
    assert not (home.bots_folder / "product" / "MEMORY.md").exists()
    assert "Rent is due" not in _system(bodies[-1])
    # The bot that changed the profile reads it again too, at its own next turn.
    assert "Works at the new job" in _system(bodies[3])
    stored = atlas.conversations.messages(atlas.bot_conversation("finance"))
    assert [m.text for m in stored if m.text][:1] == ["I changed jobs."]


def test_an_edit_to_a_bots_mission_reaches_its_next_turn(
    assistant: tuple[Assistant, Heard], fake: FakeModelServer
) -> None:
    atlas, _ = assistant
    bot = atlas.bots.create("finance", body=MISSION)
    fake.reply(FakeReply(text="one"), FakeReply(text="two"))

    async def scenario() -> None:
        conversation = atlas.conversation(atlas.bot_conversation("finance"))
        conversation.send("first")
        await conversation.wait()
        (bot.folder / "BOT.md").write_text("Never pay late fees.\n", encoding="utf-8")
        conversation.send("second")
        await conversation.wait()
        await atlas.close()

    _run(scenario)
    first, second = fake.bodies(CHAT)

    assert _system(first).startswith(MISSION)
    assert _system(second).startswith("Never pay late fees.")


def test_a_bot_runs_on_its_own_model_unless_the_conversation_picked_another(
    assistant: tuple[Assistant, Heard], fake: FakeModelServer
) -> None:
    atlas, _ = assistant
    atlas.bots.create("finance", body=MISSION, model="openrouter/openai/gpt-6-luna")
    fake.reply(FakeReply(text="ok"))

    async def scenario() -> None:
        conversation = atlas.conversation(atlas.bot_conversation("finance"))
        conversation.send("hi")
        await conversation.wait()
        await atlas.close()

    _run(scenario)

    assert fake.bodies(CHAT)[0]["model"] == "openai/gpt-6-luna"


@pytest.mark.parametrize(
    ("break_it", "said"),
    [
        (lambda folder: (folder / "BOT.md").unlink(), "There is no bot 'finance'"),
        (
            lambda folder: (folder / "BOT.md").write_text("---\nmodel: luna\n---\n", "utf-8"),
            "it must be provider/model",
        ),
    ],
)
def test_a_bot_that_cannot_run_ends_the_turn_with_the_reason_and_keeps_the_message(
    assistant: tuple[Assistant, Heard],
    break_it: Callable[[Path], object],
    said: str,
) -> None:
    atlas, heard = assistant
    bot = atlas.bots.create("finance", body=MISSION)

    async def scenario() -> str:
        fixed = atlas.bot_conversation("finance")
        break_it(bot.folder)
        conversation = atlas.conversation(fixed)
        conversation.send("Is the rent paid?")
        await conversation.wait()
        await atlas.close()
        return fixed

    fixed = _run(scenario)
    ended = heard[-1][1]

    assert isinstance(ended, TurnEnded)
    assert ended.outcome is TurnOutcome.FAILED
    assert said in ended.text
    assert [m.text for m in atlas.conversations.messages(fixed)] == ["Is the rent paid?"]


def test_there_is_no_fixed_conversation_for_a_bot_that_does_not_exist(
    assistant: tuple[Assistant, Heard],
) -> None:
    atlas, _ = assistant

    with pytest.raises(BotError, match="There is no bot 'ghost'"):
        atlas.bot_conversation("ghost")


def test_a_bots_subagent_is_given_no_more_than_the_bot(
    assistant: tuple[Assistant, Heard], fake: FakeModelServer
) -> None:
    atlas, _ = assistant
    atlas.bots.create("finance", body=MISSION, tools=["read_file", "memory", "delegate"])
    fake.reply(FakeReply(text="Done."))

    async def scenario() -> None:
        parent = atlas.bot_conversation("finance")
        child = atlas.conversations.create(source=SUBAGENT, parent_id=parent, bot="finance")
        conversation = atlas.conversation(
            child.id, role=ConversationRole.SUBAGENT, prompt="Read the bill."
        )
        conversation.send("go")
        await conversation.wait()
        await atlas.close()

    _run(scenario)
    body = fake.bodies(CHAT)[0]

    # A child has no memory and does not delegate; of the rest, only what the bot may use.
    assert _tools(body) == {"read_file"}
    assert _system(body).startswith("You are Atlas, a personal agent.")
    assert _system(body).endswith("Read the bill.")


def _skill(name: str) -> str:
    return f"---\nname: {name}\ndescription: Use when testing.\n---\nDo the thing.\n"


def _listed(body: dict[str, Any]) -> str:
    tools = {tool["function"]["name"]: tool["function"] for tool in body.get("tools", [])}
    return str(tools["use_skill"]["description"]) if "use_skill" in tools else ""


def test_a_skill_a_bot_makes_is_its_own_and_says_who_made_it(
    assistant: tuple[Assistant, Heard], fake: FakeModelServer, home: Home
) -> None:
    atlas, _ = assistant
    atlas.bots.create("finance", body=MISSION)
    atlas.bots.create("work", body="You are the work bot.", skills=["deploy"])
    for name in ("deploy", "weekly-report"):
        (home.skills_folder / name).mkdir(parents=True)
        (home.skills_folder / name / "SKILL.md").write_text(_skill(name), encoding="utf-8")
    create = {"operations": [{"action": "create", "name": "bills", "content": _skill("bills")}]}
    fake.reply(
        FakeReply(calls=[FakeCall("skill_manage", json.dumps(create), id="s1")]),
        FakeReply(text="Kept."),
        FakeReply(text="finance again"),
        FakeReply(text="work"),
        FakeReply(text="atlas"),
    )

    async def scenario() -> None:
        finance = atlas.conversation(atlas.bot_conversation("finance"))
        finance.send("Keep how we pay the bills as a skill.")
        await finance.wait()
        await finance.close()
        atlas.forget(finance.id)
        # A conversation opened anew lists the skills as they are now.
        finance = atlas.conversation(atlas.bot_conversation("finance"))
        finance.send("hi")
        await finance.wait()
        work = atlas.conversation(atlas.bot_conversation("work"))
        work.send("hi")
        await work.wait()
        mine = atlas.conversation(atlas.conversations.create().id)
        mine.send("hi")
        await mine.wait()
        await atlas.close()

    _run(scenario)
    bodies = fake.bodies(CHAT)

    # In the bot's folder, never in the home's library.
    assert (home.bots_folder / "finance" / "skills" / "bills" / "SKILL.md").is_file()
    assert not (home.skills_folder / "bills").exists()
    record = SkillUsage(Database(home.database_file), "finance").record("bills")
    assert record is not None
    assert (record.creator, record.origin) == ("finance", Origin.USER)
    # The bot that made it sees it, with the whole library; the bot with a list sees its list;
    # the main agent sees the library.
    assert all(name in _listed(bodies[2]) for name in ("bills", "deploy", "weekly-report"))
    assert "deploy" in _listed(bodies[3])
    assert "weekly-report" not in _listed(bodies[3])
    assert "bills" not in _listed(bodies[3])
    assert "bills" not in _listed(bodies[4])
    assert "weekly-report" in _listed(bodies[4])
