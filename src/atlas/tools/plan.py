"""plan: the task list of a piece of work (decided on 09/10/2026).

One tool: without arguments it reads the list; with `todos` it writes the whole list, or merges
by id with `merge`. Every call returns the whole list. List order is priority, only one item is in
progress at a time, an item is done only once verified, and a parent nests subtasks.

- **The list outlives a compaction**: its open items come back after the summary, finished ones
  left out, since a finished item put back makes the model do it again.
- **The list outlives a restart**: it is rebuilt from the last plan result in the history, so no
  other storage is needed.
- **Atlas V3's plan** (M-32) moved the plan out of the turn's text for the same two reasons; this
  is that plan, as a tool.
- **Deferred, found through tool search** (decided on 09/10/2026): a plan is reached for when a
  multi-step task comes, not on every turn. Its open items still follow a compaction, as the loop
  carries every tool's text, deferred ones too.

Its limits keep a plan replayed from history from growing without bound.
"""

import json
from collections.abc import Sequence
from typing import Any, cast

from atlas.agent import Tool
from atlas.models import AssistantMessage, Message, ToolResult

STATUSES = ("pending", "in_progress", "completed", "cancelled")
MAX_ITEMS = 256
MAX_CONTENT_CHARS = 4_000
#: Opens the plan put back after a compaction.
CARRIED_HEADER = "[Your plan, kept across the compaction of the conversation]"
_MARKERS = {"completed": "[x]", "in_progress": "[>]", "pending": "[ ]", "cancelled": "[~]"}
_ACTIVE = ("pending", "in_progress")
_CUT = "… [cut]"

type Item = dict[str, str]


class PlanError(ValueError):
    """A write the plan refused; the list stays as it was."""


class Plan:
    """One conversation's task list."""

    def __init__(self) -> None:
        self._items: list[Item] = []

    @property
    def items(self) -> list[Item]:
        return [item.copy() for item in self._items]

    def write(self, todos: list[Any], *, merge: bool = False) -> list[Item]:
        """Replace the list, or merge by id; the whole list after. A refused write changes
        nothing."""
        before = self.items
        try:
            if merge:
                self._merge(todos)
            else:
                self._items = _ordered([_valid(todo) for todo in _last_per_id(todos)])
        except PlanError:
            self._items = before
            raise
        del self._items[MAX_ITEMS:]
        _repair_parents(self._items)
        return self.items

    def _merge(self, todos: list[Any]) -> None:
        current = {item["id"]: item for item in self._items}
        for todo in _last_per_id(todos):
            if not isinstance(todo, dict):
                continue
            fields = cast("dict[str, Any]", todo)
            item_id = str(fields.get("id", "")).strip()
            if not item_id:
                continue
            item = current.get(item_id)
            if item is None:
                new = _valid(fields)
                current[new["id"]] = new
                self._items.append(new)
                continue
            if fields.get("content"):
                item["content"] = _capped(str(fields["content"]).strip())
            status = str(fields.get("status") or "").strip().lower()
            if status in STATUSES:
                item["status"] = status
            if "parent" in fields:
                parent = str(fields["parent"] or "").strip()
                if parent:
                    item["parent"] = parent
                else:
                    item.pop("parent", None)
        self._items = _ordered(self._items)

    def carried(self) -> str | None:
        """The open items, for after a compaction; `None` when nothing is open. A parent stays,
        with its real status, while any of its subtasks is open."""
        subtasks: dict[str, list[Item]] = {}
        for entry in self._items:
            owner = entry.get("parent")
            if owner:
                subtasks.setdefault(owner, []).append(entry)

        def branch(entry: Item, indent: int) -> list[str]:
            """`entry`'s line and its open subtasks' lines; none when nothing in it is open."""
            nested = [
                line for sub in subtasks.get(entry["id"], []) for line in branch(sub, indent + 1)
            ]
            state = entry["status"]
            if state not in _ACTIVE and not nested:
                return []
            head = f"{'  ' * indent}- {_MARKERS[state]} {entry['id']}. {entry['content']} ({state})"
            return [head, *nested]

        body = [
            line for entry in self._items if not entry.get("parent") for line in branch(entry, 0)
        ]
        return "\n".join([CARRIED_HEADER, *body]) if body else None

    def result(self) -> str:
        """What every call returns: the whole list and how many items are in each state."""
        items = self.items
        summary = {"total": len(items)} | {
            status: sum(1 for item in items if item["status"] == status) for status in STATUSES
        }
        return json.dumps({"todos": items, "summary": summary}, ensure_ascii=False)

    def hydrate(self, history: Sequence[Message]) -> None:
        """Take the list from the last plan result in `history`, as when a conversation resumes."""
        calls = {
            call.id
            for message in history
            if isinstance(message, AssistantMessage)
            for call in message.tool_calls
            if call.name == "plan"
        }
        for message in reversed(history):
            if not isinstance(message, ToolResult) or message.call_id not in calls:
                continue
            try:
                found: object = json.loads(message.text)
            except ValueError:
                continue
            todos = cast("dict[str, Any]", found).get("todos") if isinstance(found, dict) else None
            if isinstance(todos, list):
                try:
                    self.write(cast("list[Any]", todos))
                except PlanError:
                    continue
                return

    def tool(self) -> Tool:
        async def run(arguments: dict[str, Any]) -> str:
            todos = arguments.get("todos")
            if todos is None:
                return self.result()
            if not isinstance(todos, list):
                return f"[Atlas: todos must be a list, got {type(todos).__name__}.]"
            try:
                self.write(cast("list[Any]", todos), merge=arguments.get("merge") is True)
            except PlanError as error:
                return f"[Atlas: {error}]"
            return self.result()

        return Tool(
            name="plan",
            description=(
                "A checklist for work of three or more steps, or for several tasks the user "
                "asked for at once. When the user wants every one of N things done, give each "
                "its own item, so none goes missing unnoticed. Called without parameters, it "
                "shows the list as it stands.\n"
                "The order of the list is its priority. At most ONE item is in_progress at any "
                "moment. Split a big phase into subtasks by giving them a parent. Set an item "
                "completed only once its work is checked as done, never because it is meant to "
                "be. When a step fails, cancel it and add a revised item in its place. Every "
                "call answers with the whole current list."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "todos": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "content": {"type": "string"},
                                "status": {"type": "string", "enum": list(STATUSES)},
                                "parent": {
                                    "type": "string",
                                    "description": "The id of the item this one is part of.",
                                },
                            },
                            "required": ["id", "content", "status"],
                        },
                    },
                    "merge": {
                        "type": "boolean",
                        "description": "true: update items by id and add new ones. false "
                        "(default): replace the whole list.",
                    },
                },
                "additionalProperties": False,
            },
            run=run,
            deferred=True,
            carried=self.carried,
        )


