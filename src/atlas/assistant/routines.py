"""A routine's run for this Atlas (the jobs subject's `Executor`): a prompt runs as a new
conversation nobody watches; a command runs in the working folder.

A fresh session per run, titled after the job, with no clarify and approvals denied, no background
review, the loop guards halting, and an inactivity watchdog (600 seconds idle) rather than a
wall-clock cap; a script runs for at most an hour, its output delivered as it is, and an empty one
is silent.

**A bot's routine** (docs/ARCHITECTURE.md, Bots) runs as the bot, in a conversation of its own
that belongs to the bot, and what it answered enters the bot's fixed conversation as a message the
bot reads and answers: the recipient is the bot itself, which receives the output as an incoming
message, acts on anything that needs action, and responds in its conversation. A silent run and a
failed one deliver nothing; the routines' list says how each run ended.
"""

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING

from atlas.agent import TurnOutcome
from atlas.assistant.model import ConversationRole, routine_notice
from atlas.bots import BotError
from atlas.conversations import ROUTINE
from atlas.jobs import Routine, Run, RunStatus, is_silent
from atlas.models import ModelChoice
from atlas.tools import Commands, Workspace

if TYPE_CHECKING:
    from atlas.assistant.service import Assistant

_logger = logging.getLogger("atlas.assistant")

#: Seconds a routine's turn may go without doing anything before it is stopped.
IDLE_SECONDS = 600.0
#: Seconds a routine's command may run.
COMMAND_SECONDS = 3600
#: How often the watchdog looks.
_LOOK_SECONDS = 1.0


class RoutineRunner:
    """Runs routines for one Atlas."""

    def __init__(self, assistant: "Assistant", *, idle: float = IDLE_SECONDS) -> None:
        self._assistant = assistant
        self._idle = idle

    async def run(self, routine: Routine, run: Run) -> Run:
        if routine.command is not None:
            return await self._command(routine.command, run)
        return await self._prompt(routine, run)

    async def _prompt(self, routine: Routine, run: Run) -> Run:
        assistant = self._assistant
        stored = assistant.conversations.create(source=ROUTINE, title=routine.name, bot=routine.bot)
        if routine.model_provider is not None and routine.model_name is not None:
            choice = ModelChoice(routine.model_provider, routine.model_name, routine.model_effort)
            assistant.conversations.set_model(stored.id, choice)
        conversation = assistant.conversation(stored.id, role=ConversationRole.ROUTINE)
        run = dataclasses.replace(run, conversation_id=stored.id)
        try:
            conversation.send(routine.prompt or "")
            while conversation.running:
                await asyncio.sleep(_LOOK_SECONDS)
                idle = (assistant.clock() - conversation.last_activity).total_seconds()
                if conversation.running and idle > self._idle:
                    await conversation.interrupt()
                    _logger.warning(
                        "A routine was stopped without activity.", extra={"routine": routine.id}
                    )
                    return dataclasses.replace(
                        run,
                        status=RunStatus.ERROR,
                        error=f"Stopped after {self._idle:.0f} seconds without activity.",
                    )
            await conversation.wait()
        finally:
            await conversation.close()
            assistant.forget(stored.id)
        ended = conversation.last_ended
        if ended is None:
            return dataclasses.replace(run, status=RunStatus.ERROR, error="The run never answered.")
        if ended.outcome is not TurnOutcome.ANSWERED:
            return dataclasses.replace(
                run, status=RunStatus.ERROR, output=ended.text, error=ended.error or ended.outcome
            )
        status = RunStatus.SILENT if is_silent(ended.text) else RunStatus.OK
        if status is RunStatus.OK and routine.bot is not None:
            self._deliver(routine.bot, routine.name, ended.text)
        return dataclasses.replace(run, status=status, output=ended.text)

    def _deliver(self, bot: str, name: str, output: str) -> None:
        """Hand a run's answer to the bot's fixed conversation, where it starts a turn now or
        after the one running."""
        assistant = self._assistant
        try:
            fixed = assistant.bot_conversation(bot)
        except BotError as error:
            _logger.warning("A routine's result reached no bot: %s", error)
            return
        assistant.conversation(fixed).notice(routine_notice(name, output))

    async def _command(self, command: str, run: Run) -> Run:
        assistant = self._assistant
        commands = Commands(
            Workspace(assistant.workspace),
            assistant.home.commands_folder,
            path=lambda: assistant.programs,
        )
        output, code = await commands.execute(command, COMMAND_SECONDS)
        if code is None:
            error = f"Stopped after {COMMAND_SECONDS} seconds, with every process it started."
            return dataclasses.replace(run, status=RunStatus.ERROR, output=output, error=error)
        if code != 0:
            return dataclasses.replace(
                run, status=RunStatus.ERROR, output=output, error=f"exit code {code}"
            )
        quiet = output.strip() in ("", "(no output)")
        return dataclasses.replace(
            run, status=RunStatus.SILENT if quiet else RunStatus.OK, output=output
        )
