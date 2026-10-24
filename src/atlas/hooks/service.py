"""Running hooks: a plugin's commands and Python at the loop's events (step 16k).

**A command hook runs as Claude Code runs it:** the event's JSON on its stdin, in the conversation's
folder, with `CLAUDE_PROJECT_DIR`, `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PLUGIN_DATA` in its environment
and in place of `${...}` in its command and arguments. With `args` it runs as it is; without,
through bash (Git Bash on Windows, as Claude Code; PowerShell when the hook says so, or when
there is no Git Bash).

**A Python hook** (`PluginContext.register_hook`) gets the same JSON as a dict and returns the same
object, or `None`: one shape for both keeps a plugin's hooks the same whichever it writes.

**The hooks of an event run together** (Claude Code's), each with its timeout: Claude Code's for a
command (600 s, 30 s for `UserPromptSubmit`), 30 s for Python. One that fails or times out is
logged and lets the action go on, unless it says `onFailure: "block"` (Claude Code's); a
`PreToolUse` hook fails open too, as in Claude Code, since the gate still stands behind.
"""

import asyncio
import inspect
import json
import logging
import os
import re
import shutil
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from atlas.agent import HookVerdict
from atlas.hooks.model import (
    BLOCK_EXIT,
    POST_TOOL_USE,
    POST_TOOL_USE_FAILURE,
    PRE_TOOL_USE,
    SESSION_START,
    STOP,
    USER_PROMPT_SUBMIT,
    Answer,
    CommandHook,
    combined,
    from_tool_input,
    matches,
    read_answer,
    read_object,
    rule_matches,
    tool_input,
)

#: How long a Python hook may take.
PYTHON_TIMEOUT = 30.0
_WINDOWS = sys.platform == "win32"
_PLACE = re.compile(r"\$\{(CLAUDE_PROJECT_DIR|CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA)\}")
_OPTION = re.compile(r"\$\{user_config\.([A-Za-z_][A-Za-z0-9_]*)\}")

_logger = logging.getLogger("atlas.hooks")

#: A Python hook: the event's JSON, and its answer in Claude Code's JSON, or `None`.
type HookCallback = Callable[
    [dict[str, Any]], Awaitable[Mapping[str, Any] | None] | Mapping[str, Any] | None
]


@dataclass(frozen=True, slots=True)
class PluginCommand:
    """A command hook of a plugin, with where its plugin lives."""

    plugin: str
    hook: CommandHook
    root: Path
    data: Path
    #: Its plugin's `userConfig` options by key, the sensitive ones read from the vault: Claude
    #: Code's `CLAUDE_PLUGIN_OPTION_<KEY>` in the hook's environment, and `${user_config.KEY}` in
    #: the arguments of an exec-form hook (a shell-form command takes none, as in Claude Code,
    #: since a shell would read a value as more command).
    options: Mapping[str, str] = field(default_factory=dict[str, str])


@dataclass(frozen=True, slots=True)
class PluginCallback:
    """A Python hook a plugin registered."""

    plugin: str
    event: str
    matcher: str
    run: HookCallback


class Hooks:
    """The hooks of the plugins that are on."""

    def __init__(
        self, commands: Sequence[PluginCommand] = (), callbacks: Sequence[PluginCallback] = ()
    ) -> None:
        self.commands = tuple(entry for entry in commands if entry.hook.runs)
        self.callbacks = tuple(callbacks)
        #: The background hooks running (`async`), kept until they end.
        self._background: set[asyncio.Task[None]] = set()

    def __bool__(self) -> bool:
        return bool(self.commands or self.callbacks)

    async def run(
        self,
        event: str,
        payload: dict[str, Any],
        value: str = "",
        *,
        cwd: Path,
        wake: Callable[[str], None] | None = None,
    ) -> Answer:
        """Every hook of `event` that matches `value` (a tool's name, a session's source), run
        together; their answers combined. A background hook (`async`) is started and not waited
        for; one that rewakes hands `wake` what it says when it exits 2."""
        arguments = cast("Mapping[str, Any]", payload.get("tool_input") or {})
        chosen = [
            entry
            for entry in self.commands
            if entry.hook.event == event
            and matches(entry.hook.matcher, value)
            and rule_matches(entry.hook.rule, value, arguments)
        ]
        for entry in chosen:
            if entry.hook.background:
                task = asyncio.create_task(_background(entry, payload, cwd, wake))
                self._background.add(task)
                task.add_done_callback(self._background.discard)
        work: list[Awaitable[Answer]] = [
            _command(entry, payload, cwd) for entry in chosen if not entry.hook.background
        ]
        work += [
            _callback(entry, payload)
            for entry in self.callbacks
            if entry.event == event and matches(entry.matcher, value)
        ]
        if not work:
            return Answer()
        found = combined(await asyncio.gather(*work))
        if found.error:
            _logger.warning("A plugin's hook on %s failed: %s", event, found.error)
        return found


