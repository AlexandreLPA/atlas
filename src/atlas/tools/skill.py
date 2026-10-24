"""use_skill: loads a skill's instructions into the conversation.

- **The description lists the skills** the model may load (`atlas.skills.listing`), so it knows
  what exists without a call; the tool exists only when there is a skill to load.
- **Loading answers with the instructions**, the skill's folder (for the files it bundles) and the
  names of those files. Loading the same skill again with the same
  arguments says it is already loaded instead of sending a second copy.
- **Skills in use outlive a compaction**: after one, the latest loading of each
  comes back after the summary, its first 5,000 tokens, all of them within 25,000, the most recent
  first.
- **A load is a use** for the curator (`atlas.skills.SkillUsage`).
- **In a background review** a load always answers in full, and marks the skill read so the review
  may change it: the review never gets the "already loaded" stub, which would skip the read its
  read-before-write rule requires. It leaves the conversation's skills in use alone.
"""

from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from atlas.agent import Tool
from atlas.home import utc_now
from atlas.skills import (
    SKILL_FILE,
    USE_SKILL,
    SkillCatalog,
    SkillShelves,
    SkillUsage,
    current_review,
    listing,
    listing_budget,
    mark_read,
    rendered,
    skill_file,
    support_files,
)

#: Of each skill carried across a compaction, and of all of them (5,000 and 25,000 tokens),
#: in characters at four to a token.
CARRIED_SKILL_CHARS = 20_000
CARRIED_TOTAL_CHARS = 100_000
CARRIED_HEADER = "[Skills in use, kept across the compaction of the conversation]"
#: Files of a skill's folder named when it loads.
LISTED_FILES = 20


class SkillUse:
    """One conversation's use of skills: the tool, and the skills it loaded."""

    def __init__(
        self,
        catalog: SkillCatalog,
        window_tokens: int | None = None,
        usage: SkillUsage | SkillShelves | None = None,
        on_use: Callable[[str], None] | None = None,
    ) -> None:
        """`usage` counts each load: the library's rows, or a bot's shelves, which count it
        where the skill is. `on_use` hears each load, by the skill's name (a plugin's monitor
        waiting for it starts, step 16k)."""
        self.catalog = catalog
        self.window_tokens = window_tokens
        self.usage = usage
        self.on_use = on_use
        #: The latest text each skill loaded with, oldest first.
        self._loaded: OrderedDict[str, str] = OrderedDict()

    def tool(self) -> Tool | None:
        """The use_skill tool, or `None` when there is no skill the model may load."""
        listed = listing(self.catalog.skills(), listing_budget(self.window_tokens))
        if not listed:
            return None
        return Tool(
            name=USE_SKILL,
            description=(
                "Loads a skill: instructions written for a kind of task. When the task matches a "
                "skill below, load it first and follow it.\n\nSkills:\n" + listed
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "arguments": {"type": "string", "description": "What the skill works on."},
                },
                "required": ["name"],
                "additionalProperties": False,
            },
            run=self._run,
            carried=self.carried,
        )

    async def _run(self, arguments: dict[str, Any]) -> str:
        name = arguments.get("name")
        given = arguments.get("arguments")
        extra = given if isinstance(given, str) else ""
        skill = self.catalog.get(name.strip()) if isinstance(name, str) else None
        if skill is None or not skill.model_invocable:
            known = ", ".join(sorted(s.name for s in self.catalog.skills() if s.model_invocable))
            return f"[Atlas: there is no skill named {name!r}. The skills are: {known or 'none'}.]"
        text = rendered(skill, extra)
        if self.usage is not None:
            self.usage.used(skill.name, utc_now())
        if self.on_use is not None:
            self.on_use(skill.name)
        files = "" if skill.command else support_files(skill.folder, LISTED_FILES)
        answer = (
            f"[Skill {skill.name}, from {skill.folder}]{f'\n{files}' if files else ''}\n\n{text}"
        )
        if current_review() is not None:
            mark_read(skill_file(skill.folder) or skill.folder / SKILL_FILE)
            return answer
        if self._loaded.get(skill.name) == text:
            self._loaded.move_to_end(skill.name)
            return f"[Atlas: the skill {skill.name} is already loaded above; follow it.]"
        self._loaded[skill.name] = text
        self._loaded.move_to_end(skill.name)
        return answer

    def carried(self) -> str | None:
        parts: list[str] = []
        left = CARRIED_TOTAL_CHARS
        for name, text in reversed(self._loaded.items()):
            if left <= 0:
                break
            kept = text[: min(CARRIED_SKILL_CHARS, left)]
            if len(kept) < len(text):
                kept += f"\n[Atlas: the rest of {name} was left out; load it again to read it.]"
            parts.append(f"## {name}\n\n{kept}")
            left -= len(kept)
        return "\n\n".join([CARRIED_HEADER, *parts]) if parts else None
