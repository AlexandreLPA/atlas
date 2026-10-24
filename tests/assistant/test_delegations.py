"""The delegation record and the tools over it, for real (step 16c): instances on the workbench
run on tests/fake_claude.py through the Claude Code plugin, their turns kept in the real database;
`delegation_search` reads that record and the harness's own session, read back by the plugin from
a Claude Code folder of the test's; `delegate`'s list shows the workbench, and steering or stopping
the user's own instance asks the user every time. Atlas's model is the fake model server."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.agent import Tool
from atlas.assistant import Assistant, InstanceChanged, OpenConversation
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate, utc_now
from atlas.jobs import Answerer, Exchange, InstanceRecord, InstanceStore, TurnRecord
from atlas.models import CallLedger
from atlas.plugins import Answers, Permission, Question, Verdict
from atlas.secrets import Vault
from atlas.tools import delegation_search
from atlas.trust import ApprovalAnswer, ApprovalRequest, Choice, Outcome
from claude_code_support import claude_code
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect

PARENT = "Skills come first"


class Screen:
    """The workbench's cards: answers in order, a verdict for a permission, answers for
    questions."""

    def __init__(self, *answers: Verdict | Answers) -> None:
        self.answers = list(answers)

    async def ask(self, asker: InstanceChanged, questions: Sequence[Question]) -> Answers:
        answer = self.answers.pop(0)
        assert not isinstance(answer, Verdict)
        return answer

    async def approve(self, asker: InstanceChanged, permission: Permission) -> Verdict:
        answer = self.answers.pop(0)
        assert isinstance(answer, Verdict)
        return answer


class Approvals:
    """The user's approvals: what was asked, and yes or no."""

    def __init__(self, *yes: bool) -> None:
        self.asked: list[ApprovalRequest] = []
        self._yes = list(yes)

    async def __call__(self, request: ApprovalRequest) -> ApprovalAnswer:
        self.asked.append(request)
        choice = Choice.ONCE if self._yes.pop(0) else Choice.DENY
        return ApprovalAnswer(Outcome.ANSWERED, choice)


@pytest.fixture
def scripts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "scripts"
    folder.mkdir()
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(folder))
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(tmp_path / "claude-log.jsonl"))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "claude-auth.json"))
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", "session-{folder}")
    return folder


type Make = Callable[..., Assistant]


@pytest.fixture
def make(home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path) -> Make:
    migrate(Database(home.database_file), SCHEMAS)

    def make(desk: Screen | None = None, approvals: Approvals | None = None) -> Assistant:
        chosen = ModelSettings(provider="openrouter")
        models = connect(
            fake, "openrouter", settings=chosen, ledger=CallLedger(Database(home.database_file))
        )
        work = tmp_path / "work"
        work.mkdir(exist_ok=True)
        settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
        return Assistant(
            home,
            settings,
            models,
            vault,
            listener=lambda _conversation, _event: None,
            approver=lambda _conversation: approvals,
            desk=desk,
            executors={"claude-code": claude_code(sessions=tmp_path / "claude")},
        )

    return make


def _project(tmp_path: Path, scripts: Path, name: str, *steps: dict[str, Any]) -> Path:
    project = tmp_path / "work" / name
    project.mkdir(parents=True, exist_ok=True)
    (scripts / f"{name}.json").write_text(json.dumps(list(steps)), encoding="utf-8")
    return project


def _run[T](scenario: Callable[[], Awaitable[T]]) -> T:
    return asyncio.run(scenario())  # pyright: ignore[reportArgumentType]


async def _idle(assistant: Assistant, instance_id: str) -> None:
    for _ in range(400):
        if not assistant.workbench.get(instance_id).working:
            return
        await asyncio.sleep(0.02)
    raise AssertionError("The instance kept working.")


async def _settle(assistant: Assistant, conversation: OpenConversation) -> None:
    for _ in range(40):
        await conversation.wait()
        await assistant.subagents.settled()
        await asyncio.sleep(0.05)
        if not conversation.busy:
            return


def _tool(assistant: Assistant) -> Tool:
    store, read = assistant.instance_store, assistant._shared.read_session  # pyright: ignore[reportPrivateUsage]
    assert read is not None
    return delegation_search(store, read)


