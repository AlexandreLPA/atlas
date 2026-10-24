"""The Agent Client Protocol (ACP) from the client's side: a harness that speaks it, started as a
process, talked to over JSON-RPC on its stdio, its turns read into the SDK's events, and stopped
with every process it started (step 16p, ROADMAP: Codex, Gemini CLI e DeepSeek).

Part of the SDK since version 18, with I/O, apart from the pure `atlas.plugins.sdk`: a plugin
for a harness that speaks ACP (the Gemini CLI first, DeepSeek next) imports it instead of writing
the channel again. ACP's own schema (`agent-client-protocol`, protocol version 1) names every
field here; Atlas does not depend on its Python package, which brings pydantic and changes its
models faster than the harnesses do, and reads what comes as plain dictionaries.

- **One JSON object a line**, both ways: Atlas's requests and the harness's replies, its
  `session/update` notifications, and the requests it makes of Atlas (a permission), which Atlas
  answers. The channel is Atlas's Codex client's (`CodexServer`), made general: the error code is
  kept (`-32000` asks for a login), and a request may wait for as long as a turn lasts
  (`session/prompt` answers only when the turn ends).
- **Atlas offers no file system and no terminal** (`clientCapabilities`): the harness reads,
  writes and runs commands itself, in its own sandbox, as in its terminal. A request for one is
  answered "method not found".
- **A permission is chosen by its kind** (`allow_once`, `reject_once`), never by its id, which
  each harness names its own way.
- **What it says comes in chunks**: they are joined into one `Said` (or `Thought`) until something
  else comes, as each harness's own client shows them.
"""

import asyncio
import base64
import contextlib
import json
import os
import signal
import subprocess
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from logging import Logger
from pathlib import Path
from typing import Any, cast

from atlas.plugins.sdk import (
    Called,
    CommandsChanged,
    ExecutorError,
    ExecutorEvent,
    ExecutorFile,
    Permission,
    Returned,
    Said,
    SlashCommand,
    Thought,
)

#: The version of ACP Atlas speaks.
PROTOCOL_VERSION = 1
#: How long Atlas waits for its own requests, but a prompt, which lasts its turn.
REQUEST_SECONDS = 60.0
#: How long a closing harness has to write its session down before it is killed.
CLOSE_SECONDS = 5.0
#: The longest line read: a session's replay or a tool's output may be long.
LINE_LIMIT = 16 * 1024 * 1024
#: The error code a harness answers with when it needs a login first.
AUTH_REQUIRED = -32000
#: JSON-RPC's own codes.
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603
#: How much of a tool's result is kept for the screen.
SHOWN_CHARS = 4_000
#: The images a prompt carries as themselves; every other file goes by its path.
IMAGE_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp"})

#: Answers a request the harness makes: its method and params, and the result to write back.
type Answerer = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any] | None]]
#: Hears every notification the harness writes.
type Listener = Callable[[dict[str, Any]], None]


class AcpError(ExecutorError):
    """The harness answered a request with an error; `code` is its JSON-RPC code."""

    def __init__(self, message: str, code: int = INTERNAL_ERROR) -> None:
        super().__init__(message)
        self.code = code


class UnhandledError(ExecutorError):
    """Atlas does not take a request the harness made: answered "method not found"."""


async def _refuse(method: str, params: dict[str, Any]) -> dict[str, Any] | None:
    raise UnhandledError(f"Atlas does not offer {method}.")


