"""The agent loop: one turn runs the model, the tools it asks for, and the model again, until the
model answers.

Decided on 07/10/2026, from Atlas V3:

- **No cap on a turn's steps.** A cap silently cuts a task in the middle, and V3's cap made seven
  turns of one session spend 9.4 million tokens and end with nothing written. The repeat guard,
  clearing and compaction hold the rest, the call ledger records every call, and the user can stop
  a turn at any moment.
- **Before every call** the copy sent to the model is repaired, old tool results are cleared once
  it grows, and the stored history is compacted once the copy nears the model's window
  (`atlas.agent.history`).
- **Read-only tools of one round run together**; a tool that changes something runs alone.
  Results go back in the order of the calls.
- **A model's failure ends the turn with its reason**, never with an exception: no credit, a
  refused request, a silent provider. A request refused because the conversation does not fit is
  compacted once and sent again.
- **Stopping** (the caller closes the event stream, or cancels its task) leaves the history well
  formed: every call of the reply in flight gets a result, the unfinished ones a note that they may
  have partly run.

Decided on 09/10/2026, with the memory (docs/ARCHITECTURE.md, Memory):

- **The instructions are asked for when a conversation starts and after each compaction**, and kept
  as its first message. Between the two they stay as they were, so the provider's cache holds while
  the memory they carry changes on disk.
- **A turn nobody waits on may have a budget** (`TurnLimit`).

Decided on 13/10/2026, with the bots (docs/ARCHITECTURE.md, Bots):

- **A conversation that lasts forever reads its instructions again when what they show
  changed** (`refresh_instructions`), once, at the start of a turn, since a bot's conversation
  never starts again to pick up what changed. Atlas compares the text itself rather than a
  fingerprint of it, which is the same check with nothing to keep in step. Unchanged, the stored
  instructions are reused byte for byte and the provider's cache holds.

Decided on 10/10/2026, with the server (docs/ARCHITECTURE.md, Server):

- **A message sent while a turn runs steers it**: after a round of tools, the turn asks `steering`
  for what the user sent meanwhile, and each message enters the history as the user's before the
  next model call. What arrives after the last round is the caller's to send as the next turn.

Decided on 20/10/2026, with the plugins' hooks (docs/ARCHITECTURE.md, Plugins; step 16k):

- **The loop runs its hooks at Claude Code's events** (`TurnHooks`): `SessionStart` and
  `UserPromptSubmit` before the user's message enters the history, `PreToolUse` before the gate,
  `PostToolUse` after the call, `Stop` when the model answers. A blocked prompt ends the turn with
  why, and never enters the history; a blocked call is refused with why; `ask` sends the call
  through the user's approval; a `Stop` that blocks makes the model go on, at most 3 times a turn,
  Claude Code's `stop_hook_active` set after the first.
- **What a hook adds (`additionalContext`) is a note of Atlas's**, after the user's words or the
  tool's result.
"""

import asyncio
import contextvars
import dataclasses
import json
import logging
import math
import time
from asyncio import FIRST_COMPLETED
from collections.abc import AsyncGenerator, Callable, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

from atlas.agent.guards import (
    Decision,
    LoopGuard,
    ToolTraits,
    blocked,
    guidance,
    halted,
    is_failure,
)
from atlas.agent.history import (
    CLEAR_AT_TOKENS,
    COMPACT_AT,
    CUT_HEAD_CHARS,
    CUT_TAIL_CHARS,
    ROUND_CHARS,
    SPILL_CHARS,
    STOPPED,
    TAIL_TOKENS,
    cleared,
    cut,
    estimate_tokens,
    input_window,
    is_context_overflow,
    plan_compaction,
    repaired,
    summary_message,
    summary_request,
)
from atlas.agent.model import (
    AttachmentView,
    Compacted,
    InterimText,
    Steered,
    Tool,
    ToolFinished,
    ToolGate,
    ToolOutput,
    ToolProgress,
    Toolset,
    ToolStarted,
    TurnEnded,
    TurnHooks,
    TurnLimit,
    TurnOutcome,
    Unanswered,
)
from atlas.agent.store import SpillFolder
from atlas.models import (
    TOOL_SEARCH,
    AssistantMessage,
    Cost,
    Finish,
    Message,
    ModelClient,
    ModelReply,
    ModelRequest,
    ModelRequestError,
    ModelsError,
    ReasoningDelta,
    ReplyDone,
    SystemMessage,
    TextDelta,
    ToolCall,
    ToolResult,
    ToolSpec,
    ToolStatus,
    UserMessage,
    Wire,
)

