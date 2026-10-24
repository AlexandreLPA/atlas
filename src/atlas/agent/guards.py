"""Loop guards: a turn's tool calls watched for loops, warned about, and halted when nobody watches.

The guards:

- **The identical-call streak**: the 3rd call in a row with the same tool, arguments and result gets
  a notice; a poller (`Tool.repeatable`) never does. It replaces step 4's repeat guard.
- **The cycle** (A, B, A, B...): a repeating batch of 2 to 4 calls, identical in arguments and
  results, resets the streak at every alternation, so it is looked for in the turn's last 64 calls.
- **A repeated result** of 512 characters or more, byte-identical to the one before it, is replaced
  by a pointer to it from the 2nd time on; the tool still ran.
- **Failures**: the same call failing again is warned at the 2nd and blocked at the 5th; the same
  tool failing with other arguments is warned at the 3rd and halts the turn at the 8th, except a
  tool whose failures are its work (`Tool.failure_tolerant`: a red test run, an empty search). A
  successful call that changes something resets them, since the next try is a new experiment: a
  patch-then-run loop goes on round after round.
- **A read-only call returning the same result** is warned at the 2nd and blocked at the 5th.

**The posture** (decided on 11/10/2026): attended work only warns, since someone can stop it;
unattended work (a routine) blocks and halts, since nobody stops a model that ignores warnings.

**The per-turn cap on subagents** (step 12b): a turn that already spawned 50 subagents has its
next spawn blocked, watched or not, since a runaway delegation loop costs fifty conversations
before anyone looks; a call that lists, steers or stops spawns nothing. A cap on web searches is
left out until use asks.
"""

import hashlib
import json
from collections import deque
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

#: The call in a row, same tool, arguments and result, that brings the notice; 3 tolerates one
#: double-check.
IDENTICAL_CALL_NOTICE_AT = 3
#: The longest repeating batch looked for, and how many calls are kept to find it.
MAX_CYCLE_PERIOD = 4
CYCLE_HISTORY = 64
#: A repeated result shorter than this is not worth a pointer.
RESULT_POINTER_MIN_CHARS = 512
_ARGS_PREVIEW_CHARS = 120
#: Subagents one turn may spawn, and the tool that spawns them.
MAX_SUBAGENTS_PER_TURN = 50
DELEGATE_TOOL = "delegate"


@dataclass(frozen=True, slots=True)
class GuardLimits:
    """The guards' thresholds."""

    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5


class Action(StrEnum):
    ALLOW = "allow"
    WARN = "warn"
    #: The call is not run; the model gets why.
    BLOCK = "block"
    #: The call ran; the turn ends after its round.
    HALT = "halt"


@dataclass(frozen=True, slots=True)
class ToolTraits:
    """What the guards need to know about a tool."""

    #: Reads and changes nothing: repeating it with the same result is no progress.
    read_only: bool = False
    #: A poller, legitimately called again with the same arguments.
    repeatable: bool = False
    #: Its failures are its work: different failing calls warn, never halt.
    failure_tolerant: bool = False


@dataclass(frozen=True, slots=True)
class Decision:
    action: Action = Action.ALLOW
    code: str = "allow"
    message: str = ""
    tool: str = ""
    count: int = 0

    @property
    def runs(self) -> bool:
        return self.action in (Action.ALLOW, Action.WARN)


@dataclass(frozen=True, slots=True)
class Observation:
    """What the streak and cycle detectors make of a finished call: a notice appended after its
    result, and a pointer that replaces a byte-identical result."""

    notice: str | None = None
    pointer: str | None = None


