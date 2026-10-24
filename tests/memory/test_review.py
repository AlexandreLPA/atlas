"""The instructions that carry the memory, and the background review's memory half, against the
fake model server.

The model side is real (the SDK, the wire, the call ledger); the memory is real files, and the
memory tool is the real one. A compaction is the loop's own, on a small window.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from atlas.agent import (
    SUMMARY_MARK,
    AgentLoop,
    Tool,
    Toolset,
    TurnLimit,
    TurnOutcome,
    model_switch_note,
)
from atlas.home import Database, Home, migrate
from atlas.memory import (
    MEMORY_GUIDANCE,
    MEMORY_GUIDANCE_WITHOUT_SKILLS,
    MEMORY_REVIEW_PROMPT,
    REVIEW_INPUT_UNKNOWN,
    Action,
    BackgroundReview,
    Instructions,
    MemoryFiles,
    Operation,
    Proposals,
    ProposalStore,
    ReviewEnded,
    Target,
    review_due,
    review_limit,
)
from atlas.memory import SCHEMA as MEMORY_SCHEMA
from atlas.models import (
    SCHEMA,
    AssistantMessage,
    CallLedger,
    Message,
    SystemMessage,
    ToolCall,
    UserMessage,
)
from atlas.tools import memory
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect, Run

CHAT = "/v1/chat/completions"


@pytest.fixture
def files(tmp_path: Path) -> MemoryFiles:
    return MemoryFiles(tmp_path / "memory")


def _save(files: MemoryFiles, target: Target, entry: str) -> None:
    assert files.change(target, [Operation(Action.ADD, entry)]).entries is not None


def _small_model(home: Home, context: int = 12_000) -> None:
    home.models_file.write_text(
        '[[models]]\nprovider = "vllm"\nid = "small"\n'
        f"limit = {{ context = {context}, output = 2000 }}\ntool_call = true\n\n",
        encoding="utf-8",
    )


# -- the instructions ----------------------------------------------------------------------------


def test_the_instructions_carry_who_the_agent_is_the_guidance_and_the_memory(
    files: MemoryFiles,
) -> None:
    instructions = Instructions(files, "You are Atlas.")
    assert "USER PROFILE" not in instructions()

    _save(files, Target.USER, "Name: Ana")
    text = instructions()

    assert text.startswith("You are Atlas.\n\n")
    assert MEMORY_GUIDANCE in text
    assert "Skills come first" in text
    assert "record it with skill_manage" in text
    assert "conversation_search" in text
    assert text.endswith("Name: Ana")

    without = Instructions(files, "You are Atlas.", skills=False)()
    assert MEMORY_GUIDANCE_WITHOUT_SKILLS in without
    assert "skill_manage" not in without


def test_a_new_conversation_starts_with_the_instructions_and_keeps_them_while_memory_changes(
    connect: Connect, run: Run, files: MemoryFiles
) -> None:
    _save(files, Target.USER, "Name: Ana")
    history: list[Message] = []
    with FakeModelServer() as fake:
        fake.reply(FakeReply(text="Hi Ana."), FakeReply(text="Still Ana."))
        models = connect(fake, "openrouter")

        async def scenario() -> None:
            loop = AgentLoop(
                models.client("openrouter", "openai/gpt-4o-mini"),
                instructions=Instructions(files, "You are Atlas."),
            )
            await loop.reply(history, "hello")
            _save(files, Target.USER, "Likes tea")  # written mid-conversation
            await loop.reply(history, "again")

        run(models, scenario)
        first, second = fake.bodies(CHAT)

    assert isinstance(history[0], SystemMessage)
    assert "Name: Ana" in history[0].text
    assert first["messages"][0] == second["messages"][0]  # the cache holds
    assert "Likes tea" not in second["messages"][0]["content"]


def test_a_compaction_reads_the_instructions_again(
    connect: Connect, run: Run, home: Home, files: MemoryFiles
) -> None:
    _small_model(home)
    instructions = Instructions(files, "You are Atlas.")
    history: list[Message] = [SystemMessage(instructions())]
    for n in range(12):
        history += [
            UserMessage(f"question {n} " + "x" * 2_000),
            AssistantMessage(f"answer {n} " + "y" * 2_000),
        ]
    _save(files, Target.MEMORY, "Server runs Debian 12")
    with FakeModelServer(key=None) as local:
        local.reply(FakeReply(text="## Goal\nAnswer."), FakeReply(text="Done."))
        models = connect(local, "vllm")

        async def scenario() -> None:
            loop = AgentLoop(models.client("vllm", "small"), instructions=instructions)
            await loop.reply(history, "and now?")

        run(models, scenario)

    assert isinstance(history[1], AssistantMessage)
    assert history[1].text.startswith(SUMMARY_MARK)
    assert isinstance(history[0], SystemMessage)
    assert "Server runs Debian 12" in history[0].text


def test_a_conversation_that_lasts_forever_reads_its_instructions_again_once_they_change(
    connect: Connect, run: Run, files: MemoryFiles
) -> None:
    # A bot's fixed conversation never starts anew: without this, what another agent saved in the
    # shared profile would reach it only at its next compaction.
    history: list[Message] = []
    with FakeModelServer() as fake:
        fake.reply(FakeReply(text="one"), FakeReply(text="two"), FakeReply(text="three"))
        models = connect(fake, "openrouter")

        async def scenario() -> None:
            loop = AgentLoop(
                models.client("openrouter", "openai/gpt-4o-mini"),
                instructions=Instructions(files, "You are a bot."),
                refresh_instructions=True,
            )
            await loop.reply(history, "first")
            started = history[0]
            await loop.reply(history, "second")
            # Nothing changed: the same stored message, so the provider's cache holds.
            assert history[0] is started
            _save(files, Target.USER, "Works at the new job")
            await loop.reply(history, "third")

        run(models, scenario)
        sent = [body["messages"][0]["content"] for body in fake.bodies(CHAT)]

    assert "new job" not in sent[0]
    assert sent[1] == sent[0]
    assert sent[2].endswith("Works at the new job")
    assert isinstance(history[0], SystemMessage)
    assert history[0].text == sent[2]
    assert [m.text for m in history if isinstance(m, UserMessage)] == ["first", "second", "third"]


def test_a_caller_that_brings_its_own_system_message_keeps_it(
    connect: Connect, run: Run, files: MemoryFiles
) -> None:
    history: list[Message] = [SystemMessage("Custom.")]
    with FakeModelServer() as fake:
        fake.reply(FakeReply(text="ok"))
        models = connect(fake, "openrouter")

        async def scenario() -> None:
            loop = AgentLoop(
                models.client("openrouter", "openai/gpt-4o-mini"),
                instructions=Instructions(files, "You are Atlas."),
            )
            await loop.reply(history, "hi")

        run(models, scenario)

    assert history[0] == SystemMessage("Custom.")


# -- when a review is due ------------------------------------------------------------------------


def _users(count: int) -> list[Message]:
    return [UserMessage(f"m{n}") for n in range(count)]


def test_a_review_is_due_every_ten_user_messages_without_a_memory_write() -> None:
    saved = AssistantMessage(tool_calls=(ToolCall("c", "memory", "{}"),))

    assert not review_due([])
    assert not review_due(_users(9))
    assert review_due(_users(10))
    assert not review_due(_users(11))
    assert review_due(_users(20))
    assert not review_due([*_users(10), saved, *_users(3)])
    assert review_due([*_users(4), saved, *_users(10)])
    # A model switch note is not something the user said (the review counts user turns, not notes).
    note = model_switch_note("gemini-3.8-flash", "Google Gemini")
    assert not review_due([*_users(9), note])
    assert review_due([*_users(5), note, *_users(5)])


# -- the review ----------------------------------------------------------------------------------


async def _never(arguments: dict[str, Any]) -> str:
    raise AssertionError("the review ran a tool other than memory")


def _proposals(home: Home, files: MemoryFiles) -> Proposals:
    database = Database(home.database_file)
    migrate(database, (MEMORY_SCHEMA,))
    return Proposals(files, ProposalStore(database))


def _tool_results(body: dict[str, Any]) -> list[str]:
    return [m["content"] for m in body["messages"] if m["role"] == "tool"]


def test_what_the_review_would_take_out_becomes_a_proposal(
    connect: Connect, run: Run, home: Home, files: MemoryFiles
) -> None:
    proposals = _proposals(home, files)
    _save(files, Target.USER, "Name: Ana")
    history: list[Message] = [UserMessage("I am Bia now."), AssistantMessage("Hi Bia.")]
    replace = {"target": "user", "action": "replace", "old_text": "Ana", "content": "Name: Bia"}
    batch = {
        "target": "user",
        "operations": [
            {"action": "add", "content": "Likes tea"},
            {"action": "remove", "old_text": "Ana"},
        ],
    }
    with FakeModelServer() as fake:
        fake.reply(
            FakeReply(calls=[FakeCall("memory", json.dumps(replace))]),
            FakeReply(calls=[FakeCall("memory", json.dumps(batch))]),
            FakeReply(text="Proposed."),
        )
        models = connect(fake, "openrouter")

        async def scenario() -> ReviewEnded:
            review = BackgroundReview(
                models.client("openrouter", "openai/gpt-4o-mini"),
                Toolset([memory(files, proposals)]),
            )
            return await review.run(history, scope="conv-1")

        ended = run(models, scenario)
        *_, last = fake.bodies(CHAT)

    assert files.entries(Target.USER) == ["Name: Ana"]  # nothing applied, the batch's add included
    made = proposals.all()
    assert ended.proposals == tuple(made)  # what its summary will say
    assert ended.saved == frozenset()
    assert [[op.action for op in p.operations] for p in made] == [
        [Action.REPLACE],
        [Action.ADD, Action.REMOVE],
    ]
    assert [p.operations[-1].entry for p in made] == ["Name: Ana", "Name: Ana"]
    assert {p.conversation_id for p in made} == {"conv-1"}
    results = _tool_results(last)
    assert len(results) == 2
    assert all(text.startswith("Proposed, not applied") for text in results)
    assert all(p.id in text for p, text in zip(made, results, strict=True))


def test_with_nowhere_to_keep_a_proposal_the_review_is_refused(
    connect: Connect, run: Run, files: MemoryFiles
) -> None:
    """It fails closed when it cannot stage: a plain refusal, and 'add' still works."""
    _save(files, Target.USER, "Name: Ana")
    remove = {"target": "user", "action": "remove", "old_text": "Ana"}
    add = {"target": "user", "action": "add", "content": "Likes tea"}
    with FakeModelServer() as fake:
        fake.reply(
            FakeReply(calls=[FakeCall("memory", json.dumps(remove))]),
            FakeReply(calls=[FakeCall("memory", json.dumps(add))]),
            FakeReply(text="Done."),
        )
        models = connect(fake, "openrouter")

        async def scenario() -> None:
            review = BackgroundReview(
                models.client("openrouter", "openai/gpt-4o-mini"), Toolset([memory(files)])
            )
            await review.run([UserMessage("I like tea.")])

        run(models, scenario)
        *_, last = fake.bodies(CHAT)

    refused, added = _tool_results(last)
    assert "may not replace or remove memory entries" in refused
    assert added.startswith("Entry added.")
    assert files.entries(Target.USER) == ["Name: Ana", "Likes tea"]


def test_a_full_memory_is_consolidated_by_a_proposal_end_to_end(
    connect: Connect, run: Run, home: Home, tmp_path: Path
) -> None:
    """The near-limit case: the add is refused for the limit, the consolidation the model
    sends next is proposed, and the memory stays as it was."""
    files = MemoryFiles(tmp_path / "small", {Target.USER: 100, Target.MEMORY: 100})
    proposals = _proposals(home, files)
    _save(files, Target.MEMORY, "x" * 60)
    add = {"target": "memory", "action": "add", "content": "y" * 60}
    consolidate = {
        "target": "memory",
        "operations": [
            {"action": "remove", "old_text": "x" * 60},
            {"action": "add", "content": "y" * 60},
        ],
    }
    with FakeModelServer() as fake:
        fake.reply(
            FakeReply(calls=[FakeCall("memory", json.dumps(add))]),
            FakeReply(calls=[FakeCall("memory", json.dumps(consolidate))]),
            FakeReply(text="Proposed."),
        )
        models = connect(fake, "openrouter")

        async def scenario() -> None:
            review = BackgroundReview(
                models.client("openrouter", "openai/gpt-4o-mini"),
                Toolset([memory(files, proposals)]),
            )
            await review.run([UserMessage("Remember the y's.")], scope="conv-1")

        run(models, scenario)
        *_, last = fake.bodies(CHAT)

    over, proposed = _tool_results(last)
    assert over.startswith("[Atlas: Memory would be at 123/100 chars")
    assert proposed.startswith("Proposed, not applied")
    assert files.entries(Target.MEMORY) == ["x" * 60]
    [proposal] = proposals.all()
    assert proposal.operations == (
        Operation(Action.REMOVE, old_text="x" * 60, entry="x" * 60),
        Operation(Action.ADD, "y" * 60),
    )


def test_a_review_for_skills_only_is_not_given_the_memory(
    connect: Connect, run: Run, files: MemoryFiles
) -> None:
    add = {"target": "user", "action": "add", "content": "Name: Ana"}
    with FakeModelServer() as fake:
        fake.reply(
            FakeReply(calls=[FakeCall("memory", json.dumps(add))]),
            FakeReply(text="Nothing to save."),
        )
        models = connect(fake, "openrouter")

        async def scenario() -> None:
            review = BackgroundReview(
                models.client("openrouter", "openai/gpt-4o-mini"), Toolset([memory(files)])
            )
            await review.run([UserMessage("I am Ana.")], memory=False, skills=True)

        run(models, scenario)
        first, last = fake.bodies(CHAT)

    assert files.entries(Target.USER) == []
    assert "bring the skill library up to date" in first["messages"][-1]["content"]
    assert "You can only call skill management tools" in first["messages"][-1]["content"]
    assert "denied non-whitelisted tool: memory" in last["messages"][-1]["content"]


def test_the_review_saves_with_the_memory_tool_only_and_leaves_the_conversation_alone(
    connect: Connect, run: Run, home: Home, files: MemoryFiles
) -> None:
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    ledger = CallLedger(database)
    other = Tool("run_command", "Runs a command.", {"type": "object", "properties": {}}, _never)
    tools = Toolset([memory(files), other])
    history: list[Message] = [
        SystemMessage("You are Atlas."),
        UserMessage("Call me Ana, and I like short answers."),
        AssistantMessage("Sure, Ana."),
    ]
    before = list(history)
    save = {"target": "user", "action": "add", "content": "Name: Ana; prefers short answers"}
    with FakeModelServer() as fake:
        fake.reply(
            FakeReply(calls=[FakeCall("run_command", "{}")]),
            FakeReply(calls=[FakeCall("memory", json.dumps(save))]),
            FakeReply(text="Saved."),
        )
        models = connect(fake, "openrouter", ledger=ledger)

        async def scenario() -> ReviewEnded:
            review = BackgroundReview(models.client("openrouter", "openai/gpt-4o-mini"), tools)
            return await review.run(history, scope="conv-1")

        ended = run(models, scenario)
        first, *_ = fake.bodies(CHAT)

    assert ended.turn.outcome is TurnOutcome.ANSWERED
    assert ended.saved == frozenset({Target.USER})
    assert files.entries(Target.USER) == ["Name: Ana; prefers short answers"]
    assert history == before
    assert first["messages"][-1]["content"].startswith(MEMORY_REVIEW_PROMPT)
    assert {tool["function"]["name"] for tool in first["tools"]} == {"memory", "run_command"}
    assert {(call.purpose, call.scope) for call in ledger.recent()} == {
        ("background_review", "conv-1")
    }


def test_a_turn_with_a_budget_stops_when_it_runs_out(
    connect: Connect, run: Run, files: MemoryFiles
) -> None:
    looping = FakeReply(calls=[FakeCall("memory", json.dumps({"target": "memory"}))])
    with FakeModelServer() as fake:
        fake.reply(looping, looping, looping)
        models = connect(fake, "openrouter")

        async def scenario() -> tuple[TurnOutcome, str, int]:
            loop = AgentLoop(
                models.client("openrouter", "openai/gpt-4o-mini"),
                [memory(files)],
                limit=TurnLimit(model_calls=2, input_tokens=10**9),
            )
            ended = await loop.reply([], "go")
            return ended.outcome, ended.text, ended.model_calls

        outcome, text, calls = run(models, scenario)

    assert outcome is TurnOutcome.LIMITED
    assert calls == 2
    assert "budget" in text


def test_the_reviews_budget_is_three_quarters_of_the_window_capped(
    connect: Connect, run: Run, home: Home
) -> None:
    _small_model(home, context=12_000)
    with FakeModelServer(key=None) as local:
        models = connect(local, "vllm")

        async def scenario() -> tuple[int, int]:
            small = review_limit(models.client("vllm", "small"))
            unknown = review_limit(models.client("vllm", "nobody-knows-this-model"))
            return small.input_tokens, unknown.input_tokens

        small, unknown = run(models, scenario)

    assert small == 7_500  # three quarters of 10,000: the window less the output
    assert unknown == REVIEW_INPUT_UNKNOWN
