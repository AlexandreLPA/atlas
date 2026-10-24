"""Subagents for real: a conversation delegates through its delegate tool, each child runs as a
conversation of its own through the real adapter and the fake model server, and the batch comes
back as one notice. The database, the tools and the approval gate are real, in a temporary home.

The fake model tells the parent from its children by their system prompts: the parent's carries
the memory guidance ("Skills come first"), and each child's carries its own context."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.agent import TurnEnded
from atlas.assistant import (
    Assistant,
    ConversationEvent,
    ConversationRole,
    OpenConversation,
    SubagentChanged,
)
from atlas.conversations import SUBAGENT, Kind
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate, utc_now
from atlas.jobs import STEER_OPEN, Subagent, SubagentStatus, SubagentStore
from atlas.models import CallLedger, UserMessage
from atlas.secrets import Vault
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"
#: Only the parent's system prompt holds it: the memory guidance.
PARENT = "Skills come first"


type Make = Callable[[], tuple[Assistant, list[tuple[str, ConversationEvent]]]]


@pytest.fixture
def make(home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path) -> Make:
    migrate(Database(home.database_file), SCHEMAS)

    def make() -> tuple[Assistant, list[tuple[str, ConversationEvent]]]:
        chosen = ModelSettings(provider="openrouter")
        ledger = CallLedger(Database(home.database_file))
        models = connect(fake, "openrouter", settings=chosen, ledger=ledger)
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
        )
        return assistant, heard

    return make


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())  # pyright: ignore[reportArgumentType]


def _spawn(*tasks: tuple[str, str]) -> FakeReply:
    payload = [{"goal": goal, "context": context} for goal, context in tasks]
    return FakeReply(calls=[FakeCall("delegate", json.dumps({"tasks": payload}))], when=PARENT)


async def _settle(assistant: Assistant, conversation: OpenConversation) -> None:
    """Until the parent's turns, its children and their notice are all done."""
    for _ in range(20):
        await conversation.wait()
        await assistant.subagents.settled()
        await asyncio.sleep(0.05)
        if not conversation.busy:
            return


def _bodies(fake: FakeModelServer, holding: str) -> list[dict[str, Any]]:
    """The requests whose system prompt holds `holding`."""
    return [body for body in fake.bodies(CHAT) if holding in str(body["messages"][0]["content"])]


def _notices(conversation: OpenConversation) -> list[str]:
    return [
        message.text
        for message in conversation.history
        if isinstance(message, UserMessage) and message.text.startswith("[ASYNC DELEGATION")
    ]


# -- a batch ----------------------------------------------------------------------------------


def test_a_batch_runs_in_parallel_and_comes_back_as_one_notice(
    make: Make, fake: FakeModelServer, home: Home
) -> None:
    fake.reply(
        _spawn(
            ("Count the apples in the basket.", "ctx-apples"),
            ("Count the pears in the basket.", "ctx-pears"),
        ),
        FakeReply(text="Two helpers are counting.", when=PARENT),
        FakeReply(text="There are 3 apples.", when="ctx-apples", wait_before=0.3),
        FakeReply(text="There are 5 pears.", when="ctx-pears"),
        FakeReply(text="3 apples and 5 pears.", when=PARENT),
    )
    assistant, heard = make()

    async def scenario() -> OpenConversation:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("How much fruit is in the basket?")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    notices = _notices(conversation)
    assert len(notices) == 1, "one notice for the whole call"
    notice = notices[0]
    assert notice.startswith("[ASYNC DELEGATION BATCH COMPLETE — d-")
    assert "TASK 1/2: Count the apples in the basket.  (status=completed" in notice
    assert "There are 3 apples." in notice
    assert "There are 5 pears." in notice
    ended = [event for c, event in heard if c == conversation.id and isinstance(event, TurnEnded)]
    assert [event.text for event in ended] == ["Two helpers are counting.", "3 apples and 5 pears."]
    stored = assistant.conversations.messages(conversation.id)
    assert [m.kind for m in stored if m.text.startswith("[ASYNC")] == [Kind.NOTICE]

    dispatched = json.loads(conversation.history[3].text)  # the delegate call's result
    assert dispatched["status"] == "dispatched"
    assert dispatched["count"] == 2

    children = [
        event for c, event in heard if c == conversation.id and isinstance(event, SubagentChanged)
    ]
    assert {child.status for child in children} == {"running", "completed"}
    store = SubagentStore(Database(home.database_file))
    records = store.delegation(children[0].delegation_id)
    assert [(r.status, r.delivered, r.model_calls) for r in records] == [
        (SubagentStatus.COMPLETED, True, 1),
        (SubagentStatus.COMPLETED, True, 1),
    ]
    for record in records:
        info = assistant.conversations.info(record.conversation_id)
        assert info.source == SUBAGENT
    assert all(info.source != SUBAGENT for info in assistant.conversations.recent(50))


