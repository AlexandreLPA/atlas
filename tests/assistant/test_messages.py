"""Messages between the home's agents, through the real assistant and the fake model server: the
main agent asks a bot from any conversation and its answer comes back, a bot writes to another,
silence and failure come back as what they are, and a chain with no word from the user stops.

The agents call the model at once, so each scripted reply is for one agent: the fake model gives
it to the request whose instructions say who it is (`You are `@finance`.`).
"""

import asyncio
import dataclasses
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.agent import TurnEnded, TurnOutcome
from atlas.assistant import Assistant
from atlas.assistant import messages as messages_module
from atlas.assistant.messages import reply_notice
from atlas.bots import MESSAGE_MARK
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.models import CallLedger
from atlas.secrets import Vault
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"


@pytest.fixture
def atlas(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> Assistant:
    migrate(Database(home.database_file), SCHEMAS)
    chosen = ModelSettings(provider="openrouter")
    ledger = CallLedger(Database(home.database_file))
    models = connect(fake, "openrouter", settings=chosen, ledger=ledger)
    work = tmp_path / "work"
    work.mkdir()
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    return Assistant(home, settings, models, vault, listener=lambda c, e: None)


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())  # pyright: ignore[reportArgumentType]


async def _quiet(atlas: Assistant, *conversations: str) -> None:
    """Until no message is on its way and none of the conversations has a turn running."""
    for _ in range(3_000):
        busy = any(atlas.conversation(found).busy for found in conversations)
        if not busy and not atlas.messages.pending:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("The agents never went quiet.")


def _who(agent: str) -> str:
    return f"You are `@{agent}`."


def _says(agent: str, text: str, **fields: Any) -> FakeReply:
    """A reply only `agent` takes."""
    return FakeReply(text=text, when=_who(agent), **fields)


def _sends(agent: str, target: str, message: str, call: str = "m1") -> FakeReply:
    """`agent` calls message_agent."""
    arguments = json.dumps({"target": target, "message": message})
    return FakeReply(calls=[FakeCall("message_agent", arguments, id=call)], when=_who(agent))


def _system(body: dict[str, Any]) -> str:
    return str(body["messages"][0]["content"])


def _bodies(fake: FakeModelServer, agent: str) -> list[dict[str, Any]]:
    """The requests `agent` made, in order."""
    return [body for body in fake.bodies(CHAT) if _who(agent) in _system(body)]


def _last(body: dict[str, Any]) -> str:
    return str(body["messages"][-1]["content"])


def _tools(body: dict[str, Any]) -> set[str]:
    return {tool["function"]["name"] for tool in body.get("tools", [])}


def _results(fake: FakeModelServer) -> set[str]:
    """Every tool result any agent read."""
    return {
        str(message["content"])
        for body in fake.bodies(CHAT)
        for message in body["messages"]
        if message["role"] == "tool"
    }


def _said(atlas: Assistant, conversation: str) -> list[str]:
    return [message.text for message in atlas.conversations.messages(conversation)]


def test_the_main_agent_asks_a_bot_from_any_conversation_and_the_answer_comes_back(
    atlas: Assistant, fake: FakeModelServer
) -> None:
    atlas.bots.create("finance", title="Finance", description="Bills and taxes.", body="Finance.")
    atlas.bots.create("work", title="Work", description="The job's projects.")
    fake.reply(
        _sends("atlas", "finance", "Was this month's rent paid, and when?"),
        _says("atlas", "I asked Finance."),
        _says("finance", "Paid on the 5th."),
        _says("atlas", "Finance says the rent was paid on the 5th."),
    )

    async def scenario() -> tuple[str, str]:
        mine = atlas.conversations.create().id
        atlas.conversation(mine).send("Ask the finance bot whether the rent was paid.")
        fixed = atlas.bot_conversation("finance")
        await _quiet(atlas, mine, fixed)
        await atlas.close()
        return mine, fixed

    mine, fixed = _run(scenario)
    asked, acked, told = _bodies(fake, "atlas")
    (as_bot,) = _bodies(fake, "finance")

    # The roster is in the main agent's instructions, and the tool beside its others.
    assert "## Messaging other agents" in _system(asked)
    assert "- `@finance` — Finance — Bills and taxes." in _system(asked)
    assert "- `@work` — Work — The job's projects." in _system(asked)
    assert "message_agent" in _tools(asked)
    # Fire and forget: the call returns the hand-off, and the turn ends.
    assert '"status": "queued"' in _last(acked)
    assert '"to": "@finance"' in _last(acked)
    # The bot reads who wrote, in its own conversation, as itself, and knows its teammates.
    assert _last(as_bot) == f"{MESSAGE_MARK}Atlas (@atlas): Was this month's rent paid, and when?"
    assert _system(as_bot).startswith("Finance.")
    assert "- `@atlas` — Atlas — the home's main agent" in _system(as_bot)
    assert "- `@work` — Work" in _system(as_bot)
    assert "`@finance` —" not in _system(as_bot)
    # Its answer comes back to the conversation that asked, as a notice, never as the user's.
    assert _last(told).startswith("[IMPORTANT: @finance answered the message you sent to @finance")
    assert _last(told).endswith("\n\nPaid on the 5th.")
    assert _said(atlas, mine)[-1] == "Finance says the rent was paid on the 5th."
    assert _said(atlas, fixed)[-1] == "Paid on the 5th."


