"""A skill, how it is listed and loaded, the rules a write must pass, and the texts of the skill
review. Pure: text in, text out.

A skill is a folder with a `SKILL.md` inside, in Claude Code's format (decided 2026-10-07, ROADMAP,
Plugins): YAML frontmatter, then the instructions. What Atlas reads, as Claude Code reads it:

- `name`, by default the folder's name; `description`, by default the body's first line of prose;
  `when_to_use`, added to the description. Fields Atlas does not read are ignored, not refused.
- `disable-model-invocation: true` keeps the skill out of the model's reach: unlisted and refused.
- `metadata.requires` says what the skill needs: `bins`, `mcp` and `os` (`Needs`, step 16s).
- **The listing** names every skill; descriptions fill a budget of 1% of the model's context window
  and each is cut at 60 characters (decided 2026-10-09, so skill_manage fits the always-on
  budget). Past the budget a skill is listed by name only.
- **Loading** substitutes `${CLAUDE_SKILL_DIR}` with the skill's folder and `$ARGUMENTS` with what
  the call passed, or appends `ARGUMENTS: ...` when no placeholder took them.

The folder is the only layout: Atlas V3 decided it on 22/09/2026 ("solução definitiva, sem
duplicatas") after 10 of 19 loose skill files pointed at code outside themselves by absolute path.

**Writing** has these rules: a new skill's name is lowercase and at most 64 characters; its
frontmatter has a name and a description, the description at most 1,024 characters and, on a new
skill, at most 60, so the listing never cuts the trigger; `SKILL.md` holds at most 100,000
characters; a support file lives under `references/`, `templates/`, `scripts/` or `assets/`, at
most 1 MiB; and the frontmatter's name is the folder's, because the catalog finds a skill by that
name.

**Versions** are named as Atlas V3 named them (`domain/versionamento.py`): the UTC instant, unique
against what is already on disk, because Windows reads the clock in steps of about 15.6 ms (V3
measured 2 distinct values in 200 calls), and ordered by name, never by a file's time, which a
copy rewrites.
"""

import difflib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, cast

import yaml

from atlas.skills.errors import SkillError

SKILL_FILE = "SKILL.md"
USE_SKILL = "use_skill"
SKILL_MANAGE = "skill_manage"
#: Each skill's description and when_to_use together, in the listing, cut to 57 characters and an
#: ellipsis past it.
DESCRIPTION_CHARS = 60
#: The listing's budget when the model's window is unknown.
LISTING_CHARS = 8_000
#: A name that can be a folder: no path separators, no leading dot.
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: A plugin's skill, named after its plugin as Claude Code names it (`video:render`).
PLUGIN_SKILL = re.compile(r"^[a-z0-9][a-z0-9-]*:[A-Za-z0-9][A-Za-z0-9._-]*$")
_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0"})
#: The systems a skill may say it works on, and the other names it may give them.
SYSTEMS = ("windows", "macos", "linux")
_SYSTEM_NAMES = {"win32": "windows", "darwin": "macos"}


@dataclass(frozen=True, slots=True)
class Needs:
    """What a skill says it needs, under `metadata.requires` in its frontmatter (step 16s): the
    programs it runs (`bins`), the MCP servers it calls (`mcp`) and the systems it works on
    (`os`). The marketplace checks them before it installs a skill; a skill that misses one still
    loads, and the model may ask for what is missing."""

    programs: tuple[str, ...] = ()
    servers: tuple[str, ...] = ()
    systems: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Skill:
    name: str
    description: str
    #: The instructions, without the frontmatter.
    body: str
    folder: Path
    #: `False` when the frontmatter says `disable-model-invocation: true`.
    model_invocable: bool = True
    #: `False` when the frontmatter says `user-invocable: false`: Claude Code's skill the model
    #: loads but the `/` menu does not offer.
    user_invocable: bool = True
    #: A plugin's command (`commands/<name>.md`), one file where a skill is a folder: its
    #: folder holds the plugin's other commands, which are not its files.
    command: bool = False
    needs: Needs = Needs()


