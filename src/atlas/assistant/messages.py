"""Messages between the home's agents: the main agent and the bots write to one another, and
each answer finds its way back.

Decided with the user on 13/10/2026 (docs/ARCHITECTURE.md, Bots):

- **A message enters the receiver's conversation as its next turn**, with who sent it in front
  (`Message from 🤖 <name> (@<handle>):`): a bot's fixed conversation, where messages to
  the same bot run in order, one turn at a time, after the turn running there.
- **Fire and forget**: the sender is told the message was handed over and goes on. What the
  receiver's turn answers comes back as a notice that starts a turn in the sender's conversation;
  an answer of silence
  comes back as nothing to add, and a failed turn as its failure, never as the agent's words.
- **One answer, never two**: a receiver that writes back with message_agent to who asked, in the
  turn the message started, has answered; its turn's text does not go back as well. The real proof
  found it (13/10/2026): the bot answered both ways, and the main agent read the answer twice.
- **The main agent writes from any of its conversations**, asked for by the user. So a bot that
  writes to the main agent reaches the conversation the main agent last wrote to it from; with none,
  the message is refused, and the bot says it in its own conversation, where the user reads it.
- **A ceiling on the messages that go between agents with no word from the user**
  (`MAX_AGENT_HOPS`): a prompt's rule and the silent reply alone do not hold the ping-pong. Each
  message carries how many came before it since the user last spoke; past the ceiling the tool
  refuses and tells the agent to stop and tell the user.
- **Known limit**: a message waiting, and an answer on its way, live in this process. When the
  server stops, a message whose turn already ran is in the receiver's conversation, and its
  answer is not delivered again. Receipts kept on disk would cover it.
"""

import asyncio
import logging
import secrets
from collections.abc import Callable
from typing import TYPE_CHECKING

from atlas.agent import TurnEnded, TurnOutcome
from atlas.assistant.model import ConversationRole
from atlas.bots import MAIN_AGENT, Bot, BotError, attributed, resolve_target
from atlas.jobs import is_silent
from atlas.tools import MAX_MESSAGE_CHARS

if TYPE_CHECKING:
    from atlas.assistant.service import Assistant, OpenConversation

_logger = logging.getLogger("atlas.assistant")

#: Messages that may go between agents since the user last spoke.
MAX_AGENT_HOPS = 6