def test_a_child_has_the_subagent_prompt_and_tools_and_its_calls_are_its_own(
    make: Make, fake: FakeModelServer, home: Home
) -> None:
    fake.reply(
        _spawn(("Find the invoice for March.", "ctx-invoice")),
        FakeReply(text="On it.", when=PARENT),
        FakeReply(text="Not found.", when="ctx-invoice"),
        FakeReply(text="It was not found.", when=PARENT),
    )
    assistant, _ = make()
    home.soul_file.write_text("You are Kestrel, dry and terse.", encoding="utf-8")

    async def scenario() -> None:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Find my March invoice.")
        await _settle(assistant, conversation)
        await assistant.close()

    _run(scenario)

    child = _bodies(fake, "ctx-invoice")[0]
    system = str(child["messages"][0]["content"])
    # The soul is the parent's: the child is Atlas, and skips SOUL.md.
    assert system.startswith("You are Atlas, a personal agent.")
    assert "Kestrel" not in system
    assert "SOUL.md" not in system
    assert str(_bodies(fake, PARENT)[0]["messages"][0]["content"]).startswith("You are Kestrel")
    assert "You are a focused subagent working on a specific delegated task." in system
    assert "CONTEXT:\nctx-invoice" in system
    assert PARENT not in system, "a child gets no memory snapshot or guidance"
    assert child["messages"][1] == {"role": "user", "content": "Find the invoice for March."}
    names = {tool["function"]["name"] for tool in child["tools"]}
    assert {"delegate", "memory", "ask_user", "cronjob_manage"}.isdisjoint(names)
    assert {"read_file", "run_command", "skill_manage"} <= names
    parent = _bodies(fake, PARENT)[0]
    assert "delegate" in {tool["function"]["name"] for tool in parent["tools"]}
    ledger = CallLedger(Database(home.database_file))
    purposes = [call.purpose for call in ledger.recent()]
    assert purposes.count("subagent") == 1
    assert purposes.count("turn") == 3


EDITOR = """---
name: editor
description: Cuts and orders the scenes of a video.
tools: Read, Grep, Bash
model: inherit
---
You are the video editor. Cut what drags.
"""


def test_a_plugin_s_agent_is_a_predefined_subagent_with_its_instructions_and_tools(
    make: Make, fake: FakeModelServer, tmp_path: Path
) -> None:
    """Claude Code's `agents/<name>.md` (step 16k): `delegate` offers it by name; its child has
    its instructions before the task's context and only the tools it names, Claude Code's names
    read as Atlas's; a name nobody brings delegates nothing."""
    agent = tmp_path / "agents" / "editor.md"
    agent.parent.mkdir()
    agent.write_text(EDITOR, encoding="utf-8")
    task = {"goal": "Cut the intro.", "context": "ctx-cut", "agent": "video:editor"}
    fake.reply(
        FakeReply(
            calls=[
                FakeCall("delegate", json.dumps({"tasks": [{**task, "agent": "video:nobody"}]}))
            ],
            when=PARENT,
        ),
        FakeReply(calls=[FakeCall("delegate", json.dumps({"tasks": [task]}))], when=PARENT),
        FakeReply(text="Cutting.", when=PARENT),
        FakeReply(text="Cut 4 seconds.", when="ctx-cut"),
        FakeReply(text="The intro is 4 seconds shorter.", when=PARENT),
    )
    assistant, _ = make()

    async def scenario() -> OpenConversation:
        await assistant.set_plugins([], {}, {}, agents={"video": [agent]})
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Make the intro shorter.")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    parent = _bodies(fake, PARENT)[0]
    [delegate] = [tool for tool in parent["tools"] if tool["function"]["name"] == "delegate"]
    assert (
        "- video:editor: Cuts and orders the scenes of a video."
        in delegate["function"]["description"]
    )
    items = delegate["function"]["parameters"]["properties"]["tasks"]["items"]
    assert items["properties"]["agent"]["enum"] == ["video:editor"]
    refused = conversation.history[3].text
    assert "no predefined subagent 'video:nobody'" in refused

    child = _bodies(fake, "ctx-cut")[0]
    system = str(child["messages"][0]["content"])
    assert "You are the video editor. Cut what drags.\n\nYou are a focused subagent" in system
    names = {tool["function"]["name"] for tool in child["tools"]}
    assert names == {"read_file", "find_files", "run_command"}


# -- control ----------------------------------------------------------------------------------


async def _first_live(assistant: Assistant, parent_id: str) -> str:
    for _ in range(200):
        live = assistant.subagents.live(parent_id)
        if live:
            return live[0].subagent_id
        await asyncio.sleep(0.02)
    raise AssertionError("No subagent started.")