type TurnEvent = (
    TextDelta
    | ReasoningDelta
    | InterimText
    | ToolStarted
    | ToolProgress
    | ToolFinished
    | Steered
    | Compacted
    | TurnEnded
)

#: The call a running tool belongs to, and where its progress goes; set per call, in its task.
_reporting: contextvars.ContextVar[tuple["asyncio.Queue[ToolProgress]", ToolCall]] = (
    contextvars.ContextVar("atlas_tool_progress")
)


def report_progress(event: object) -> None:
    """From inside a running tool: pass `event` on to the turn's events, as a `ToolProgress`.
    Outside a tool call it does nothing."""
    target = _reporting.get(None)
    if target is not None:
        queue, call = target
        queue.put_nowait(ToolProgress(call.id, call.name, event))


def current_call_id() -> str | None:
    """The id of the tool call this runs inside, so what it asks the user (an approval) names the
    call it holds; `None` outside a tool call."""
    target = _reporting.get(None)
    return None if target is None else target[1].id


#: Compactions in one turn: the loop stops compacting when it keeps not helping.
MAX_COMPACTIONS_PER_TURN = 2
#: Failed compactions in a row after which the loop stops trying on its own, or a session could
#: fail thousands of times in a row.
MAX_COMPACTION_FAILURES = 3
#: Empty replies (no text, no tool call) asked again before the turn gives up.
EMPTY_REPLY_RETRIES = 2
#: Times a turn goes on because a `Stop` hook asked.
STOP_HOOK_CONTINUES = 3
#: A result cut by the round's budget must still be long enough to be worth cutting.
_CUTTABLE_CHARS = CUT_HEAD_CHARS + CUT_TAIL_CHARS + 500

_logger = logging.getLogger("atlas.agent")


@dataclass
class _Turn:
    """What one turn has spent and done so far."""

    model_calls: int = 0
    input_tokens: int = 0
    known_usd: Decimal = Decimal(0)
    unknown_cost: bool = False
    compactions: int = 0
    empty_replies: int = 0
    overflow_retried: bool = False
    stop_continues: int = 0

    def paid(self, reply: ModelReply) -> None:
        self.model_calls += 1
        if reply.usage is not None:
            self.input_tokens += reply.usage.input_tokens
        cost: Cost = reply.cost
        if cost.usd is None:
            self.unknown_cost = True
        else:
            self.known_usd += cost.usd

    def spent(self, limit: TurnLimit | None) -> bool:
        return limit is not None and (
            self.model_calls >= limit.model_calls or self.input_tokens >= limit.input_tokens
        )

    def ended(self, outcome: TurnOutcome, text: str, error: str | None = None) -> TurnEnded:
        usd = None if self.unknown_cost else self.known_usd
        return TurnEnded(outcome, text, self.model_calls, usd, error)


@dataclass(frozen=True, slots=True)
class _Anchor:
    """The provider's count of a conversation at one call."""

    history: list[Message]
    #: The history's length when the call was sent.
    length: int
    tokens: int


@dataclass
class _Round:
    """A tool round's state, shared with the loop."""

    loaded: frozenset[str] = frozenset()
    results: dict[str, ToolFinished] = field(default_factory=dict[str, ToolFinished])
    chars: int = 0
    halt: Decision | None = None


#: What the model reads when a `Stop` hook asks it to go on.
_HOOK_GOES_ON = "[Atlas: a plugin's hook asks you to go on before you finish: {}]"


def _noted(message: UserMessage, notes: Sequence[str]) -> UserMessage:
    """The message with what the hooks added, as a note of Atlas's after the user's words."""
    said = [note for note in notes if note.strip()]
    if not said:
        return message
    added = "\n".join(f"[Atlas: a plugin's hook adds: {note}]" for note in said)
    return dataclasses.replace(message, text=f"{message.text}\n\n{added}")