def parse_command(text: str, file: Path) -> Skill:
    """A plugin's command, Claude Code's `commands/<name>.md`, read as a skill as Claude Code reads
    it now: named by its file, the frontmatter optional, its `description` or its first line
    describing it. Raises `SkillError` as `parse_skill` does."""
    fields, body = _split(text, file.parent)
    name = file.stem
    if not NAME.fullmatch(name):
        raise SkillError(f"The command {file} has a name that cannot be a skill's.")
    about = " ".join(
        str(fields[key]).strip() for key in ("description", "when_to_use") if fields.get(key)
    )
    return Skill(
        name=name,
        description=_flat(about) or _first_line(body),
        body=body.strip(),
        folder=file.parent,
        model_invocable=str(fields.get("disable-model-invocation", "")).lower() not in _TRUE,
        user_invocable=str(fields.get("user-invocable", "true")).lower() not in _FALSE,
        command=True,
    )


def parse_skill(text: str, folder: Path) -> Skill:
    """A skill from its `SKILL.md` text. Raises `SkillError` when the frontmatter is broken or the
    name cannot be a folder's."""
    fields, body = _split(text, folder)
    name = str(fields.get("name") or folder.name).strip()
    if not NAME.fullmatch(name):
        raise SkillError(
            f"The skill in {folder} is named {name!r}, which cannot be a folder's name. Use "
            "letters, digits, dots, dashes and underscores."
        )
    about = " ".join(
        str(fields[key]).strip() for key in ("description", "when_to_use") if fields.get(key)
    )
    return Skill(
        name=name,
        description=_flat(about) or _first_line(body),
        body=body.strip(),
        folder=folder,
        model_invocable=str(fields.get("disable-model-invocation", "")).lower() not in _TRUE,
        user_invocable=str(fields.get("user-invocable", "true")).lower() not in _FALSE,
        needs=_needs(fields),
    )


def _needs(fields: Mapping[str, Any]) -> Needs:
    metadata: object = fields.get("metadata")
    requires: object = (
        cast("Mapping[str, object]", metadata).get("requires")
        if isinstance(metadata, Mapping)
        else None
    )
    if not isinstance(requires, Mapping):
        return Needs()
    declared = cast("Mapping[str, object]", requires)

    def names(key: str) -> tuple[str, ...]:
        value = declared.get(key)
        listed = [value] if isinstance(value, str) else value
        if not isinstance(listed, list):
            return ()
        clean = (str(item).strip() for item in cast("list[object]", listed))
        return tuple(dict.fromkeys(item for item in clean if item))

    systems = (_SYSTEM_NAMES.get(name.lower(), name.lower()) for name in names("os"))
    return Needs(names("bins"), names("mcp"), tuple(dict.fromkeys(systems)))


def _split(text: str, folder: Path) -> tuple[dict[str, Any], str]:
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            head = "\n".join(lines[1:index])
            try:
                loaded: object = yaml.safe_load(head) if head.strip() else {}
            except yaml.YAMLError as error:
                raise SkillError(
                    f"The frontmatter of {folder / SKILL_FILE} is not valid YAML: {error}"
                ) from error
            if loaded is None:
                loaded = {}
            if not isinstance(loaded, dict):
                raise SkillError(f"The frontmatter of {folder / SKILL_FILE} is not a table.")
            return cast("dict[str, Any]", loaded), "\n".join(lines[index + 1 :])
    return {}, text


def _first_line(body: str) -> str:
    for line in body.splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean:
            return clean
    return ""


def _flat(text: str) -> str:
    return " ".join(text.split())


def listing(skills: list[Skill], budget: int) -> str:
    """One line per skill the model may load, names always, descriptions while the budget lasts."""
    lines: list[str] = []
    used = 0
    for skill in sorted(skills, key=lambda s: s.name.lower()):
        if not skill.model_invocable:
            continue
        about = cut_description(skill.description)
        line = f"- {skill.name}: {about}" if about else f"- {skill.name}"
        if about and used + len(line) > budget:
            line = f"- {skill.name}"
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)