#: What each decision tells the model, by its code: warnings first, then blocks and halts.
_WORDING: dict[str, str] = {
    "repeated_exact_failure_warning": (
        "{tool} failed {count} times, its arguments the same each time, which looks like a loop. "
        "Read the error and change your approach rather than trying the same call again."
    ),
    "idempotent_no_progress_warning": (
        "{tool} gave back the same result {count} times. Rather than calling it again as it is, "
        "work from the result you already have or change the query."
    ),
    "repeated_exact_failure_block": (
        "Blocked {tool}: the same call failed {count} times, its arguments the same each time. "
        "Do not try it again as it is: take another approach, or say what stands in the way."
    ),
    "idempotent_no_progress_block": (
        "Blocked {tool}: {count} times now, this read-only call has given back the same result. "
        "Do not run it again as it is: work from the result you already have, or ask a "
        "different query."
    ),
    "identical_call_streak_halt": (
        "Stopped {tool}: called with the same arguments, it returned the same result {count} "
        "times in a row. Do not call it again as it is: work from the result you already have, "
        "or change your approach."
    ),
    "identical_cycle_halt": (
        "Stopped {tool}: one cycle of tool calls (period {period}), its arguments and results "
        "the same each time, has gone round {count} times. Running that batch again as it is "
        "gets nowhere: work from the results you already have, or change your approach."
    ),
    "same_tool_failure_halt": (
        "Stopped {tool}: {count} failures this turn. Leave the path that keeps failing with this "
        "tool and take another approach."
    ),
    "loop_subagent_cap": (
        "Blocked {tool}: this turn already spawned {count} subagents (limit {cap}), the mark of a "
        "delegation loop out of control. Finish with the results in hand and answer the user."
    ),
}

_STREAK_NOTICE = (
    "[Atlas: this is the {ordinal} call in a row to {tool}, the same arguments bringing back the "
    "same result each time. Do not make it again: give other arguments, use another tool, or "
    "carry on with what you already have.]"
)
_CYCLE_NOTICE = (
    "[Atlas: for {count} rounds, the same cycle of {period} tool calls (the last of them {tool}) "
    "came back with the same arguments and the same results. Do not run that batch again: give "
    "other arguments, use another tool, or carry on with what you already have.]"
)


type _Signature = tuple[str, str]


