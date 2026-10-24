"""The home's bots: named agents that live beside the main agent, each with its own definition,
memory and skills folder (docs/ARCHITECTURE.md, Bots)."""

from atlas.bots.errors import BotError
from atlas.bots.model import (
    BOT_FILE,
    DESCRIPTION_CHARS,
    MAIN_AGENT,
    MEMORY_FILE,
    MESSAGE_MARK,
    RESERVED_NAMES,
    SKILLS_FOLDER,
    TITLE_CHARS,
    Bot,
    attributed,
    composed_mission,
    definition_problem,
    name_problem,
    parse_bot,
    render_bot,
    resolve_target,
    roster_section,
)
from atlas.bots.store import Bots

__all__ = [
    "BOT_FILE",
    "DESCRIPTION_CHARS",
    "MAIN_AGENT",
    "MEMORY_FILE",
    "MESSAGE_MARK",
    "RESERVED_NAMES",
    "SKILLS_FOLDER",
    "TITLE_CHARS",
    "Bot",
    "BotError",
    "Bots",
    "attributed",
    "composed_mission",
    "definition_problem",
    "name_problem",
    "parse_bot",
    "render_bot",
    "resolve_target",
    "roster_section",
]
