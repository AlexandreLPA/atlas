"""The handoff packet (asked for by the user on 21/10/2026): what a session's route hands a new
session of another executor when its own reaches its plan's limit. A Claude Code session cannot
go on as a Codex one, nor the other way round, so the destination starts a session of its own, and
its first prompt, the normal one that goes on with the work, carries what the first one did.

It is put together here from what is kept, deterministically and with no model call (Principle
1: the destination's model does the work): the steps the source harness kept of its session, the
same kinds for every harness (`TranscriptEntry`), or Atlas's own record of the card when the
harness cannot read them; the files its turns changed; why the switch happened. The newest steps
are kept first when the packet would pass its size, and what was left out is said, so the
destination knows to look at the folder itself."""

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from atlas.models import AssistantMessage, Message, ToolResult, ToolStatus, UserMessage
from atlas.plugins import TranscriptEntry

#: What starts every packet: a packet the destination's session hands on again is known by it.
HANDOFF_MARK = "[Atlas handoff]"
#: The packet's size, in characters: about 10,000 tokens, well inside every harness's window and
#: cheap on its plan.
HANDOFF_CHARS = 40_000
#: The most one step takes of it: a long result is cut, its start and end kept.
STEP_CHARS = 1_500
#: The most the task takes, as the user wrote it.
TASK_CHARS = 6_000
#: The tools a harness keeps its plan with: Claude Code's and Codex's.
PLAN_TOOLS = frozenset({"TodoWrite", "update_plan"})
_DONE = frozenset({"completed", "done", "cancelled"})


@dataclass(frozen=True, slots=True)
class Handoff:
    """What a packet is made of. `source` and `target` name the executor and the account ("Claude
    Code · main"); `session` is the source's session, kept where its harness keeps it."""

    source: str
    target: str
    folder: str
    reason: str
    entries: tuple[TranscriptEntry, ...]
    session: str = ""
    #: The files the source's turns changed, as its harness named them.
    changed: tuple[str, ...] = ()


def entries_of(
    history: Sequence[Message], paths: dict[str, str] | None = None
) -> tuple[TranscriptEntry, ...]:
    """Atlas's own record of a card as a harness's steps, for a harness that cannot read its
    session: the user's prompts, with the paths of their files from `paths` (attachment id to
    path), what it said, its tool calls and their results."""
    found: list[TranscriptEntry] = []
    for message in history:
        if isinstance(message, UserMessage):
            files = tuple(
                (paths or {}).get(attachment.id, attachment.name)
                for attachment in message.attachments
            )
            found.append(TranscriptEntry("text", message.text, by="user", files=files))
        elif isinstance(message, AssistantMessage):
            if message.text.strip():
                found.append(TranscriptEntry("text", message.text))
            for call in message.tool_calls:
                found.append(TranscriptEntry("tool", call.arguments, tool=call.name, call=call.id))
        elif isinstance(message, ToolResult):
            failed = message.status is not ToolStatus.COMPLETED
            found.append(
                TranscriptEntry("result", message.text, call=message.call_id, failed=failed)
            )
    return tuple(found)


def handoff_packet(handoff: Handoff, limit: int = HANDOFF_CHARS) -> str:
    """The packet, as the destination's first prompt: the same handoff gives the same text."""
    entries = handoff.entries
    said = [e for e in entries if e.kind == "text" and e.by == "user" and e.text.strip()]
    task = _task(said[0].text) if said else ""
    latest = said[-1].text if len(said) > 1 else ""
    if latest.startswith(HANDOFF_MARK):
        latest = ""
    head = [
        f"{HANDOFF_MARK} You are taking over a task that {handoff.source} was working on, in this "
        f"folder. Its session cannot go on here, so this packet is what you get of it, put "
        "together by Atlas from what that session kept, with no summary by a model. Read it, look "
        "at the folder as it is now (the work may be half done), and go on with the task from "
        "where it stopped: do not start it over, and do not redo what is already done.",
        _section("Why you are taking over", handoff.reason),
        _section("Working folder", handoff.folder),
        _section("The task", _cut(task, TASK_CHARS) or "(the session kept no prompt)"),
    ]
    if latest and latest != task:
        head.append(_section("The latest instruction", _cut(latest, TASK_CHARS)))
    pending = _pending(entries)
    if pending:
        head.append(_section("Pending work and open questions", "\n".join(pending)))
    files = _files(handoff, entries)
    if files:
        head.append(_section("Files", "\n".join(files)))
    source = f"{handoff.source}, session {handoff.session}" if handoff.session else handoff.source
    tail = [
        _section(
            "Continue",
            f"The session you take over is {source}; you cannot open it, and need not. "
            f"Continue the task from where it stopped, as {handoff.target}.",
        )
    ]
    fixed = sum(len(part) + 2 for part in (*head, *tail))
    steps = _steps(entries, max(limit - fixed - 200, 0))
    return "\n\n".join((*head, _section("What happened, oldest first", steps), *tail))


