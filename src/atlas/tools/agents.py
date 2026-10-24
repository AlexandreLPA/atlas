"""message_agent: one agent of the home writes to another, the main agent to a bot, a bot to
another or to the main agent, and goes on; the answer comes back later.

Decided with the user on 13/10/2026 (docs/ARCHITECTURE.md, Bots):

- **Fire and forget**: the call checks the target against the live roster, hands the message over
  with the sender's attribution in front, and returns an acknowledgement; the answer arrives
  later as a notice, as a subagent's does.
- **Always on, and only in a home that has a bot**, in the conversations the user holds with the
  main agent and in each bot's fixed conversation: a routine's run and a subagent are not given
  it, as no one-shot session is.
"""

from typing import Any, Protocol

from atlas.agent import Tool

NAME = "message_agent"
#: The cap on a message between agents, in characters.
MAX_MESSAGE_CHARS = 16_000


class Messenger(Protocol):
    """A conversation's way to the home's other agents. It answers with what the model reads."""

    async def send(self, target: str, message: str) -> str: ...


DESCRIPTION = (
    "Writes to ANOTHER agent of this home, a teammate. Like a text message, it is "
    "FIRE-AND-FORGET and asynchronous: the target is checked against the live roster, your "
    "message lands in that agent's own conversation with your attribution put in front for "
    "you, and the call returns at once with an acknowledgement of the dispatch — status queued "
    "and a delivery_id (it was handed over, not yet delivered). Their reply does NOT come back "
    "from the call, and you must not wait or poll for it: send, end your turn, and a notice "
    "wakes you with what happened, their reply or a failed delivery. WRITE the message "
    "yourself, as what YOU want to tell that agent: the point first, with the concrete request "
    "or result. Never copy in what the user wrote word for word — pass on what needs acting "
    "on, in your words — and what was said in a private 1:1 chat stays private. Write to one "
    "teammate that clearly fits, when that truly serves what the user is after; several only "
    "when the user explicitly asked. The teammate roster in your system prompt (names and "
    "roles) tells you who the right recipient is."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "target": {
            "type": "string",
            "description": "The teammate to write to, by its name in your roster ('finance').",
        },
        "message": {
            "type": "string",
            "description": f"What YOU wrote for that agent, at most {MAX_MESSAGE_CHARS} "
            "chars. Leave out the 'Message from …' prefix: Atlas adds it.",
        },
    },
    "required": ["target", "message"],
    "additionalProperties": False,
}


def message_agent(messenger: Messenger) -> Tool:
    """The tool over a conversation's way to the other agents."""

    async def run(arguments: dict[str, Any]) -> str:
        target, message = arguments.get("target"), arguments.get("message")
        if not isinstance(target, str) or not target.strip():
            return "[Atlas: target is required: a teammate's name from your roster.]"
        if not isinstance(message, str) or not message.strip():
            return "[Atlas: message is required: what you want to say to that agent.]"
        if len(message) > MAX_MESSAGE_CHARS:
            return (
                f"[Atlas: the message has {len(message)} characters; the most is "
                f"{MAX_MESSAGE_CHARS}. Send the point, and put the rest in a file they can read.]"
            )
        return await messenger.send(target.strip(), message.strip())

    return Tool(name=NAME, description=DESCRIPTION, parameters=PARAMETERS, run=run)


BOT_CREATE = "bot_create"


class BotMaker(Protocol):
    """How the main agent makes a bot. It answers with what the model reads."""

    def create(self, name: str, title: str, description: str, mission: str) -> str: ...


BOT_CREATE_DESCRIPTION = (
    "Create a Bot: a persistent named agent of this home, with its own mission, memory, skills, "
    "fixed conversation and routines, which the user talks to in the Bots list and you reach "
    "with message_agent. Make a separate Bot only when the work has a distinct goal, tools and "
    "sources, working style, approval boundary or recurring schedule: a stable role. For a "
    "one-off task use delegate instead. ASK THE USER FIRST, saying the name and what it would be "
    "for, and create it only once they agree. Keep the roster small: start with one Bot owning "
    "an end-to-end outcome, and add specialists only for stable roles."
)

BOT_CREATE_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Its handle: lowercase letters, digits, - and _ ('inbox-triage').",
        },
        "title": {"type": "string", "description": "How the roster calls it ('Inbox Triage')."},
        "description": {
            "type": "string",
            "description": "What it is for, in one line: its role in the roster.",
        },
        "mission": {
            "type": "string",
            "description": "Its standing rules: what must remain true in every conversation. "
            "Optional; without it, one is composed from the title and the description.",
        },
    },
    "required": ["name", "title", "description"],
    "additionalProperties": False,
}


def bot_create(maker: BotMaker) -> Tool:
    """The tool the main agent makes a bot with, once the user agreed (Grok Bot: an existing Bot
    can suggest or create focused Bots, and is told to ask first and keep the roster small)."""

    async def run(arguments: dict[str, Any]) -> str:
        fields = [arguments.get(key) for key in ("name", "title", "description", "mission")]
        name, title, description, mission = (
            value.strip() if isinstance(value, str) else "" for value in fields
        )
        if not name:
            return "[Atlas: name is required: the bot's handle, such as inbox-triage.]"
        return maker.create(name, title, description, mission)

    return Tool(
        name=BOT_CREATE,
        description=BOT_CREATE_DESCRIPTION,
        parameters=BOT_CREATE_PARAMETERS,
        run=run,
        deferred=True,
    )
