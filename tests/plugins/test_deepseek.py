"""The DeepSeek plugin as Atlas drives it, through the SDK's executor (step 16q): the real pipes and
real ACP, against tests/fake_dsh.py, which speaks it as `dsh --profile acp` does with a script in
place of the model and writes its sessions as `dsh` does. The host here plays Atlas: it keeps what
it hears and answers what is asked."""

import asyncio
import json
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import httpx2
import pytest

from atlas.plugins import (
    Account,
    Answers,
    Called,
    ExecutorError,
    ExecutorEvent,
    ExecutorFile,
    ExecutorInstance,
    ExecutorSpec,
    Finished,
    LimitReached,
    Permission,
    Question,
    Returned,
    Said,
    Started,
    Thought,
    Verdict,
)
from deepseek_support import FAKE, FOLDER, deepseek, plugin_module


@pytest.fixture
def script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "Script":
    return Script(tmp_path, monkeypatch)


class Script:
    def __init__(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.file = folder / "script.json"
        self.log = folder / "log.jsonl"
        self.home = folder / ".dsh"
        self.home.mkdir()
        self.data = folder / "plugin-data"
        self.project = folder / "project"
        self.project.mkdir()
        monkeypatch.setenv("FAKE_DSH_SCRIPT", str(self.file))
        monkeypatch.setenv("FAKE_DSH_LOG", str(self.log))
        monkeypatch.setenv("DSH_HOME", str(self.home))
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-user")
        self.write()

    def write(self, *steps: dict[str, Any]) -> None:
        self.file.write_text(json.dumps(list(steps)), encoding="utf-8")

    def prompts(self, **steps: list[dict[str, Any]]) -> None:
        self.file.write_text(json.dumps({"prompts": steps}), encoding="utf-8")

    def received(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def starts(self) -> list[dict[str, Any]]:
        """The first line of each process: its command line, preset, home, key and patch."""
        return [line for line in self.received() if "argv" in line]

    def sent(self, method: str) -> list[dict[str, Any]]:
        return [line["params"] for line in self.received() if line.get("method") == method]

    def answers(self) -> list[dict[str, Any]]:
        return [
            line["result"]
            for line in self.received()
            if "result" in line and isinstance(line.get("id"), int) and line["id"] > 1000
        ]

    def executor(self, **options: Any) -> Any:
        return deepseek(self.data, **options)


class Host:
    """Atlas, as the instance sees it."""

    def __init__(self, approve: Callable[[Permission], Awaitable[Verdict]] | None = None) -> None:
        self.events: list[ExecutorEvent] = []
        self.permissions: list[Permission] = []
        self.failures: list[str] = []
        self._approve = approve

    def event(self, event: ExecutorEvent) -> None:
        self.events.append(event)

    async def ask(self, questions: Sequence[Question]) -> Answers:
        raise AssertionError("dsh asks no questions over ACP")

    async def approve(self, permission: Permission) -> Verdict:
        self.permissions.append(permission)
        return Verdict(True) if self._approve is None else await self._approve(permission)

    def failed(self, reason: str) -> None:
        self.failures.append(reason)


def _run(script: Script, host: Host, *prompts: str, spec: ExecutorSpec | None = None) -> Host:
    async def scenario() -> None:
        instance = await script.executor().start(spec or ExecutorSpec(script.project), host)
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


def _session(host: Host) -> str:
    return next(event.session for event in host.events if isinstance(event, Started))


def test_a_task_asks_for_its_tools_and_ends_with_its_last_words(script: Script) -> None:
    script.write(
        {"think": "The tests import the wrong module."},
        {"say": "I will run the tests."},
        {"command": "pytest -q"},
        {"say": "All green."},
    )

    host = _run(script, Host(), "Fix the tests.")

    started = host.events[0]
    assert started == Started(_session(host), model="deepseek-v4-flash")
    assert [type(event) for event in host.events[1:]] == [
        Thought,
        Said,
        Called,
        Returned,
        Said,
        Finished,
    ]
    called = next(event for event in host.events if isinstance(event, Called))
    assert (called.tool, json.loads(called.arguments)["command"]) == ("tool", "pytest -q")
    assert [p.summary for p in host.permissions] == ["Use a tool: bash"]
    assert script.answers() == [{"outcome": {"outcome": "selected", "optionId": "allow-once"}}]
    assert (_finished(host).text, _finished(host).reason) == ("All green.", "completed")
    start = script.starts()[0]
    assert start["mode"] == "workspace-write", "it asks, as Atlas's switch is off"
    assert start["keyed"], "the user's own key reaches it"


def test_the_session_logs_are_off_unless_the_setting_keeps_them(script: Script) -> None:
    script.write({"say": "Hi."})
    _run(script, Host(), "Hello.")
    off = script.starts()[-1]
    assert off["argv"][2] == "--patch"
    assert "session-log-deepseek" in off["patch"]
    assert "enabled: false" in off["patch"]

    async def logged() -> None:
        instance = await script.executor(session_logs=True).start(
            ExecutorSpec(script.project), Host()
        )
        await instance.stop()

    asyncio.run(logged())
    assert script.starts()[-1]["argv"] == ["--profile", "acp"], "on: dsh as it comes"


def test_a_refusal_rejects_and_its_reason_goes_in_as_a_steer(script: Script) -> None:
    script.prompts(**{"Clean up": [{"command": "rm -rf build"}, {"sleep": 5}, {"say": "Left it."}]})

    async def deny(permission: Permission) -> Verdict:
        return Verdict(False, "Keep the build folder.")

    host = _run(script, Host(approve=deny), "Clean up.")

    assert script.answers() == [{"outcome": {"outcome": "selected", "optionId": "reject-once"}}]
    returned = next(event for event in host.events if isinstance(event, Returned))
    assert returned.failed
    note = "[Atlas: the request was refused: Keep the build folder.]"
    assert Said(f"Heard: {note}") in host.events
    assert script.sent("session/cancel") != [], "dsh takes one prompt at a time: it is cancelled"
    assert len([event for event in host.events if isinstance(event, Finished)]) == 1


def test_a_steer_cancels_the_turn_and_goes_in_next(script: Script) -> None:
    script.prompts(**{"Wait": [{"command": "sleep 30"}, {"sleep": 30}, {"say": "Never."}]})

    async def scenario() -> Host:
        host = Host()
        instance = await script.executor().start(ExecutorSpec(script.project, auto=True), host)
        try:
            running = asyncio.create_task(instance.prompt("Wait for it."))
            while not any(isinstance(event, Returned) for event in host.events):
                await asyncio.sleep(0.02)
            await instance.steer("Also say BANANA.")
            await asyncio.wait_for(running, 20)
        finally:
            await instance.stop()
        return host

    host = asyncio.run(scenario())

    assert Said("Heard: Also say BANANA.") in host.events
    assert Said("Never.") not in host.events
    finished = [event for event in host.events if isinstance(event, Finished)]
    assert [(event.text, event.reason) for event in finished] == [
        ("Heard: Also say BANANA.", "completed")
    ]


def test_its_automatic_mode_is_full_access_and_switching_it_starts_it_again(
    script: Script,
) -> None:
    script.write({"command": "make"}, {"say": "Built."})

    async def scenario() -> Host:
        host = Host()
        instance = await script.executor().start(ExecutorSpec(script.project, auto=True), host)
        try:
            await instance.prompt("Build it.")
            assert host.permissions == []
            await instance.set_auto(False)
            await instance.prompt("Build it again.")
        finally:
            await instance.stop()
        return host

    host = asyncio.run(scenario())

    assert [start["mode"] for start in script.starts()] == ["danger-full-access", "workspace-write"]
    assert script.sent("session/resume")[-1]["sessionId"] == _session(host), "the same session"
    assert len(host.permissions) == 1, "off again, it asks"


def test_stop_cancels_the_turn_and_ends_the_process(script: Script) -> None:
    script.write({"sleep": 60}, {"say": "Never."})

    async def scenario() -> tuple[Host, ExecutorInstance]:
        host = Host()
        instance = await script.executor().start(ExecutorSpec(script.project), host)
        running = asyncio.create_task(instance.prompt("Wait."))
        await asyncio.sleep(0.3)
        await asyncio.wait_for(instance.stop(), 20)
        await asyncio.wait_for(running, 20)
        return host, instance

    host, instance = asyncio.run(scenario())

    assert script.sent("session/cancel") != []
    assert _finished(host).reason == "interrupted"
    agent: Any = getattr(instance, "agent")  # noqa: B009 - the instance's own, not the protocol's
    assert agent.ended.is_set()


def test_a_spent_balance_is_the_accounts_limit(script: Script) -> None:
    script.write({"say": "Starting."}, {"limit": True})

    host = _run(script, Host(), "Go.")

    assert LimitReached() in host.events
    finished = _finished(host)
    assert finished.failed
    assert "Insufficient Balance" in finished.text


def test_a_session_is_listed_read_back_from_its_log_and_goes_on(script: Script) -> None:
    script.write({"say": "First."}, {"write": "notes.txt", "content": "hi"})
    session = _session(_run(script, Host(), "Look around."))
    executor = script.executor()

    async def read() -> tuple[Any, Any, Any]:
        return (
            await executor.sessions(folder=script.project),
            await executor.folders(),
            await executor.transcript(session, script.project),
        )

    listed, folders, transcript = asyncio.run(read())

    assert [(info.id, info.title) for info in listed] == [(session, "Look around.")]
    assert [folder.folder for folder in folders] == [script.project]
    assert [(entry.kind, entry.by, entry.tool) for entry in transcript.entries] == [
        ("text", "user", ""),
        ("text", "harness", ""),
        ("tool", "harness", "write"),
        ("result", "harness", "write"),
    ]
    assert transcript.entries[2].files == (str(script.project / "notes.txt"),)

    script.write({"say": "Again."})
    again = _run(script, Host(), "Go on.", spec=ExecutorSpec(script.project, resume=session))

    assert _session(again) == session
    entries = asyncio.run(executor.transcript(session, script.project)).entries
    assert [entry.text for entry in entries if entry.by == "user"] == ["Look around.", "Go on."]


def test_a_session_with_no_prompt_is_left_out_and_copies_are_refused(script: Script) -> None:
    executor = script.executor()

    async def scenario() -> None:
        instance = await executor.start(ExecutorSpec(script.project), Host())
        await instance.stop()
        with pytest.raises(ExecutorError, match="copies no session"):
            await executor.start(ExecutorSpec(script.project, resume="x", fork=True), Host())

    asyncio.run(scenario())

    assert asyncio.run(executor.sessions()) == []
    with pytest.raises(ExecutorError, match="deletes no session"):
        asyncio.run(executor.delete_session("x", script.project))


def test_its_models_and_efforts_come_from_its_options(script: Script) -> None:
    async def scenario() -> tuple[Any, Host]:
        executor = script.executor()
        models = await executor.models()
        host = Host()
        spec = ExecutorSpec(script.project, model="deepseek-v4-pro", effort="max")
        instance = await executor.start(spec, host)
        try:
            await instance.set_model("deepseek-v4-flash", "low")
            with pytest.raises(ExecutorError, match="no model"):
                await instance.set_model("gpt-9", None)
        finally:
            await instance.stop()
        return models, host

    models, host = asyncio.run(scenario())

    assert [(model.id, model.efforts) for model in models] == [
        ("deepseek-v4-flash", ("off", "low", "high", "max")),
        ("deepseek-v4-pro", ("off", "low", "high", "max")),
    ]
    assert host.events[0] == Started(_session(host), model="deepseek-v4-pro")
    options = [
        (sent["configId"], sent["value"]) for sent in script.sent("session/set_config_option")
    ]
    assert options == [
        ("model", '["deepseek-official","deepseek-v4-pro"]'),
        ("reasoning_effort", "max"),
        ("model", '["deepseek-official","deepseek-v4-flash"]'),
        ("reasoning_effort", "low"),
    ]


def test_files_go_by_their_paths(script: Script, tmp_path: Path) -> None:
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF")
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG")
    script.write({"say": "Read."})

    async def scenario() -> None:
        instance = await script.executor().start(ExecutorSpec(script.project), Host())
        try:
            files = [
                ExecutorFile("paper.pdf", "application/pdf", paper),
                ExecutorFile("shot.png", "image/png", image),
            ]
            await instance.prompt("Look.", files=files)
        finally:
            await instance.stop()

    asyncio.run(scenario())

    blocks = script.sent("session/prompt")[0]["prompt"]
    assert [block["type"] for block in blocks] == ["text", "text"], "it takes no images over ACP"
    assert str(paper) in blocks[0]["text"]
    assert str(image) in blocks[0]["text"]


def test_the_plugins_key_and_another_accounts_home_reach_it(
    script: Script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    other = tmp_path / ".dsh-work"
    script.write({"say": "Hi."})

    executor = script.executor(home=other, api_key="sk-vault")
    found = asyncio.run(executor.detect())

    assert (found.installed, found.version, found.supported) == (True, "0.2.1-rc.2", True)
    assert found.account is not None
    assert (found.account.logged_in, found.account.login) == (True, "API key")

    async def scenario() -> None:
        instance = await executor.start(ExecutorSpec(script.project), Host())
        await instance.stop()

    asyncio.run(scenario())
    start = script.starts()[-1]
    assert start["keyed"]
    assert start["home"] == str(other)
    assert executor.account_folder() == other
    assert script.executor().new_account_folder("work").name == ".dsh-work"


def test_without_a_key_it_is_not_logged_in_and_says_where_one_goes(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    executor = script.executor()

    found = asyncio.run(executor.detect())
    assert found.account is not None
    assert not found.account.logged_in

    (script.home / ".credentials.yaml").write_text("DEEPSEEK_API_KEY: sk-kept\n", encoding="utf-8")
    # The key its own credentials file keeps, as `dsh` saves it.
    assert asyncio.run(executor.detect()).account == Account(logged_in=True, login="API key")
    with pytest.raises(ExecutorError, match=r"platform.deepseek.com"):
        asyncio.run(executor.log_in(""))


def test_detection_says_too_old_and_not_installed(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_DSH_VERSION", "0.1.9")
    old = asyncio.run(script.executor().detect())
    assert not old.supported
    assert old.problem.startswith("dsh 0.1.9 is older than 0.2.1")
    missing = asyncio.run(script.executor(cli=None).detect())
    assert not missing.installed
    assert "npm install -g @deepseek-ai/dsh" in missing.problem


def test_its_balance_is_deepseeks_with_the_key(script: Script) -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200,
            json={
                "is_available": True,
                "balance_infos": [{"currency": "USD", "total_balance": "4.20"}],
            },
        )

    usage = asyncio.run(script.executor(transport=httpx2.MockTransport(handler)).usage())

    assert usage.plan == "balance 4.20 USD"
    assert usage.windows == ()
    assert "no plan limits" in usage.unavailable
    assert seen[0].headers["authorization"] == "Bearer sk-user"
    assert str(seen[0].url) == "https://api.deepseek.com/user/balance"


def test_the_plugin_registers_its_executor_and_the_core_offers_it_auto() -> None:
    manifest = json.loads((FOLDER / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["atlas"]["executors"] == ["deepseek"]
    assert [setting["name"] for setting in manifest["atlas"]["settings"]] == [
        "program",
        "session_logs",
    ]
    package = sys.modules["atlas_plugin_deepseek"]
    assert package.program(json.dumps(FAKE)) == FAKE
    with pytest.raises(ExecutorError, match="JSON list"):
        package.program("dsh")


def test_npms_script_is_run_with_the_node_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = plugin_module("executor")
    prefix = tmp_path / "npm"
    bin_script = prefix / executor.SCRIPT
    bin_script.parent.mkdir(parents=True)
    bin_script.write_text("", encoding="utf-8")
    (prefix / "node.exe").write_text("", encoding="utf-8")
    monkeypatch.setattr(executor.sys, "platform", "win32")
    monkeypatch.setattr(
        executor.shutil, "which", lambda name: str(prefix / "dsh.cmd") if name == "dsh" else None
    )

    assert executor.installed_cli() == [str(prefix / "node.exe"), str(bin_script)]
