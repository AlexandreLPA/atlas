"""skill_manage: the tool for writing skills (decided on 09/10/2026), always on.

- **One advertised shape:** the call is an `operations` array, a single change being a list of one,
  each action its own shape so a model cannot put one action's text in another's slot. The flat
  legacy shape (`action`, `name`... at the top) is still accepted, unadvertised.
- **The description** says that use_skill reads a skill, the description's limit is the listing's 60
  characters, a delete archives, and the previous version is kept. There are no categories and no
  curator-only `absorbed_into`: Atlas's skills are one folder deep, and its curator merges nothing.
- **Who writes:** inside a background review (`atlas.skills.reviewing`) a new skill is the
  review's, existing files must be loaded first, and nothing is deleted; anywhere else the skill
  is the user's. What a review's call applied is noted for its summary.
"""

from typing import Any, cast

from atlas.agent import Tool
from atlas.skills import (
    SKILL_MANAGE,
    Origin,
    SkillChangeError,
    SkillLibrary,
    SkillOperation,
    SkillShelves,
    current_review,
)

_DESCRIPTION = (
    "Creates, changes and deletes skills, your procedural memory for recurring kinds of task. "
    "Pass an operations array (one change: a list of one); all or nothing — a failed op "
    "rolls back every skill touched — and the prior version is kept. Ops: create (a whole "
    "SKILL.md, frontmatter name = the skill's; must come before that skill's other ops), patch "
    "(old_string/new_string, a targeted fix, preferred; content alone REPLACES the whole file: "
    "read it with use_skill() first), write_file/remove_file (supporting files), delete (sole op "
    "only; archives the skill). A skill's description: at most 60 chars, a trigger that stands "
    "alone: 'Use when <trigger>. <one-line behavior>.' Lessons, not logs: imperative rule + why, "
    "no PR numbers, dates or incident stories, one rule per lesson, references/ named by topic "
    "(extend before adding)."
)

_NAME = {"type": "string"}


def _text(description: str) -> dict[str, str]:
    """A string argument the model reads `description` about."""
    return {"type": "string", "description": description}


_FILE_PATH = _text(
    "RELATIVE to the skill's folder, never absolute, no leading slash, e.g. "
    "'references/api.md'; under references/, templates/, scripts/ or assets/."
)

#: The keys each action takes, besides `action` and `name`.
_KEYS = {
    "create": {"content"},
    "patch": {"old_string", "new_string", "replace_all", "file_path", "content"},
    "write_file": {"file_path", "file_content"},
    "remove_file": {"file_path"},
    "delete": set[str](),
}


def _shape(action: str, properties: dict[str, Any], required: tuple[str, ...]) -> dict[str, Any]:
    fields: dict[str, Any] = {"name": _NAME, "action": {"type": "string", "enum": [action]}}
    fields.update(properties)
    return {
        "type": "object",
        "properties": fields,
        "required": ["name", "action", *required],
        "additionalProperties": False,
    }


_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "operations": {
            "type": "array",
            "description": (
                "Ops in order, each naming its skill (lowercase, hyphens or underscores, at "
                "most 64 chars). Each action has its own shape; a field of another action's is "
                "invalid."
            ),
            "items": {
                "anyOf": [
                    _shape(
                        "create",
                        {
                            "content": _text(
                                "The whole SKILL.md: YAML frontmatter, then the markdown body."
                            )
                        },
                        ("content",),
                    ),
                    _shape(
                        "patch",
                        {
                            "old_string": _text("The text to replace, matched as edit_file does."),
                            "new_string": _text("What replaces it; empty removes the match."),
                            "replace_all": {
                                "type": "boolean",
                                "description": "Change every match (default false).",
                            },
                            "file_path": _text(
                                "A supporting file, as write_file names it; SKILL.md by default."
                            ),
                        },
                        ("old_string", "new_string"),
                    ),
                    _shape(
                        "patch",
                        {
                            "content": _text(
                                "A new SKILL.md that REPLACES the whole file; the last resort."
                            )
                        },
                        ("content",),
                    ),
                    _shape(
                        "write_file",
                        {
                            "file_path": _FILE_PATH,
                            "file_content": _text("The supporting file's whole text."),
                        },
                        ("file_path", "file_content"),
                    ),
                    _shape(
                        "remove_file",
                        {"file_path": _text("A supporting file, as write_file names it.")},
                        ("file_path",),
                    ),
                    _shape("delete", {}, ()),
                ]
            },
        }
    },
    "required": ["operations"],
}


def skill_manage(library: SkillLibrary | SkillShelves) -> Tool:
    """The tool that writes the skills in `library`: the home's, or a bot's own folder and
    the home's behind it."""

    async def run(arguments: dict[str, Any]) -> str:
        operations = _operations(arguments)
        if isinstance(operations, str):
            return f"[Atlas: {operations} Nothing was changed.]"
        review = current_review()
        try:
            lines = library.apply(
                operations,
                Origin.REVIEW if review is not None else Origin.USER,
                review.guard() if review is not None else None,
                review.note() if review is not None else None,
            )
        except SkillChangeError as error:
            return f"[Atlas: {error}]"
        if review is not None:
            review.wrote(operations)
        return "\n".join(lines)

    return Tool(name=SKILL_MANAGE, description=_DESCRIPTION, parameters=_PARAMETERS, run=run)


def _operations(arguments: dict[str, Any]) -> list[SkillOperation] | str:
    raw = arguments.get("operations")
    if raw is None and "action" in arguments:
        raw = [{key: value for key, value in arguments.items() if key != "operations"}]
    if not isinstance(raw, list) or not raw:
        return "operations must be a non-empty array of operations."
    operations: list[SkillOperation] = []
    for number, item in enumerate(cast("list[object]", raw), start=1):
        if not isinstance(item, dict):
            return f"Operation {number} is not an object."
        fields = cast("dict[str, Any]", item)
        action, name = fields.get("action"), fields.get("name")
        if not isinstance(action, str) or action not in _KEYS:
            return (
                f"Operation {number} has action {action!r}. Use: create, patch, write_file, "
                "remove_file, delete."
            )
        if not isinstance(name, str) or not name.strip():
            return f"Operation {number} ({action}) needs the skill's name."
        extra = sorted(set(fields) - _KEYS[action] - {"action", "name"})
        if extra:
            takes = ", ".join(sorted(_KEYS[action])) or "nothing but the name"
            return (
                f"Operation {number} ({action}) does not take {', '.join(extra)}; it takes {takes}."
            )
        texts = {key: fields.get(key) for key in _KEYS[action] - {"replace_all"}}
        wrong = sorted(
            key for key, value in texts.items() if value is not None and not isinstance(value, str)
        )
        if wrong:
            return f"Operation {number} ({action}): {', '.join(wrong)} must be text."
        operations.append(
            SkillOperation(
                action=action,
                name=name.strip(),
                content=texts.get("content"),
                file_path=texts.get("file_path"),
                file_content=texts.get("file_content"),
                old_string=texts.get("old_string"),
                new_string=texts.get("new_string"),
                replace_all=fields.get("replace_all") is True,
            )
        )
    return operations
