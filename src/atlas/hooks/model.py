"""Hooks: Claude Code's `hooks.json`, which hooks match an event, and what a hook's answer says.
Pure: text in, data out.

Decided on 20/10/2026 (docs/ARCHITECTURE.md, Plugins; step 16k), from Claude Code's hooks reference
of that day:

- **Claude Code's events, names and JSON**, so its plugins' hooks run in Atlas unchanged: the
  events Atlas fires are `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
  `PostToolUseFailure` and `Stop`. A hook on another of Claude Code's events is read and never runs;
  the consent says so.
- **The file:** an event maps to groups, each a `matcher` and its `hooks`; a hook is
  `{"type": "command", "command", "args"?, "timeout"?, "shell"?, "onFailure"?, "if"?, "async"?,
  "asyncRewake"?}`. With `args` the command runs as it is (exec form), without them through a
  shell. `if` is Claude Code's permission rule (`Bash(git *)`), which the call must match too;
  `async` runs it in the background, its answer not read, and `asyncRewake` brings what it says
  when it exits 2 to the model as a notice. Claude Code's other types
  (`http`, `prompt`, `agent`, `mcp_tool`) are refused, with what to do.
- **The matcher, Claude Code's rules:** empty, missing or `*` matches all; letters, digits, `_`,
  `-`, spaces, `,` and `|` alone are names, one of which must be equal; anything else is a regular
  expression searched in the name. `UserPromptSubmit` and `Stop` take no matcher.
- **Atlas's tools under Claude Code's names, a choice of Atlas's (new):** a matcher is tried on
  the tool's name and on Claude Code's name for the same tool (`Bash` for `run_command`, `Read`
  for `read_file`), and a file tool's `path` is also given as `file_path`, so a hook written for
  Claude Code's tools reaches Atlas's.
- **The answer, Claude Code's:** exit 0 with a JSON object on stdout (`decision`, `reason`,
  `continue`, `stopReason`, `hookSpecificOutput` with `permissionDecision`,
  `permissionDecisionReason`, `updatedInput`, `additionalContext`, `updatedToolOutput`); exit 0
  with plain text, context for `UserPromptSubmit` and `SessionStart`; exit 2, a block whose reason
  is stderr, where the event can block; any other exit, an error that lets the action go on. A
  context is cut at Claude Code's 10,000 characters. `allow` is read and lowers nothing: Atlas's
  gate still decides.
"""

import fnmatch
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

from atlas.hooks.errors import HookConfigError

SESSION_START = "SessionStart"
USER_PROMPT_SUBMIT = "UserPromptSubmit"
PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
POST_TOOL_USE_FAILURE = "PostToolUseFailure"
STOP = "Stop"
#: The events Atlas fires.
EVENTS = (
    SESSION_START,
    USER_PROMPT_SUBMIT,
    PRE_TOOL_USE,
    POST_TOOL_USE,
    POST_TOOL_USE_FAILURE,
    STOP,
)
#: Claude Code's other events: read, shown, never fired by Atlas.
OTHER_EVENTS = frozenset(
    {
        "Setup", "UserPromptExpansion", "PermissionRequest", "PermissionDenied", "PostToolBatch",
        "Notification", "MessageDisplay", "SubagentStart", "SubagentStop", "TaskCreated",
        "TaskCompleted", "StopFailure", "TeammateIdle", "InstructionsLoaded", "ConfigChange",
        "CwdChanged", "DirectoryAdded", "FileChanged", "WorktreeCreate", "WorktreeRemove",
        "PreCompact", "PostCompact", "PreModelSwitch", "PostModelSwitch", "Elicitation",
        "ElicitationResult", "SessionEnd",
    }
)  # fmt: skip
#: Events that take no matcher (Claude Code ignores one there).
_UNMATCHED = frozenset({USER_PROMPT_SUBMIT, STOP})
#: Events a hook can stop (`PreToolUse` a call, `UserPromptSubmit` a prompt, `Stop` the end).
_BLOCKING = frozenset({PRE_TOOL_USE, USER_PROMPT_SUBMIT, STOP})
#: Events whose plain stdout is context for the model (Claude Code's list, of Atlas's events).
_PLAIN_CONTEXT = frozenset({USER_PROMPT_SUBMIT, SESSION_START})
#: Claude Code's default timeouts, in seconds.
COMMAND_TIMEOUT = 600.0
PROMPT_TIMEOUT = 30.0
#: Claude Code cuts a hook's `additionalContext` here.
CONTEXT_CHARS = 10_000
#: The exit code that blocks, Claude Code's and Cursor's.
BLOCK_EXIT = 2
#: Claude Code's names for Atlas's tools, for a matcher written for Claude Code's.
CLAUDE_CODE_NAMES = {
    "run_command": "Bash",
    "read_file": "Read",
    "write_file": "Write",
    "edit_file": "Edit",
    "find_files": "Grep",
    "web_fetch": "WebFetch",
    "web_search": "WebSearch",
}
_FILE_TOOLS = frozenset({"read_file", "write_file", "edit_file"})
_NAMES = re.compile(r"^[A-Za-z0-9_\- ,|]*$")
_HOOK_FIELDS = frozenset(
    {
        "type",
        "command",
        "args",
        "timeout",
        "shell",
        "onFailure",
        "statusMessage",
        "if",
        "async",
        "asyncRewake",
    }
)