def test_a_steer_reaches_the_child_after_its_tool_calls(make: Make, fake: FakeModelServer) -> None:
    fake.reply(
        _spawn(("Write the weekly report.", "ctx-report")),
        FakeReply(text="Started.", when=PARENT),
        FakeReply(
            calls=[FakeCall("read_file", json.dumps({"path": "notes.txt"}))],
            when="ctx-report",
            wait_before=0.5,
        ),
        FakeReply(text="Report written, in English.", when="ctx-report"),
        FakeReply(text="Done.", when=PARENT),
    )
    assistant, _ = make()
    answers: list[str] = []

    async def scenario() -> OpenConversation:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Write my weekly report.")
        child = await _first_live(assistant, conversation.id)
        answers.append(
            await assistant.subagents.steer(conversation.id, child, "Write it in English.")
        )
        answers.append(await assistant.subagents.steer("someone-else", child, "Stop."))
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    assert json.loads(answers[0])["status"] == "queued"
    assert "No live subagent" in answers[1], "another conversation cannot steer it"
    second = _bodies(fake, "ctx-report")[1]["messages"]
    steer = [m for m in second if m["role"] == "user" and STEER_OPEN in str(m["content"])]
    assert len(steer) == 1
    assert "Write it in English." in steer[0]["content"]
    assert "Report written, in English." in _notices(conversation)[0]


def test_a_steer_that_lands_too_late_is_said_in_the_summary(
    make: Make, fake: FakeModelServer
) -> None:
    fake.reply(
        _spawn(("Check the backup folder.", "ctx-backup")),
        FakeReply(text="Started.", when=PARENT),
        FakeReply(text="The backup is fine.", when="ctx-backup", wait_before=0.5),
        FakeReply(text="Fine.", when=PARENT),
    )
    assistant, _ = make()

    async def scenario() -> OpenConversation:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Check my backups.")
        child = await _first_live(assistant, conversation.id)
        await assistant.subagents.steer(conversation.id, child, "Also check the dates.")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    notice = _notices(conversation)[0]
    assert "The backup is fine." in notice
    assert "[steer did not land — the subagent finished before it could be delivered: " in notice
    assert "Also check the dates." in notice


def test_a_stopped_child_returns_what_it_had(make: Make, fake: FakeModelServer) -> None:
    fake.reply(
        _spawn(("Read every log of the year.", "ctx-logs")),
        FakeReply(text="Started.", when=PARENT),
        FakeReply(
            text="January is clean so far.",
            calls=[FakeCall("read_file", json.dumps({"path": "jan.log"}))],
            when="ctx-logs",
        ),
        FakeReply(text="never sent", when="ctx-logs", wait_before=2),
        FakeReply(text="Stopped it.", when=PARENT),
    )
    assistant, _ = make()
    said: list[str] = []

    async def scenario() -> OpenConversation:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Read my logs.")
        child = await _first_live(assistant, conversation.id)
        for _ in range(200):
            if len(_bodies(fake, "ctx-logs")) == 2:
                break
            await asyncio.sleep(0.02)
        said.append(await assistant.subagents.stop(conversation.id, child))
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    assert json.loads(said[0])["status"] == "interrupt_requested"
    notice = _notices(conversation)[0]
    assert "(status=interrupted" in notice
    assert "Partial output:\nJanuary is clean so far." in notice


def test_stopping_the_parents_turn_stops_its_children(make: Make, fake: FakeModelServer) -> None:
    fake.reply(
        _spawn(("Translate the whole manual.", "ctx-manual")),
        FakeReply(text="still thinking", when=PARENT, wait_before=2),
        FakeReply(text="never sent", when="ctx-manual", wait_before=2),
        FakeReply(text="The translation was stopped.", when=PARENT),
    )
    assistant, _ = make()

    async def scenario() -> OpenConversation:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Translate the manual.")
        await _first_live(assistant, conversation.id)
        await asyncio.sleep(0.2)
        assert await conversation.interrupt()
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    assert "(status=interrupted" in _notices(conversation)[0]


def test_a_frozen_child_is_stopped_as_stalled(make: Make, fake: FakeModelServer) -> None:
    fake.reply(
        _spawn(("Download the archive.", "ctx-archive")),
        FakeReply(text="Started.", when=PARENT),
        FakeReply(text="never sent", when="ctx-archive", wait_before=2),
        FakeReply(text="It stalled.", when=PARENT),
    )
    assistant, _ = make()
    assistant.subagents.idle = 0.4
    assistant.subagents.look = 0.05

    async def scenario() -> OpenConversation:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("Download the archive.")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    notice = _notices(conversation)[0]
    assert "(status=stalled" in notice
    assert "Subagent stopped making progress — no activity for" in notice


