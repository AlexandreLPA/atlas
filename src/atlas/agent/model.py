"""The agent loop's own data: the tools it runs and the events of a turn. Pure, no I/O.

The loop guards that watch a turn's calls live in `guards.py`; they replaced step 4's repeat guard
in step 12.
"""

from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from atlas.agent.errors import ToolSetError

if TYPE_CHECKING:  # the type only: model.py imports no other subject at run time
    from atlas.models import Attachment, Image, ModelInfo, ToolStatus, UserMessage, Wire


@dataclass(frozen=True, slots=True)
class ToolOutput:
    """A result that does more than say something: a tool search's finds become callable, and
    images go to the model."""

    text: str
    #: Deferred tools this result loads into the conversation.
    loaded: tuple[str, ...] = ()
    #: Images for the model to see, such as a picture read_file opened.
    images: "tuple[Image, ...]" = ()


@dataclass(frozen=True, slots=True)
class Tool:
    """A tool the model may call. `run` takes the parsed arguments and returns the result text."""

    name: str
    description: str
    #: The JSON Schema of the arguments.
    parameters: dict[str, Any]
    run: Callable[[dict[str, Any]], Awaitable[str | ToolOutput]]
    #: Reads and changes nothing, so it may run alongside the round's other read-only calls.
    read_only: bool = False
    #: Meant to be called again and again with the same arguments, such as polling a job.
    repeatable: bool = False
    #: Its failures are its work (a red test run, a page that is down): failing with different
    #: arguments is diagnosis, so the loop guards warn and never halt it.
    failure_tolerant: bool = False
    #: An oversized result goes whole to a spill file. A tool that pages its own output
    #: (`read_file`) turns this off, or the model would read a spill file into another spill file.
    spills: bool = True
    #: Kept out of the model's tool list until a tool search loads it into the conversation.
    deferred: bool = False
    #: Where the tool comes from: `atlas`, or an MCP server or a plugin (`mcp:github`). Tool search
    #: lists the sources it can search, and approvals will read it.
    source: str = "atlas"
    #: Every call waits for the user's approval: a tool of an MCP server marked untrusted that may
    #: write. The approval gate reads it (`atlas.trust`).
    needs_approval: bool = False
    #: What the tool keeps that must outlive a compaction, such as the plan's open items. After a
    #: compaction the loop puts it after the summary, for a deferred tool too.
    carried: Callable[[], str | None] | None = None


#: The check every call passes before its tool runs, with the parsed arguments. It returns `None`
#: to let the call run, or what the model receives instead of a result: the call ends `denied`, or
#: `unanswered` when that text is `Unanswered`. Approvals plug in here (step 7 of the roadmap); a
#: call reached through tool search passes it like any other, because in Atlas V3 a tool moved
#: behind `mcp_call` silently lost its approval.
type ToolGate = Callable[[Tool, dict[str, Any]], Awaitable[str | None]]


class Unanswered(str):
    """A gate's refusal because nobody answered its approval in time: the text the model reads,
    marked so the call ends `unanswered`, since nobody refused it."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class HookVerdict:
    """What the hooks of one event said together (step 16k), in Claude Code's terms: the loop
    reads the fields its event takes, and nothing else."""

    #: Stop what was about to happen (a prompt, a call), and why: the user or the model reads it.
    blocked: str | None = None
    #: A call must wait for the user's approval, whatever the gate would decide (`ask`). An
    #: `allow` is no field: a plugin never lowers the user's approvals.
    ask: bool = False
    #: The call's arguments, rewritten (`updatedInput`).
    arguments: dict[str, Any] | None = None
    #: What the model reads beside the prompt or the result (`additionalContext`).
    context: str = ""
    #: The tool's result, replaced (`updatedToolOutput`).
    output: str | None = None


class TurnHooks(Protocol):
    """The hooks a loop runs at its events (step 16k): Atlas's plugins' commands and Python, in
    Claude Code's events. Each call returns once every hook that matches answered, or gave up."""

    async def session_start(self) -> HookVerdict:
        """`SessionStart`: before a conversation's first message."""
        ...

    async def prompt(self, text: str) -> HookVerdict:
        """`UserPromptSubmit`: before a message of the user enters the history."""
        ...

    async def before_tool(self, name: str, arguments: dict[str, Any]) -> HookVerdict:
        """`PreToolUse`: before a call runs, and before the gate."""
        ...

    async def after_tool(
        self, name: str, arguments: dict[str, Any], result: str, failed: bool
    ) -> HookVerdict:
        """`PostToolUse` (`PostToolUseFailure` when `failed`): after a call ran."""
        ...

    async def stop(self, text: str, again: bool) -> HookVerdict:
        """`Stop`: the model answered; `blocked` asks it to go on, with why. `again` is
        Claude Code's `stop_hook_active`: this answer follows one a hook asked for."""
        ...