@dataclass(frozen=True, slots=True)
class CommandHook:
    """One `command` hook of a `hooks.json`."""

    event: str
    matcher: str
    command: str
    #: Exec form: the command run with these as its arguments, no shell. `None`: shell form.
    args: tuple[str, ...] | None = None
    timeout: float | None = None
    #: `powershell` for a shell-form hook written for it; otherwise bash.
    shell: str = ""
    #: `onFailure: "block"`: a hook that fails or times out blocks, on an event that can.
    fail_closed: bool = False
    #: Claude Code's `if`: a permission rule (`Bash(git *)`, `Edit(*.py)`) the tool call must
    #: match too, so a hook runs only on the calls it is for; empty, every call its matcher takes.
    rule: str = ""
    #: `async`: it runs in the background, never holding the action, and its answer is not read.
    background: bool = False
    #: `asyncRewake`: a background hook that exits 2 brings its stderr to the model as a notice.
    rewake: bool = False

    @property
    def runs(self) -> bool:
        """Atlas fires its event."""
        return self.event in EVENTS

    def seconds(self) -> float:
        if self.timeout is not None:
            return self.timeout
        return PROMPT_TIMEOUT if self.event == USER_PROMPT_SUBMIT else COMMAND_TIMEOUT

    def summary(self) -> str:
        """The hook in a line, for the consent."""
        what = " ".join((self.command, *(self.args or ())))
        on = f"{self.event} ({self.matcher})" if self.matcher else self.event
        later = "" if self.runs else ", which Atlas does not fire yet"
        return f"`{what}` on {on}{later}"


@dataclass(frozen=True, slots=True)
class Answer:
    """What one hook said, or the hooks of an event together."""

    blocked: str | None = None
    ask: bool = False
    arguments: dict[str, Any] | None = None
    context: str = ""
    output: str | None = None
    #: The hook failed without blocking (a crash, an exit other than 0 and 2, a timeout): why.
    error: str | None = None


def parse_hooks(document: object, where: str) -> list[CommandHook]:
    """The hooks of an event map (`hooks.json`'s `hooks`, or a manifest's inline `hooks`). Raises
    `HookConfigError` naming what is wrong."""
    if not isinstance(document, dict):
        raise HookConfigError(f"{where} must be an object of events.")
    hooks: list[CommandHook] = []
    for event, groups in cast("dict[str, Any]", document).items():
        if event not in EVENTS and event not in OTHER_EVENTS:
            raise HookConfigError(f"{where} has an event Claude Code does not know: {event!r}.")
        if not isinstance(groups, list):
            raise HookConfigError(f"{where}: {event} must be a list of matcher groups.")
        for group in cast("list[Any]", groups):
            hooks += _group(event, group, where)
    return hooks


def _group(event: str, group: object, where: str) -> list[CommandHook]:
    if not isinstance(group, dict):
        raise HookConfigError(f"{where}: each group of {event} must be an object.")
    fields = cast("dict[str, Any]", group)
    matcher = fields.get("matcher", "")
    if not isinstance(matcher, str):
        raise HookConfigError(f"{where}: a matcher of {event} must be text.")
    if not _NAMES.match(matcher):
        try:
            re.compile(matcher)
        except re.error as error:
            raise HookConfigError(
                f"{where}: the matcher {matcher!r} is no pattern: {error}"
            ) from None
    entries = fields.get("hooks", [])
    if not isinstance(entries, list):
        raise HookConfigError(f"{where}: the hooks of a group of {event} must be a list.")
    return [_hook(event, matcher.strip(), entry, where) for entry in cast("list[Any]", entries)]