class LoopGuard:
    """One turn's guard. `attended` is someone watching (a conversation, a subagent's parent);
    otherwise blocks and halts are on."""

    def __init__(
        self,
        *,
        attended: bool = True,
        limits: GuardLimits | None = None,
        max_subagents: int = MAX_SUBAGENTS_PER_TURN,
    ) -> None:
        self.hard_stop = not attended
        self.limits = limits or GuardLimits()
        self.max_subagents = max_subagents
        self._subagents = 0
        self._exact_failures: dict[_Signature, int] = {}
        self._tool_failures: dict[str, int] = {}
        self._progress_since_failure: dict[_Signature, bool] = {}
        self._no_progress: dict[_Signature, tuple[str, int]] = {}
        self._halt: Decision | None = None
        self._streak: tuple[_Signature, str] | None = None
        self._streak_count = 0
        self._streak_first_call = ""
        self._history: deque[tuple[_Signature, str, bool]] = deque(maxlen=CYCLE_HISTORY)

    @property
    def halt(self) -> Decision | None:
        """The block or halt that ends the turn after its round, if one came."""
        return self._halt

    def before(self, tool: str, arguments: dict[str, Any], traits: ToolTraits) -> Decision:
        """Whether the call may run."""
        if tool == DELEGATE_TOOL:
            # The cap holds whoever watches.
            spawns = _spawns(arguments)
            if spawns and self._subagents >= self.max_subagents:
                return self._decide(
                    Action.BLOCK, "loop_subagent_cap", tool, self._subagents, cap=self.max_subagents
                )
            self._subagents += spawns
        if not self.hard_stop:
            return Decision(tool=tool)
        signature = _signature(tool, arguments)
        exact = (
            0
            if self._progress_since_failure.get(signature)
            else self._exact_failures.get(signature, 0)
        )
        if exact >= self.limits.exact_failure_block_after:
            return self._decide(Action.BLOCK, "repeated_exact_failure_block", tool, exact)
        record = self._no_progress.get(signature) if traits.read_only else None
        if record is not None and record[1] >= self.limits.no_progress_block_after:
            return self._decide(Action.BLOCK, "idempotent_no_progress_block", tool, record[1])
        return Decision(tool=tool)

    def after(
        self, tool: str, arguments: dict[str, Any], result: str, traits: ToolTraits, *, failed: bool
    ) -> Decision:
        """Count a finished call's failure or progress; a warning, or the halt, comes back."""
        signature = _signature(tool, arguments)
        if failed:
            if self._progress_since_failure.pop(signature, False):
                self._exact_failures.pop(signature, None)
            exact = self._exact_failures[signature] = self._exact_failures.get(signature, 0) + 1
            same = self._tool_failures[tool] = self._tool_failures.get(tool, 0) + 1
            self._no_progress.pop(signature, None)
            if (
                self.hard_stop
                and not traits.failure_tolerant
                and same >= self.limits.same_tool_failure_halt_after
            ):
                return self._decide(Action.HALT, "same_tool_failure_halt", tool, same)
            if exact >= self.limits.exact_failure_warn_after:
                return self._decide(Action.WARN, "repeated_exact_failure_warning", tool, exact)
            if same >= self.limits.same_tool_failure_warn_after:
                return Decision(
                    Action.WARN, "same_tool_failure_warning", _recovery(tool, same), tool, same
                )
            return Decision(tool=tool, count=exact)
        self._exact_failures.pop(signature, None)
        self._tool_failures.pop(tool, None)
        if not traits.read_only:
            # A change that landed is progress for every failing call still counted.
            self._progress_since_failure.update(dict.fromkeys(self._exact_failures, True))
            self._tool_failures.clear()
            self._no_progress.pop(signature, None)
            return Decision(tool=tool)
        digest = _result_hash(result)
        previous = self._no_progress.get(signature)
        repeats = previous[1] + 1 if previous is not None and previous[0] == digest else 1
        self._no_progress[signature] = (digest, repeats)
        if repeats >= self.limits.no_progress_warn_after:
            return self._decide(Action.WARN, "idempotent_no_progress_warning", tool, repeats)
        return Decision(tool=tool, count=repeats)

    def observe(
        self,
        tool: str,
        arguments: dict[str, Any],
        result: str,
        traits: ToolTraits,
        *,
        call_id: str,
        failed: bool,
    ) -> Observation:
        """The streak and the cycle, after each call in the order the model asked for them."""
        signature = _signature(tool, arguments)
        digest = _result_hash(result)
        if (signature, digest) == self._streak:
            self._streak_count += 1
        else:
            self._streak, self._streak_count, self._streak_first_call = (
                (signature, digest),
                1,
                call_id,
            )
        count = self._streak_count
        notice = None
        if not traits.repeatable and count >= IDENTICAL_CALL_NOTICE_AT:
            notice = _STREAK_NOTICE.format(ordinal=_ordinal(count), tool=tool)
            if self.hard_stop and count >= self.limits.no_progress_block_after:
                self._decide(
                    Action.HALT, "identical_call_streak_halt", tool, count, only_first=True
                )
        self._history.append((signature, digest, traits.repeatable))
        if notice is None:
            cycle = self._cycle()
            if cycle is not None:
                period, laps = cycle
                notice = _CYCLE_NOTICE.format(count=laps, period=period, tool=tool)
                if self.hard_stop and laps >= self.limits.no_progress_block_after:
                    self._decide(
                        Action.HALT,
                        "identical_cycle_halt",
                        tool,
                        laps,
                        only_first=True,
                        period=period,
                    )
        pointer = None
        if count >= 2 and not failed and len(result) >= RESULT_POINTER_MIN_CHARS:
            pointer = self._pointer(tool, arguments)
        return Observation(notice, pointer)

    def _cycle(self) -> tuple[int, int] | None:
        """The smallest period, 2 to 4, whose last laps repeat with identical calls and results
        enough times; a cycle made only of pollers is legitimate waiting."""
        calls = list(self._history)
        for period in range(2, MAX_CYCLE_PERIOD + 1):
            if len(calls) < period * IDENTICAL_CALL_NOTICE_AT:
                continue
            laps = _laps(calls, period)
            if laps < IDENTICAL_CALL_NOTICE_AT:
                continue
            if any(not repeatable for _, _, repeatable in calls[-period:]):
                return period, laps
        return None

    def _pointer(self, tool: str, arguments: dict[str, Any]) -> str:
        preview = _canonical(arguments)
        if len(preview) > _ARGS_PREVIEW_CHARS:
            preview = preview[:_ARGS_PREVIEW_CHARS] + "…"
        first = self._streak_first_call
        where = f" (call {first})" if first else ""
        return (
            f"[Atlas: this result is byte-identical to the {tool} result earlier this turn{where}. "
            f"Refer to that result; it has not changed. Arguments: {preview}]"
        )

    def _decide(
        self,
        action: Action,
        code: str,
        tool: str,
        count: int,
        *,
        only_first: bool = False,
        **values: Any,
    ) -> Decision:
        decision = Decision(
            action, code, _WORDING[code].format(tool=tool, count=count, **values), tool, count
        )
        if action in (Action.BLOCK, Action.HALT) and not (only_first and self._halt is not None):
            self._halt = decision
        return decision