class ConversationHooks:
    """The hooks as one conversation's loop runs them (`atlas.agent.TurnHooks`): the event's JSON
    with the conversation's id, folder and mode, and the answer as the loop reads it."""

    def __init__(
        self,
        hooks: Callable[[], Hooks],
        *,
        session: str,
        cwd: Path,
        auto: Callable[[], bool] = lambda: False,
        wake: Callable[[str], None] | None = None,
    ) -> None:
        """`hooks` gives the plugins' hooks as they are now: a plugin turned on or off while the
        conversation is open changes its next event. `wake` hears what a background hook that
        rewakes says, as a notice."""
        self._hooks = hooks
        self._session = session
        self._cwd = cwd
        self._auto = auto
        self._wake = wake

    def _payload(self, event: str, **fields: Any) -> dict[str, Any]:
        return {
            "session_id": self._session,
            "transcript_path": "",
            "cwd": str(self._cwd),
            "permission_mode": "auto" if self._auto() else "default",
            "hook_event_name": event,
            **fields,
        }

    async def _run(self, event: str, value: str = "", **fields: Any) -> Answer:
        hooks = self._hooks()
        if not hooks:
            return Answer()
        payload = self._payload(event, **fields)
        return await hooks.run(event, payload, value, cwd=self._cwd, wake=self._wake)

    async def session_start(self) -> HookVerdict:
        said = await self._run(SESSION_START, "startup", source="startup")
        return HookVerdict(context=said.context)

    async def prompt(self, text: str) -> HookVerdict:
        said = await self._run(USER_PROMPT_SUBMIT, prompt=text)
        return HookVerdict(blocked=said.blocked, context=said.context)

    async def before_tool(self, name: str, arguments: dict[str, Any]) -> HookVerdict:
        shown = tool_input(name, arguments)
        said = await self._run(PRE_TOOL_USE, name, tool_name=name, tool_input=shown, tool_use_id="")
        written = None if said.arguments is None else from_tool_input(name, said.arguments, shown)
        return HookVerdict(
            blocked=said.blocked, ask=said.ask, arguments=written, context=said.context
        )

    async def after_tool(
        self, name: str, arguments: dict[str, Any], result: str, failed: bool
    ) -> HookVerdict:
        event = POST_TOOL_USE_FAILURE if failed else POST_TOOL_USE
        extra: dict[str, Any] = {"error": result} if failed else {"tool_response": result}
        said = await self._run(
            event, name, tool_name=name, tool_input=tool_input(name, arguments), **extra
        )
        return HookVerdict(context=said.context, output=said.output)

    async def stop(self, text: str, again: bool) -> HookVerdict:
        said = await self._run(STOP, stop_hook_active=again, last_assistant_message=text)
        return HookVerdict(blocked=said.blocked)


async def _background(
    entry: PluginCommand,
    payload: dict[str, Any],
    cwd: Path,
    wake: Callable[[str], None] | None,
) -> None:
    """A background hook: run, its answer not read; one that rewakes and exits 2, whatever its
    event, hands `wake` its stderr, as Claude Code's `asyncRewake` wakes the model."""
    said = await _command(entry, payload, cwd, wake=wake if entry.hook.rewake else None)
    if said.error:
        _logger.warning("A plugin's background hook failed: %s", said.error)


