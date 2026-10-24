"""memory: the memory tool, which keeps the profile and the memory (`atlas.memory`).

Decided on 09/10/2026 (docs/ARCHITECTURE.md, Memory). The description is never trimmed to save
context: it is too critical to risk degradation. Its two shapes (one change, or `operations` applied
together against the limit), `new_text` as another name for `content`, a missing target read as
`memory`, and the reply that says the write is done, so the model does not repeat it.

**A turn that keeps failing to consolidate stops being told to retry**: after 3 failures in a row
the reply says to leave memory as it is and answer the user. This tool does not see turns, so it
counts failures in a row and starts over after the stop or a success.

**In a review nobody watches** (`atlas.memory.proposing`), a call that would replace or remove an
entry is not applied: it becomes a memory proposal, adds and all. It is refused first wherever the
write itself would be. With nowhere to keep proposals, it is refused: staging fails closed.
What the review saved and proposed is noted for its summary.
"""

from typing import Any, cast

from atlas.agent import Tool
from atlas.memory import (
    MEMORY_TOOL,
    Action,
    Applied,
    MemoryFiles,
    Operation,
    Proposals,
    Target,
    current_proposing,
    replaces_or_removes,
    usage,
)

#: Consolidation failures in a row before the tool tells the model to stop.
MAX_CONSOLIDATION_FAILURES = 3

_DESCRIPTION = (
    "Keeps durable facts in memory that persists across conversations. Every later turn carries "
    "it: keep entries short and high in signal.\n\n"
    "HOW: put ALL changes in ONE call, an 'operations' array (items: "
    "{action, content?, old_text?}). It applies all or nothing, and the char limit is checked "
    "only on the FINAL result: one call can remove or replace stale entries to make room AND "
    "add new ones, even when the add alone would overflow. The reply shows current/limit chars "
    "and confirms the write; one batch ends the update, so don't repeat it. The bare "
    "action/content/old_text fields are for a lone change only.\n\n"
    "WHEN: only for facts true in EVERY conversation, whatever the task: who the user is, stable "
    "facts of the environment, standing conventions no task owns. What you learn doing a task "
    "(its procedures and pitfalls, the user's preferences and corrections for that kind of work) "
    "goes in that task's skill via skill_manage, which loads only when relevant; memory rides on "
    "every turn and must stay small.\n\n"
    "IF FULL: the add is refused and shows the current entries. Then send ONE batch that both "
    "cuts or shortens enough stale entries and adds the new one.\n\n"
    "TARGETS: 'user' holds who the user is (name, role, preferences, style); 'memory' holds your "
    "notes (environment, conventions, tool quirks, lessons).\n\n"
    "SKIP: the trivial or obvious, facts easy to find again, raw data dumps, task progress, logs "
    "of finished work, temporary TODO state (conversation_search finds those). A reusable "
    "procedure is a skill, not memory."
)

_OPERATION = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["add", "replace", "remove"]},
        "content": {
            "type": "string",
            "description": "The entry's text, for add and replace; on replace, the COMPLETE new "
            "entry (the whole entry is written over). Also accepted as 'new_text'.",
        },
        "new_text": {"type": "string", "description": "Another name for 'content' in a batch op."},
        "old_text": {
            "type": "string",
            "description": "Text inside the entry replace or remove acts on, to find it.",
        },
    },
    "required": ["action"],
}