class AgentMessages:
    """The messages between one Atlas's agents."""

    def __init__(self, assistant: "Assistant") -> None:
        self._assistant = assistant
        #: For each bot, the main agent's conversation that last wrote to it.
        self._last_from_main: dict[str, str] = {}
        self._waiting: set[asyncio.Task[None]] = set()

    def messenger(self, conversation: "OpenConversation") -> "_Messenger | None":
        """A conversation's way to the other agents; `None` where it has none: a routine's run, a
        subagent, and a home with no bot."""
        if conversation.role is not ConversationRole.CONVERSATION:
            return None
        if not self._assistant.bots.list():
            return None
        return _Messenger(self, conversation)

    @property
    def pending(self) -> bool:
        """An answer is still on its way back to who asked."""
        return bool(self._waiting)

    async def close(self) -> None:
        for task in list(self._waiting):
            task.cancel()
        await asyncio.gather(*self._waiting, return_exceptions=True)

    def send(self, sender: "OpenConversation", target: str, message: str) -> str:
        assistant = self._assistant
        bots = assistant.bots.list()
        me = next((bot for bot in bots if bot.name == sender.bot_name), None)
        found = resolve_target(target, sender.bot_name, bots)
        if isinstance(found, str):
            return f"[Atlas: {found}]"
        hops = sender.agent_hops + 1
        if hops > MAX_AGENT_HOPS:
            return (
                f"[Atlas: {MAX_AGENT_HOPS} messages have gone between agents since the user last "
                "spoke, so this one was not sent. Stop here, and say in your reply what is "
                "pending, for the user to decide.]"
            )
        try:
            where = self._conversation_of(found, sender)
        except BotError as error:
            return f"[Atlas: {error}]"
        if where is None:
            return (
                f"[Atlas: @{MAIN_AGENT} has not written to you, so it has no conversation to "
                "receive this. Say it in your reply instead: the user reads your conversation.]"
            )
        if found is not None and sender.bot_name is None:
            self._last_from_main[found.name] = sender.id
        handle = MAIN_AGENT if found is None else found.name
        # Written to who asked, in the turn their message started: this is the answer.
        sender.replying_to(handle)
        delivery = f"d-{secrets.token_hex(4)}"
        origin = sender.bot_name or MAIN_AGENT
        answer, replied = assistant.conversation(where).deliver(
            attributed(me, message), hops, origin
        )
        task = asyncio.create_task(self._return(answer, replied, sender.id, handle, delivery, hops))
        self._waiting.add(task)
        task.add_done_callback(self._waiting.discard)
        _logger.info(
            "A message between agents was handed over.",
            extra={"delivery": delivery, "from": sender.bot_name or MAIN_AGENT, "to": handle},
        )
        return (
            f'{{"status": "queued", "delivery_id": "{delivery}", "to": "@{handle}"}}\n'
            "Handed over, not answered yet. Finish your turn: the reply arrives later as a "
            "notice. Do not send it again."
        )

    def _conversation_of(self, target: Bot | None, sender: "OpenConversation") -> str | None:
        """The conversation that receives a message: a bot's fixed one, or, for the main agent,
        the one it last wrote to this bot from."""
        if target is not None:
            return self._assistant.bot_conversation(target.name)
        return self._last_from_main.get(sender.bot_name or "")

    async def _return(
        self,
        answer: "asyncio.Future[TurnEnded | None]",
        replied: Callable[[], bool],
        sender: str,
        handle: str,
        delivery: str,
        hops: int,
    ) -> None:
        """Bring the receiver's answer back to the sender's conversation, as a notice."""
        ended = await answer
        if replied() and ended is not None and ended.outcome is TurnOutcome.ANSWERED:
            return  # it answered with message_agent; that message is the answer
        try:
            self._assistant.conversation(sender).notice(
                reply_notice(handle, delivery, ended), hops=hops
            )
        except Exception:  # an answer nobody awaits: logged, never raised into the loop
            _logger.exception("An agent's answer could not be delivered.", extra={"to": sender})


def reply_notice(handle: str, delivery: str, ended: TurnEnded | None) -> str:
    """What the sender reads when its message was answered, or was not."""
    about = f"the message you sent to @{handle} (delivery {delivery})"
    if ended is None:
        return f"[IMPORTANT: {about} was stopped before it was answered. Do not resend it blindly.]"
    if ended.outcome is not TurnOutcome.ANSWERED:
        why = " ".join((ended.text or str(ended.outcome)).split())
        return (
            f"[IMPORTANT: {about} was not answered: {why} Do not resend it blindly; tell the user.]"
        )
    if is_silent(ended.text) or not ended.text.strip():
        return f"[IMPORTANT: @{handle} read {about} and had nothing to add.]"
    reply = ended.text.strip()
    if len(reply) > MAX_MESSAGE_CHARS:
        cut = len(reply) - MAX_MESSAGE_CHARS
        reply = f"[{cut} earlier characters were cut.]\n{reply[-MAX_MESSAGE_CHARS:]}"
    return (
        f"[IMPORTANT: @{handle} answered {about}. This is that agent's reply, not the user's: "
        f"relay what matters to the user, saying which agent it came from.]\n\n{reply}"
    )


class _Messenger:
    """One conversation's way to the other agents (`atlas.tools.Messenger`)."""

    def __init__(self, messages: AgentMessages, conversation: "OpenConversation") -> None:
        self._messages = messages
        self._conversation = conversation

    async def send(self, target: str, message: str) -> str:
        return self._messages.send(self._conversation, target, message)