def _search(tool: Tool, **arguments: object) -> str:
    said = _run(lambda: tool.run(arguments))
    assert isinstance(said, str), "delegation_search answers in text"
    return said


def test_a_turn_is_recorded_with_who_answered_and_the_files_it_changed(
    make: Make, scripts: Path, tmp_path: Path
) -> None:
    questions = [{"question": "Which color?", "options": [{"label": "Blue"}]}]
    site = _project(
        tmp_path,
        scripts,
        "site",
        {"questions": questions},
        {"tool": "Write", "input": {"file_path": "index.html", "content": "<h1>Hi</h1>"}},
        {"tool": "Bash", "input": {"command": "rm -rf dist"}},
        {"result": "The header is blue."},
    )
    assistant = make(Screen({"Which color?": "Blue"}, Verdict(True), Verdict(False, "Keep dist.")))

    async def scenario() -> str:
        run = await assistant.workbench.start("claude-code", site)
        await assistant.workbench.prompt(run.id, "Make the header blue.")
        await _idle(assistant, run.id)
        await assistant.close()
        return run.id

    instance_id = _run(scenario)

    store = assistant.instance_store
    record = store.get(instance_id)
    assert record is not None
    assert (record.executor, record.owner, record.state, record.session_id) == (
        "claude-code",
        "user",
        "ended",
        "session-site",
    )
    [turn] = store.turns(instance_id)
    assert (turn.prompt, turn.text, turn.failed, turn.reason) == (
        "Make the header blue.",
        "The header is blue.",
        False,
        "completed",
    )
    assert turn.files == ("index.html",)
    assert turn.exchanges == (
        Exchange("question", "Which color?", '{"Which color?": "Blue"}', Answerer.USER),
        Exchange("permission", "Write: index.html", "allowed", Answerer.USER),
        Exchange("permission", "Bash: rm -rf dist", "denied: Keep dist.", Answerer.USER),
    )
    [hit] = store.search("blue", 5)
    assert hit.instance.id == instance_id
    assert "»blue«" in hit.snippet.lower()


def test_delegation_search_browses_discovers_reads_and_scrolls(
    make: Make, scripts: Path, tmp_path: Path
) -> None:
    site = _project(tmp_path, scripts, "site", {"say": "Done."}, {"result": "Fixed the menu."})
    api = _project(tmp_path, scripts, "api", {"result": "The API answers in 20 ms."})
    assistant = make(Screen())

    async def scenario() -> tuple[str, str]:
        workbench = assistant.workbench
        first = await workbench.start("claude-code", site)
        await workbench.prompt(first.id, "Fix the broken menu.")
        await _idle(assistant, first.id)
        second = await workbench.start("claude-code", api)
        await workbench.prompt(second.id, "Make the API faster.")
        await _idle(assistant, second.id)
        await assistant.close()
        return first.id, second.id

    menu, faster = _run(scenario)
    # The harness's own session, as Claude Code keeps it, for the read and the scroll.
    sessions = tmp_path / "claude" / "projects" / "C--work-site"
    sessions.mkdir(parents=True)
    lines = [
        {"type": "user", "message": {"content": "Fix the broken menu."}},
        *[{"type": "assistant", "message": {"content": [{"type": "text", "text": f"Step {n}."}]}}
          for n in range(1, 31)],
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Edit", "input": {"file_path": "menu.js"}}]}},
    ]  # fmt: skip
    # Each line the child of the one before, as Claude Code keeps a session (step 16f reads the
    # chain).
    chained = [
        {**line, "uuid": f"u{number}", "parentUuid": f"u{number - 1}" if number else None}
        for number, line in enumerate(lines)
    ]
    (sessions / "session-site.jsonl").write_text(
        "\n".join(json.dumps(line) for line in chained), encoding="utf-8"
    )
    tool = _tool(assistant)

    browsed = _search(tool)
    assert browsed.startswith("Showing 2 most recent delegations:")
    assert browsed.index(faster) < browsed.index(menu), "the newest first"
    assert "'Fix the broken menu.' → 'Fixed the menu.'" in browsed
    assert faster not in _search(tool, folder="site")
    assert _search(tool, executor="codex").startswith("There are no delegations yet")
    assert "is not a time bound" in _search(tool, after="yesterday")

    found = _search(tool, query="menu")
    assert found.startswith("1 delegation(s) found for menu.")
    assert f"delegation {menu}, the user's, ended" in found
    assert "Turn 1" in found
    assert "ended (completed): Fixed the menu." in found
    assert _search(tool, query="nothingmatches").startswith("No matching delegations found.")

    read = _search(tool, instance_id=menu)
    assert "Its session (31 entries, as Claude Code keeps it):" in read
    assert "#1 user: Fix the broken menu." in read
    assert "entries more; scroll with around=<number>." in read
    assert "Edit" not in read, "tool entries only when asked"
    with_tools = _search(tool, instance_id=menu, include_tools=True)
    assert "#32 tool Edit" in with_tools

    scrolled = _search(tool, instance_id=menu, around=15, window=2)
    entries = [line for line in scrolled.splitlines() if line.startswith("#") and line[1] != "#"]
    assert [line.split(":")[0] for line in entries] == [
        "#13 harness",
        "#14 harness",
        "#15 harness",
        "#16 harness",
        "#17 harness",
    ]
    assert "is not in" in _search(tool, instance_id=menu, around=900)

    expired = _search(tool, instance_id=faster)
    assert "Fixed" not in expired
    assert "Claude Code deleted this session" in expired
    assert "The API answers in 20 ms." in expired, "the record still answers"
    assert "There is no delegation" in _search(tool, instance_id="in-none")