def _arguments(call: ToolCall) -> dict[str, Any]:
    """A call's arguments as a JSON object, or empty when the model wrote something else."""
    try:
        parsed: object = json.loads(call.arguments or "{}")
    except ValueError:
        return {}
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}


class AgentLoop:
    """Runs turns on one model with one set of tools."""

    def __init__(
        self,
        client: ModelClient,
        tools: Toolset | Sequence[Tool] = (),
        *,
        spill: SpillFolder | None = None,
        gate: ToolGate | None = None,
        instructions: Callable[[], str] | None = None,
        purpose: str = "turn",
        limit: TurnLimit | None = None,
        attended: bool = True,
        refresh_instructions: bool = False,
        attachments: AttachmentView | None = None,
        hooks: TurnHooks | None = None,
    ) -> None:
        """`instructions` builds the conversation's system prompt, asked for when it starts and
        after each compaction, and also at the start of a turn with `refresh_instructions`,
        which replaces the stored ones when they differ; `purpose` names the turn's calls in
        the call ledger; `limit` is a budget for a turn nobody waits on; `attended` is someone
        watching, so the loop guards only warn: a routine is not, and they
        halt it; `attachments` builds what each call's model reads of the user's attached
        files. Without it a message's attachments reach the model as their names alone. `hooks`
        are the plugins' hooks, run at the turn's events."""
        self._hooks = hooks
        self._tools = tools if isinstance(tools, Toolset) else Toolset(tools)
        self._client = client
        self._spill = spill
        self._gate = gate
        self._instructions = instructions
        self._purpose = purpose
        self._limit = limit
        self._attended = attended
        self._refresh_instructions = refresh_instructions
        self._attachments = attachments
        self._window = input_window(client.info)
        #: What the provider counted for the last call, and where that call's history ended.
        self._anchor: _Anchor | None = None
        self._compaction_failures = 0
        if self._window is None:
            _logger.warning(
                "The input window of %s at %s is unknown, so the conversation is compacted only "
                "when the provider says it does not fit. Declare limit.context for it in "
                "models.toml.",
                client.model,
                client.provider.name,
            )

    @property
    def client(self) -> ModelClient:
        return self._client

    @property
    def window(self) -> int | None:
        """The model's input window, `None` when unknown."""
        return self._window

    @property
    def last_input_tokens(self) -> int | None:
        """What the provider counted for the last call, `None` before any (the conversation's
        current size, never a sum over its calls)."""
        return None if self._anchor is None else self._anchor.tokens

    def switch(self, client: ModelClient) -> None:
        """Go on with another model, between turns, keeping the tools and their state. The
        context is measured again from the new provider's count, since two providers count the same
        history differently."""
        self._client = client
        self._window = input_window(client.info)
        self._anchor = None
        self._compaction_failures = 0

    async def run(
        self,
        history: list[Message],
        text: str | UserMessage,
        *,
        scope: str | None = None,
        steering: Callable[[], Sequence[str | UserMessage]] | None = None,
    ) -> AsyncGenerator[TurnEvent]:
        """One turn. `history` is the conversation, changed in place: the user's message (its text,
        or the message with its attachments), every reply, every tool result and a compaction's
        summary land in it. `steering` gives, after each round of tools, the messages the user
        sent since; they enter the history.

        To stop a turn, close this stream (`contextlib.aclosing`) or cancel the task reading it.
        A bare `break` leaves the stream open until the interpreter finalizes it, and the stopped
        calls' results reach the history late; history repair still puts them in place.
        """
        if self._instructions is not None:
            if not (history and isinstance(history[0], SystemMessage)):
                history.insert(0, SystemMessage(self._instructions()))
            elif self._refresh_instructions:
                fresh = self._instructions()
                if history[0].text != fresh:
                    history[0] = SystemMessage(fresh)
                    self._anchor = None  # the provider has not counted this history yet
        message = text if isinstance(text, UserMessage) else UserMessage(text)
        turn, guard = _Turn(), LoopGuard(attended=self._attended)
        if self._hooks is not None:
            notes: list[str] = []
            if not any(isinstance(item, UserMessage) for item in history):
                notes.append((await self._hooks.session_start()).context)
            said = await self._hooks.prompt(message.text)
            if said.blocked is not None:
                yield turn.ended(
                    TurnOutcome.FAILED,
                    f"A plugin's hook stopped this message: {said.blocked}",
                    "HookBlocked",
                )
                return
            notes.append(said.context)
            message = _noted(message, notes)
        history.append(message)
        while True:
            if turn.spent(self._limit):
                yield turn.ended(
                    TurnOutcome.LIMITED,
                    f"I stopped after {turn.model_calls} model calls and "
                    f"{turn.input_tokens:,} input tokens, this turn's budget.",
                )
                return
            if self._near_window(history) and turn.compactions < MAX_COMPACTIONS_PER_TURN:
                compacted = await self._compact(history, scope, turn)
                if compacted is not None:
                    yield compacted
            copy, sent = self._copy(history), len(history)
            tools, loaded = self._specs(copy)
            request = ModelRequest(
                tuple(copy),
                tools,
                loaded_tools=loaded,
                purpose=self._purpose,
                scope=scope,
                # Every deferred tool, for a wire that loads them by reference (Anthropic's), so
                # the tool list never changes and neither the cache nor the reasoning breaks.
                deferred_tools=tuple(_spec(tool) for tool in self._tools.deferred),
                # A person paces the conversation's turns: a routine and
                # a subagent do not, and their calls keep the short cache.
                long_cache=self._purpose == "turn" and self._attended,
            )
            reply: ModelReply | None = None
            try:
                async with aclosing(self._client.stream(request)) as events:
                    async for event in events:
                        if isinstance(event, ReplyDone):
                            reply = event.reply
                        else:
                            yield event
            except ModelRequestError as error:
                if not is_context_overflow(str(error)) or turn.overflow_retried:
                    yield turn.ended(TurnOutcome.FAILED, str(error), type(error).__name__)
                    return
                turn.overflow_retried = True
                compacted = await self._compact(history, scope, turn, forced=True)
                if compacted is None:
                    message = (
                        f"The conversation no longer fits in {self._client.model}'s context, and "
                        f"compacting it did not work: {error} Start a new conversation, or try "
                        "again."
                    )
                    yield turn.ended(TurnOutcome.FAILED, message, type(error).__name__)
                    return
                yield compacted
                continue
            except ModelsError as error:
                yield turn.ended(TurnOutcome.FAILED, str(error), type(error).__name__)
                return
            if reply is None:  # the client always ends with ReplyDone or raises
                yield turn.ended(TurnOutcome.FAILED, "The model call ended without a reply.")
                return
            turn.paid(reply)
            if reply.usage is not None and reply.usage.input_tokens > 0:
                self._anchor = _Anchor(history, sent, reply.usage.input_tokens)
            if not reply.text.strip() and not reply.tool_calls:
                turn.empty_replies += 1
                if turn.empty_replies <= EMPTY_REPLY_RETRIES:
                    continue
                yield turn.ended(
                    TurnOutcome.FAILED,
                    f"{self._client.model} returned an empty reply "
                    f"{turn.empty_replies} times. Try again, or choose another model.",
                )
                return
            history.append(reply.message)
            if not reply.tool_calls:
                if self._hooks is not None and turn.stop_continues < STOP_HOOK_CONTINUES:
                    stop = await self._hooks.stop(reply.text, turn.stop_continues > 0)
                    if stop.blocked is not None:
                        turn.stop_continues += 1
                        history.append(UserMessage(_HOOK_GOES_ON.format(stop.blocked)))
                        continue
                yield turn.ended(TurnOutcome.ANSWERED, reply.text)
                return
            if reply.text.strip():
                # Said at the end of the call, before its first tool starts, so the screen never
                # guesses which text was interim.
                yield InterimText(reply.text)
            state = _Round(loaded=frozenset(spec.name for spec in loaded))
            async with aclosing(self._round(reply.tool_calls, history, guard, state)) as round_:
                async for event in round_:
                    yield event
            if state.halt is not None:
                said = halted(state.halt, attended=self._attended)
                history.append(AssistantMessage(said))
                _logger.warning(
                    "The loop guard ended a turn.",
                    extra={"tool": state.halt.tool, "code": state.halt.code},
                )
                yield turn.ended(TurnOutcome.HALTED, said)
                return
            for said in steering() if steering is not None else ():
                message = said if isinstance(said, UserMessage) else UserMessage(said)
                history.append(message)
                yield Steered(message.text, message.attachments)

    async def reply(
        self, history: list[Message], text: str | UserMessage, *, scope: str | None = None
    ) -> TurnEnded:
        """One turn, waited for whole."""
        async with aclosing(self.run(history, text, scope=scope)) as events:
            async for event in events:
                if isinstance(event, TurnEnded):
                    return event
        raise AssertionError("A turn always ends with TurnEnded.")  # pragma: no cover

    # -- the tool round --------------------------------------------------------------------------

    async def _round(
        self,
        calls: tuple[ToolCall, ...],
        history: list[Message],
        guard: LoopGuard,
        state: _Round,
    ) -> AsyncGenerator[ToolStarted | ToolProgress | ToolFinished]:
        """Run a reply's calls, and leave one result per call in the history, whatever happens."""
        try:
            for group in self._groups(calls):
                if state.halt is not None:
                    # Shown as calls that never ran, so the live screen and the stored
                    # conversation hold the same calls.
                    for call in group:
                        done = ToolFinished(call.id, call.name, _NOT_RUN, ToolStatus.CANCELLED)
                        state.results[call.id] = done
                        yield ToolStarted(call.id, call.name, call.arguments)
                        yield done
                    continue
                for call in group:
                    yield ToolStarted(call.id, call.name, call.arguments)
                finished: dict[str, ToolFinished] = {}
                runnable: list[ToolCall] = []
                for call in group:
                    decision = guard.before(call.name, _arguments(call), self._traits(call.name))
                    if decision.runs:
                        runnable.append(call)
                    else:
                        finished[call.id] = ToolFinished(
                            call.id, call.name, blocked(decision), ToolStatus.FAILED
                        )
                async with aclosing(self._run_group(runnable, finished, state.loaded)) as progress:
                    async for event in progress:
                        yield event
                for call, done in ((call, finished[call.id]) for call in group):
                    done = self._guarded(call, self._fit_round(call, done, state), guard)
                    state.results[call.id] = done
                    yield done
                state.halt = guard.halt
        finally:
            # Every call gets its result, in order, even when the user stopped the turn midway.
            for call in calls:
                done = state.results.get(call.id)
                if done is None:
                    history.append(ToolResult(call.id, STOPPED, status=ToolStatus.CANCELLED))
                else:
                    history.append(
                        ToolResult(call.id, done.result, done.loaded, done.images, done.status)
                    )

    async def _run_group(
        self, group: list[ToolCall], finished: dict[str, ToolFinished], loaded: frozenset[str]
    ) -> AsyncGenerator[ToolProgress]:
        """Run a group's calls together, passing on what they report while they run; each
        finished call lands in `finished`. Closing this stops the calls still running."""
        queue: asyncio.Queue[ToolProgress] = asyncio.Queue()
        tasks = {asyncio.create_task(self._execute(call, loaded, queue)): call for call in group}
        pending: set[asyncio.Task[Any]] = set(tasks)
        try:
            while pending:
                waiting = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({*pending, waiting}, return_when=FIRST_COMPLETED)
                if waiting in done:
                    yield waiting.result()
                else:
                    waiting.cancel()
                pending -= done
            while not queue.empty():
                yield queue.get_nowait()
            for task, call in tasks.items():
                finished[call.id] = task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def _groups(self, calls: tuple[ToolCall, ...]) -> list[list[ToolCall]]:
        """Consecutive read-only calls run together; any other call runs alone."""
        groups: list[list[ToolCall]] = []
        together = False
        for call in calls:
            tool = self._tools.get(call.name)
            read_only = tool is not None and tool.read_only
            if read_only and together and groups:
                groups[-1].append(call)
            else:
                groups.append([call])
            together = read_only
        return groups

    async def _execute(
        self,
        call: ToolCall,
        available: frozenset[str],
        progress: "asyncio.Queue[ToolProgress] | None" = None,
    ) -> ToolFinished:
        started = time.monotonic()
        done = await self._attempt(call, available, progress)
        return dataclasses.replace(done, duration_s=round(time.monotonic() - started, 3))

    async def _attempt(
        self,
        call: ToolCall,
        available: frozenset[str],
        progress: "asyncio.Queue[ToolProgress] | None",
    ) -> ToolFinished:
        failed = ToolStatus.FAILED
        if progress is not None:
            _reporting.set((progress, call))
        tool = self._tools.get(call.name)
        if tool is None:
            known = ", ".join(sorted(t.name for t in self._tools.always)) or "none"
            more = f" Find others with {TOOL_SEARCH}." if self._tools.get(TOOL_SEARCH) else ""
            text = f"[Atlas: there is no tool named {call.name}. The tools are: {known}.{more}]"
            return ToolFinished(call.id, call.name, text, failed)
        if tool.deferred and call.name not in available:
            # A deferred name called directly is refused until a tool search loads it: the model
            # has not seen the schema, and guesses the arguments (Gemini did, in step 5's proof).
            text = (
                f"[Atlas: {call.name} is not loaded, so it did not run. Load it with "
                f'{TOOL_SEARCH} (query "{call.name}"), then call it with the parameters it shows.]'
            )
            return ToolFinished(call.id, call.name, text, failed)
        try:
            arguments: object = json.loads(call.arguments or "{}")
        except ValueError as error:
            text = (
                f"[Atlas: the arguments were not valid JSON ({error}). Call {call.name} again with "
                "a JSON object.]"
            )
            return ToolFinished(call.id, call.name, text, failed)
        if not isinstance(arguments, dict):
            text = f"[Atlas: the arguments must be a JSON object. Call {call.name} again.]"
            return ToolFinished(call.id, call.name, text, failed)
        parsed = cast("dict[str, Any]", arguments)
        if self._hooks is not None:
            before = await self._hooks.before_tool(call.name, parsed)
            if before.blocked is not None:
                text = f"[Atlas: a plugin's hook refused this call: {before.blocked}]"
                return ToolFinished(call.id, call.name, text, ToolStatus.DENIED)
            if before.arguments is not None:
                parsed = before.arguments
            if before.ask and not tool.needs_approval:
                tool = dataclasses.replace(tool, needs_approval=True)
        if self._gate is not None:
            refusal = await self._gate(tool, parsed)
            if refusal is not None:
                unanswered = isinstance(refusal, Unanswered)
                status = ToolStatus.UNANSWERED if unanswered else ToolStatus.DENIED
                return ToolFinished(call.id, call.name, str(refusal), status)
        try:
            output = await tool.run(parsed)
        except Exception as error:  # the tool edge: a tool is foreign code (MCP, plugins), logged
            _logger.warning("The tool %s failed.", call.name, exc_info=True)
            text = f"[Atlas: {call.name} failed: {type(error).__name__}: {error}]"
            return await self._after(call, parsed, text, failed)
        if isinstance(output, ToolOutput):
            text, loaded, images = output.text, self._loadable(output.loaded), output.images
        else:
            text, loaded, images = output, (), ()
        # A result that says it failed (an error Atlas wrote for the tool, a command's non-zero
        # exit code) is a failed call, read from the text.
        status = ToolStatus.FAILED if is_failure(text, raised=False) else ToolStatus.COMPLETED
        if self._hooks is not None:
            text = (await self._after(call, parsed, text, status)).result
        if len(text) > SPILL_CHARS:
            spilled = self._spilled(call, tool, text)
            return dataclasses.replace(spilled, status=status, loaded=loaded, images=images)
        return ToolFinished(call.id, call.name, text, status, loaded=loaded, images=images)

    async def _after(
        self, call: ToolCall, arguments: dict[str, Any], text: str, status: ToolStatus
    ) -> ToolFinished:
        """The result through the `PostToolUse` hooks: replaced, and followed by what they add."""
        if self._hooks is not None:
            failed = status is not ToolStatus.COMPLETED
            said = await self._hooks.after_tool(call.name, arguments, text, failed)
            if said.output is not None:
                text = said.output
            if said.context:
                text = f"{text}\n\n[Atlas: a plugin's hook adds: {said.context}]"
        return ToolFinished(call.id, call.name, text, status)

    def _guarded(self, call: ToolCall, done: ToolFinished, guard: LoopGuard) -> ToolFinished:
        """A finished call through the loop guards: a byte-identical result becomes a pointer to
        the first, and a notice or a warning follows the result."""
        if done.result.startswith("[Atlas: Blocked "):
            return done
        traits, arguments = self._traits(call.name), _arguments(call)
        failed = done.status is not ToolStatus.COMPLETED
        decision = guard.after(call.name, arguments, done.result, traits, failed=failed)
        seen = guard.observe(
            call.name, arguments, done.result, traits, call_id=call.id, failed=failed
        )
        result = seen.pointer if seen.pointer is not None else done.result
        if seen.notice is not None:
            result = f"{result}\n\n{seen.notice}"
        result = guidance(result, decision)
        return done if result == done.result else dataclasses.replace(done, result=result)

    def _traits(self, name: str) -> ToolTraits:
        tool = self._tools.get(name)
        if tool is None:
            return ToolTraits()
        return ToolTraits(tool.read_only, tool.repeatable, tool.failure_tolerant)

    def _loadable(self, names: tuple[str, ...]) -> tuple[str, ...]:
        """The names among `names` of deferred tools that exist, each once."""
        found: dict[str, None] = {}
        for name in names:
            tool = self._tools.get(name)
            if tool is not None and tool.deferred:
                found[name] = None
        return tuple(found)

    def _spilled(self, call: ToolCall, tool: Tool, output: str) -> ToolFinished:
        path = None
        if tool.spills and self._spill is not None:
            saved = self._spill.save(call.id, output)
            path = None if saved is None else str(saved)
        return ToolFinished(call.id, call.name, cut(output, path), ToolStatus.COMPLETED, path)

    def _fit_round(self, call: ToolCall, done: ToolFinished, state: _Round) -> ToolFinished:
        """Keep the round's results within `ROUND_CHARS` together: past it, a result is cut too."""
        if (
            state.chars + len(done.result) > ROUND_CHARS
            and done.spill_path is None
            and len(done.result) > _CUTTABLE_CHARS
        ):
            tool = self._tools.get(call.name)
            if tool is not None:
                spilled = self._spilled(call, tool, done.result)
                done = dataclasses.replace(
                    done, result=spilled.result, spill_path=spilled.spill_path
                )
        state.chars += len(done.result)
        return done

    # -- the context -----------------------------------------------------------------------------

    def _copy(self, history: Sequence[Message]) -> list[Message]:
        """What the model receives: the history repaired, and cleared once it grows.

        Whether to clear is decided on the whole history, which only grows within a conversation,
        so clearing does not switch on and off from one call to the next. Anthropic's server
        clears with the same rule (step 3b): clearing here would edit the past its kept reasoning
        is sealed to, a 400, where its own clearing never invalidates it.
        """
        copy = self._attached(repaired(history))
        if self._client.provider.wire is Wire.MESSAGES:
            return copy
        clear_at = (
            CLEAR_AT_TOKENS if self._window is None else min(CLEAR_AT_TOKENS, self._window // 2)
        )
        if self._estimate(copy) > clear_at:
            copy = cleared(copy)
        return copy

    def _attached(self, messages: list[Message]) -> list[Message]:
        """The user's messages with what this call's model reads of their attachments."""
        view, client = self._attachments, self._client
        out: list[Message] = []
        for message in messages:
            if isinstance(message, UserMessage) and message.attachments:
                if view is None:
                    names = ", ".join(attachment.name for attachment in message.attachments)
                    message = UserMessage(f"{message.text}\n\n[Atlas: attached: {names}]")
                else:
                    message = view(message, client.info, client.provider.wire)
            out.append(message)
        return out

    def _specs(
        self, messages: Sequence[Message]
    ) -> tuple[tuple[ToolSpec, ...], tuple[ToolSpec, ...]]:
        """The tools offered on the next call, and the deferred tools the conversation loaded,
        in the order it loaded them. A loaded tool stays while the result that loaded it is in the
        history; one that went away (its MCP server left) is dropped."""
        always = tuple(_spec(tool) for tool in self._tools.always)
        loaded: dict[str, ToolSpec] = {}
        for message in messages:
            if not isinstance(message, ToolResult):
                continue
            for name in message.loaded:
                tool = self._tools.get(name)
                if tool is not None and tool.deferred and name not in loaded:
                    loaded[name] = _spec(tool)
        return always, tuple(loaded.values())

    def _estimate(self, messages: Sequence[Message]) -> int:
        always, loaded = self._specs(messages)
        specs = [dataclasses.asdict(spec) for spec in (*always, *loaded)]
        return estimate_tokens(messages) + math.ceil(len(json.dumps(specs).encode()) / 4)

    def _level(self, history: list[Message]) -> int:
        """The tokens the next call would send: what the provider counted for the last call of
        this conversation, plus an estimate of what entered it since."""
        anchor = self._anchor
        if anchor is not None and anchor.history is history and anchor.length <= len(history):
            return anchor.tokens + estimate_tokens(history[anchor.length :])
        return self._estimate(self._copy(history))

    def _near_window(self, history: list[Message]) -> bool:
        if self._window is None or self._compaction_failures >= MAX_COMPACTION_FAILURES:
            return False
        return self._level(history) > self._window * COMPACT_AT

    async def _compact(
        self, history: list[Message], scope: str | None, turn: _Turn, *, forced: bool = False
    ) -> Compacted | None:
        """Replace the older part of the history with the model's summary. On any failure the
        history stays exactly as it was: V3 once replaced a conversation with a notice when its
        summarizer had no credit, and 126 messages became 14."""
        # The kept part is at most a fifth of the window, so a small local model
        # still has room for the summary and the next turn.
        tail = TAIL_TOKENS if self._window is None else min(TAIL_TOKENS, self._window // 5)
        plan = plan_compaction(repaired(history), tail_tokens=tail)
        if plan is None:
            return None
        before = self._level(history)
        request = dataclasses.replace(summary_request(plan, self._window), scope=scope)
        try:
            summary = await self._client.reply(request)
        except ModelsError as error:
            self._compaction_failures += 1
            _logger.warning(
                "The conversation could not be compacted (%d failures in a row): %s",
                self._compaction_failures,
                error,
                extra={"forced": forced},
            )
            return None
        turn.paid(summary)
        if not summary.text.strip():
            self._compaction_failures += 1
            _logger.warning("The model wrote an empty summary; the conversation was kept whole.")
            return None
        if summary.finish is Finish.LENGTH:
            # A cut summary would put in the history a past that ends at the cut.
            self._compaction_failures += 1
            _logger.warning("The summary was cut short; the conversation was kept whole.")
            return None
        self._compaction_failures = 0
        message = summary_message(summary.text, plan)
        # Every tool's, deferred ones too: a plan found through tool search still has open items.
        tools = (*self._tools.always, *self._tools.deferred)
        carried = [text for tool in tools if (text := _carried(tool))]
        if carried:
            message = dataclasses.replace(message, text="\n\n".join([message.text, *carried]))
        head = plan.head
        if self._instructions is not None and head:
            # The memory may have changed on disk since the conversation started: the instructions
            # are read again now, when the cache breaks anyway.
            head = (SystemMessage(self._instructions()), *head[1:])
        history[:] = [*head, message, *plan.tail]
        self._anchor = None  # the provider has not counted this history yet
        turn.compactions += 1
        after = self._estimate(self._copy(history))
        _logger.info(
            "The conversation was compacted.",
            extra={"tokens_before": before, "tokens_after": after, "forced": forced},
        )
        return Compacted(before, after)


def _carried(tool: Tool) -> str | None:
    """What a tool keeps across a compaction; a tool that fails here costs only its note."""
    if tool.carried is None:
        return None
    try:
        return tool.carried()
    except Exception:  # the tool edge: a tool is foreign code, logged
        _logger.warning("The tool %s could not say what it carries.", tool.name, exc_info=True)
        return None


def _spec(tool: Tool) -> ToolSpec:
    return ToolSpec(tool.name, tool.description, tool.parameters)


_NOT_RUN = "[Atlas: not run: the repeat guard stopped the turn before this call.]"