def guidance(result: str, decision: Decision) -> str:
    """A warning or a halt appended to the call's real result."""
    if decision.action not in (Action.WARN, Action.HALT) or not decision.message:
        return result
    label = "Tool loop hard stop" if decision.action is Action.HALT else "Tool loop warning"
    return f"{result}\n\n[{label}: {decision.code}; count={decision.count}; {decision.message}]"


def blocked(decision: Decision) -> str:
    """What the model gets in place of a blocked call's result."""
    return f"[Atlas: {decision.message}]"


def halted(decision: Decision, *, attended: bool = False) -> str:
    """What the turn says when the guards end it: unattended, or by the subagent cap."""
    if attended:
        return f"I stopped this turn: {decision.message}"
    return (
        f"I stopped this turn: {decision.message} Nobody was watching this run, so the loop guard "
        "ended it instead of letting it repeat."
    )


def _spawns(arguments: dict[str, Any]) -> int:
    """Subagents one delegate call spawns: its tasks, or one; a call that lists, steers or stops
    spawns none."""
    action = arguments.get("action")
    if isinstance(action, str) and action.strip().lower() in ("list", "steer", "stop"):
        return 0
    tasks: object = arguments.get("tasks")
    return len(cast("list[object]", tasks)) if isinstance(tasks, list) and tasks else 1


def is_failure(result: str, *, raised: bool) -> bool:
    """Whether a call failed: the tool raised, or Atlas answered for it (`[Atlas: ...]`, a refusal
    or an error), or a command ended with a non-zero exit code."""
    if raised or result.startswith("[Atlas:"):
        return True
    last = result.rstrip().rsplit("\n", 1)[-1]
    return last.startswith("[exit code ") and last != "[exit code 0]"


def _recovery(tool: str, count: int) -> str:
    """An action-oriented hint for a tool failing again and again."""
    opening = (
        f"{tool} failed {count} times this turn, which looks like a loop. Keep working with "
        "tools rather than falling back to replies in text alone, but find the cause before the "
        "next try: read the latest error or output first and check what you assumed. "
    )
    if tool != "run_command":
        return opening + (
            "Change the arguments, narrow the query or the path, give an absolute path where it "
            "matters, or pick another tool that can move things forward. When the cause lies "
            "outside, one diagnostic try is enough: report it then rather than repeating the "
            "path that keeps failing."
        )
    return opening + (
        "When a command fails, run a small diagnostic first (where the working folder is and "
        "what is in it); then try an absolute path, a plainer command, another folder, or "
        "another tool such as read_file, write_file or edit_file."
    )


def _signature(tool: str, arguments: dict[str, Any]) -> _Signature:
    return tool, _sha256(_canonical(arguments))


def _laps(calls: list[tuple[_Signature, str, bool]], period: int) -> int:
    """How many blocks of `period` calls, counted back from the end, repeat the last block's
    calls and results."""
    last = [call[:2] for call in calls[-period:]]
    laps, end = 1, len(calls) - period
    while end >= period and [call[:2] for call in calls[end - period : end]] == last:
        laps += 1
        end -= period
    return laps


#: Compact, key-sorted JSON: equal arguments give equal text whatever their order.
_ENCODER = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _canonical(value: Any) -> str:
    return _ENCODER.encode(value)


def _result_hash(result: str) -> str:
    try:
        parsed = json.loads(result)
    except ValueError:
        return _sha256(result)
    return _sha256(_canonical(parsed))


def _ordinal(count: int) -> str:
    if 11 <= count % 100 <= 13:
        return f"{count}th"
    return f"{count}{({1: 'st', 2: 'nd', 3: 'rd'}).get(count % 10, 'th')}"


def _sha256(value: str) -> str:
    # A scraped page can hold a lone UTF-16 surrogate, which strict UTF-8 refuses to encode.
    data = value.encode("utf-8", errors="surrogatepass")
    return hashlib.sha256(data).hexdigest()