class AcpAgent:
    """One harness that speaks ACP: `command` started in `folder`, with `environment`. `title`
    names it in what Atlas says of it."""

    def __init__(
        self,
        command: Sequence[str],
        folder: Path,
        *,
        title: str,
        environment: Mapping[str, str],
        logger: Logger,
        answer: Answerer = _refuse,
        listen: Listener = lambda _message: None,
        answered: Callable[[], None] = lambda: None,
    ) -> None:
        self._command = list(command)
        self._folder = folder
        self._title = title
        self._environment = dict(environment)
        self._logger = logger
        self._answer = answer
        self._listen = listen
        #: Told once an answer to one of the harness's requests is written.
        self._answered = answered
        self._process: asyncio.subprocess.Process | None = None
        self._writing = asyncio.Lock()
        self._next = 1
        self._waiting: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._asked: dict[str, asyncio.Task[None]] = {}
        self._readers: list[asyncio.Task[None]] = []
        self._stderr: list[str] = []
        #: Set once its output closed: the process ended or is ending.
        self.ended = asyncio.Event()
        #: What its `initialize` answered.
        self.initialized: dict[str, Any] = {}

    async def start(self, client: Mapping[str, Any]) -> dict[str, Any]:
        """Start the harness and its handshake, `client` being Atlas's `initialize` params.
        Raises `ExecutorError`."""
        options: dict[str, Any] = {}
        if sys.platform == "win32":
            options["creationflags"] = subprocess.CREATE_NO_WINDOW | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0
            )
        else:
            options["start_new_session"] = True
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self._command,
                cwd=self._folder,
                env=self._environment,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=LINE_LIMIT,
                **options,
            )
        except OSError as error:
            raise ExecutorError(f"{self._title} could not start: {error}") from error
        self._readers = [
            asyncio.create_task(self._pump()),
            asyncio.create_task(self._read_errors()),
        ]
        self.initialized = await self.request("initialize", dict(client))
        return self.initialized

    async def request(
        self, method: str, params: Mapping[str, Any], *, seconds: float | None = REQUEST_SECONDS
    ) -> dict[str, Any]:
        """One of Atlas's own requests, and its result; with `seconds` `None`, waited for as long
        as it takes (a prompt). Raises `ExecutorError`, `AcpError` when the harness refused."""
        request_id, self._next = self._next, self._next + 1
        waiting = asyncio.get_running_loop().create_future()
        self._waiting[request_id] = waiting
        try:
            await self._write(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": dict(params)}
            )
            async with asyncio.timeout(seconds):
                return await waiting
        except TimeoutError as error:
            raise ExecutorError(
                f"{self._title} did not answer {method} in {seconds or 0:.0f} seconds."
            ) from error
        finally:
            self._waiting.pop(request_id, None)

    async def notify(self, method: str, params: Mapping[str, Any]) -> None:
        """A notification to the harness, such as `session/cancel`. Raises `ExecutorError`."""
        await self._write({"jsonrpc": "2.0", "method": method, "params": dict(params)})

    async def close(self) -> None:
        """Close its input, give it a moment to write its session down, then stop every process
        it started."""
        process = self._process
        if process is None:
            return
        for task in self._asked.values():
            task.cancel()
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(process.wait(), CLOSE_SECONDS)
        if process.returncode is None:
            await stop_tree(process)
        for reader in self._readers:
            reader.cancel()
        await asyncio.gather(*self._readers, return_exceptions=True)

    @property
    def pid(self) -> int | None:
        return None if self._process is None else self._process.pid

    @property
    def errors(self) -> str:
        """The end of what the harness wrote on its error output."""
        return "\n".join(self._stderr[-20:])

    # -- the channel ------------------------------------------------------------------------------

    async def _pump(self) -> None:
        process = self._process
        if process is None or process.stdout is None:  # pragma: no cover - start() made them
            return
        try:
            while line := await process.stdout.readline():
                try:
                    message = cast("dict[str, Any]", json.loads(line))
                except ValueError:
                    continue
                if not isinstance(message, dict):  # pyright: ignore[reportUnnecessaryIsInstance]
                    continue
                if "method" not in message:
                    self._replied(message)
                elif "id" in message:
                    key = str(message["id"])
                    self._asked[key] = asyncio.create_task(self._handle(message))
                else:
                    self._hear(message)
        finally:
            for waiting in self._waiting.values():
                if not waiting.done():
                    waiting.set_exception(ExecutorError(f"{self._title} ended."))
            self.ended.set()

    def _hear(self, message: dict[str, Any]) -> None:
        """A notification to the listener: one it cannot read is logged and the channel goes on,
        so the turn is not lost with it."""
        try:
            self._listen(message)
        except Exception:  # a listener is the edge: its failure is logged, the reading goes on
            self._logger.exception("A notification of %s could not be read.", self._title)

    async def _handle(self, message: dict[str, Any]) -> None:
        request_id: object = message.get("id")
        method = str(message.get("method") or "")
        params = cast("dict[str, Any]", message.get("params") or {})
        try:
            result = await self._answer(method, params)
            await self._write({"jsonrpc": "2.0", "id": request_id, "result": result})
            self._answered()
        except asyncio.CancelledError:
            raise
        except UnhandledError as error:
            await self._fail(request_id, METHOD_NOT_FOUND, str(error))
        except ExecutorError as error:
            await self._fail(request_id, INTERNAL_ERROR, str(error))
        except Exception as error:  # an answerer is the edge: its failure goes back, logged
            self._logger.exception("A request of %s could not be answered.", self._title)
            await self._fail(request_id, INTERNAL_ERROR, str(error))
        finally:
            self._asked.pop(str(request_id), None)

    async def _fail(self, request_id: object, code: int, said: str) -> None:
        with contextlib.suppress(ExecutorError):
            await self._write(
                {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": said}}
            )

    def _replied(self, message: dict[str, Any]) -> None:
        raw: object = message.get("id")
        if not isinstance(raw, int):
            return
        waiting = self._waiting.get(raw)
        if waiting is None or waiting.done():
            return
        error: object = message.get("error")
        if isinstance(error, dict):
            found = cast("dict[str, Any]", error)
            code = found.get("code")
            waiting.set_exception(
                AcpError(
                    str(found.get("message") or "an unknown error"),
                    code if isinstance(code, int) else INTERNAL_ERROR,
                )
            )
        else:
            waiting.set_result(_dict(message.get("result")))

    async def _write(self, message: dict[str, Any]) -> None:
        process = self._process
        if process is None or process.stdin is None or process.stdin.is_closing():
            raise ExecutorError(f"{self._title} is no longer listening.")
        async with self._writing:
            try:
                process.stdin.write((json.dumps(message) + "\n").encode())
                await process.stdin.drain()
            except (ConnectionError, OSError) as error:
                raise ExecutorError(f"{self._title} is no longer listening: {error}") from error

    async def _read_errors(self) -> None:
        process = self._process
        if process is None or process.stderr is None:  # pragma: no cover - start() made them
            return
        while line := await process.stderr.readline():
            self._stderr.append(line.decode("utf-8", "replace").rstrip())
            del self._stderr[:-200]


async def stop_tree(process: asyncio.subprocess.Process) -> None:
    """Stop a process and every process it started."""
    if process.returncode is None:
        if sys.platform == "win32":
            killer = await asyncio.create_subprocess_exec(
                "taskkill",
                "/T",
                "/F",
                "/PID",
                str(process.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            await killer.wait()
        else:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(process.wait(), CLOSE_SECONDS)


# -- what Atlas writes ----------------------------------------------------------------------------


def initialize_params(name: str, title: str, version: str) -> dict[str, Any]:
    """Atlas's handshake: no file system and no terminal offered."""
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "clientCapabilities": {
            "fs": {"readTextFile": False, "writeTextFile": False},
            "terminal": False,
        },
        "clientInfo": {"name": name, "title": title, "version": version},
    }


def prompt_blocks(
    text: str, files: Sequence[ExecutorFile] = (), note: str = ""
) -> list[dict[str, Any]]:
    """A prompt as ACP carries it: the images as themselves, then `note` (what the plugin says of
    the files), then the user's words."""
    blocks: list[dict[str, Any]] = []
    for item in files:
        if item.media_type in IMAGE_TYPES:
            with contextlib.suppress(OSError):
                data = base64.b64encode(item.path.read_bytes()).decode("ascii")
                blocks.append({"type": "image", "mimeType": item.media_type, "data": data})
    if note:
        blocks.append({"type": "text", "text": note})
    if text.strip() or not blocks:
        blocks.append({"type": "text", "text": text})
    return blocks


def permission_outcome(options: Sequence[Mapping[str, Any]], allow: bool) -> dict[str, Any]:
    """The answer to `session/request_permission`: the option of the kind the verdict is, once
    before always, or `cancelled` when the harness offered none."""
    kinds = ("allow_once", "allow_always") if allow else ("reject_once", "reject_always")
    for kind in kinds:
        for option in options:
            if option.get("kind") == kind and option.get("optionId"):
                return {"outcome": {"outcome": "selected", "optionId": option["optionId"]}}
    return {"outcome": {"outcome": "cancelled"}}


# -- what the harness writes ----------------------------------------------------------------------


#: How each kind of ACP tool is named on the card.
_TOOLS = {
    "execute": "shell",
    "edit": "edit",
    "read": "read",
    "search": "search",
    "fetch": "fetch",
    "delete": "delete",
    "move": "move",
    "think": "think",
}
#: How a permission's kind is titled.
_ASKS = {
    "execute": "Run a command",
    "edit": "Change files",
    "delete": "Delete files",
    "move": "Move files",
    "fetch": "Fetch from the web",
    "read": "Read files",
}


@dataclass(slots=True)
class Turn:
    """What one turn's `session/update`s said so far: the words being joined, the tools called,
    and its last message, which ends it."""

    said: list[str] = field(default_factory=list[str])
    thought: list[str] = field(default_factory=list[str])
    called: set[str] = field(default_factory=set[str])
    last: str = ""

    def flush(self) -> list[ExecutorEvent]:
        """What was being joined, said."""
        events: list[ExecutorEvent] = []
        if thought := "".join(self.thought).strip():
            events.append(Thought(thought))
        if said := "".join(self.said).strip():
            events.append(Said(said))
            self.last = said
        self.thought.clear()
        self.said.clear()
        return events


def update_events(update: Mapping[str, Any], turn: Turn) -> list[ExecutorEvent]:
    """The events one `session/update` makes, with what the turn joined so far."""
    kind = update.get("sessionUpdate")
    if kind == "agent_message_chunk":
        if turn.thought:
            events = turn.flush()
            turn.said.append(block_text(update.get("content")))
            return events
        turn.said.append(block_text(update.get("content")))
        return []
    if kind == "agent_thought_chunk":
        events = turn.flush() if turn.said else []
        turn.thought.append(block_text(update.get("content")))
        return events
    if kind == "tool_call":
        return [*turn.flush(), *_called(update, turn)]
    if kind == "tool_call_update":
        events = turn.flush()
        call = str(update.get("toolCallId") or "")
        if call and call not in turn.called and update.get("title"):
            events.extend(_called(update, turn))
        status = update.get("status")
        if status in ("completed", "failed"):
            events.append(
                Returned(call, cut(content_text(update.get("content"))), status == "failed")
            )
        return events
    if kind == "available_commands_update":
        return [CommandsChanged(commands_of(update.get("availableCommands")))]
    return []


def _called(update: Mapping[str, Any], turn: Turn) -> list[ExecutorEvent]:
    call = str(update.get("toolCallId") or "")
    if not call or call in turn.called:
        return []
    turn.called.add(call)
    arguments: dict[str, Any] = {"title": str(update.get("title") or "")}
    raw: object = update.get("rawInput")
    if isinstance(raw, dict):
        arguments.update(cast("dict[str, Any]", raw))
    return [
        Called(
            call,
            tool_name(update.get("kind")),
            json.dumps(arguments, ensure_ascii=False),
            locations(update.get("locations")),
        )
    ]


def tool_name(kind: object) -> str:
    """How an ACP tool kind is named on the card."""
    return _TOOLS.get(str(kind or ""), "tool")


def locations(raw: object) -> tuple[str, ...]:
    """The paths a tool call names."""
    return tuple(str(path) for item in _list(raw) if (path := _dict(item).get("path")))


def permission_of(params: Mapping[str, Any]) -> Permission:
    """A `session/request_permission` as Atlas asks it."""
    call = _dict(params.get("toolCall"))
    kind = str(call.get("kind") or "")
    title = str(call.get("title") or "")
    shown = content_text(call.get("content"))
    return Permission(
        tool=tool_name(kind),
        summary=f"{_ASKS.get(kind, 'Use a tool')}: {title}"
        if title
        else _ASKS.get(kind, "Use a tool"),
        title=_ASKS.get(kind, "Use a tool"),
        description=cut(shown),
    )


def commands_of(raw: object) -> tuple[SlashCommand, ...]:
    """The slash commands an `available_commands_update` names."""
    found: list[SlashCommand] = []
    for item in _list(raw):
        command = _dict(item)
        if not command.get("name"):
            continue
        hint = str(_dict(command.get("input")).get("hint") or "")
        found.append(
            SlashCommand(
                str(command["name"]).lstrip("/"),
                str(command.get("description") or ""),
                hint,
                builtin=True,
            )
        )
    return tuple(found)


def block_text(raw: object) -> str:
    """The text of one content block, empty for any other kind."""
    block = _dict(raw)
    return str(block.get("text") or "") if block.get("type") == "text" else ""


def content_text(raw: object) -> str:
    """What a tool call's content says: its text, and the files its diffs change."""
    lines: list[str] = []
    for item in _list(raw):
        entry = _dict(item)
        if entry.get("type") == "content":
            if text := block_text(entry.get("content")):
                lines.append(text)
        elif entry.get("type") == "diff" and entry.get("path"):
            lines.append(f"Changed {entry['path']}")
    return "\n".join(lines)


def cut(text: str) -> str:
    """Text kept for the screen, cut where it is long."""
    return text if len(text) <= SHOWN_CHARS else text[:SHOWN_CHARS] + " […]"


def _dict(value: object) -> dict[str, Any]:
    return cast("dict[str, Any]", value) if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return cast("list[Any]", value) if isinstance(value, list) else []
