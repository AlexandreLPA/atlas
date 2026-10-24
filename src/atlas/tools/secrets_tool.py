"""secrets: the agent asks the user for an API key by name, and the key goes straight to the
vault (decided on 14/10/2026, roadmap step 16d).

The MCP specification forbids pasting a key through a form, so the key has its own request.

- **`request(name, reason)`**: a server request `secret` goes to the user's app, with a masked
  card. The user pastes the value, which the server writes to the vault; the model gets only
  `stored`. The turn waits 15 minutes (because making a key on the provider's
  page takes time, past the server's usual 300 s for `ask_user`). Nobody attached: `no_answer` at
  once, with `atlas secrets set NAME` as the hint.
- **`list`**: the vault's names with each one's date, without decrypting. The value never reaches
  the model.
- **No `delete`**: the Atlas vault deletes for good, with no bin. An agent misled by a page would
  wipe the key with no way back. Deleting is only `atlas secrets delete` on the terminal (roadmap,
  Chave pedida na conversa).

**The name** is `^[A-Z][A-Z0-9_]{0,127}$`: an environment-variable form that fits `${NAME}` in
`mcp.json`. The vault takes a wider shape; the tool is stricter so the model asks only for keys
that MCP can consume.

**The reason** is up to 200 characters; the card shows it under the name, so the user knows what
the key is for.

**Deferred**, found through tool search as `plan` is (roadmap, Ferramentas): the tool is reached
for only when a key is actually missing.

**Not given to subagents, routines or the background review**: as `ask_user` is, this tool is
given only to conversations with someone to answer.

**Only the server sees the value.** The tool never receives it, so it cannot be logged inside the
turn, kept in the conversation, replayed from `conversation.events`, or echoed back to a frame
the client sees twice. The server registers the value with the vault's redactor before writing
it and returns `{"status": "stored", "name": ...}` to the model.
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from atlas.agent import Tool

#: An environment-variable name that fits `${NAME}` in Claude Code's `mcp.json`.
#: The tool's name, as the model calls it.
SECRETS_TOOL = "secrets"
NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
MAX_NAME_CHARS = 128
MAX_REASON_CHARS = 200
#: Making a key on the provider's page takes time, past the server's usual 300 s.
REQUEST_SECONDS = 15 * 60


class SecretOutcome(StrEnum):
    """How the request ended, for the model."""

    STORED = "stored"
    #: The user closed the card without pasting.
    SKIPPED = "skipped"
    #: The provider refused the key, or the vault would not take it (locked, damaged).
    FAILED = "failed"
    #: Nobody is attached to the conversation. The hint tells the user to run `atlas secrets set`.
    NO_ANSWER = "no_answer"


@dataclass(frozen=True, slots=True)
class SecretRequest:
    """What `request` passes to the asker."""

    name: str
    reason: str


@dataclass(frozen=True, slots=True)
class SecretAnswer:
    """What the asker returns. `detail` is a human-readable message passed to the model, used only
    for `FAILED` and `NO_ANSWER`."""

    outcome: SecretOutcome
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SecretView:
    """One secret as `list` reports it: name and when it was stored, never the value."""

    name: str
    updated_at: str


#: The server's path from the model's call to the user's app and back. Returns an answer with
#: what to tell the model; the value never comes back here.
type SecretAsker = Callable[[SecretRequest], Awaitable[SecretAnswer]]

#: The vault's listing of secrets as the tool reports them. Decrypts nothing.
type SecretLister = Callable[[], list[SecretView]]


def make_secrets_tool(secret_asker: SecretAsker, secret_lister: SecretLister) -> Tool:
    """The tool with two actions, `request` and `list`. It is deferred: the agent finds it with
    tool search when it needs a key. The value never reaches `run`; only the server sees it."""

    async def run(arguments: dict[str, Any]) -> str:
        action = str(arguments.get("action") or "").strip()
        if action == "request":
            return await _request(secret_asker, arguments)
        if action == "list":
            return _listing(secret_lister())
        return (
            '[Atlas: secrets needs action="request" (ask the user for a key by name) or '
            'action="list" (the keys the vault holds).]'
        )

    return Tool(
        name=SECRETS_TOOL,
        description=(
            "Asks the user for a secret by name, or lists the vault's secrets. The value is "
            "never sent to the model: it goes from the app straight into the vault, which the "
            "providers, the `web_search` backends, the MCP servers that name it in `mcp.json`, "
            "and the plugins that declare it read. "
            'action="request" shows a masked card in the app; the result is one of: '
            "`stored` (the key is in the vault; the server MCP or the plugin that needs it "
            "starts); `no_answer` (no app is attached: tell the user to run "
            "`atlas secrets set NAME`, or go on without it); `skipped` (the user dismissed the "
            "card); `failed` (the vault refused the value, or the provider rejected the key: "
            "the detail says what to do). "
            'action="list" returns the names in the vault with each one\'s date; the value is '
            "never listed. "
            "Pick a name of the form `UPPER_SNAKE_CASE` (an environment-variable name that fits "
            "`${NAME}` in mcp.json). Give a one-line reason: the user sees it on the card. "
            "On a `no_answer`, tell the user how to go on; do not ask them to paste the key "
            "into the chat."
        ),
        parameters={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["request", "list"],
                    "description": (
                        "`request`: ask the user for the secret `name`, with a one-line "
                        "`reason`. `list`: the vault's names and dates."
                    ),
                },
                "name": {
                    "type": "string",
                    "description": (
                        "For `request`: the secret's name, UPPER_SNAKE_CASE, up to 128 "
                        "characters. Fits `${NAME}` in `mcp.json`."
                    ),
                },
                "reason": {
                    "type": "string",
                    "description": (
                        f"For `request`: one line, up to {MAX_REASON_CHARS} characters, saying "
                        "what the secret is for. The user sees it on the card."
                    ),
                },
            },
            "required": ["action"],
            "additionalProperties": False,
        },
        run=run,
        deferred=True,
        source="atlas.secrets",
    )


async def _request(secret_asker: SecretAsker, arguments: dict[str, Any]) -> str:
    name = str(arguments.get("name") or "").strip()
    reason = str(arguments.get("reason") or "").strip()
    if not name:
        return "[Atlas: secrets.request needs the secret's `name`.]"
    if len(name) > MAX_NAME_CHARS or not NAME_PATTERN.match(name):
        return (
            f"[Atlas: {name!r} is not a secret name. Use UPPER_SNAKE_CASE: letters, digits "
            f"and underscores, starting with a letter, up to {MAX_NAME_CHARS} characters.]"
        )
    if not reason:
        return "[Atlas: secrets.request needs a one-line `reason`; the user sees it on the card.]"
    if len(reason) > MAX_REASON_CHARS:
        return (
            f"[Atlas: secrets.request `reason` has {len(reason):,} characters, past the "
            f"{MAX_REASON_CHARS} allowed. Keep it to one line.]"
        )
    answer = await secret_asker(SecretRequest(name=name, reason=reason))
    return _result(answer, name)


def _result(answer: SecretAnswer, name: str) -> str:
    said: dict[str, Any] = {"status": answer.outcome.value, "name": name}
    if answer.outcome is SecretOutcome.NO_ANSWER:
        said["hint"] = f"atlas secrets set {name}"
    if answer.detail and answer.outcome in (SecretOutcome.FAILED, SecretOutcome.NO_ANSWER):
        said["detail"] = answer.detail
    return json.dumps(said, ensure_ascii=False)


def _listing(views: list[SecretView]) -> str:
    rows = [{"name": view.name, "updated_at": view.updated_at} for view in views]
    return json.dumps({"secrets": rows}, ensure_ascii=False)


def parse_name(raw: object) -> str | None:
    """The secret's name if `raw` is one of the shape the tool takes, else `None`. For callers
    that want to validate the name before showing a card."""
    if not isinstance(raw, str):
        return None
    name = raw.strip()
    if not name or len(name) > MAX_NAME_CHARS or not NAME_PATTERN.match(name):
        return None
    return name


def parse_arguments(raw: dict[str, Any]) -> tuple[str, str, str] | str:
    """`(action, name, reason)` for a well-formed call, or what is wrong. Used by tests and the
    fake-model script to pose the same calls the real model would."""
    action = str(cast("object", raw.get("action")) or "").strip()
    if action not in ("request", "list"):
        return 'action must be "request" or "list".'
    if action == "list":
        return "list", "", ""
    name = str(cast("object", raw.get("name")) or "").strip()
    if parse_name(name) is None:
        return f"{name!r} is not UPPER_SNAKE_CASE within {MAX_NAME_CHARS} characters."
    reason = str(cast("object", raw.get("reason")) or "").strip()
    if not reason:
        return "reason is empty."
    if len(reason) > MAX_REASON_CHARS:
        return f"reason has {len(reason):,} characters, past {MAX_REASON_CHARS}."
    return "request", name, reason
