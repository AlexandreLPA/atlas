"""The Gemini CLI plugin as Atlas drives it, through the SDK's executor (step 16p): the real pipes
and real ACP, against tests/fake_gemini.py, which speaks it as `gemini --acp` does with a script
in place of the model and writes its sessions as the Gemini CLI does. The host here plays Atlas:
it keeps what it hears and answers what is asked."""

import asyncio
import json
import logging
import os
import sys
import time
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest

from atlas.plugins import (
    Answers,
    Called,
    CommandsChanged,
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
    SlashCommand,
    Started,
    Thought,
    UsageChanged,
    UsageWindow,
    Verdict,
)
from gemini_support import FAKE, FOLDER, gemini, plugin_module


@pytest.fixture
def script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> "Script":
    return Script(tmp_path, monkeypatch)


class Script:
    def __init__(self, folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.file = folder / "script.json"
        self.log = folder / "log.jsonl"
        self.home = folder / "home"
        self.state = self.home / ".gemini"
        self.state.mkdir(parents=True)
        self.project = folder / "project"
        self.project.mkdir()
        monkeypatch.setenv("FAKE_GEMINI_SCRIPT", str(self.file))
        monkeypatch.setenv("FAKE_GEMINI_LOG", str(self.log))
        monkeypatch.setenv("GEMINI_CLI_HOME", str(self.home))
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        monkeypatch.delenv("FAKE_GEMINI_UNTRUSTED", raising=False)
        self.log_in()
        self.write()

    def log_in(self, seconds: float = 3600) -> None:
        """The user's own login with Google, as the Gemini CLI leaves it."""
        settings = {"security": {"auth": {"selectedType": "oauth-personal"}}}
        (self.state / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
        (self.state / "google_accounts.json").write_text(
            json.dumps({"active": "user@example.com", "old": []}), encoding="utf-8"
        )
        expiry = int((time.time() + seconds) * 1000)
        (self.state / "oauth_creds.json").write_text(
            json.dumps({"access_token": "token", "expiry_date": expiry}), encoding="utf-8"
        )

    def write(self, *steps: dict[str, Any]) -> None:
        self.file.write_text(json.dumps(list(steps)), encoding="utf-8")

    def prompts(self, **steps: list[dict[str, Any]]) -> None:
        self.file.write_text(json.dumps({"prompts": steps}), encoding="utf-8")

    def received(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def sent(self, method: str) -> list[dict[str, Any]]:
        """The params of each request Atlas sent with `method`."""
        return [line["params"] for line in self.received() if line.get("method") == method]

    def answers(self) -> list[dict[str, Any]]:
        """What Atlas answered to the Gemini CLI's own requests."""
        return [
            line["result"]
            for line in self.received()
            if "result" in line and isinstance(line.get("id"), int) and line["id"] > 1000
        ]


class Host:
    """Atlas, as the instance sees it: what it heard, what it was asked, and its answers."""

    def __init__(self, approve: Callable[[Permission], Awaitable[Verdict]] | None = None) -> None:
        self.events: list[ExecutorEvent] = []
        self.permissions: list[Permission] = []
        self.failures: list[str] = []
        self._approve = approve
        self.during: Callable[[ExecutorEvent], Coroutine[Any, Any, None]] | None = None

    def event(self, event: ExecutorEvent) -> None:
        self.events.append(event)
        if self.during is not None:
            asyncio.get_running_loop().create_task(self.during(event))

    async def ask(self, questions: Sequence[Question]) -> Answers:
        raise AssertionError("the Gemini CLI asks no questions over ACP")

    async def approve(self, permission: Permission) -> Verdict:
        self.permissions.append(permission)
        return Verdict(True) if self._approve is None else await self._approve(permission)

    def failed(self, reason: str) -> None:
        self.failures.append(reason)


def _run(script: Script, host: Host, *prompts: str, spec: ExecutorSpec | None = None) -> Host:
    async def scenario() -> None:
        instance = await gemini().start(spec or ExecutorSpec(script.project), host)
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


def test_a_task_asks_for_its_command_and_ends_with_its_last_words(script: Script) -> None:
    script.write(
        {"think": "The tests import the wrong module."},
        {"say": "I will run the tests."},
        {"command": "pytest -q"},
        {"say": "All green."},
    )

    host = _run(script, Host(), "Fix the tests.")

    started = host.events[0]
    assert isinstance(started, Started)
    assert started.model == "auto"
    # Its commands come whenever it says them, apart from the turn.
    kinds = [type(event) for event in host.events if not isinstance(event, CommandsChanged)]
    assert kinds[1:] == [Thought, Said, Called, Returned, Said, Finished], host.events
    assert Said("I will run the tests.") in host.events, "two chunks, said as one"
    called = next(event for event in host.events if isinstance(event, Called))
    assert called.tool == "shell"
    assert json.loads(called.arguments)["title"] == "pytest -q"
    assert [(p.tool, p.title, p.summary) for p in host.permissions] == [
        ("shell", "Run a command", "Run a command: pytest -q")
    ]
    assert script.answers() == [{"outcome": {"outcome": "selected", "optionId": "proceed_once"}}]
    finished = _finished(host)
    assert (finished.text, finished.failed, finished.reason) == ("All green.", False, "completed")
    assert finished.session == started.session
    initialize = script.sent("initialize")[0]
    assert initialize["clientCapabilities"] == {
        "fs": {"readTextFile": False, "writeTextFile": False},
        "terminal": False,
    }, "it reads, writes and runs commands itself"
    assert script.received()[0]["argv"] == ["--acp"]
    assert script.sent("session/set_mode") == [], "asking, as it opened"


def test_a_file_change_asks_and_is_written_once_allowed(script: Script) -> None:
    script.write({"write": "notes.txt", "content": "hello"}, {"say": "Wrote it."})

    host = _run(script, Host(), "Write the notes.")

    called = next(event for event in host.events if isinstance(event, Called))
    assert (called.tool, called.files) == ("edit", (str(script.project / "notes.txt"),))
    assert [(p.tool, p.title) for p in host.permissions] == [("edit", "Change files")]
    assert (script.project / "notes.txt").read_text(encoding="utf-8") == "hello"


def test_a_refusal_rejects_and_its_reason_goes_in_as_a_prompt(script: Script) -> None:
    script.prompts(
        **{
            "Clean up": [{"command": "rm -rf build"}, {"sleep": 5}, {"say": "Left it."}],
        }
    )

    async def deny(permission: Permission) -> Verdict:
        return Verdict(False, "Keep the build folder.")

    host = _run(script, Host(approve=deny), "Clean up.")

    returned = next(event for event in host.events if isinstance(event, Returned))
    assert returned.failed
    assert script.answers() == [{"outcome": {"outcome": "selected", "optionId": "cancel"}}]
    note = "[Atlas: the request was refused: Keep the build folder.]"
    assert Said(f"Heard: {note}") in host.events, "the reason, read in the same turn"
    assert len([event for event in host.events if isinstance(event, Finished)]) == 1
    assert _finished(host).reason == "completed"


def test_a_refusal_without_a_reason_prompts_nothing(script: Script) -> None:
    script.write({"command": "rm -rf build"}, {"say": "Left it."})

    async def deny(permission: Permission) -> Verdict:
        return Verdict(False)

    host = _run(script, Host(approve=deny), "Clean up.")

    assert len(script.sent("session/prompt")) == 1
    assert _finished(host).text == "Left it."


def test_its_automatic_mode_is_yolo_and_asks_nothing(script: Script) -> None:
    script.write({"command": "make"}, {"say": "Built."})

    async def scenario() -> Host:
        host = Host()
        instance = await gemini().start(ExecutorSpec(script.project, auto=True), host)
        try:
            await instance.prompt("Build it.")
            assert host.permissions == []
            await instance.set_auto(False)
            await instance.prompt("Build it again.")
        finally:
            await instance.stop()
        return host

    host = asyncio.run(scenario())

    assert [mode["modeId"] for mode in script.sent("session/set_mode")] == ["yolo", "default"]
    assert len(host.permissions) == 1, "off again, it asks"


def test_a_folder_it_does_not_trust_keeps_asking_and_says_why(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_GEMINI_UNTRUSTED", "1")
    script.write({"command": "make"}, {"say": "Built."})

    async def scenario() -> tuple[Host, str]:
        host = Host()
        instance = await gemini().start(ExecutorSpec(script.project, auto=True), host)
        try:
            await instance.prompt("Build it.")
            with pytest.raises(ExecutorError) as refused:
                await instance.set_auto(True)
        finally:
            await instance.stop()
        return host, str(refused.value)

    host, refused = asyncio.run(scenario())

    note = plugin_module("executor").untrusted_note()
    assert host.events[1] == Said(note)
    assert len(host.permissions) == 1
    assert "does not trust this folder" in refused


def test_a_steer_is_a_second_prompt_and_the_turn_ends_once(script: Script) -> None:
    script.prompts(**{"Wait": [{"command": "sleep 30"}, {"sleep": 30}, {"say": "Never."}]})

    async def scenario() -> Host:
        host = Host()
        instance = await gemini().start(ExecutorSpec(script.project), host)
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
    assert len(finished) == 1
    assert (finished[0].text, finished[0].reason) == ("Heard: Also say BANANA.", "completed")


def test_a_steer_with_no_turn_running_is_refused(script: Script) -> None:
    async def scenario() -> None:
        instance = await gemini().start(ExecutorSpec(script.project), Host())
        try:
            with pytest.raises(ExecutorError, match="no turn"):
                await instance.steer("Hello.")
        finally:
            await instance.stop()

    asyncio.run(scenario())


def test_stop_cancels_the_turn_and_ends_the_process(script: Script) -> None:
    script.write({"sleep": 60}, {"say": "Never."})

    async def scenario() -> tuple[Host, ExecutorInstance]:
        host = Host()
        instance = await gemini().start(ExecutorSpec(script.project), host)
        running = asyncio.create_task(instance.prompt("Wait."))
        await asyncio.sleep(0.3)
        await asyncio.wait_for(instance.stop(), 20)
        await asyncio.wait_for(running, 20)
        return host, instance

    host, instance = asyncio.run(scenario())

    assert script.sent("session/cancel") != []
    assert _finished(host).reason == "interrupted"
    assert cast("Any", instance).agent.ended.is_set()


def test_the_daily_quota_spent_is_the_plans_limit(script: Script) -> None:
    script.write({"say": "Starting."}, {"limit": True})

    host = _run(script, Host(), "Go.")

    assert LimitReached() in host.events
    finished = _finished(host)
    assert finished.failed
    assert "exhausted your daily quota" in finished.text


def test_with_no_login_it_says_to_log_in(script: Script) -> None:
    (script.state / "oauth_creds.json").unlink()

    async def scenario() -> None:
        await gemini().start(ExecutorSpec(script.project), Host())

    with pytest.raises(ExecutorError, match="not logged in"):
        asyncio.run(scenario())


def test_a_session_is_listed_for_its_folder_however_the_folder_is_spelled(
    script: Script, tmp_path: Path
) -> None:
    # A short name (C:\Users\RUNNER~1), a junction or a link: the Gemini CLI's own record of the
    # folder is read resolved, so the folder asked for must be too (GitHub's runners, 24/10/2026).
    script.write({"say": "First."})
    session = _session(_run(script, Host(), "Look around."))
    alias = tmp_path / "alias"
    if sys.platform == "win32":
        import _winapi  # noqa: PLC0415 - Windows's only

        _winapi.CreateJunction(str(script.project), str(alias))
    else:
        alias.symlink_to(script.project, target_is_directory=True)

    listed = asyncio.run(gemini().sessions(folder=alias))

    assert [info.id for info in listed] == [session]


def test_a_session_is_listed_read_back_and_goes_on_without_its_replay(script: Script) -> None:
    script.write({"say": "First."}, {"command": "ls"})
    first = _run(script, Host(), "Look around.")
    session = _session(first)

    async def read() -> tuple[Sequence[Any], Sequence[Any], Any]:
        executor = gemini()
        return (
            await executor.sessions(folder=script.project),
            await executor.folders(),
            await executor.transcript(session, script.project),
        )

    listed, folders, transcript = asyncio.run(read())

    assert [(info.id, info.title) for info in listed] == [(session, "Look around.")]
    assert os.path.normcase(listed[0].folder) == os.path.normcase(script.project)
    assert [os.path.normcase(folder.folder) for folder in folders] == [
        os.path.normcase(script.project)
    ]
    assert [(entry.kind, entry.by) for entry in transcript.entries] == [
        ("text", "user"),
        ("text", "harness"),
        ("tool", "harness"),
        ("result", "harness"),
    ]
    assert transcript.entries[2].tool == "run_shell_command"
    assert transcript.entries[3].text == "done"

    script.write({"say": "Again."})
    again = _run(script, Host(), "Go on.", spec=ExecutorSpec(script.project, resume=session))

    assert _session(again) == session
    assert script.sent("session/load")[-1]["sessionId"] == session
    assert Said("First.") not in again.events, "the replay is not said again"
    assert _finished(again).text == "Again."
    asyncio.run(asyncio.sleep(0))
    entries = asyncio.run(gemini().transcript(session, script.project)).entries
    assert [entry.text for entry in entries if entry.by == "user"] == ["Look around.", "Go on."]


def test_a_fork_and_another_accounts_session_go_on_in_copies(
    script: Script, tmp_path: Path
) -> None:
    script.write({"say": "Original."})
    session = _session(_run(script, Host(), "Start."))

    forked = _run(
        script, Host(), "Branch.", spec=ExecutorSpec(script.project, resume=session, fork=True)
    )
    assert _session(forked) not in ("", session)

    other = tmp_path / ".gemini-work"
    (other / ".gemini").mkdir(parents=True)
    (other / ".gemini" / "oauth_creds.json").write_text("{}", encoding="utf-8")
    spec = ExecutorSpec(script.project, resume=session, resume_from=script.state)

    async def scenario() -> Host:
        host = Host()
        instance = await gemini(home=other).start(spec, host)
        try:
            await instance.prompt("Go on there.")
        finally:
            await instance.stop()
        return host

    there = asyncio.run(scenario())

    copy = _session(there)
    assert copy not in ("", session)
    entries = asyncio.run(gemini(home=other).transcript(copy, script.project)).entries
    assert [entry.text for entry in entries if entry.by == "user"] == ["Start.", "Go on there."]
    assert script.received()[-1]["method"] != "", "the other account ran in its own home"
    homes = [line["home"] for line in script.received() if "home" in line]
    assert os.path.normcase(str(other)) in [os.path.normcase(home) for home in homes]


def test_a_session_is_deleted_with_what_it_kept_but_never_while_it_runs(script: Script) -> None:
    script.write({"say": "Hi."})
    session = _session(_run(script, Host(), "Hello."))
    executor = gemini()
    found = plugin_module("home").GeminiHome(script.state).find(session)
    assert found is not None
    logs = found.parent.parent / "logs"
    logs.mkdir()
    (logs / f"session-{session}.jsonl").write_text("{}", encoding="utf-8")

    asyncio.run(executor.delete_session(session, script.project))

    assert not found.exists()
    assert not (logs / f"session-{session}.jsonl").exists()
    with pytest.raises(ExecutorError, match="no longer keeps"):
        asyncio.run(executor.delete_session(session, script.project))

    async def running() -> None:
        host = Host()
        instance = await gemini().start(ExecutorSpec(script.project), host)
        try:
            with pytest.raises(ExecutorError, match="runs in Atlas"):
                await executor.delete_session(_session(host), script.project)
        finally:
            await instance.stop()

    asyncio.run(running())


def test_its_models_and_commands_come_from_a_session_it_opens(script: Script) -> None:
    async def scenario() -> tuple[
        Sequence[ModelChoice], Sequence[SlashCommand], Host, Sequence[SlashCommand]
    ]:
        executor = gemini()
        models = await executor.models()
        before = await executor.commands(folder=script.project)
        host = Host()
        instance = await executor.start(ExecutorSpec(script.project, model="gemini-2.5-pro"), host)
        try:
            for _ in range(100):
                if any(isinstance(event, CommandsChanged) for event in host.events):
                    break
                await asyncio.sleep(0.02)
            await instance.set_model("gemini-3.5-flash", None)
        finally:
            await instance.stop()
        return models, before, host, await executor.commands(folder=script.project)

    models, before, host, after = asyncio.run(scenario())

    assert [model.id for model in models] == ["auto", "gemini-2.5-pro", "gemini-3.5-flash"]
    assert models[0].description == "Lets Gemini choose."
    assert before == []
    assert host.events[0] == Started(_session(host), model="gemini-2.5-pro"), "the model asked for"
    changed = next(event for event in host.events if isinstance(event, CommandsChanged))
    assert changed.commands[0] == SlashCommand(
        "memory", "Manage memory.", "show|refresh", builtin=True
    )
    assert after == list(changed.commands)
    assert [sent["modelId"] for sent in script.sent("session/set_model")] == [
        "gemini-2.5-pro",
        "gemini-3.5-flash",
    ]


def test_files_go_as_images_and_paths_in_folders_it_may_read(
    script: Script, tmp_path: Path
) -> None:
    attachments = tmp_path / "attachments"
    attachments.mkdir()
    image = attachments / "shot.png"
    image.write_bytes(b"\x89PNG fake")
    paper = attachments / "paper.pdf"
    paper.write_bytes(b"%PDF")
    script.write({"say": "Seen."})

    async def scenario() -> None:
        spec = ExecutorSpec(script.project, directories=(attachments,))
        instance = await gemini().start(spec, Host())
        try:
            files = [
                ExecutorFile("shot.png", "image/png", image),
                ExecutorFile("paper.pdf", "application/pdf", paper),
            ]
            await instance.prompt("Look.", files=files)
        finally:
            await instance.stop()

    asyncio.run(scenario())

    assert script.received()[0]["argv"] == ["--acp", "--include-directories", str(attachments)]
    blocks = script.sent("session/prompt")[0]["prompt"]
    assert [block["type"] for block in blocks] == ["image", "text", "text"]
    assert blocks[0]["mimeType"] == "image/png"
    assert str(paper) in blocks[1]["text"]
    assert blocks[2]["text"] == "Look."


def test_detection_reads_the_version_and_the_login_from_its_files(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = asyncio.run(gemini().detect())
    assert (found.installed, found.version, found.supported) == (True, "0.65.0", True)
    assert found.account is not None
    assert (found.account.logged_in, found.account.login) == (True, "user@example.com")

    monkeypatch.setenv("FAKE_GEMINI_VERSION", "0.40.2")
    old = asyncio.run(gemini().detect())
    assert not old.supported
    assert old.problem.startswith("Gemini CLI 0.40.2 is older than 0.65.0")

    missing = asyncio.run(gemini(None).detect())
    assert not missing.installed
    assert "npm install -g @google/gemini-cli" in missing.problem


def test_the_account_is_read_from_its_files_as_they_are() -> None:
    account_of = plugin_module("protocol").account_of
    google = {"security": {"auth": {"selectedType": "oauth-personal"}}}
    keyed = {"security": {"auth": {"selectedType": "gemini-api-key"}}}
    who = {"active": "user@example.com"}

    assert account_of(google, who, True, {}).login == "user@example.com"
    assert not account_of(google, who, False, {}).logged_in, "no token kept"
    assert not account_of(keyed, {}, False, {}).logged_in, "an API key it does not have"
    assert account_of(keyed, {}, False, {"GEMINI_API_KEY": "k"}).login == "API key"
    assert account_of({}, {}, False, {"GEMINI_API_KEY": "k"}).logged_in


def test_log_in_goes_through_its_own_login(script: Script, tmp_path: Path) -> None:
    other = tmp_path / ".gemini-work"

    account = asyncio.run(gemini(home=other).log_in(""))

    assert (account.logged_in, account.login) == (True, "other@example.com")
    assert script.sent("authenticate") == [{"methodId": "oauth-personal"}]
    assert (other / ".gemini" / "oauth_creds.json").is_file()


def _google(buckets: list[dict[str, Any]], seen: list[httpx2.Request]) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.url.path.endswith(":loadCodeAssist"):
            return httpx2.Response(
                200, json={"currentTier": {"id": "free-tier"}, "cloudaicompanionProject": "p-1"}
            )
        return httpx2.Response(200, json={"buckets": buckets})

    return httpx2.MockTransport(handler)


def test_the_plans_limits_are_googles_quota_read_with_its_token(script: Script) -> None:
    seen: list[httpx2.Request] = []
    buckets = [
        {
            "modelId": "gemini-2.5-pro",
            "remainingFraction": 0.75,
            "resetTime": "2026-10-22T00:00:00Z",
        },
        {"modelId": "gemini-2.5-pro", "remainingFraction": 0.1, "tokenType": "TOKENS"},
        {"modelId": "gemini-3.5-flash", "remainingFraction": 1},
    ]

    usage = asyncio.run(gemini(transport=_google(buckets, seen)).usage())

    assert usage.plan == "free"
    assert usage.windows == (
        UsageWindow(
            "gemini-2.5-pro", "gemini-2.5-pro", 25.0, "2026-10-22T00:00:00Z", model="gemini-2.5-pro"
        ),
        UsageWindow("gemini-3.5-flash", "gemini-3.5-flash", 0.0, model="gemini-3.5-flash"),
    )
    assert [request.headers["authorization"] for request in seen] == ["Bearer token"] * 2
    assert json.loads(seen[1].content) == {"project": "p-1"}


def test_the_limits_are_not_read_with_a_token_that_expired_or_an_api_key(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[httpx2.Request] = []
    script.log_in(seconds=-10)
    expired = asyncio.run(gemini(transport=_google([], seen)).usage())
    assert "renews its login" in expired.unavailable
    assert seen == [], "Atlas never renews its token"

    settings = {"security": {"auth": {"selectedType": "gemini-api-key"}}}
    (script.state / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    keyed = asyncio.run(gemini(transport=_google([], seen)).usage())
    assert "API key" in keyed.unavailable


def test_after_a_turn_the_limits_are_told(script: Script) -> None:
    seen: list[httpx2.Request] = []
    buckets = [{"modelId": "gemini-2.5-pro", "remainingFraction": 0.5}]
    script.write({"say": "Done."})

    async def scenario() -> Host:
        host = Host()
        instance = await gemini(transport=_google(buckets, seen)).start(
            ExecutorSpec(script.project), host
        )
        try:
            await instance.prompt("Go.")
            for _ in range(100):
                if any(isinstance(event, UsageChanged) for event in host.events):
                    break
                await asyncio.sleep(0.02)
        finally:
            await instance.stop()
        return host

    host = asyncio.run(scenario())

    changed = next(event for event in host.events if isinstance(event, UsageChanged))
    assert [(window.id, window.used) for window in changed.windows] == [("gemini-2.5-pro", 50.0)]


def test_connectors_are_its_settings_and_its_extensions(script: Script) -> None:
    settings = json.loads((script.state / "settings.json").read_text(encoding="utf-8"))
    settings["mcpServers"] = {"docs": {"command": "docs"}, "Github": {"httpUrl": "https://x"}}
    (script.state / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    extension = script.state / "extensions" / "notes"
    extension.mkdir(parents=True)
    (extension / "gemini-extension.json").write_text(
        json.dumps({"name": "notes", "mcpServers": {"notes": {}, "docs": {}}}), encoding="utf-8"
    )
    (script.state / "mcp-server-enablement.json").write_text(
        json.dumps({"github": {"enabled": False}}), encoding="utf-8"
    )

    found = asyncio.run(gemini().connectors())

    assert sorted(found, key=lambda connector: connector.name) == [
        Connector("Github", "disabled", "user"),
        Connector("docs", "pending", "user"),
        Connector("notes", "pending", "plugin", "notes"),
    ]


def test_the_environment_keeps_the_users_login_and_never_a_typed_login_prompt() -> None:
    environment = plugin_module("protocol").environment
    kept = environment({"GEMINI_API_KEY": "k", "NO_BROWSER": "1", "PATH": "p"}, "/x")
    assert kept == {"GEMINI_API_KEY": "k", "PATH": "p", "GEMINI_CLI_HOME": "/x"}
    assert environment({"GEMINI_API_KEY": "k"}, None, "vault")["GEMINI_API_KEY"] == "vault"


def test_a_personal_account_works_on_the_key_the_plugin_keeps(script: Script) -> None:
    (script.state / "oauth_creds.json").unlink()
    settings = {"security": {"auth": {"selectedType": "gemini-api-key"}}}
    (script.state / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    script.write({"say": "Keyed."})
    executor = plugin_module("executor").GeminiCli(
        FAKE, logger=logging.getLogger("t"), api_key="vault-key"
    )

    async def scenario() -> tuple[Host, Any]:
        host = Host()
        found = await executor.detect()
        instance = await executor.start(ExecutorSpec(script.project), host)
        try:
            await instance.prompt("Go.")
        finally:
            await instance.stop()
        return host, found

    host, found = asyncio.run(scenario())

    assert (found.account.logged_in, found.account.login) == (True, "API key")
    assert _finished(host).text == "Keyed."
    assert script.received()[0]["key"] == "vault-key"
    usage = asyncio.run(executor.usage())
    assert "API key" in usage.unavailable


def test_google_refusing_a_personal_login_says_to_keep_a_key(
    script: Script, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_GEMINI_NO_PERSONAL_LOGIN", "1")

    with pytest.raises(ExecutorError) as refused:
        asyncio.run(gemini().log_in(""))

    assert "https://aistudio.google.com/apikey" in str(refused.value)
    assert "GEMINI_API_KEY secret" in str(refused.value)


def test_npms_script_is_run_with_the_node_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = plugin_module("executor")
    prefix = tmp_path / "npm"
    script = prefix / executor.SCRIPT
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    (prefix / "node.exe").write_text("", encoding="utf-8")
    (prefix / "gemini.cmd").write_text("", encoding="utf-8")
    monkeypatch.setattr(executor.sys, "platform", "win32")
    monkeypatch.setattr(
        executor.shutil,
        "which",
        lambda name: str(prefix / "gemini.cmd") if name == "gemini" else None,
    )

    assert executor.installed_cli() == [str(prefix / "node.exe"), str(script)]

    (prefix / "node.exe").unlink()
    monkeypatch.setattr(
        executor.shutil,
        "which",
        lambda name: {"gemini": str(prefix / "gemini.cmd"), "node": "C:/node/node.exe"}.get(name),
    )
    assert executor.installed_cli() == ["C:/node/node.exe", str(script)]


def test_the_plugin_registers_its_executor_on_its_program_setting() -> None:
    manifest = json.loads((FOLDER / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["atlas"]["executors"] == ["gemini-cli"]
    package = sys.modules["atlas_plugin_gemini_cli"]
    assert package.program(json.dumps(FAKE)) == FAKE
    with pytest.raises(ExecutorError, match="JSON list"):
        package.program("gemini")