def test_a_home_with_no_bot_has_neither_the_roster_nor_the_tool(
    atlas: Assistant, fake: FakeModelServer
) -> None:
    fake.reply(FakeReply(text="Hi."))

    async def scenario() -> None:
        mine = atlas.conversations.create().id
        atlas.conversation(mine).send("Hello")
        await atlas.conversation(mine).wait()
        await atlas.close()

    _run(scenario)
    body = fake.bodies(CHAT)[0]

    assert "message_agent" not in _tools(body)
    assert "Messaging other agents" not in _system(body)


def test_a_bot_writes_to_another_and_to_the_main_agent_where_it_last_wrote_from(
    atlas: Assistant, fake: FakeModelServer
) -> None:
    atlas.bots.create("finance", title="Finance")
    atlas.bots.create("work", title="Work")

    async def scenario() -> tuple[str, str]:
        finance = atlas.bot_conversation("finance")
        work = atlas.bot_conversation("work")
        # The user tells the finance bot something. It has no way to the main agent yet, and
        # writes to the work bot, found by its title.
        fake.reply(
            _sends("finance", "atlas", "The user changed jobs.", "m1"),
            _sends("finance", "Work", "The user changed jobs; salary lands on the 10th.", "m2"),
            _says("finance", "Told Work."),
            _says("work", "Noted."),
            _says("finance", "Work has it."),
        )
        atlas.conversation(finance).send("I changed jobs.")
        await _quiet(atlas, finance, work)
        # Now the main agent writes to finance from one of its conversations, and finance, asked,
        # writes back to it there before answering.
        fake.reply(
            _sends("atlas", "finance", "What changed this week?", "m3"),
            _says("atlas", "Asked."),
            _sends("finance", "atlas", "One more thing: the rent rose.", "m4"),
            _says("finance", "The user changed jobs."),
            _says("atlas", "Thanks."),
            _says("finance", "[SILENT]"),
        )
        mine = atlas.conversations.create().id
        atlas.conversation(mine).send("Ask finance what changed.")
        await _quiet(atlas, finance, work, mine)
        await atlas.close()
        return finance, mine

    finance, mine = _run(scenario)

    # With no conversation of the main agent's to receive it, a bot is told to say it itself.
    assert any("@atlas has not written to you" in result for result in _results(fake))
    # A bot reads who wrote.
    assert _last(_bodies(fake, "work")[0]) == (
        f"{MESSAGE_MARK}Finance (@finance): The user changed jobs; salary lands on the 10th."
    )
    # Once the main agent wrote from a conversation, a bot's message reaches that conversation;
    # written in the turn that answers the main agent, it is the answer, and the turn's text
    # does not come back as well.
    in_mine = _said(atlas, mine)
    assert f"{MESSAGE_MARK}Finance (@finance): One more thing: the rent rose." in in_mine
    assert not any(text.endswith("\n\nThe user changed jobs.") for text in in_mine)
    # What the main agent answered went back to the bot, which had nothing to add.
    assert any(text.endswith("\n\nThanks.") for text in _said(atlas, finance))
    assert not atlas.messages.pending


