"""delegate: its arguments read and checked, each action reaching the conversation's
subagents, and its answers for what cannot be done. The subagents behind it are proved with
the real assistant in tests/assistant/test_subagents.py; here the delegator only records."""

import asyncio
import json
from collections.abc import Sequence
from typing import Any

import pytest

from atlas.jobs import MAX_TASKS, Task
from atlas.tools import delegate, executor_accounts


class Recorder:
    def __init__(self, executors: tuple[str, ...] = ()) -> None:
        self.installed = list(executors)
        self.handed: list[str | None] = []
        self.autos: list[bool] = []
        self.resumed: list[tuple[str, str, str | None, tuple[str, ...]]] = []
        self.routes: list[tuple[str, ...]] = []
        self.listed_accounts: list[str | None] = []
        self.logins: list[str] = []
        self.account_executors: list[str | None] = []
        self.spawned: list[tuple[Task, ...]] = []
        self.steered: list[tuple[str, str]] = []
        self.stopped: list[str] = []

    async def spawn(
        self,
        tasks: tuple[Task, ...],
        executor: str | None = None,
        *,
        auto: bool = False,
        route: Sequence[str] = (),
    ) -> str:
        self.spawned.append(tasks)
        self.handed.append(executor)
        self.autos.append(auto)
        self.routes.append(tuple(route))
        return "dispatched"

    def executors(self) -> list[str]:
        return self.installed

    async def resume(
        self,
        subagent_id: str,
        message: str,
        executor: str | None = None,
        route: Sequence[str] = (),
    ) -> str:
        self.resumed.append((subagent_id, message, executor, tuple(route)))
        return "resumed"

    async def accounts(self, executor: str | None = None) -> str:
        self.listed_accounts.append(executor)
        return "accounts"

    async def account(self, email: str, executor: str | None = None) -> str:
        self.logins.append(email)
        self.account_executors.append(executor)
        return "switched"

    def list(self) -> str:
        return "listed"

    async def steer(self, subagent_id: str, message: str) -> str:
        self.steered.append((subagent_id, message))
        return "queued"

    async def stop(self, subagent_id: str) -> str:
        self.stopped.append(subagent_id)
        return "stopped"


def _call(recorder: Recorder, **arguments: Any) -> str:
    tool = delegate(recorder)
    assert tool is not None

    async def call() -> object:
        return await tool.run(arguments)

    result = asyncio.run(call())
    assert isinstance(result, str)
    return result


def test_there_is_no_tool_where_no_subagent_can_be_made() -> None:
    assert delegate(None) is None


def test_the_description_names_atlass_tools_and_no_others() -> None:
    tool = delegate(Recorder())
    assert tool is not None
    assert "END YOUR TURN" in tool.description
    assert "SELF-REPORTS" in tool.description
    assert "Children have no delegate, ask_user, memory or cronjob_manage." in tool.description
    for missing in ("execute_code", "delegate_task", "clarify", "config.yaml", "transcript"):
        assert missing not in tool.description
    assert set(tool.parameters["properties"]) == {"tasks", "action", "subagent_id", "message"}


def test_a_spawn_hands_the_tasks_over_with_their_context() -> None:
    recorder = Recorder()

    said = _call(
        recorder,
        tasks=[
            {"goal": "Summarize the report.", "context": "It is in C:/r.pdf"},
            {"goal": "x" * 12},
        ],
    )

    assert said == "dispatched"
    assert recorder.spawned == [
        (Task("Summarize the report.", "It is in C:/r.pdf"), Task("x" * 12))
    ]


def test_tasks_written_as_a_json_string_are_read() -> None:
    recorder = Recorder()

    _call(recorder, tasks=json.dumps([{"goal": "Read the logs."}]))

    assert recorder.spawned == [(Task("Read the logs."),)]