def listing_budget(window_tokens: int | None) -> int:
    """1% of the model's context window, in characters (four to a token); a fixed budget
    when the window is unknown."""
    if window_tokens is None:
        return LISTING_CHARS
    return max(1_000, window_tokens // 100 * 4)


def rendered(skill: Skill, arguments: str) -> str:
    """The instructions as the model receives them."""
    text = skill.body.replace("${CLAUDE_SKILL_DIR}", str(skill.folder))
    if "$ARGUMENTS" in text:
        return text.replace("$ARGUMENTS", arguments)
    if arguments.strip():
        return f"{text}\n\nARGUMENTS: {arguments.strip()}"
    return text


def cut_description(text: str) -> str:
    """The listing's cut of a description: past 60 characters, the first 57 and an ellipsis."""
    if len(text) <= DESCRIPTION_CHARS:
        return text
    return text[: DESCRIPTION_CHARS - 3] + "..."


# -- writing ----------------------------------------------------------------------------------

#: A new skill's name: lowercase, so it is the same folder on every system.
NEW_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
NAME_CHARS = 64
MAX_DESCRIPTION_CHARS = 1_024
#: `SKILL.md` and any text file of a skill, in characters (about 36,000 tokens).
SKILL_CHARS = 100_000
SUPPORT_FILE_BYTES = 1_048_576
#: Where a skill's support files live.
SUPPORT_FOLDERS = ("assets", "references", "scripts", "templates")


def name_problem(name: str) -> str | None:
    """Why `name` cannot be a new skill's name, or `None`."""
    if not name:
        return "Skill name is required."
    if len(name) > NAME_CHARS:
        return f"Skill name exceeds {NAME_CHARS} characters."
    if not NEW_NAME.fullmatch(name):
        return (
            f"Invalid skill name '{name}'. Use lowercase letters, numbers, hyphens, dots, and "
            "underscores. Must start with a letter or digit."
        )
    return None


def skill_text_problem(text: str, name: str, *, new: bool) -> str | None:
    """Why `text` cannot be skill `name`'s `SKILL.md`, or `None`. A new skill's description must
    fit the listing (enforced on create only, so older skills stay editable)."""
    if not text.strip():
        return "The content is empty."
    if len(text) > SKILL_CHARS:
        return (
            f"SKILL.md has {len(text):,} characters, over its limit of {SKILL_CHARS:,}. Keep "
            "SKILL.md short and move the detail into support files under references/ or "
            "templates/."
        )
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return "SKILL.md must start with YAML frontmatter, a '---' line first (as any skill does)."
    if not any(line.strip() == "---" for line in lines[1:]):
        return "The frontmatter is not closed: add a '---' line after its last field."
    try:
        fields, body = _split(text, Path(name))
    except SkillError as error:
        return f"The frontmatter is not valid YAML: {error}"
    for field in ("name", "description"):
        if field not in fields:
            return f"The frontmatter needs a '{field}' field."
    if str(fields["name"]).strip() != name:
        return (
            f"The frontmatter's name is '{fields['name']}', but the skill is '{name}'. They must "
            "be the same: Atlas finds a skill by its name."
        )
    description = str(fields["description"]).strip().strip("'\"")
    if len(description) > MAX_DESCRIPTION_CHARS:
        return f"The description is longer than {MAX_DESCRIPTION_CHARS} characters."
    if new and len(description) > DESCRIPTION_CHARS:
        return (
            f"The description has {len(description)} chars, and a new skill's must fit the "
            f"{DESCRIPTION_CHARS}-char listing: one sentence, its trigger first, a period at the "
            f"end. A longer one is cut to {DESCRIPTION_CHARS - 3} chars + '...', which loses what "
            "tells the model when to use the skill. Put the detail in the body."
        )
    if not body.strip():
        return "SKILL.md needs content after the frontmatter: the instructions, the procedures."
    return None


def support_path(file_path: str) -> PurePosixPath | str:
    """A support file's path inside its skill's folder, or why it cannot be one. `SKILL.md` names
    the skill's own file."""
    if not file_path:
        return "file_path is required."
    path = PurePosixPath(file_path.replace("\\", "/"))
    if path.is_absolute() or re.match(r"^[A-Za-z]:", file_path):
        return (
            f"'{file_path}' is absolute. Give the path RELATIVE to the skill's folder, e.g. "
            "'references/api.md'."
        )
    if ".." in path.parts:
        return "Path traversal ('..') is not allowed."
    parts: tuple[str, ...] = path.parts
    if parts == (SKILL_FILE,):
        return path
    folder = parts[0] if parts else ""
    if folder not in SUPPORT_FOLDERS:
        return f"File must be under one of: {', '.join(SUPPORT_FOLDERS)}. Got: '{file_path}'"
    if len(parts) < 2:
        return f"Provide a file path, not just a folder. Example: '{folder}/myfile.md'"
    return path


def replaced(content: str, old: str, new: str, every: bool) -> tuple[str, int] | str:
    """`content` with `old` replaced by `new`, and how many times; or why it cannot be. The
    matching is edit_file's: exact, and line endings only are forgiven."""
    if not old:
        return "old_string is required: the exact text to replace."
    if old == new:
        return "old_string and new_string are the same, so there is nothing to change."
    if "\r\n" in content and "\r\n" not in old:
        old, new = old.replace("\n", "\r\n"), new.replace("\n", "\r\n")
    count = content.count(old)
    if count == 0:
        return (
            "old_string was not found. Load the file again: the text must match exactly, "
            "whitespace and indentation included."
        )
    if count > 1 and not every:
        return (
            f"old_string appears {count} times. Include more surrounding lines to make it "
            "unique, or set replace_all to change every one."
        )
    if every:
        return content.replace(old, new), count
    return content.replace(old, new, 1), 1


# -- versions ---------------------------------------------------------------------------------

#: Versions kept per skill (Atlas V3's: a skill is a few KB, and keeping too many costs little next
#: to throwing away the one good copy).
VERSIONS_KEPT = 20
_STAMP = "%Y%m%dT%H%M%S%f"
_VERSION = re.compile(r"^(\d{8}T\d{12})(?:__(\d+))?$")


def version_name(existing: Iterable[str], now: datetime) -> str:
    """A free name for a new version at `now` (UTC), given the names already on disk."""
    base = now.strftime(_STAMP)
    taken = set(existing)
    if base not in taken:
        return base
    suffix = 2
    while f"{base}__{suffix}" in taken:
        suffix += 1
    return f"{base}__{suffix}"


def newest_first(names: Iterable[str]) -> list[str]:
    """The version names among `names`, newest first; anything else is left out."""
    found: list[tuple[str, int, str]] = []
    for name in names:
        match = _VERSION.fullmatch(name)
        if match:
            found.append((match.group(1), int(match.group(2) or 1), name))
    return [name for _, _, name in sorted(found, reverse=True)]


def to_prune(names: Iterable[str], keep: int = VERSIONS_KEPT) -> list[str]:
    """The versions to drop so the newest `keep` stay."""
    return newest_first(names)[keep:]


# -- usage and the curator --------------------------------------------------------------------


class Origin(StrEnum):
    #: Made by the background review: the curator's to archive.
    REVIEW = "review"
    #: Written by hand, or made in a conversation: never archived on its own.
    USER = "user"


#: The curator: how often it runs, how long Atlas must be idle first, and when a skill of the
#: review is archived: at 30 days, because a skill nobody has loaded in a month is prompt weight,
#: not knowledge, and archiving is undone.
CURATOR_EVERY = timedelta(days=7)
CURATOR_IDLE = timedelta(hours=2)
ARCHIVE_AFTER = timedelta(days=30)
#: A skill never used is spared until it is this old.
UNUSED_GRACE = timedelta(days=14)


@dataclass(frozen=True, slots=True)
class SkillRecord:
    """What Atlas knows of one skill's life, beside its files."""

    name: str
    origin: Origin
    created_at: datetime
    use_count: int = 0
    last_used_at: datetime | None = None
    patch_count: int = 0
    last_patched_at: datetime | None = None
    restored_at: datetime | None = None
    archived_at: datetime | None = None
    #: Whose folder it is in: `""` for the home's library, or a bot's name.
    owner: str = ""
    #: Who made it: the main agent, a bot by its name, or `None` for the user (written by
    #: hand, or made before this was kept). It stays when the skill moves to the library.
    creator: str | None = None

    @property
    def last_activity(self) -> datetime | None:
        """The latest load, change or restore. A restore counts: kept on the old clock,
        the skill would be archived again on the next pass."""
        moments = [m for m in (self.last_used_at, self.last_patched_at, self.restored_at) if m]
        return max(moments) if moments else None


def to_archive(records: Iterable[SkillRecord], now: datetime) -> list[str]:
    """The skills the curator archives at `now`: the review's, not archived yet, with no load or
    change for `ARCHIVE_AFTER`, counted from their creation when they had none."""
    names: list[str] = []
    for record in sorted(records, key=lambda r: r.name):
        if record.origin is not Origin.REVIEW or record.archived_at is not None:
            continue
        anchor = record.last_activity or record.created_at
        if record.use_count == 0 and anchor > now - UNUSED_GRACE:
            continue  # absence of evidence, not staleness
        if anchor <= now - ARCHIVE_AFTER:
            names.append(record.name)
    return names


# -- the skill review -------------------------------------------------------------------------

#: Model calls without a skill change before a skill review.
SKILL_REVIEW_EVERY = 10

#: A skill diff's limits: the files and lines shown for a skill_manage call.
SKILL_DIFF_FILES = 6
SKILL_DIFF_LINES = 80
#: Unchanged lines around each change. A skill's lines are whole paragraphs, so one is enough to
#: place a change (decided 13/10/2026).
SKILL_DIFF_CONTEXT = 1


def skill_diff(files: Mapping[str, tuple[str | None, str | None]]) -> str:
    """The skill diff of `files`: what each held before and holds after, by its path inside the
    skill, `None` for a file that was not or is no longer there. `SKILL.md` comes first; a file
    left as it was is skipped; past the limits a last line counts what was left out. Empty when
    nothing changed."""
    sections: list[list[str]] = []
    for path in sorted(files, key=lambda path: (path != SKILL_FILE, path)):
        before, after = files[path]
        lines = list(
            difflib.unified_diff(
                (before or "").splitlines(),
                (after or "").splitlines(),
                f"a/{path}" if before is not None else "/dev/null",
                f"b/{path}" if after is not None else "/dev/null",
                n=SKILL_DIFF_CONTEXT,
                lineterm="",
            )
        )
        if lines:
            sections.append(lines)
    shown: list[str] = []
    for section in sections[:SKILL_DIFF_FILES]:
        shown += section[: SKILL_DIFF_LINES - len(shown)]
    omitted = sum(len(section) for section in sections) - len(shown)
    if omitted:
        files_left = max(0, len(sections) - SKILL_DIFF_FILES)
        more = f" in {files_left} more file(s)" if files_left else ""
        shown.append(f"[Atlas: {omitted} more line(s){more}; the skill's folder has the rest.]")
    return "\n".join(shown)


#: The shape contract for anything written into a skill: it prevents the hoarding library, one
#: references/ file per session and incident narration.
LESSON_LAYER = (
    "A skill is the playbook for one class of task, done the quickest correct way and the way "
    "THIS user wants it done: the procedure, the tools and commands known to work, their order, "
    "how the user likes the result to look, and the traps that waste time. Following it, a later "
    "conversation should give the user what they want on the first attempt. How to write one "
    "well:\n"
    "  • Lead with the procedure: the steps in the order they happen, with the real commands, tool "
    "calls and decision points. Hang each lesson and pitfall on the step it concerns.\n"
    "  • Write a pitfall as an imperative rule that generalizes, plus one clause on WHY (the "
    "mechanism behind it). For example: 'Before widening a helper's signature, grep the test tree "
    "for the SYMBOL — mocks written by hand copy the old shape and break on a shard you did not "
    "run.' Do not tell the story of this conversation.\n"
    "  • Leave out PR and issue numbers, dates, ticket IDs and the user's words quoted as content: "
    "the rule has to hold without the incident that taught it. Quote briefly ONLY when the quote "
    "states the rule more clearly than anything else would.\n"
    "  • A lesson learned twice is still ONE rule. Before adding one, search the skill and its "
    "references/ for where it is already said, and sharpen or clarify that sentence instead of "
    "writing it a second time.\n"
    "  • Do not repeat what the environment already teaches: a repository's AGENTS.md files, the "
    "tool descriptions, anything else loaded every time. The skill holds the WORKFLOW and the "
    "pitfalls, not a map of the codebase or a list of a tool's parameters.\n"
    "  • Rules that always apply (the user's standing preferences, checks every run of the task "
    "must pass) go in SKILL.md itself, in full. references/ holds depth needed only now and then — "
    "a decision table, a recipe, a note on the domain — one reusable topic per file, never "
    "'<date>-<incident>.md'. Extend an existing references/ file before making a new one: a skill "
    "buried in dozens of one-off references has failed, not grown.\n"
    "  • When the skill is wrong, correct it where it stands: rewrite the sentence that misled, "
    "never add an 'UPDATE: actually...' below it.\n\n"
)

#: What never becomes a skill.
DO_NOT_CAPTURE = (
    " (each would become a lasting rule you set for yourself, and it would get in your way once "
    "the environment changes):\n"
    "  • Failures that come from the environment: a binary or package not installed, errors of a "
    "fresh install, paths broken by a migration, 'command not found', credentials not set up. The "
    "user can fix those — they are not lasting rules.\n"
    "  • Claims that a tool or feature is broken ('the browser tools fail', 'tool X never "
    "works', 'run_command can't do Y'). They set into refusals the agent keeps quoting against "
    "itself for months after the real problem was fixed.\n"
    "  • Passing errors of this conversation that cleared before it ended. If trying again worked, "
    "what to keep is the retry, not the failure.\n"
    "  • The story of a one-off task. 'Summarize today's market' or 'analyze this PR' is not a "
    "class of work that earns a skill.\n\n"
    "  • Failures nobody solved: when the conversation ended WITHOUT a method that actually "
    "worked — several attempts failed and the user was told to check by hand — do NOT write those "
    "attempts up as a 'reliable workflow' or a 'recommended approach'. That turns a string of "
    "untested failures into guidance a later conversation will trust and repeat. Either say "
    "'Nothing to save', or, only when you are independently sure of an alternative that really "
    "works (not one you merely guess might), capture ONLY that alternative — never the dead ends, "
    "and never presented as best practice.\n\n"
    "When a tool failed because of how things were set up, capture the FIX (the install command, "
    "the config step, the environment variable to set) in a skill that already covers setup or "
    "troubleshooting — never a standalone rule that the tool is broken.\n\n"
)

#: The read-before-write paragraph: use_skill loads SKILL.md, read_file a support file.
_READ_BEFORE_WRITE = (
    "Read-before-write (ENFORCED: skill_manage refuses a write that skips it). To patch or rewrite "
    "the SKILL.md of a skill that exists, first load it with use_skill(name) in this review. To "
    "overwrite or remove a supporting file that EXISTS, first read that very file with read_file "
    "(use_skill's answer names the skill's folder). Text shown earlier in the conversation does "
    "NOT count: the guard wants a load made during this review, and what you write must start "
    "from what you just read. A brand-new skill, or a NEW supporting file, needs no read first. "
    "When a write comes back refused with a read-before-write error, load the target it names "
    "once, retry the write once, and do not loop.\n\n"
)

#: The skill review's instructions: skills are listed in use_skill's description and loaded with
#: use_skill, and a support file is written by a write_file operation of skill_manage. Atlas's
#: curator does not consolidate overlapping skills, so the review is not told it does.
SKILL_REVIEW_PROMPT = (
    "Go over the conversation above and bring the skill library up to date. Lean toward "
    "ACTING: most conversations leave at least one skill change behind, if only a small one, and "
    "a pass that changes nothing has wasted a lesson; it is not a safe default.\n\n"
    "What the library should look like: CLASS-LEVEL skills, each a SKILL.md of rules that always "
    "apply plus a few topical files of depth in `references/`. Neither a long flat list of narrow "
    "skills that each served one conversation, nor an umbrella piling up a references/ file per "
    "conversation. This decides HOW you change the library, not WHETHER.\n\n"
    + LESSON_LAYER
    + "Any one of these signals calls for a change:\n"
    "  • The user corrected your style, tone, format, legibility or verbosity. Signs of "
    "frustration ('quit doing X', 'way too long', 'not in this format', 'no need to explain', "
    "'only the answer, please', 'I can't stand it when you Y') and an explicit 'remember this' "
    "are FIRST-CLASS signals for skills, not only for memory. Write the "
    "preference into the skill(s) it concerns, so the next conversation knows it from the start.\n"
    "  • The user corrected your workflow, your approach or the order of your steps. Put the "
    "correction in the skill that governs that class of task, as a pitfall or an explicit step.\n"
    "  • A technique, fix, workaround, debugging path or way of using a tool came up that is not "
    "trivial and would help a later conversation. Capture it.\n"
    "  • A skill loaded or consulted in this conversation proved wrong, outdated or short of a "
    "step. Patch it NOW.\n\n"
    "Order of preference: take the first action that fits, and when a signal above fired, do "
    "take one:\n"
    "  1. UPDATE A CURRENTLY-LOADED SKILL. Find the skills this conversation loaded with "
    "use_skill. When one of them covers the ground of the new lesson, PATCH that one first "
    "(loading it again with use_skill in this review; see Read-before-write below). It was the "
    "skill in play, so it is the one to extend, no matter who wrote it: skills written by hand "
    "are yours to improve as well.\n"
    "  2. UPDATE AN EXISTING UMBRELLA (find it among the skills listed in use_skill's "
    "description, then load it with use_skill). When no loaded skill fits but a class-level skill "
    "already does, patch that one: add a subsection or a pitfall, or widen a trigger.\n"
    "  3. ADD A SUPPORT FILE under an existing umbrella. A skill can carry three kinds, each in "
    "its own folder:\n"
    "     • `references/<topic>.md`: depth needed only now and then, such as a decision table, a "
    "recipe to reproduce something, a provider's quirks, condensed notes on a domain or excerpts "
    "of an API. Name it after its TOPIC and extend the file that already covers the topic; never "
    "one file per conversation or per incident, and no pasted error transcripts: boil them down "
    "to the rule.\n"
    "     • `templates/<name>.<ext>`: starting points to copy and adapt (boilerplate configs, "
    "scaffolding, a known-good example to reproduce with changes).\n"
    "     • `scripts/<name>.<ext>`: actions the skill runs directly and that can be re-run as they "
    "are (verification scripts, fixture generators, deterministic probes, whatever the agent "
    "should run instead of typing it out each time).\n"
    "     Write them with a write_file operation of skill_manage whose file_path starts with "
    "'references/', 'templates/' or 'scripts/'. Give the umbrella's SKILL.md a one-line pointer "
    "to each new support file, so later agents know it is there.\n"
    "  4. CREATE A NEW CLASS-LEVEL UMBRELLA SKILL when no skill covers the class yet. Its name "
    "MUST name the class. It MUST NOT be a PR number, an error string, a feature's codename, a "
    "library's name alone, or a conversation leftover like 'fix-X / debug-Y / audit-Z-today'. A "
    "name that only makes sense for today's task is wrong: go back to (1), (2) or (3).\n\n"
    + _READ_BEFORE_WRITE
    + "Preferences go in skills (important): when the user stated a preference of style, format "
    "or workflow, the change belongs in the body of a SKILL.md, not only in memory. Memory holds "
    "who the user is and where things and your work stand now; a skill holds how to do this "
    "class of task for this user. A complaint about how you handled a task is a lesson the skill "
    "that governs that task must carry.\n\n"
    "Never delete a skill in this review; when two existing skills overlap, point it out in your "
    "reply.\n\n"
    "Do NOT capture" + DO_NOT_CAPTURE + "'Nothing to save.' is a real answer, but it must NOT be "
    "the default. Only when the conversation went smoothly, with no correction and no new "
    "technique, say 'Nothing to save.' and stop. Otherwise, act."
)

#: The skills half of the combined review's instructions, after its "**Skills**: ", written as
#: `SKILL_REVIEW_PROMPT` is. The memory half is the memory subject's.
COMBINED_SKILLS_PART = (
    "how to do this class of task. Lean toward ACTING: most conversations leave at least one "
    "skill change behind, and a pass that changes nothing has wasted a lesson; it is not a safe "
    "default.\n\n"
    "What the skill library should look like: CLASS-LEVEL skills, each a SKILL.md of rules that "
    "always apply plus a few topical files of depth in `references/`; not narrow skills that each "
    "served one conversation, and not an umbrella piling up a references/ file per "
    "conversation.\n\n" + LESSON_LAYER + "Any one of these signals calls for a skill change:\n"
    "  • The user pushed back on your style, tone, format, legibility, verbosity or approach. "
    "For skills, as much as for memory, frustration is a FIRST-CLASS signal: 'quit doing X', "
    "'not in this format', 'I can't stand it when you Y'. Write the lesson into the skill that "
    "governs that task, so the next conversation starts with it fixed.\n"
    "  • A technique, fix, workaround or debugging path came up that is not trivial.\n"
    "  • A skill that was loaded or consulted proved wrong, incomplete or outdated: patch it "
    "now.\n\n"
    "Order of preference for skills: take the first that fits.\n"
    "  1. UPDATE A CURRENTLY-LOADED SKILL. See which skills the conversation loaded with "
    "use_skill. When one covers the lesson, PATCH it first (loading it again with use_skill in "
    "this review; see Read-before-write below). It was in play, so it is the place for the "
    "lesson, no matter who wrote it.\n"
    "  2. UPDATE AN EXISTING UMBRELLA (look through the skills listed in use_skill's description, "
    "then load the right one with use_skill). Patch it.\n"
    "  3. ADD A SUPPORT FILE under an existing umbrella, with a write_file operation of "
    "skill_manage. Three kinds: `references/<topic>.md` for topical depth (decision tables, "
    "recipes, quirks, condensed notes on a domain), extending the topical file that exists before "
    "making a new one, never a file per conversation; `templates/<name>.<ext>` for starting "
    "points to copy and adapt; `scripts/<name>.<ext>` for actions that can be re-run as they are "
    "(verification, fixture generators, probes). Point to each from SKILL.md in one line, so "
    "later agents find it.\n"
    "  4. CREATE A NEW CLASS-LEVEL UMBRELLA when nothing covers the class. Name the class, NOT a "
    "PR number, an error string, a codename, a library's name alone, or a conversation leftover "
    "like 'fix-X / debug-Y'. A name that only suits today's task sends you back to (1), (2) or "
    "(3).\n\n"
    + _READ_BEFORE_WRITE
    + "Preferences go in skills: when the user complains about how you handled a task, update the "
    "skill that governs the task rather than memory. Memory holds who the user is and where "
    "things and your work stand now; a skill holds how to do this class of task for this user. A "
    "lesson about the user's preference is kept in exactly ONE place: the skill that governs the "
    "task when there is one, USER.md only for a preference that crosses tasks and no skill owns "
    "— never both. Keeping it twice is how a memory file ends up repeating SKILL.md until both "
    "reach their size limits.\n\n"
    "Never delete a skill in this review; point out existing skills that overlap.\n\n"
    "NEVER capture as skills" + DO_NOT_CAPTURE + "Act where either of the two shows a real "
    "signal. When truly nothing stands out in either, say 'Nothing to save.' and stop — but do "
    "not settle on that by default."
)

#: The skill lines for the instructions of a conversation that has skill_manage.
SKILLS_GUIDANCE = (
    "Once you work out a workflow that is not trivial, record it with skill_manage so it can be "
    "reused. A skill with problems gets fixed with skill_manage (a patch). After a hard task, or "
    "one that took several tries, offer to save it as a skill. If a skill you loaded lacked "
    "steps, gave wrong commands or needed pitfalls you found along the way, update it before you "
    "finish."
)