# -- a routine waits --------------------------------------------------------------------------


def test_a_routines_call_waits_for_its_children_and_returns_them(
    make: Make, fake: FakeModelServer
) -> None:
    fake.reply(
        FakeReply(
            calls=[
                FakeCall(
                    "delegate",
                    json.dumps({"tasks": [{"goal": "Gather the news.", "context": "ctx-news"}]}),
                )
            ]
        ),
        FakeReply(text="Three headlines.", when="ctx-news"),
        FakeReply(text="Today: three headlines."),
    )
    assistant, _ = make()

    async def scenario() -> OpenConversation:
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id, role=ConversationRole.ROUTINE)
        conversation.send("Summarize the news.")
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    result = json.loads(conversation.history[3].text)
    assert result["results"][0]["summary"] == "Three headlines."
    assert "ran SYNCHRONOUSLY" in result["note"]
    assert _notices(conversation) == []
    assert conversation.last_ended is not None
    assert conversation.last_ended.text == "Today: three headlines."


# -- an earlier process ----------------------------------------------------------------------


def test_children_an_earlier_atlas_left_are_unknown_and_their_batch_is_delivered(
    make: Make, fake: FakeModelServer, home: Home
) -> None:
    fake.reply(FakeReply(text="I will check what the helper did.", when=PARENT))
    assistant, _ = make()
    parent = assistant.conversations.create()
    parent.history.append(UserMessage("Earlier work."))
    parent.save()
    child = assistant.conversations.create(source=SUBAGENT, parent_id=parent.id)
    store = SubagentStore(Database(home.database_file))
    now = utc_now()
    store.add(
        [
            Subagent("sa-old", "d-old", parent.id, child.id, 0, 2, "Rename the photos.", "",
                     "m", now - timedelta(minutes=5)),
            Subagent("sa-done", "d-old", parent.id, child.id, 1, 2, "List the photos.", "", "m",
                     now - timedelta(minutes=5), SubagentStatus.COMPLETED, now, "12 photos.",
                     model_calls=2, cost_usd=Decimal("0.001")),
            Subagent("sa-stale", "d-stale", parent.id, child.id, 0, 1, "Too old to deliver.", "",
                     "m", now - timedelta(hours=49)),
        ]
    )  # fmt: skip

    async def scenario() -> OpenConversation:
        assistant.start()
        conversation = assistant.conversation(parent.id)
        await _settle(assistant, conversation)
        await assistant.close()
        return conversation

    conversation = _run(scenario)

    notices = _notices(conversation)
    assert len(notices) == 1
    notice = notices[0]
    assert "Atlas restarted while this batch ran" in notice
    assert "TASK 1/2: Rename the photos.  (status=unknown" in notice
    assert "outcome unknown" in notice
    assert "12 photos." in notice
    assert "Too old to deliver." not in notice
    assert [r.status for r in store.delegation("d-old")] == [
        SubagentStatus.UNKNOWN,
        SubagentStatus.COMPLETED,
    ]
    assert all(r.delivered for r in store.delegation("d-old"))
    assert store.delegation("d-stale")[0].status is SubagentStatus.UNKNOWN


def test_a_child_running_when_atlas_closes_comes_back_with_the_next_one(
    make: Make, fake: FakeModelServer, home: Home, vault: Vault
) -> None:
    fake.reply(
        _spawn(("Sort the music library.", "ctx-music")),
        FakeReply(text="Sorting.", when=PARENT),
        FakeReply(text="never sent", when="ctx-music", wait_before=2),
        FakeReply(text="The sorting stopped when Atlas closed.", when=PARENT),
    )
    first, _ = make()

    async def closing() -> str:
        stored = first.conversations.create()
        conversation = first.conversation(stored.id)
        conversation.send("Sort my music.")
        await _first_live(first, conversation.id)
        await conversation.wait()
        await first.close()
        return conversation.id

    parent_id = _run(closing)
    store = SubagentStore(Database(home.database_file))
    [delegation] = [batch[0].delegation_id for batch in store.undelivered(utc_now() - timedelta(1))]
    [stopped] = store.delegation(delegation)
    assert (stopped.status, stopped.error) == (
        SubagentStatus.INTERRUPTED,
        "Atlas stopped while it ran.",
    )
    second = Assistant(home, first.settings, first.models, vault, listener=lambda c, e: None)

    async def reopening() -> OpenConversation:
        second.start()
        conversation = second.conversation(parent_id)
        await _settle(second, conversation)
        await second.close()
        return conversation

    conversation = _run(reopening)

    notice = _notices(conversation)[-1]
    assert "(status=interrupted" in notice
    assert "Atlas stopped while it ran." in notice
    assert store.delegation(delegation)[0].delivered