@pytest.mark.parametrize(
    ("target", "said"),
    [
        ("ghost", "There is no agent 'ghost'. Your teammates are: @finance, @work."),
        ("@atlas", "'@atlas' is you. Your teammates are: @finance, @work."),
        ("Books", "'Books' names more than one agent. Use the name: @finance, @work."),
    ],
)
def test_a_target_that_names_nobody_is_refused_with_the_roster(
    atlas: Assistant, fake: FakeModelServer, target: str, said: str
) -> None:
    atlas.bots.create("finance", title="Books")
    atlas.bots.create("work", title="Books")
    fake.reply(_sends("atlas", target, "Hello."), _says("atlas", "Nobody there."))

    async def scenario() -> None:
        mine = atlas.conversations.create().id
        atlas.conversation(mine).send("Say hello.")
        await _quiet(atlas, mine)
        await atlas.close()

    _run(scenario)

    assert _results(fake) == {f"[Atlas: {said}]"}
    assert len(fake.bodies(CHAT)) == 2


def test_messages_to_one_bot_run_in_order_one_turn_at_a_time(
    atlas: Assistant, fake: FakeModelServer
) -> None:
    atlas.bots.create("finance", title="Finance")
    twice = FakeReply(
        calls=[
            FakeCall("message_agent", json.dumps({"target": "finance", "message": text}), id=i)
            for i, text in (("m1", "First question."), ("m2", "Second question."))
        ],
        when=_who("atlas"),
    )
    fake.reply(
        twice,
        _says("atlas", "Asked twice."),
        _says("finance", "First answer."),
        _says("finance", "Second answer."),
        _says("atlas", "Got one."),
        _says("atlas", "Got the other."),
    )

    async def scenario() -> str:
        mine = atlas.conversations.create().id
        atlas.conversation(mine).send("Ask finance two things.")
        fixed = atlas.bot_conversation("finance")
        await _quiet(atlas, mine, fixed)
        await atlas.close()
        return fixed

    fixed = _run(scenario)

    assert _said(atlas, fixed) == [
        f"{MESSAGE_MARK}Atlas (@atlas): First question.",
        "First answer.",
        f"{MESSAGE_MARK}Atlas (@atlas): Second question.",
        "Second answer.",
    ]


