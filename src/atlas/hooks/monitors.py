"""Monitors: a plugin's commands that run in the background of a conversation, each line they
print reaching the model as a notice (step 16k).

Claude Code's monitors as its plugin reference has them on 20/10/2026 (`monitors/monitors.json`):

- **One per conversation**, started from its first turn (`always`), or the first time the skill it
  waits for is used (`on-skill-invoke:<skill>`); only the user's own conversations run them, as
  Claude Code runs them only in interactive sessions. A subagent, a routine's run and the review
  have none.
- **The command runs as a hook's does**, through bash (Git Bash on Windows, PowerShell without
  it), in the conversation's folder, with `${CLAUDE_PLUGIN_ROOT}` and `${CLAUDE_PLUGIN_DATA}` in
  its text and nothing else in its environment: no option, as in Claude Code.
- **Its lines are gathered**, a choice of Atlas's (new): every line is a notice in Claude Code,
  and a notice starts a turn in Atlas, which costs a model call; so the lines that come within a
  second reach the model as one notice, cut at 50 lines and 4,000 characters.
- **It stops with the conversation**, with every process it started, and when the plugins
  change; a monitor that ends by itself is said once, and not started again in that
  conversation.
"""

import asyncio
import contextlib
import logging
import os
import re
import signal
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from atlas.hooks.service import shell_command

#: Lines that come within this many seconds reach the model as one notice.
GATHER_SECONDS = 1.0
#: The most of a gathering one notice carries.
NOTICE_LINES = 50
NOTICE_CHARS = 4_000
_PLACE = re.compile(r"\$\{(CLAUDE_PLUGIN_ROOT|CLAUDE_PLUGIN_DATA)\}")
#: `always`: from the conversation's first turn.
ALWAYS = "always"

_logger = logging.getLogger("atlas.hooks")
#: No window on Windows, and a process group of its own, as `run_command`'s.
_CREATION_FLAGS = (
    subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    if sys.platform == "win32"
    else 0
)
#: How long a stopped monitor has to end before it is killed.
_GRACE_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class PluginMonitor:
    """A monitor of a plugin that is on, with where its plugin lives."""

    plugin: str
    name: str
    command: str
    description: str
    #: `always`, or the skill whose first use starts it (`render`, or `video:render`).
    when: str
    root: Path
    data: Path

    @property
    def key(self) -> str:
        return f"{self.plugin}:{self.name}"

    def waits_for(self, skill: str) -> bool:
        """Whether using `skill` starts it: its own name, or its name under the plugin's."""
        if self.when == ALWAYS:
            return False
        return self.when in (skill, skill.removeprefix(f"{self.plugin}:"))


class ConversationMonitors:
    """One conversation's monitors: started when their time comes, their lines handed to
    `notice`, stopped with it."""

    def __init__(
        self,
        notice: Callable[[str], None],
        cwd: Callable[[], Path],
        *,
        gather: float = GATHER_SECONDS,
    ) -> None:
        self._notice = notice
        self._cwd = cwd
        self._gather = gather
        self._running: dict[str, asyncio.Task[None]] = {}
        #: Those started once in this conversation, running or not: never started again.
        self._started: set[str] = set()

    def start(self, monitors: Sequence[PluginMonitor], *, skill: str | None = None) -> None:
        """Start those whose time came: the `always` ones, or those that wait for `skill`."""
        for monitor in monitors:
            wanted = monitor.when == ALWAYS if skill is None else monitor.waits_for(skill)
            if not wanted or monitor.key in self._started:
                continue
            self._started.add(monitor.key)
            self._running[monitor.key] = asyncio.create_task(self._run(monitor))

    @property
    def running(self) -> list[str]:
        return [key for key, task in self._running.items() if not task.done()]

    async def stop(self) -> None:
        """Stop every monitor, and let each start again when its time comes."""
        tasks = list(self._running.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._running.clear()
        self._started.clear()

    async def _run(self, monitor: PluginMonitor) -> None:
        places = {
            "CLAUDE_PLUGIN_ROOT": monitor.root.as_posix(),
            "CLAUDE_PLUGIN_DATA": monitor.data.as_posix(),
        }
        command = _PLACE.sub(lambda m: places[m.group(1)], monitor.command)
        cwd = self._cwd()
        try:
            monitor.data.mkdir(parents=True, exist_ok=True)
            process = await asyncio.create_subprocess_exec(
                *shell_command(command),
                stdin=subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=cwd if cwd.is_dir() else None,
                env=dict(os.environ),
                start_new_session=sys.platform != "win32",
                creationflags=_CREATION_FLAGS,
            )
        except OSError as error:
            self._notice(f"[Monitor {monitor.key} could not start: {error}]")
            return
        _logger.info("A monitor started.", extra={"monitor": monitor.key})
        try:
            await self._read(monitor, process)
            code = await process.wait()
            self._notice(f"[Monitor {monitor.key} ended (exit code {code}).]")
        finally:
            await asyncio.shield(_stop_tree(process))

    async def _read(self, monitor: PluginMonitor, process: asyncio.subprocess.Process) -> None:
        stream = process.stdout
        if stream is None:  # pragma: no cover - the pipe was asked for
            return
        loop = asyncio.get_running_loop()
        lines: list[str] = []
        due: float | None = None
        reading: asyncio.Task[bytes] | None = None
        try:
            while True:
                if reading is None:
                    reading = asyncio.create_task(stream.readline())
                timeout = None if due is None else max(0.0, due - loop.time())
                done, _ = await asyncio.wait({reading}, timeout=timeout)
                if done:
                    line = reading.result()
                    reading = None
                    if not line:
                        break
                    text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                    if text.strip():
                        lines.append(text)
                        due = due or loop.time() + self._gather
                    continue
                self._said(monitor, lines)
                lines, due = [], None
        finally:
            if reading is not None:
                reading.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reading
            if lines:
                self._said(monitor, lines)

    def _said(self, monitor: PluginMonitor, lines: list[str]) -> None:
        kept = lines[:NOTICE_LINES]
        text = "\n".join(kept)
        if len(text) > NOTICE_CHARS:
            text = text[: NOTICE_CHARS - 1] + "…"
        more = (
            f"\n[{len(lines) - len(kept)} more lines left out.]" if len(lines) > len(kept) else ""
        )
        self._notice(f"[Monitor {monitor.key}: {monitor.description}]\n{text}{more}")


async def _stop_tree(process: asyncio.subprocess.Process) -> None:
    """Stop a monitor and everything it started, as `run_command` stops a command's tree."""
    if process.returncode is not None:
        return
    if sys.platform == "win32":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/PID",
            str(process.pid),
            "/T",
            "/F",
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        await killer.wait()
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(process.wait(), _GRACE_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), _GRACE_SECONDS)