def test_the_record_outlives_a_harness_that_is_gone(home: Home, tmp_path: Path) -> None:
    migrate(Database(home.database_file), SCHEMAS)
    store = InstanceStore(Database(home.database_file))
    now = utc_now()
    store.save(
        InstanceRecord(
            "in-old", "codex", "Codex", "user", "/work/app", "", "working", now, now, "s-1"
        )
    )
    store.save_turn(TurnRecord("in-old", 1, "Port it.", "/work/app", now - timedelta(hours=1)))

    async def missing(executor: str, session: str, folder: str) -> None:
        return None

    tool = delegation_search(store, missing)
    assert "Codex is not installed now" in _search(tool, instance_id="in-old")
    assert "still running" in _search(tool, instance_id="in-old")
    assert store.abandon_running(now) == 1
    record = store.get("in-old")
    assert record is not None
    assert record.state == "ended"
    [turn] = store.turns("in-old")
    assert (turn.failed, turn.reason) == (True, "unknown")


def test_delegate_lists_the_workbench_and_asks_the_user_before_touching_their_instance(
    make: Make, scripts: Path, tmp_path: Path, fake: FakeModelServer
) -> None:
    site = _project(tmp_path, scripts, "site", {"sleep": 30}, {"result": "never"})
    approvals = Approvals(False, True)
    assistant = make(Screen(), approvals)

    async def scenario() -> tuple[list[str], str]:
        run = await assistant.workbench.start("claude-code", site)
        await assistant.workbench.prompt(run.id, "Rebuild everything.")
        steer = {"action": "steer", "subagent_id": run.id, "message": "Skip the docs."}
        stop = {"action": "stop", "subagent_id": run.id}
        fake.reply(
            FakeReply(calls=[FakeCall("delegate", json.dumps({"action": "list"}))], when=PARENT),
            FakeReply(calls=[FakeCall("delegate", json.dumps(steer))], when=PARENT),
            FakeReply(calls=[FakeCall("delegate", json.dumps(stop))], when=PARENT),
            FakeReply(text="Stopped, as you asked.", when=PARENT),
        )
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send("What is on the workbench? Then stop it.")
        await _settle(assistant, conversation)
        results = [m.text for m in conversation.history if type(m).__name__ == "ToolResult"]
        await assistant.close()
        return results, run.id

    results, instance_id = _run(scenario)

    listed = json.loads(results[0])
    [entry] = listed["workbench"]
    assert (entry["instance_id"], entry["whose"], entry["state"]) == (
        instance_id,
        "the user's",
        "working",
    )
    assert "only when the user asks you to" in listed["workbench_note"]
    assert "BLOCKED" in results[1], "the user said no to the steer"
    assert json.loads(results[2])["status"] == "stopped"
    assert [r.tool for r in approvals.asked] == ["delegate", "delegate"]
    assert "Skip the docs." in approvals.asked[0].target
    assert not approvals.asked[0].allow_always
    assert assistant.workbench.runs() == []
