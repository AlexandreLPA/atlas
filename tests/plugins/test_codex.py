"""The Codex plugin as Atlas drives it, through the SDK's executor (step 16o): the real pipes and
the real `app-server` JSON-RPC, against tests/fake_codex.py, which speaks it as `codex` does with a
script in place of the model. The host here plays Atlas: it keeps what it hears and answers what
is asked."""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any

import pytest

from atlas.plugins import (
    Answers,
    Called,
    Compacted,
    Connector,
    ExecutorError,
    ExecutorEvent,
    ExecutorFile,
    ExecutorInstance,
    ExecutorSpec,
    Finished,
    LimitReached,
    ModelChoice,
    Permission,
    Question,
    Returned,
    Said,
    Started,
    Thought,
    UsageChanged,
    UsageWindow,
    Verdict,
)
from codex_support import FAKE, FOLDER, codex, plugin_module


@pytest.fixture
def script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "Script":
    return Script(tmp_path, monkeypatch)


class Script:
    def __init__(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.file = folder / "script.json"
        self.log = folder / "log.jsonl"
        self.home = folder / ".codex"
        self.home.mkdir()
        self.project = folder / "project"
        self.project.mkdir()
        monkeypatch.setenv("FAKE_CODEX_SCRIPT", str(self.file))
        monkeypatch.setenv("FAKE_CODEX_LOG", str(self.log))
        monkeypatch.setenv("CODEX_HOME", str(self.home))
        self.write()

    def write(self, *steps: dict[str, Any]) -> None:
        self.file.write_text(json.dumps(list(steps)), encoding="utf-8")

    def turns(self, *turns: list[dict[str, Any]]) -> None:
        self.file.write_text(json.dumps({"turns": list(turns)}), encoding="utf-8")

    def received(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def sent(self, method: str) -> list[dict[str, Any]]:
        """The params of each request Atlas sent with `method`."""
        return [line["params"] for line in self.received() if line.get("method") == method]

    def answers(self) -> list[dict[str, Any]]:
        """What Atlas answered to Codex's own requests."""
        return [
            line["result"]
            for line in self.received()
            if "result" in line and isinstance(line.get("id"), int) and line["id"] > 1000
        ]


class Host:
    """Atlas, as the instance sees it: what it heard, what it was asked, and its answers."""

    def __init__(
        self,
        approve: Callable[[Permission], Awaitable[Verdict]] | None = None,
        ask: Callable[[Sequence[Question]], Awaitable[Answers]] | None = None,
    ) -> None:
        self.events: list[ExecutorEvent] = []
        self.permissions: list[Permission] = []
        self.questions: list[Sequence[Question]] = []
        self.failures: list[str] = []
        self._approve = approve
        self._ask = ask
        self.during: Callable[[ExecutorEvent], Coroutine[Any, Any, None]] | None = None

    def event(self, event: ExecutorEvent) -> None:
        self.events.append(event)
        if self.during is not None:
            asyncio.get_running_loop().create_task(self.during(event))

    async def ask(self, questions: Sequence[Question]) -> Answers:
        self.questions.append(questions)
        return None if self._ask is None else await self._ask(questions)

    async def approve(self, permission: Permission) -> Verdict:
        self.permissions.append(permission)
        return Verdict(True) if self._approve is None else await self._approve(permission)

    def failed(self, reason: str) -> None:
        self.failures.append(reason)


def _run(script: Script, host: Host, *prompts: str, spec: ExecutorSpec | None = None) -> Host:
    async def scenario() -> None:
        instance = await codex().start(spec or ExecutorSpec(script.project), host)
        try:
            for prompt in prompts:
                await instance.prompt(prompt)
        finally:
            await instance.stop()

    asyncio.run(scenario())
    return host


def _finished(host: Host) -> Finished:
    found = [event for event in host.events if isinstance(event, Finished)]
    assert found, host.events
    return found[-1]


def test_a_task_runs_its_commands_with_permission_and_ends_with_its_last_words(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-atlas")
    monkeypatch.setenv("CODEX_API_KEY", "sk-from-atlas")
    script.write(
        {"think": "The test imports the wrong module."},
        {"say": "I will run the tests."},
        {"command": "pytest -x"},
        {"say": "Fixed: the import was wrong."},
    )

    host = _run(script, Host(), "Fix the failing test.")

    started = host.events[0]
    assert isinstance(started, Started)
    assert started.model == "gpt-6-luna"
    thread = started.session
    kinds = [type(event).__name__ for event in host.events]
    assert kinds == ["Started", "Thought", "Said", "Called", "Returned", "Said", "Finished"]
    assert host.events[1] == Thought("The test imports the wrong module.")
    called = host.events[3]
    assert isinstance(called, Called)
    assert (called.tool, json.loads(called.arguments)["command"]) == ("shell", "pytest -x")
    returned = host.events[4]
    assert isinstance(returned, Returned)
    assert (returned.call, returned.text, returned.failed) == (
        called.call,
        "done\n[exit code 0]",
        False,
    )
    finished = _finished(host)
    assert (finished.text, finished.failed, finished.reason, finished.session) == (
        "Fixed: the import was wrong.",
        False,
        "completed",
        thread,
    )
    assert [(p.tool, p.summary, p.title, p.description) for p in host.permissions] == [
        ("shell", "pytest -x", "Run a command", "needs the network")
    ]
    first = script.received()[0]
    assert first["argv"] == ["app-server"]
    assert first["credentials"] == {}, "a key in Atlas never bills in place of the login"
    assert first["home"] == str(script.home), "the user's own Codex folder is kept"
    started_with = script.sent("thread/start")[0]
    assert started_with == {
        "cwd": str(script.project),
        "sandbox": "workspace-write",
        "approvalPolicy": "on-request",
        "approvalsReviewer": "user",
    }
    initialize = script.sent("initialize")[0]
    assert initialize["clientInfo"]["name"] != "atlas", "Codex gives that origin to another product"
    turn = script.sent("turn/start")[0]
    assert turn["input"] == [{"type": "text", "text": "Fix the failing test.", "text_elements": []}]
    assert script.answers() == [{"decision": "accept"}]


def test_a_refusal_declines_and_its_reason_is_steered_into_the_turn(script: Script) -> None:
    script.write({"command": "rm -rf build"}, {"sleep": 2}, {"say": "Left it."})

    async def deny(permission: Permission) -> Verdict:
        return Verdict(False, "Keep the build folder.")

    host = _run(script, Host(approve=deny), "Clean up.")

    returned = next(event for event in host.events if isinstance(event, Returned))
    assert returned.failed
    assert script.answers() == [{"decision": "decline"}]
    assert Said("Steered: [Atlas: the request was refused: Keep the build folder.]") in host.events
    assert Said("Left it.") in host.events
    assert not _finished(host).failed


def test_a_refusal_without_a_reason_steers_nothing(script: Script) -> None:
    script.write({"command": "rm -rf build"}, {"say": "Left it."})

    async def deny(permission: Permission) -> Verdict:
        return Verdict(False)

    _run(script, Host(approve=deny), "Clean up.")

    assert script.sent("turn/steer") == []


def test_a_file_change_asks_and_is_written_once_allowed(script: Script) -> None:
    script.write({"write": "notes.txt", "content": "hello"}, {"say": "Wrote it."})

    host = _run(script, Host(), "Write the notes.")

    called = next(event for event in host.events if isinstance(event, Called))
    assert called.tool == "apply_patch"
    assert called.files == (str(script.project / "notes.txt"),)
    assert [(p.tool, p.title) for p in host.permissions] == [("apply_patch", "Change files")]
    assert (script.project / "notes.txt").read_text(encoding="utf-8") == "hello"


def test_its_own_reviewer_approves_in_auto_and_the_switch_counts_from_the_next_turn(
    script: Script,
) -> None:
    script.write({"command": "make"}, {"say": "Built."})

    async def scenario() -> Host:
        host = Host()
        instance = await codex().start(ExecutorSpec(script.project, auto=True), host)
        try:
            await instance.prompt("Build it.")
            await instance.set_auto(False)
            await instance.prompt("Again.")
        finally:
            await instance.stop()
        return host

    host = asyncio.run(scenario())

    assert script.sent("thread/start")[0]["approvalsReviewer"] == "auto_review"
    assert [turn["approvalsReviewer"] for turn in script.sent("turn/start")] == [
        "auto_review",
        "user",
    ]
    # Only the second turn, its own reviewer off, asked the user.
    assert len(host.permissions) == 1


def test_a_steer_enters_the_turn_that_runs(script: Script) -> None:
    script.write({"sleep": 5}, {"say": "Done."})
    host = Host()

    async def scenario() -> None:
        instance = await codex().start(ExecutorSpec(script.project), host)
        try:
            prompt = asyncio.create_task(instance.prompt("Go."))
            await asyncio.sleep(0.5)
            await instance.steer("Use the other folder.")
            await prompt
        finally:
            await instance.stop()

    asyncio.run(scenario())

    assert Said("Steered: Use the other folder.") in host.events
    steer = script.sent("turn/steer")[0]
    assert steer["expectedTurnId"], "a steer names the turn it enters"
    assert steer["input"][-1]["text"] == "Use the other folder."


def test_a_steer_with_no_turn_running_is_refused(script: Script) -> None:
    async def scenario() -> None:
        instance = await codex().start(ExecutorSpec(script.project), Host())
        try:
            with pytest.raises(ExecutorError, match="no turn running"):
                await instance.steer("Hello.")
        finally:
            await instance.stop()

    asyncio.run(scenario())


def test_stopping_interrupts_the_turn_and_ends_its_process(script: Script) -> None:
    script.write({"sleep": 30}, {"say": "Never."})
    host = Host()

    async def scenario() -> None:
        instance = await codex().start(ExecutorSpec(script.project), host)
        prompt = asyncio.create_task(instance.prompt("Go."))
        await asyncio.sleep(0.5)
        await asyncio.wait_for(instance.stop(), 15)
        prompt.cancel()
        await asyncio.gather(prompt, return_exceptions=True)
        assert instance_server(instance).ended.is_set()

    asyncio.run(scenario())

    assert script.sent("turn/interrupt"), "the turn was interrupted before the process stopped"
    assert Said("Never.") not in host.events


def instance_server(instance: ExecutorInstance) -> Any:
    return instance.server  # type: ignore[attr-defined]


def test_the_plan_limit_ends_the_turn_failed(script: Script) -> None:
    script.write({"say": "Starting."}, {"limit": True})

    host = _run(script, Host(), "Go.")

    assert LimitReached() in host.events
    finished = _finished(host)
    assert finished.failed
    assert finished.text == "You've hit your usage limit."


def test_a_compaction_and_the_plan_s_windows_are_told(script: Script) -> None:
    script.write({"compact": True}, {"usage": [30, 55]}, {"say": "Done."})

    host = _run(script, Host(), "Go.")

    assert Compacted(90_000) in host.events
    changed = next(event for event in host.events if isinstance(event, UsageChanged))
    assert [(w.id, w.used, w.minutes, w.title) for w in changed.windows] == [
        ("primary", 30.0, 300, "5-hour limit"),
        ("secondary", 55.0, 10_080, "Weekly limit"),
    ]


def test_a_question_reaches_the_user_and_the_answers_go_back(script: Script) -> None:
    questions = [
        {
            "id": "runner",
            "header": "Runner",
            "question": "Which runner?",
            "isOther": False,
            "isSecret": False,
            "options": [
                {"label": "Jest", "description": "the old one"},
                {"label": "Vitest", "description": ""},
            ],
        }
    ]
    script.write({"questions": questions})

    async def answer(asked: Sequence[Question]) -> Answers:
        return {"Which runner?": "Vitest"}

    host = _run(script, Host(ask=answer), "Set up tests.")

    asked = host.questions[0][0]
    assert (asked.text, asked.header, [o.label for o in asked.options]) == (
        "Which runner?",
        "Runner",
        ["Jest", "Vitest"],
    )
    assert Said('Answers: {"runner": {"answers": ["Vitest"]}}') in host.events


def test_files_go_as_codex_takes_them_and_come_back_in_the_transcript(
    script: Script, tmp_path: Path
) -> None:
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n")
    document = tmp_path / "spec.pdf"
    document.write_bytes(b"%PDF")
    files = (
        ExecutorFile("shot.png", "image/png", image),
        ExecutorFile("spec.pdf", "application/pdf", document),
    )
    script.write({"say": "Read them."})
    host = Host()

    async def scenario() -> None:
        instance = await codex().start(ExecutorSpec(script.project), host)
        try:
            await instance.prompt("Look.", files=files)
        finally:
            await instance.stop()

    asyncio.run(scenario())

    sent = script.sent("turn/start")[0]["input"]
    assert sent[0] == {"type": "localImage", "path": str(image)}
    assert str(document) in sent[1]["text"]
    assert sent[2]["text"] == "Look."
    started = host.events[0]
    assert isinstance(started, Started)
    transcript = asyncio.run(codex().transcript(started.session, script.project))
    first = transcript.entries[0]
    assert (first.kind, first.by, first.text, first.files) == (
        "text",
        "user",
        "Look.",
        (str(image), str(document)),
    )


def test_sessions_are_listed_read_back_resumed_forked_and_deleted(script: Script) -> None:
    script.write({"command": "ls"}, {"say": "Listed."})
    host = _run(script, Host(), "List the files.")
    started = host.events[0]
    assert isinstance(started, Started)
    thread = started.session
    executor = codex()

    listed = asyncio.run(executor.sessions())
    assert [(s.id, s.folder, s.title) for s in listed] == [
        (thread, script.project, "List the files.")
    ]
    assert listed[0].updated_at.endswith("Z")
    assert [f.folder for f in asyncio.run(executor.folders())] == [script.project]
    assert [s.id for s in asyncio.run(executor.sessions(folder=script.project))] == [thread]
    assert asyncio.run(executor.sessions(folder=script.project / "other")) == []
    assert all(params.get("useStateDbOnly") for params in script.sent("thread/list"))

    transcript = asyncio.run(executor.transcript(thread, script.project))
    assert [(e.kind, e.tool, e.by) for e in transcript.entries] == [
        ("text", "", "user"),
        ("tool", "shell", "harness"),
        ("result", "", "harness"),
        ("text", "", "harness"),
    ]
    assert transcript.entries[-1].text == "Listed."
    assert transcript.entries[1].call == transcript.entries[2].call

    resumed = _run(script, Host(), "Again.", spec=ExecutorSpec(script.project, resume=thread))
    assert resumed.events[0] == Started(thread, model="gpt-6-luna")
    forked = _run(
        script, Host(), "In a copy.", spec=ExecutorSpec(script.project, resume=thread, fork=True)
    )
    copy = forked.events[0]
    assert isinstance(copy, Started)
    assert copy.session != thread
    assert len(asyncio.run(executor.sessions())) == 2

    asyncio.run(executor.delete_session(copy.session, script.project))
    assert [s.id for s in asyncio.run(executor.sessions())] == [thread]
    gone = asyncio.run(executor.transcript(copy.session, script.project))
    assert gone.expired


def test_a_session_that_runs_here_is_not_deleted(script: Script) -> None:
    async def scenario() -> None:
        host = Host()
        instance = await codex().start(ExecutorSpec(script.project), host)
        try:
            started = host.events[0]
            assert isinstance(started, Started)
            with pytest.raises(ExecutorError, match="runs in Atlas"):
                await codex().delete_session(started.session, script.project)
        finally:
            await instance.stop()

    asyncio.run(scenario())


def test_a_session_unknown_to_codex_fails_to_resume(script: Script) -> None:
    async def scenario() -> None:
        with pytest.raises(ExecutorError, match="no rollout"):
            await codex().start(ExecutorSpec(script.project, resume="gone"), Host())

    asyncio.run(scenario())


def test_detection_says_the_version_and_the_login(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = asyncio.run(codex().detect())
    assert (found.installed, found.version, found.minimum, found.supported) == (
        True,
        "0.163.0",
        "0.163.0",
        True,
    )
    assert found.account is not None
    assert (found.account.login, found.account.plan) == ("user@example.com", "plus")

    monkeypatch.setenv("FAKE_CODEX_VERSION", "0.150.2")
    old = asyncio.run(codex().detect())
    assert (old.installed, old.supported) == (True, False)
    assert "older than 0.163.0" in old.problem

    monkeypatch.setenv("FAKE_CODEX_VERSION", "0.163.0-alpha.10.2")
    assert asyncio.run(codex().detect()).supported, "the Store app's alpha is that version"

    monkeypatch.setenv("FAKE_CODEX_LOGGED_OUT", "1")
    nobody = asyncio.run(codex().detect())
    assert nobody.account is not None
    assert not nobody.account.logged_in

    missing = asyncio.run(codex(None).detect())
    assert (missing.installed, missing.supported) == (False, False)
    assert "Codex is not installed" in missing.problem


def test_models_connectors_and_the_plan_s_limits_are_read(script: Script) -> None:
    executor = codex()

    assert asyncio.run(executor.models()) == [
        ModelChoice(
            "gpt-6-luna", "GPT-6 Luna", "The default.", ("low", "medium", "high"), "gpt-6-luna"
        ),
        ModelChoice("gpt-5.6-terra", "GPT-5.6 Terra", "", ("ultra",), "gpt-5.6-terra"),
    ]
    assert asyncio.run(executor.connectors()) == [
        Connector("docs", "connected", "user"),
        Connector("github", "needs-auth", "plugin"),
    ]
    usage = asyncio.run(executor.usage())
    assert usage.plan == "plus"
    assert usage.windows == (
        UsageWindow("primary", "5-hour limit", 12.0, "2026-09-21T14:13:20Z", 300),
        UsageWindow("secondary", "Weekly limit", 40.0, "", 10_080),
    )
    assert usage.extra_usage is False, "no credits: at a limit it stops"
    usage_of = plugin_module("protocol").usage_of
    window = {"usedPercent": 1, "windowDurationMins": 300}
    paid = {"rateLimits": {"primary": window, "credits": {"hasCredits": True, "unlimited": False}}}
    assert usage_of(paid).extra_usage is True, "past the limit its credits pay"
    assert usage_of({"rateLimits": {"primary": window}}).extra_usage is None
    assert asyncio.run(executor.commands(folder=script.project)) == []
    assert not executor.capabilities().commands
    assert not executor.capabilities().questions


def test_the_model_and_effort_start_and_switch_from_the_next_turn(script: Script) -> None:
    script.write({"say": "Ok."})
    executor = codex()
    asyncio.run(executor.models())

    with pytest.raises(ExecutorError, match="not max"):
        asyncio.run(
            executor.start(ExecutorSpec(script.project, model="gpt-6-luna", effort="max"), Host())
        )

    async def scenario() -> None:
        instance = await executor.start(
            ExecutorSpec(script.project, model="gpt-6-luna", effort="high"), Host()
        )
        try:
            await instance.prompt("One.")
            await instance.set_model("gpt-5.6-terra", "ultra")
            await instance.prompt("Two.")
        finally:
            await instance.stop()

    asyncio.run(scenario())

    assert script.sent("thread/start")[-1]["model"] == "gpt-6-luna"
    assert [(t.get("model"), t.get("effort")) for t in script.sent("turn/start")] == [
        ("gpt-6-luna", "high"),
        ("gpt-5.6-terra", "ultra"),
    ]


def test_another_account_is_its_own_folder_logged_in_by_codex(
    script: Script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script.write({"say": "Mine."})
    host = _run(script, Host(), "In the main account.")
    started = host.events[0]
    assert isinstance(started, Started)
    main = codex()
    other_folder = main.new_account_folder("side")
    assert other_folder.name == ".codex-side"
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    other_folder = tmp_path / ".codex-side"
    other = main.for_account(other_folder)
    assert other.account_folder() == other_folder

    nobody = asyncio.run(other.detect())
    assert nobody.account is not None
    assert not nobody.account.logged_in, "a new account's folder has no login"
    logged = asyncio.run(other.log_in(""))
    assert (logged.logged_in, logged.login) == (True, "other@example.com")
    assert (other_folder / "fake-auth.json").is_file()
    assert main.account_folder() == script.home

    # The main account's session goes on in a copy kept by the other account.
    moved = Host()
    spec = ExecutorSpec(script.project, resume=started.session, fork=True, resume_from=script.home)

    async def scenario() -> None:
        instance = await other.start(spec, moved)
        try:
            await instance.prompt("On the other account.")
        finally:
            await instance.stop()

    asyncio.run(scenario())

    copy = moved.events[0]
    assert isinstance(copy, Started)
    assert copy.session != started.session
    assert [s.id for s in asyncio.run(other.sessions())] == [copy.session]
    assert [s.id for s in asyncio.run(main.sessions())] == [started.session]


def test_a_process_that_ends_between_turns_is_told(script: Script) -> None:
    host = Host()

    async def scenario() -> None:
        instance = await codex().start(ExecutorSpec(script.project), host)
        server = instance_server(instance)
        await plugin_module("server").stop_tree(server._process)  # pyright: ignore[reportPrivateUsage]
        await asyncio.wait_for(server.ended.wait(), 10)
        await asyncio.sleep(0.1)
        await instance.stop()

    asyncio.run(scenario())

    assert host.failures
    assert host.failures[0].startswith("Codex ended.")


def test_the_program_setting_and_where_codex_is_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = plugin_module("executor")
    package = sys.modules["atlas_plugin_codex"]
    program = package.program
    assert program('["codex", "--flag"]') == ["codex", "--flag"]
    with pytest.raises(ExecutorError, match="not a JSON list"):
        program("codex")
    with pytest.raises(ExecutorError, match="JSON list of the program"):
        program("[]")

    # npm's `codex.cmd` starts Node; the native program its package carries is run instead.
    if sys.platform == "win32":
        prefix = tmp_path / "npm"
        native = (
            prefix
            / "node_modules"
            / "@openai"
            / "codex"
            / "node_modules"
            / "@openai"
            / "codex-win32-x64"
            / "vendor"
            / "x86_64-pc-windows-msvc"
            / "bin"
            / "codex.exe"
        )
        native.parent.mkdir(parents=True)
        native.write_bytes(b"")
        shim = prefix / "codex.cmd"
        shim.write_text("@echo off", encoding="utf-8")

        def which(name: str) -> str | None:
            return str(shim) if name == "codex" else None

        monkeypatch.setattr(found.shutil, "which", which)
        assert found.codex_program() == native
    assert FOLDER.name == "codex"
    assert FAKE[0] == sys.executable


def test_the_environment_keeps_the_login_and_the_folder() -> None:
    environment = plugin_module("protocol").environment
    kept = environment(
        {
            "PATH": "x",
            "OPENAI_API_KEY": "k",
            "openai_base_url": "u",
            "CODEX_API_KEY": "k",
            "CODEX_HOME": "h",
        },
        None,
    )
    assert kept == {"PATH": "x", "CODEX_HOME": "h", "RUST_LOG": "warn"}
    assert environment({}, "other")["CODEX_HOME"] == "other"