def _section(title: str, body: str) -> str:
    return f"## {title}\n{body.strip()}"


def _task(text: str) -> str:
    """The task a session was given: the packet's own, when it began from one."""
    if not text.startswith(HANDOFF_MARK):
        return text
    _, found, rest = text.partition("\n## The task\n")
    return rest.split("\n## ", 1)[0].strip() if found else ""


def _cut(text: str, most: int) -> str:
    """`text` within `most` characters, its start and end kept, as a spilled result is."""
    text = text.strip()
    if len(text) <= most:
        return text
    half = max(most // 2 - 20, 0)
    return f"{text[:half]}\n[… {len(text) - 2 * half:,} characters left out …]\n{text[-half:]}"


def _pending(entries: Sequence[TranscriptEntry]) -> list[str]:
    """What is left to do, as the session kept it: the plan's steps not done, the questions it
    asked with no answer, and the last turn, when the limit stopped it before it said anything."""
    found: list[str] = []
    plan = next((e for e in reversed(entries) if e.kind == "tool" and e.tool in PLAN_TOOLS), None)
    if plan is not None:
        found += [f"- [ ] {step}" for step in _plan_steps(plan.text)]
    answered = {e.call for e in entries if e.kind == "answer" and e.call}
    for entry in entries:
        if entry.kind == "question" and (not entry.call or entry.call not in answered):
            found.append(f"- Asked the user, with no answer yet: {_cut(entry.text, STEP_CHARS)}")
    last = next((e for e in reversed(entries) if e.kind == "text"), None)
    if last is not None and last.by == "user":
        found.append(
            "- The last instruction above was stopped by the limit before the session answered it."
        )
    return found


def _plan_steps(text: str) -> list[str]:
    """The steps of a plan not done yet, from Claude Code's `TodoWrite` (`todos`, `content`) or
    Codex's `update_plan` (`plan`, `step`); none when its input cannot be read."""
    try:
        given: object = json.loads(text)
    except ValueError:
        return []
    if not isinstance(given, dict):
        return []
    kept = cast("dict[str, object]", given)
    items = kept.get("todos", kept.get("plan"))
    if not isinstance(items, list):
        return []
    steps: list[str] = []
    for item in cast("list[object]", items):
        if not isinstance(item, dict):
            continue
        step = cast("dict[str, object]", item)
        status = str(step.get("status", ""))
        named = str(step.get("content", step.get("step", ""))).strip()
        if named and status not in _DONE:
            steps.append(f"{named} ({status})" if status else named)
    return steps


def _files(handoff: Handoff, entries: Sequence[TranscriptEntry]) -> list[str]:
    """The files that matter, by path: those changed, and those the user attached."""
    found = [f"- changed: {path}" for path in dict.fromkeys(handoff.changed)]
    attached = dict.fromkeys(path for e in entries for path in e.files)
    found += [f"- attached by the user: {path}" for path in attached]
    return found


def _steps(entries: Sequence[TranscriptEntry], room: int) -> str:
    """The session's steps within `room` characters, the newest kept first, oldest first on the
    page; what was left out, said."""
    lines: list[str] = []
    used = 0
    for entry in reversed(entries):
        line = _step(entry)
        if not line:
            continue
        if used + len(line) + 1 > room:
            break
        lines.append(line)
        used += len(line) + 1
    shown = [e for e in entries if _step(e)]
    left = len(shown) - len(lines)
    lines.reverse()
    if left:
        lines.insert(0, f"({left} earlier steps left out: the folder holds what they did.)")
    return "\n".join(lines) or "(the session kept no steps)"


def _step(entry: TranscriptEntry) -> str:
    """One step, on its own lines."""
    text = _cut(entry.text, STEP_CHARS)
    match entry.kind:
        case "text" if entry.by == "user":
            if entry.text.startswith(HANDOFF_MARK):
                return "- User: (the handoff packet from the session before)"
            return f"- User: {text}" if text else ""
        case "text":
            return f"- Agent: {text}" if text else ""
        case "tool":
            return f"- Tool call {entry.tool}: {text}"
        case "result":
            return f"- Tool result{' (failed)' if entry.failed else ''}: {text}"
        case "question":
            return f"- Asked the user: {text}"
        case "answer":
            return f"- The user answered: {text}"
        case "compacted":
            return f"- (The session was compacted here: {text})"
    return ""