def _hook(event: str, matcher: str, entry: object, where: str) -> CommandHook:
    if not isinstance(entry, dict):
        raise HookConfigError(f"{where}: each hook of {event} must be an object.")
    fields = cast("dict[str, Any]", entry)
    kind = fields.get("type", "command")
    if kind != "command":
        raise HookConfigError(
            f"{where}: Atlas runs command hooks; a hook of type {kind!r} on {event} is not loaded "
            "yet. Write it as a command."
        )
    command = fields.get("command")
    if not isinstance(command, str) or not command.strip():
        raise HookConfigError(f"{where}: a hook on {event} needs a command.")
    args = fields.get("args")
    if args is not None and (
        not isinstance(args, list) or not all(isinstance(a, str) for a in cast("list[Any]", args))
    ):
        raise HookConfigError(f"{where}: the args of a hook on {event} must be a list of texts.")
    timeout = fields.get("timeout")
    if timeout is not None and (
        isinstance(timeout, bool) or not isinstance(timeout, int | float) or timeout <= 0
    ):
        raise HookConfigError(f"{where}: the timeout of a hook on {event} is seconds, above 0.")
    shell = fields.get("shell", "")
    if shell not in ("", "bash", "powershell"):
        raise HookConfigError(f"{where}: the shell of a hook on {event} is bash or powershell.")
    rule = fields.get("if", "")
    if not isinstance(rule, str) or (rule and not _RULE.match(rule.strip())):
        raise HookConfigError(
            f"{where}: the if of a hook on {event} is a permission rule, such as Bash(git *)."
        )
    return CommandHook(
        event=event,
        matcher="" if event in _UNMATCHED else matcher,
        command=command,
        args=None if args is None else tuple(cast("list[str]", args)),
        timeout=None if timeout is None else float(timeout),
        shell=shell,
        fail_closed=fields.get("onFailure") == "block",
        rule=rule.strip(),
        background=fields.get("async") is True,
        rewake=fields.get("async") is True and fields.get("asyncRewake") is True,
    )


#: Claude Code's permission rule, as a hook's `if` writes it: a tool's name, and a pattern.
_RULE = re.compile(r"^([A-Za-z0-9_]+)(?:\((.*)\))?$", re.DOTALL)
#: What a rule's pattern is read against, by the tool's Claude Code name.
_RULE_FIELD = {"Bash": "command", "Read": "file_path", "Write": "file_path", "Edit": "file_path",
               "WebFetch": "url", "Grep": "pattern", "Glob": "pattern"}  # fmt: skip


def rule_matches(rule: str, tool: str, arguments: Mapping[str, Any]) -> bool:
    """Whether a tool call matches a hook's `if` (Claude Code's permission rule): the tool's name,
    Atlas's or Claude Code's, and its pattern, `*` any text and a final `:*` any rest, against the
    command, the file, the address or the search. Empty: every call."""
    if not rule:
        return True
    found = _RULE.match(rule)
    if found is None:
        return False
    name, pattern = found.group(1), found.group(2)
    claude = CLAUDE_CODE_NAMES.get(tool, tool)
    if name not in (tool, claude):
        return False
    if pattern is None or pattern in ("", "*"):
        return True
    field = _RULE_FIELD.get(claude)
    value = arguments.get(field) if field is not None else None
    if not isinstance(value, str):
        return False
    if pattern.endswith(":*"):
        return value.startswith(pattern[:-2])
    return fnmatch.fnmatchcase(value, pattern)


def matches(matcher: str, value: str) -> bool:
    """Claude Code's matcher rule; a tool's name is tried under Claude Code's name for it too."""
    if matcher in ("", "*"):
        return True
    names = [value, *([CLAUDE_CODE_NAMES[value]] if value in CLAUDE_CODE_NAMES else [])]
    if _NAMES.match(matcher):
        wanted = {part.strip() for part in re.split(r"[|,]", matcher) if part.strip()}
        return any(name in wanted for name in names)
    try:
        pattern = re.compile(matcher)
    except re.error:
        return False
    return any(pattern.search(name) for name in names)