#: What a model reads of a user message's attachments, built before every call from the files
#: (`atlas.conversations.attachments`): the message as the model of that call, with what it takes
#: and over its wire, should get it. The stored message never changes (docs/ARCHITECTURE.md,
#: Attachments).
type AttachmentView = Callable[["UserMessage", "ModelInfo | None", "Wire"], "UserMessage"]


class Toolset:
    """The tools of a loop, which may change while Atlas runs: an MCP server connects, reloads its
    tools or goes away. The always-on tools are offered on every call; deferred ones wait for a
    tool search."""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ToolSetError(
                f"Two tools are named {tool.name}; the model's calls could not tell them apart. "
                "Rename one."
            )
        self._tools[tool.name] = tool

    def replace(self, source: str, tools: Iterable[Tool]) -> None:
        """Put `tools` in place of every tool from `source`, as when an MCP server reloads."""
        kept = {name: tool for name, tool in self._tools.items() if tool.source != source}
        previous, self._tools = self._tools, kept
        try:
            for tool in tools:
                self.add(tool)
        except ToolSetError:
            self._tools = previous
            raise

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    @property
    def always(self) -> tuple[Tool, ...]:
        return tuple(tool for tool in self._tools.values() if not tool.deferred)

    @property
    def deferred(self) -> tuple[Tool, ...]:
        return tuple(tool for tool in self._tools.values() if tool.deferred)


# -- the events of a turn ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolStarted:
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ToolProgress:
    """Something a tool reported while it ran, such as an outside executor's text or its own tool
    calls (`report_progress`). For the user's screen; the model sees only the result."""

    call_id: str
    name: str
    event: object


@dataclass(frozen=True, slots=True)
class ToolFinished:
    call_id: str
    name: str
    #: What the model receives: the result, cut and with a spill path when it was too large.
    result: str
    #: How the call ended: the stored result keeps it, and the screen colors the call by it.
    status: "ToolStatus"
    #: The file holding the whole result, when it did not fit.
    spill_path: str | None = None
    #: The deferred tools the result loaded into the conversation.
    loaded: tuple[str, ...] = ()
    #: Images that came with the result, for the model to see.
    images: "tuple[Image, ...]" = ()
    #: How long the call took, approval included; not stored.
    duration_s: float = 0.0


@dataclass(frozen=True, slots=True)
class InterimText:
    """The text of a model call that ended asking for tools, said before its first tool starts. It
    already streamed as text; this tells the screen it was interim, and a channel sends it as a
    message of its own."""

    text: str


@dataclass(frozen=True, slots=True)
class Steered:
    """A message the user sent while the turn ran entered the history, after a round of tools."""

    text: str
    #: The files it came with.
    attachments: "tuple[Attachment, ...]" = ()


@dataclass(frozen=True, slots=True)
class Compacted:
    """The older part of the conversation was replaced by a summary."""

    tokens_before: int
    tokens_after: int


class TurnOutcome(StrEnum):
    #: The model answered.
    ANSWERED = "answered"
    #: The repeat guard ended the turn.
    HALTED = "halted"
    #: A model call failed (no credit, refused, unavailable) and the turn could not go on.
    FAILED = "failed"
    #: The turn's budget ran out before the model answered (`TurnLimit`).
    LIMITED = "limited"


@dataclass(frozen=True, slots=True)
class TurnLimit:
    """A budget for a turn nobody waits on. The user's turns have none (docs/ARCHITECTURE.md, The
    agent loop); one nobody watches can replay millions of input tokens before anyone looks."""

    model_calls: int
    input_tokens: int


@dataclass(frozen=True, slots=True)
class TurnEnded:
    """The last event of a turn."""

    outcome: TurnOutcome
    #: The answer, or what Atlas tells the user about why the turn stopped.
    text: str
    #: Model calls made in the turn, compactions included.
    model_calls: int
    #: What the turn's calls cost; `None` when one of them had an unknown cost.
    cost_usd: Decimal | None
    #: The kind of model error that ended a failed turn, such as `ModelOutOfCreditError`.
    error: str | None = None