def test_a_chain_with_no_word_from_the_user_stops_at_the_ceiling(
    atlas: Assistant, fake: FakeModelServer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(messages_module, "MAX_AGENT_HOPS", 2)
    atlas.bots.create("finance")
    atlas.bots.create("work")

    async def scenario() -> None:
        mine = atlas.conversations.create().id
        finance, work = atlas.bot_conversation("finance"), atlas.bot_conversation("work")
        fake.reply(
            _sends("atlas", "finance", "Check with work.", "m1"),  # the first message
            _says("atlas", "Asked."),
            _sends("finance", "work", "Atlas wants us to check.", "m2"),  # the second
            _says("finance", "Passed it on."),
            _sends("work", "finance", "Checking back with you.", "m3"),  # a third: refused
            _says("work", "I stopped: two messages went with no word from the user."),
            # The answers travelling back start no new message.
            _says("finance", "Work stopped."),
            _says("atlas", "It went no further."),
        )
        atlas.conversation(mine).send("Have finance check with work.")
        await _quiet(atlas, mine, finance, work)
        # The user speaks again: the count starts over.
        fake.reply(
            _sends("atlas", "finance", "And now?", "m4"),
            _says("atlas", "Asked again."),
            _says("finance", "Still fine."),
            _says("atlas", "Fine."),
        )
        atlas.conversation(mine).send("Ask finance again.")
        await _quiet(atlas, mine, finance)
        await atlas.close()

    _run(scenario)
    results = _results(fake)

    refused = [result for result in results if "have gone between agents" in result]
    assert len(refused) == 1
    assert refused[0].startswith("[Atlas: 2 messages have gone between agents since the user")
    assert sum('"status": "queued"' in result for result in results) == 3
    assert _last(_bodies(fake, "work")[0]).endswith("Atlas wants us to check.")


def test_silence_and_a_failed_turn_come_back_as_what_they_are(
    atlas: Assistant, fake: FakeModelServer
) -> None:
    bot = atlas.bots.create("finance")

    async def scenario() -> str:
        mine = atlas.conversations.create().id
        fixed = atlas.bot_conversation("finance")
        fake.reply(
            _sends("atlas", "finance", "FYI: the rent rose.", "m1"),
            _says("atlas", "Told finance."),
            _says("finance", "[SILENT]"),
            _says("atlas", "Finance had nothing to add."),
        )
        atlas.conversation(mine).send("Tell finance the rent rose.")
        await _quiet(atlas, mine, fixed)
        # A bot that cannot run: its turn fails, and the failure is what comes back.
        (bot.folder / "BOT.md").write_text("---\nmodel: luna\n---\nFinance.\n", encoding="utf-8")
        await atlas.conversation(fixed).close()
        atlas.forget(fixed)
        fake.reply(
            _sends("atlas", "finance", "Is the rent paid?", "m2"),
            _says("atlas", "Asked."),
            _says("atlas", "Finance could not answer."),
        )
        atlas.conversation(mine).send("Ask finance whether the rent is paid.")
        await _quiet(atlas, mine, fixed)
        await atlas.close()
        return mine

    mine = _run(scenario)
    notices = [text for text in _said(atlas, mine) if text.startswith("[IMPORTANT: ")]

    assert len(notices) == 2
    assert "@finance read the message you sent to @finance" in notices[0]
    assert notices[0].endswith("and had nothing to add.]")
    assert "[SILENT]" not in notices[0]
    assert "was not answered:" in notices[1]
    assert "it must be provider/model" in notices[1]
    assert "Do not resend it blindly" in notices[1]


def test_a_message_stopped_before_its_answer_says_so_to_the_sender(
    atlas: Assistant, fake: FakeModelServer
) -> None:
    atlas.bots.create("finance")
    fake.reply(
        _sends("atlas", "finance", "Is the rent paid?"),
        _says("atlas", "Asked."),
        _says("finance", "Too late.", wait_before=30),  # the bot never gets to answer
        _says("atlas", "Finance was stopped."),
    )

    async def scenario() -> str:
        mine = atlas.conversations.create().id
        atlas.conversation(mine).send("Ask finance.")
        fixed = atlas.conversation(atlas.bot_conversation("finance"))
        for _ in range(1_000):
            if fixed.running and _bodies(fake, "finance"):
                break
            await asyncio.sleep(0.01)
        assert await fixed.interrupt()
        await _quiet(atlas, mine)
        await atlas.close()
        return mine

    mine = _run(scenario)

    assert any("was stopped before it was answered" in text for text in _said(atlas, mine))
    assert _said(atlas, mine)[-1] == "Finance was stopped."


def test_the_reply_notice_says_whose_words_they_are_and_cuts_a_long_one() -> None:
    long = "x" * 20_000 + " the end"
    answered = TurnEnded(TurnOutcome.ANSWERED, long, 1, None)

    notice = reply_notice("finance", "d-1", answered)

    assert notice.startswith("[IMPORTANT: @finance answered the message you sent to @finance")
    assert "This is that agent's reply, not the user's" in notice
    assert "[4008 earlier characters were cut.]" in notice
    assert notice.endswith(" the end")
    failed = dataclasses.replace(answered, outcome=TurnOutcome.FAILED, text="No credit.")
    assert "was not answered: No credit." in reply_notice("finance", "d-1", failed)


def test_a_bot_that_answers_with_message_agent_is_heard_once(
    atlas: Assistant, fake: FakeModelServer
) -> None:
    # Found by the real proof on 13/10/2026: the bot wrote back with the tool, as bots do, and
    # its turn's text went back as well, so the main agent read the answer twice.
    atlas.bots.create("finance", title="Finance")
    fake.reply(
        _sends("atlas", "finance", "When is the rent due?", "m1"),
        _says("atlas", "Asked."),
        _sends("finance", "atlas", "The rent is due on the 5th.", "m2"),
        _says("finance", "Sent."),
        _says("atlas", "Finance says the 5th."),
        _says("finance", "[SILENT]"),
    )

    async def scenario() -> str:
        mine = atlas.conversations.create().id
        atlas.conversation(mine).send("Ask finance when the rent is due.")
        await _quiet(atlas, mine, atlas.bot_conversation("finance"))
        await atlas.close()
        return mine

    mine = _run(scenario)
    came_back = [
        text
        for text in _said(atlas, mine)
        if text.startswith(("[IMPORTANT: @finance", f"{MESSAGE_MARK}Finance"))
    ]

    assert came_back == [f"{MESSAGE_MARK}Finance (@finance): The rent is due on the 5th."]
    assert _said(atlas, mine)[-1] == "Finance says the 5th."
    # The message that is an answer starts no new exchange: nothing more ran.
    assert len(_bodies(fake, "atlas")) == 3