def _valid(todo: object) -> Item:
    if not isinstance(todo, dict):
        return {"id": "?", "content": "(not an item)", "status": "pending"}
    fields = cast("dict[str, Any]", todo)
    item_id = str(fields.get("id", "")).strip() or "?"
    content = str(fields.get("content", "")).strip()
    if not content:
        raise PlanError(f"Item {item_id!r} has no content; every item needs a description.")
    status = str(fields.get("status", "pending")).strip().lower()
    item = {
        "id": item_id,
        "content": _capped(content),
        "status": status if status in STATUSES else "pending",
    }
    parent = str(fields.get("parent") or "").strip()
    if parent and parent != item_id:
        item["parent"] = parent
    return item


def _capped(content: str) -> str:
    if len(content) <= MAX_CONTENT_CHARS:
        return content
    return content[: MAX_CONTENT_CHARS - len(_CUT)] + _CUT


def _last_per_id(todos: list[Any]) -> list[Any]:
    """One entry per id, the last one, where it stands."""
    last: dict[str, int] = {}
    for index, todo in enumerate(todos):
        if isinstance(todo, dict):
            key = str(cast("dict[str, Any]", todo).get("id", "")).strip() or "?"
        else:
            key = f"\x00{index}"
        last[key] = index
    return [todos[index] for index in sorted(last.values())]


def _repair_parents(items: list[Item]) -> None:
    """Drop a parent that does not exist, and break cycles: such items become top level."""
    known = {entry["id"]: entry for entry in items}
    unknown_parent = [entry for entry in items if entry.get("parent", "") not in ("", *known)]
    for orphan in unknown_parent:
        orphan.pop("parent", None)
    for entry in items:
        if _in_cycle(entry, known):
            entry.pop("parent", None)


def _in_cycle(item: Item, known: dict[str, Item]) -> bool:
    """Whether walking up from `item` through its parents comes back to an item already met."""
    met = {item["id"]}
    above = item.get("parent")
    while above:
        if above in met:
            return True
        met.add(above)
        above = known[above].get("parent")
    return False


def _ordered(items: list[Item]) -> list[Item]:
    """The item in progress goes before any pending one written above it; a nested list keeps
    the order it was written in, so no subtask is torn from its siblings."""
    if any(item.get("parent") for item in items):
        return items
    states = [item["status"] for item in items]
    if "in_progress" not in states:
        return items
    current = states.index("in_progress")
    waiting = [index for index, state in enumerate(states[:current]) if state == "pending"]
    if not waiting:
        return items
    rest = items[:current] + items[current + 1 :]
    rest.insert(waiting[0], items[current])
    return rest