@pytest.mark.parametrize(
    ("tasks", "said"),
    [
        (None, "No tasks provided."),
        ([], "No tasks provided."),
        ("[{", "does not parse as JSON"),
        ([{"context": "x"}], "Task 0 is missing a 'goal'."),
        (["read it"], "Task 0 must be an object, got str."),
        ([{"goal": f"Task number {i}"} for i in range(MAX_TASKS + 1)], "Too many tasks: 11"),
        ([{"goal": "todo"}, {"goal": "Write the summary."}], "placeholder goal ('todo')"),
        ([{"goal": "Fix <file name> now"}, {"goal": "Write the summary."}], "template marker"),
        ([{"goal": "Fix it"}, {"goal": "Write the summary."}], "goal is too short ('Fix it')"),
    ],
)
def test_a_spawn_that_cannot_be_made_is_refused_with_its_reason(tasks: object, said: str) -> None:
    recorder = Recorder()

    answer = _call(recorder, tasks=tasks)

    assert answer.startswith("[Atlas: ")
    assert said in answer
    assert recorder.spawned == []


def test_a_single_short_goal_is_fine() -> None:
    recorder = Recorder()

    _call(recorder, tasks=[{"goal": "Fix it"}])

    assert recorder.spawned == [(Task("Fix it"),)]


def test_the_control_actions_reach_the_conversations_subagents() -> None:
    recorder = Recorder()

    assert _call(recorder, action="list") == "listed"
    assert _call(recorder, action="steer", subagent_id=" sa-1 ", message=" in English ") == "queued"
    assert _call(recorder, action="STOP", subagent_id="sa-2", tasks=[]) == "stopped"

    assert recorder.steered == [("sa-1", "in English")]
    assert recorder.stopped == ["sa-2"]
    assert recorder.spawned == []


@pytest.mark.parametrize(
    ("arguments", "said"),
    [
        ({"action": "steer", "message": "go"}, "action='steer' requires subagent_id"),
        ({"action": "stop"}, "action='stop' requires subagent_id"),
        ({"action": "steer", "subagent_id": "sa-1"}, "requires a non-empty 'message'"),
        ({"action": "pause"}, "Unknown action 'pause'"),
    ],
)
def test_a_control_action_missing_what_it_needs_is_refused(
    arguments: dict[str, Any], said: str
) -> None:
    recorder = Recorder()

    answer = _call(recorder, **arguments)

    assert answer.startswith("[Atlas: ")
    assert said in answer
    assert recorder.steered == recorder.stopped == []


def test_without_an_outside_executor_the_tool_says_nothing_of_them() -> None:
    tool = delegate(Recorder())
    assert tool is not None
    assert "executor" not in tool.parameters["properties"]
    assert "OUTSIDE EXECUTORS" not in tool.description


def test_an_installed_executor_takes_the_tasks_with_their_folders() -> None:
    recorder = Recorder(("claude-code",))
    tool = delegate(recorder)
    assert tool is not None
    assert tool.parameters["properties"]["executor"]["enum"] == ["claude-code"]
    assert "folder" in tool.parameters["properties"]["tasks"]["items"]["properties"]
    assert "OUTSIDE EXECUTORS" in tool.description

    said = _call(recorder, executor="claude-code", tasks=[{"goal": "Fix it.", "folder": "C:/p"}])
    wrong = _call(recorder, executor="codex", tasks=[{"goal": "Fix it."}])

    assert said == "dispatched"
    assert recorder.spawned == [(Task("Fix it.", "", "C:/p"),)]
    assert recorder.handed == ["claude-code"]
    assert wrong == "[Atlas: There is no executor 'codex'. Installed: claude-code.]"


def test_an_executors_delegation_is_auto_unless_manual_is_asked_for_by_name() -> None:
    recorder = Recorder(("claude-code",))
    tool = delegate(recorder)
    assert tool is not None
    approval = tool.parameters["properties"]["approval"]
    assert approval["enum"] == ["manual", "auto"]
    assert "Default 'auto'" in approval["description"]
    task = [{"goal": "Fix it.", "folder": "C:/p"}]

    auto = _call(recorder, executor="claude-code", tasks=task)
    named = _call(recorder, executor="claude-code", tasks=task, approval=" Auto ")
    manual = _call(recorder, executor="claude-code", tasks=task, approval="manual")
    unknown = _call(recorder, executor="claude-code", tasks=task, approval="yolo")
    subagents = _call(recorder, tasks=[{"goal": "Fix it."}], approval="auto")

    assert [auto, named, manual, subagents] == ["dispatched"] * 4
    assert recorder.autos == [True, True, False, False]
    assert unknown == "[Atlas: Unknown approval 'yolo'. Use 'auto' (default) or 'manual'.]"
    assert len(recorder.spawned) == 4