def tool_input(tool: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    """A call's arguments as a hook reads them: a file tool's `path` also as `file_path`."""
    given = dict(arguments)
    if tool in _FILE_TOOLS and "path" in given and "file_path" not in given:
        given["file_path"] = given["path"]
    return given


def from_tool_input(
    tool: str, written: Mapping[str, Any], sent: Mapping[str, Any]
) -> dict[str, Any]:
    """A hook's `updatedInput` as the tool takes it: the `file_path` it was shown goes back to
    `path`."""
    taken = dict(written)
    if tool in _FILE_TOOLS and "file_path" in taken and "file_path" not in sent:
        file_path = taken.pop("file_path")
        if taken.get("path") == sent.get("path"):  # it changed `file_path`, or neither
            taken["path"] = file_path
    return taken


def read_answer(event: str, code: int, stdout: str, stderr: str) -> Answer:
    """What a command hook said, from its exit code and its output."""
    if code == BLOCK_EXIT:
        why = stderr.strip() or "A hook refused it."
        if event in _BLOCKING:
            return Answer(blocked=why)
        if event in (POST_TOOL_USE, POST_TOOL_USE_FAILURE):
            return Answer(context=_cut(why))
        return Answer(error=why)
    if code != 0:
        first = (stderr.strip() or stdout.strip()).splitlines()
        return Answer(error=f"It exited with {code}" + (f": {first[0]}" if first else "."))
    text = stdout.strip()
    if not text:
        return Answer()
    try:
        parsed: object = json.loads(text)
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        return read_object(event, cast("dict[str, Any]", parsed))
    return Answer(context=_cut(text)) if event in _PLAIN_CONTEXT else Answer()


def read_object(event: str, said: Mapping[str, Any]) -> Answer:
    """What a hook's JSON object says, for its event (a Python hook returns the same object)."""
    blocked: str | None = None
    ask = False
    arguments: dict[str, Any] | None = None
    contexts: list[str] = []
    output: str | None = None
    reason = said.get("reason")
    if said.get("decision") == "block":
        why = reason if isinstance(reason, str) and reason.strip() else "A hook refused it."
        if event in _BLOCKING:
            blocked = why
        else:
            contexts.append(why)  # after the call, Claude Code's block is feedback for the model
    if said.get("continue") is False and event != STOP and event in _BLOCKING:
        stop_reason = said.get("stopReason")
        blocked = (
            stop_reason if isinstance(stop_reason, str) and stop_reason else "A hook stopped it."
        )
    specific = said.get("hookSpecificOutput")
    if isinstance(specific, dict):
        found = cast("dict[str, Any]", specific)
        decision = found.get("permissionDecision")
        why = found.get("permissionDecisionReason")
        if event == PRE_TOOL_USE and decision == "deny":
            blocked = why if isinstance(why, str) and why.strip() else "A hook refused this call."
        elif event == PRE_TOOL_USE and decision == "ask":
            ask = True
        updated = found.get("updatedInput")
        if event == PRE_TOOL_USE and isinstance(updated, dict):
            arguments = cast("dict[str, Any]", updated)
        context = found.get("additionalContext")
        if isinstance(context, str) and context.strip():
            contexts.append(context)
        replaced = found.get("updatedToolOutput")
        if event in (POST_TOOL_USE, POST_TOOL_USE_FAILURE) and replaced is not None:
            output = replaced if isinstance(replaced, str) else json.dumps(replaced)
    return Answer(blocked, ask, arguments, _cut("\n".join(contexts)), output)


def combined(answers: Iterable[Answer]) -> Answer:
    """The hooks of one event together, in the order they were declared: every block's reason, any
    ask, the first rewritten arguments and output, every context."""
    found = list(answers)
    blocks = [a.blocked for a in found if a.blocked is not None]
    contexts = [a.context for a in found if a.context]
    errors = [a.error for a in found if a.error]
    return Answer(
        blocked="; ".join(blocks) if blocks else None,
        ask=any(a.ask for a in found),
        arguments=next((a.arguments for a in found if a.arguments is not None), None),
        context=_cut("\n".join(contexts)),
        output=next((a.output for a in found if a.output is not None), None),
        error="; ".join(errors) if errors else None,
    )


def summaries(hooks: Sequence[CommandHook]) -> list[str]:
    return [hook.summary() for hook in hooks]


def _cut(text: str) -> str:
    return text if len(text) <= CONTEXT_CHARS else text[: CONTEXT_CHARS - 1] + "…"