def memory(files: MemoryFiles, proposals: Proposals | None = None) -> Tool:
    """The memory tool over the profile and the memory in `files`; what an unattended review would
    take out goes to `proposals`."""
    failures = 0

    async def run(arguments: dict[str, Any]) -> str:
        nonlocal failures
        target_name = arguments.get("target") or Target.MEMORY.value
        if target_name not in (Target.USER.value, Target.MEMORY.value):
            return "[Atlas: target must be 'user' (the user's profile) or 'memory' (your notes).]"
        target = Target(target_name)
        operations = _operations(arguments)
        if isinstance(operations, str):
            return f"[Atlas: {operations}]"
        unattended = current_proposing()
        if unattended is not None and replaces_or_removes(operations):
            if proposals is None:
                return (
                    "[Atlas: the background review may not replace or remove memory entries "
                    "('replace'/'remove', including in a batch); 'add' is still available.]"
                )
            made = proposals.propose(target, operations, unattended.conversation_id)
            if not isinstance(made, Applied):
                failures = 0
                unattended.made.append(made)
                return (
                    "Proposed, not applied: the background review may not replace or remove "
                    "memory entries unattended, so this change waits for the user to approve or "
                    f"discard it (proposal {made.id}). This update is complete — do not repeat it."
                )
            applied = made
        else:
            applied = files.change(target, operations)
        if applied.entries is not None:
            failures = 0
            if unattended is not None and applied.changed:
                unattended.saved.add(target)
            return _saved(files, target, applied, len(operations))
        if applied.consolidation:
            failures += 1
            if failures > MAX_CONSOLIDATION_FAILURES:
                failures = 0
                return (
                    "[Atlas: saving to memory failed several times in a row. Stop retrying: leave "
                    "memory as it is, and go on with your reply to the user. The fact can be saved "
                    "in a later turn.]"
                )
        return _failure(files, target, applied)

    return Tool(
        name=MEMORY_TOOL,
        description=_DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["add", "replace", "remove"],
                    "description": "What to do (single-op shape). Omit it with 'operations'.",
                },
                "target": {
                    "type": "string",
                    "enum": ["memory", "user"],
                    "description": "The store to change: 'user' is the user's profile, "
                    "'memory' your own notes.",
                },
                "content": {
                    "type": "string",
                    "description": "The entry's text, required for 'add' and 'replace'. On "
                    "'replace' it is the COMPLETE new entry: the matched entry is written over "
                    "whole, so include all you want to keep. 'new_text' is an alias, meaning the "
                    "same full entry.",
                },
                "old_text": {
                    "type": "string",
                    "description": "REQUIRED for 'replace' and 'remove' (single-op shape): a "
                    "short, unique piece of text that IDENTIFIES the existing entry to change. "
                    "It locates the entry; it is not cut out. Omit only for 'add'.",
                },
                "new_text": {
                    "type": "string",
                    "description": "Alias of 'content' (single-op shape): on 'replace', the "
                    "COMPLETE new entry, not an edit of old_text. Given both, 'content' is used.",
                },
                "operations": {
                    "type": "array",
                    "description": "Batch shape: operations applied all or nothing, the char "
                    "budget checked on the end result. Preferred for several changes or to "
                    "consolidate and make room. Items: {action, content?, old_text?}.",
                    "items": _OPERATION,
                },
            },
            "required": ["target"],
        },
        run=run,
    )


def _operations(arguments: dict[str, Any]) -> list[Operation] | str:
    """The operations a call asks for, or why they cannot be read."""
    raw = arguments.get("operations")
    if raw:
        if not isinstance(raw, list):
            return "operations must be a list of {action, content?, old_text?} objects."
        items = cast("list[object]", raw)
    else:
        items = [arguments]
    operations: list[Operation] = []
    for number, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            return f"operation {number} must be an object {{action, content?, old_text?}}."
        fields = cast("dict[str, Any]", item)
        action = fields.get("action")
        if action not in ("add", "replace", "remove"):
            where = "" if len(items) == 1 else f"operation {number}: "
            return f"{where}action must be add, replace or remove."
        content = fields.get("content") or fields.get("new_text") or ""
        old_text = fields.get("old_text") or ""
        operations.append(Operation(Action(action), str(content), str(old_text)))
    return operations


def _saved(files: MemoryFiles, target: Target, applied: Applied, count: int) -> str:
    entries = applied.entries or []
    if not applied.changed:
        said = "Entry already exists (no duplicate added)."
    elif count > 1:
        said = f"Applied {count} operation(s)."
    elif applied.replaced:
        said = "Entry replaced."
    elif applied.removed:
        said = "Entry removed."
    else:
        said = "Entry added."
    lines = [f"{said} {target}: {usage(entries, files.limits[target])}, {len(entries)} entries."]
    # What a whole-entry write overwrote is said back, so the loss is never silent.
    for number, entry in applied.replaced.items():
        where = "" if count == 1 else f" by operation {number}"
        lines.append(f"Replaced{where}, whole: {entry}")
    for number, entry in applied.removed.items():
        where = "" if count == 1 else f" by operation {number}"
        lines.append(f"Removed{where}: {entry}")
    lines.append("Write saved. This update is complete — do not repeat it.")
    return "\n".join(lines)


def _failure(files: MemoryFiles, target: Target, applied: Applied) -> str:
    lines = [f"[Atlas: {applied.error}]"]
    if applied.matches:
        lines += ["Entries it matched:", *(f"- {entry}" for entry in applied.matches)]
    if applied.closest:
        lines += ["Closest entries:", *(f"- {entry}" for entry in applied.closest)]
    if applied.show_entries:
        entries = files.entries(target)
        limit = files.limits[target]
        if entries:
            lines.append(f"Current entries of {target} ({usage(entries, limit)}):")
            lines += [f"- {entry}" for entry in entries]
        else:
            lines.append(f"The {target} store is empty.")
    return "\n".join(lines)