def test_without_an_executor_there_is_no_approval_to_choose() -> None:
    tool = delegate(Recorder())
    assert tool is not None
    assert "approval" not in tool.parameters["properties"]


def test_with_claude_code_the_tool_switches_accounts_and_resumes_only_when_asked_right() -> None:
    recorder = Recorder(("claude-code",))
    tool = delegate(recorder)
    assert tool is not None
    assert "resume" in tool.parameters["properties"]["action"]["enum"]
    assert "account" in tool.parameters["properties"]

    assert _call(recorder, action="account", account=" b@example.com ") == "switched"
    assert _call(recorder, action="account") == "switched", "whichever the user signs in to"
    assert _call(recorder, action="resume", subagent_id="sa-1") == "resumed"
    assert "takes the account's email" in _call(recorder, action="account", account="nobody")
    assert "requires the subagent_id" in _call(recorder, action="resume")
    assert recorder.logins == ["b@example.com", ""]
    assert recorder.resumed == [("sa-1", "", None, ())]


def test_with_two_executors_the_tool_names_both_and_switches_the_one_named() -> None:
    recorder = Recorder(("claude-code", "codex"))
    tool = delegate(recorder)
    assert tool is not None
    assert "executor='claude-code' or 'codex'" in tool.description
    assert "Claude Code" not in tool.description

    assert _call(recorder, action="account", account="b@example.com", executor="codex") == (
        "switched"
    )
    assert _call(recorder, action="account", account="b@example.com", executor="pi") == "switched"
    assert _call(recorder, action="account", account="b@example.com") == "switched"
    # An executor not installed is not passed on: the one that switches accounts is.
    assert recorder.account_executors == ["codex", None, None]


def test_without_an_executor_there_is_no_account_or_resume() -> None:
    recorder = Recorder()
    assert "Unknown action 'account'" in _call(recorder, action="account", account="b@x.com")


def test_an_account_and_a_route_are_named_by_target_names_and_checked() -> None:
    """Asked for by the user on 21/10/2026: Atlas picks the account a task runs on, and where it
    goes on at a plan limit, across executors."""
    installed = ("claude-code", "claude-code@main", "claude-code@work", "codex")
    recorder = Recorder(installed)
    tool = delegate(recorder)
    assert tool is not None
    properties = tool.parameters["properties"]
    assert properties["executor"]["enum"] == list(installed)
    assert "executor='claude-code' or 'codex'" in tool.description, "each executor named once"
    assert "'name@account'" in tool.description
    task = [{"goal": "Fix it.", "folder": "C:/p"}]

    on_work = _call(recorder, executor="claude-code@work", tasks=task, route=["codex"])
    # An account added since the tool was made is the delegator's to check.
    on_new = _call(recorder, executor="claude-code@new", tasks=task, route='["claude-code@new"]')
    wrong = _call(recorder, executor="claude-code", tasks=task, route=["pi"])
    loose = _call(recorder, tasks=task, route=["codex"])
    moved = _call(recorder, action="resume", subagent_id="sa-1", executor="codex", route=["codex"])
    nowhere = _call(recorder, action="resume", subagent_id="sa-1", executor="pi")

    assert [on_work, on_new, moved] == ["dispatched", "dispatched", "resumed"]
    assert recorder.handed == ["claude-code@work", "claude-code@new"]
    assert recorder.routes == [("codex",), ("claude-code@new",)]
    assert wrong.startswith("[Atlas: The route names 'pi'")
    assert "give `executor`" in loose
    assert recorder.resumed == [("sa-1", "", "codex", ("codex",))]
    assert nowhere.startswith("[Atlas: There is no executor 'pi'")


def test_the_accounts_tool_is_deferred_and_only_where_an_executor_is() -> None:
    assert executor_accounts(None) is None
    assert executor_accounts(Recorder()) is None
    recorder = Recorder(("claude-code",))
    tool = executor_accounts(recorder)
    assert tool is not None
    assert (tool.deferred, tool.read_only) == (True, True)

    async def call() -> object:
        return await tool.run({"executor": "claude-code"})

    assert asyncio.run(call()) == "accounts"
    assert recorder.listed_accounts == ["claude-code"]
