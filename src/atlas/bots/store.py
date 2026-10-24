"""The home's bots on disk: one folder each in `bots/`, found by its name.

- **A bot being made lives in `bots/<name>.creating/`** and becomes `bots/<name>/` only once its
  definition is written whole, as a home being made does (`atlas.home`): a half-made bot is never
  listed. The suffix cannot be part of a bot's name.
- **A definition that cannot be read is skipped by the listing**, with a warning, and refused by
  name with the reason, as the skill catalog treats a broken skill: one bad file never hides the
  other bots.
"""

import logging
import os
import shutil
import stat
from collections.abc import Callable, Sequence
from pathlib import Path

from atlas.bots.errors import BotError
from atlas.bots.model import (
    BOT_FILE,
    Bot,
    composed_mission,
    definition_problem,
    name_problem,
    parse_bot,
    render_bot,
)
from atlas.home import replace_file

_logger = logging.getLogger(__name__)

#: What a bot being made is called until it is whole.
CREATING_SUFFIX = ".creating"


class Bots:
    """The bots of one home."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder

    def list(self) -> list[Bot]:
        """The bots that can be read, by name."""
        found: list[Bot] = []
        for folder in _children(self._folder):
            # A folder that cannot be a bot's (one being made, say) is not a broken bot.
            if name_problem(folder.name) is not None or not (folder / BOT_FILE).is_file():
                continue
            try:
                found.append(_read(folder))
            except BotError as error:
                _logger.warning("A bot was skipped: %s", error)
        return found

    def get(self, name: str) -> Bot:
        """The bot of this name. Raises `BotError` when there is none or it cannot be read."""
        problem = name_problem(name)
        if problem is not None:
            raise BotError(problem)
        folder = self._folder / name
        if not (folder / BOT_FILE).is_file():
            raise BotError(f"There is no bot {name!r}. See `atlas bot list`.")
        return _read(folder)

    def create(
        self,
        name: str,
        *,
        title: str = "",
        description: str = "",
        body: str = "",
        model: str | None = None,
        tools: Sequence[str] | None = None,
        mcp_servers: Sequence[str] | None = None,
        skills: Sequence[str] | None = None,
    ) -> Bot:
        """Make a bot. With no mission written, it is born with one composed from its title and
        description. Raises `BotError` when the name is taken or the
        definition is refused."""
        folder = self._folder / name
        bot = _defined(folder, name, title, description, body, model, tools, mcp_servers, skills)
        if folder.exists():
            raise BotError(
                f"There is already a bot {name!r}, at {folder}. Pick another name, or delete it "
                f"with `atlas bot delete {name}`."
            )
        staging = self._folder / f"{name}{CREATING_SUFFIX}"
        if staging.exists():
            _remove_tree(staging)
        staging.mkdir(parents=True)
        try:
            replace_file(staging / BOT_FILE, render_bot(bot).encode("utf-8"))
            staging.rename(folder)
        except OSError as error:
            if staging.exists():
                _remove_tree(staging)
            raise BotError(f"The bot {name!r} could not be made at {folder}: {error}") from error
        return bot

    def update(
        self,
        name: str,
        *,
        title: str = "",
        description: str = "",
        body: str = "",
        model: str | None = None,
        tools: Sequence[str] | None = None,
        mcp_servers: Sequence[str] | None = None,
        skills: Sequence[str] | None = None,
    ) -> Bot:
        """Write a bot's definition anew, whole; its memory and skills stay. Raises `BotError`
        when there is no such bot or the definition is refused."""
        folder = self.get(name).folder
        bot = _defined(folder, name, title, description, body, model, tools, mcp_servers, skills)
        try:
            replace_file(folder / BOT_FILE, render_bot(bot).encode("utf-8"))
        except OSError as error:
            raise BotError(f"The bot {name!r} could not be written: {error}") from error
        return bot

    def delete(self, name: str) -> None:
        """Remove a bot's folder: its definition, its memory and its skills."""
        problem = name_problem(name)
        if problem is not None:
            raise BotError(problem)
        folder = self._folder / name
        if not folder.is_dir():
            raise BotError(f"There is no bot {name!r}. See `atlas bot list`.")
        try:
            _remove_tree(folder)
        except OSError as error:
            raise BotError(
                f"The bot {name!r} could not be removed from {folder}: {error}"
            ) from error


def _defined(
    folder: Path,
    name: str,
    title: str,
    description: str,
    body: str,
    model: str | None,
    tools: Sequence[str] | None,
    mcp_servers: Sequence[str] | None,
    skills: Sequence[str] | None,
) -> Bot:
    """A definition from what the user gave, checked. Raises `BotError`."""
    problem = name_problem(name)
    if problem is not None:
        raise BotError(problem)
    shown, about = " ".join(title.split()) or name, " ".join(description.split())
    bot = Bot(
        name=name,
        title=shown,
        description=about,
        body=body.strip() or composed_mission(name, shown, about),
        folder=folder,
        model=model.strip() if model and model.strip() else None,
        tools=None if tools is None else tuple(tools),
        mcp_servers=None if mcp_servers is None else tuple(mcp_servers),
        skills=None if skills is None else tuple(skills),
    )
    problem = definition_problem(bot)
    if problem is not None:
        raise BotError(problem)
    return bot


def _read(folder: Path) -> Bot:
    file = folder / BOT_FILE
    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise BotError(f"{file} cannot be read: {error}") from error
    return parse_bot(text, folder)


def _children(root: Path) -> list[Path]:
    try:
        return sorted(
            child for child in root.iterdir() if child.is_dir() and not child.name.startswith(".")
        )
    except OSError:
        return []


def _remove_tree(path: Path) -> None:
    """Remove a folder; a read-only file is made writable first (Windows)."""

    def writable(function: Callable[..., object], failed: str, _error: BaseException) -> None:
        os.chmod(failed, stat.S_IWRITE)  # noqa: PTH101 - the path rmtree hands over
        function(failed)

    shutil.rmtree(path, onexc=writable)