async def _command(
    entry: PluginCommand,
    payload: dict[str, Any],
    cwd: Path,
    *,
    wake: Callable[[str], None] | None = None,
) -> Answer:
    hook = entry.hook
    places = {
        "CLAUDE_PROJECT_DIR": cwd.as_posix(),
        "CLAUDE_PLUGIN_ROOT": entry.root.as_posix(),
        "CLAUDE_PLUGIN_DATA": entry.data.as_posix(),
    }

    def fill(text: str) -> str:
        return _PLACE.sub(lambda m: places[m.group(1)], text)

    def fill_argument(text: str) -> str:
        return _OPTION.sub(lambda m: entry.options.get(m.group(1), ""), fill(text))

    command = fill(hook.command)
    argv = (
        [command, *(fill_argument(arg) for arg in hook.args)]
        if hook.args is not None
        else _shell(hook.shell, command)
    )
    options = {f"CLAUDE_PLUGIN_OPTION_{key.upper()}": value for key, value in entry.options.items()}
    environment = {**os.environ, **places, **options}
    seconds = hook.seconds()
    try:
        entry.data.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd if cwd.is_dir() else None,
            env=environment,
        )
    except OSError as error:
        return _failed(entry, f"{entry.plugin}'s hook `{hook.command}` could not start: {error}")
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(json.dumps(payload).encode("utf-8")), seconds
        )
    except TimeoutError:
        process.kill()
        await process.wait()
        return _failed(
            entry, f"{entry.plugin}'s hook `{hook.command}` took more than {seconds:g} s."
        )
    except asyncio.CancelledError:
        process.kill()
        raise
    code = process.returncode if process.returncode is not None else 1
    if wake is not None and code == BLOCK_EXIT:
        woke = stderr.decode("utf-8", errors="replace").strip() or "A hook woke Atlas."
        wake(f"[Hook of {entry.plugin}]\n{woke}")
    said = read_answer(
        hook.event,
        code,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )
    if said.error is not None:
        return _failed(entry, f"{entry.plugin}'s hook `{hook.command}`: {said.error}")
    return said


def _failed(entry: PluginCommand, why: str) -> Answer:
    """A hook that failed: it blocks only when it says so and its event can be blocked."""
    if entry.hook.fail_closed and entry.hook.event in (PRE_TOOL_USE, USER_PROMPT_SUBMIT, STOP):
        return Answer(blocked=why, error=why)
    return Answer(error=why)


async def _callback(entry: PluginCallback, payload: dict[str, Any]) -> Answer:
    try:
        said: object = entry.run(dict(payload))
        if inspect.isawaitable(said):
            said = await asyncio.wait_for(cast("Awaitable[object]", said), PYTHON_TIMEOUT)
    except TimeoutError:
        return Answer(
            error=f"{entry.plugin}'s {entry.event} hook took more than {PYTHON_TIMEOUT:g} s."
        )
    except Exception as error:  # a plugin's own code: its failure is told and logged
        _logger.warning("A plugin's hook failed.", extra={"plugin": entry.plugin}, exc_info=True)
        return Answer(
            error=f"{entry.plugin}'s {entry.event} hook raised {type(error).__name__}: {error}"
        )
    if said is None:
        return Answer()
    if isinstance(said, Mapping):
        return read_object(entry.event, cast("Mapping[str, Any]", said))
    return Answer(
        error=f"{entry.plugin}'s {entry.event} hook returned something other than a dict."
    )


def _shell(kind: str, command: str) -> list[str]:
    """Claude Code's shells: bash (Git Bash on Windows), or PowerShell when the hook says so or
    Windows has no Git Bash."""
    if kind != "powershell":
        bash = _git_bash() if _WINDOWS else shutil.which("bash")
        if bash is not None:
            return [bash, "-c", command]
        if not _WINDOWS:
            return [shutil.which("sh") or "/bin/sh", "-c", command]
    return [shutil.which("pwsh") or "powershell.exe", "-NoProfile", "-Command", command]


def _git_bash() -> str | None:
    """Git for Windows's bash, never WSL's `bash.exe` in System32, as Claude Code looks."""
    git = shutil.which("git")
    if git is None:
        return None
    for candidate in (
        Path(git).parent.parent / "bin" / "bash.exe",
        Path(git).parent.parent.parent / "bin" / "bash.exe",
    ):
        if candidate.is_file():
            return str(candidate)
    return None


def shell_command(command: str) -> list[str]:
    """How a command of a plugin's runs through a shell: a shell-form hook's, a monitor's."""
    return _shell("bash", command)
