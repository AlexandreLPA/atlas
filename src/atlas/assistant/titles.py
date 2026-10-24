"""Conversation titles written by a small model.

**Two stages.** The instant title, the first line the user wrote, is stored with the first message
and never fails (`atlas.conversations`). A side call to a small model, in the background as the
first turn starts, then replaces it with one that names the conversation; when the call fails the
instant title stays. Provenance keeps the order (`TitleSource`): the model's title replaces only the
instant one, and neither replaces a name someone gave.

**Who writes it, decided by the user on 14/10/2026.** Claude Haiku 5.5 with its reasoning off,
the cheapest it takes; when the vault has no Anthropic key or the call fails (no credit, a refused
key), GPT-6 Luna with the effort `none`, which costs the same; with neither, the instant title
stays. Never the conversation's own model: a title is worth a fraction of a cent, and a large
model would cost a hundred times more. A provider with no credit, or that refuses its key, is then
skipped for 10 minutes (`TitleQuarantine`; asked for by the user on 15/10/2026, when every title
paid a refused Haiku call first).

**The request:** 3 to 7 words, in the user's language, answered as `{"title": ...}`, from at most
1000 characters of the message. The reply is read loosely (a fence, a `Title:` prefix, prose),
since the JSON is asked, not forced. The guards:

- a reply longer than 12 words is refused, not cut: the model answered the message instead of
  naming it;
- a reply that repeats one of the prompt's examples is refused;
- the title the prompt asks for a bare greeting is a placeholder: the instant title stays, and
  turns 2 and 3 try again (`TITLE_TURNS`), so a failing model costs at most three calls. The
  placeholder is asked in English whatever the user's language, since a translated one
  ("Saudação amigável") would not be recognized and would settle the title for good. The
  placeholder is never stored as the instant title: Atlas keeps the user's own words.

Left out: the response format forced by the provider (each wire spells it its own way, and the
guards catch what it prevents), and the wait for a model on the user's machine, which shares its
one slot with the turn (both title models are hosted).
"""

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import cast

from atlas.models import (
    ModelAuthError,
    ModelOutOfCreditError,
    ModelRequest,
    Models,
    ModelsError,
    SystemMessage,
    UserMessage,
)

#: The call ledger's name for a title's call.
TITLE_PURPOSE = "title"
#: The models that write titles, the first whose provider has a key first: (provider, model).
TITLE_MODELS = (("anthropic", "claude-haiku-5-5"), ("openai", "gpt-6-luna"))
#: How long a title provider with no credit, or that refuses its key, is skipped.
QUARANTINE = timedelta(minutes=10)
#: The turns that may title a conversation still holding its instant title: the first, and two
#: more for one that opened with a greeting or whose call failed.
TITLE_TURNS = 3
#: What the model reads of the message.
MAX_TITLE_INPUT_CHARS = 1000
#: Room for a fenced reply, or a model reasoning despite being asked not to, without letting a
#: runaway reply cost more than a fraction of a cent (64 cut the JSON short).
TITLE_MAX_TOKENS = 512
#: Longer is an answer, not a title.
MAX_TITLE_WORDS = 12
#: The longest title kept.
MAX_TITLE_CHARS = 80

_GOOD_EXAMPLES = ("Rename columns in sales CSV", "Nginx 502 after certificate renewal")
#: What the prompt asks for a message with no topic yet: a placeholder, not a title.
GREETING_TITLE = "Friendly greeting"
_VAGUE_EXAMPLE = "Server problem"
_ECHOES = frozenset(title.lower() for title in (*_GOOD_EXAMPLES, _VAGUE_EXAMPLE))

TITLE_PROMPT = (
    "Your job is naming chat sessions. From the user's first message, write the title they will "
    "spot this conversation by when scanning a list.\n\n"
    "Rules:\n"
    "- 3 to 7 words, in sentence case: a capital only on the first word and on proper nouns.\n"
    "- Say what the user wants DONE, not that a question was asked.\n"
    "- Technical terms, file names, numbers and error codes stay exactly as written.\n"
    "- Leave out filler words such as the, this, my, a, an.\n"
    "- No punctuation at the end, no quotes, no tool names, no 'Title:' in front.\n"
    "- Do not answer the message, ever. Only name it.\n"
    "- Always give a title, a bare greeting included.\n"
    f'- A bare greeting with no topic yet is titled exactly "{GREETING_TITLE}", in English.\n'
    "- Every other title is in the same language as the user's message.\n"
    + "".join(f'Good: {{"title": "{title}"}}\n' for title in (*_GOOD_EXAMPLES, GREETING_TITLE))
    + f'Too vague: {{"title": "{_VAGUE_EXAMPLE}"}}\n'
    'Too long: {"title": "Find out why the sales CSV export loses its last column whenever the '
    'file is large"}\n\n'
    'Reply with JSON only: {"title": "..."}'
)

_FENCED = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)
_JSON_TITLE = re.compile(r'"title"\s*:\s*"((?:[^"\\]|\\.)*)"')
_EDGES = re.compile(r"^[\W_]+|[\W_]+$")

_logger = logging.getLogger("atlas.assistant")


class TitleQuarantine:
    """The title providers skipped for now; titles are side calls. A provider with no credit, or
    that refuses its key, is skipped for `QUARANTINE`, so each title does not pay a refused call
    first: depletion lasts hours. Kept in this process only; a restart tries again."""

    def __init__(self, clock: Callable[[], datetime]) -> None:
        self._clock = clock
        self._until: dict[str, datetime] = {}

    def hold(self, provider: str) -> datetime:
        """Skip `provider` from now for `QUARANTINE`; until when."""
        until = self._clock() + QUARANTINE
        self._until[provider] = until
        return until

    def held(self, provider: str) -> bool:
        until = self._until.get(provider)
        if until is None:
            return False
        if self._clock() >= until:
            del self._until[provider]
            return False
        return True


async def write_title(
    models: Models, message: str, scope: str | None = None, *, quarantine: TitleQuarantine
) -> str | None:
    """A title for a conversation that opened with `message`, from the first title model whose
    provider has a key, is not in `quarantine`, and answers; `None` when none can, the reply is
    not a title, or it is the greeting's placeholder."""
    if not message.strip():
        return None
    try:
        keyed = {provider.id for provider, stored in models.keyed() if stored}
    except ModelsError as error:
        _logger.warning("No title model could be chosen: %s", error)
        return None
    request = title_request(message, scope)
    for provider, model in TITLE_MODELS:
        if provider not in keyed or quarantine.held(provider):
            continue
        try:
            reply = await models.client(provider, model).reply(request)
        except (ModelOutOfCreditError, ModelAuthError) as error:
            # Lasts hours: the next model, and this one is skipped for a while.
            until = quarantine.hold(provider)
            _logger.warning(
                "A title model failed; titles skip it until %s: %s",
                until.isoformat(timespec="seconds"),
                error,
                extra={"provider": provider},
            )
            continue
        except ModelsError as error:
            # The next model (decided on 14/10/2026).
            _logger.warning("A title model failed: %s", error, extra={"provider": provider})
            continue
        title = read_title(reply.text)
        if title is None:
            _logger.info("A title model's reply was not a title.", extra={"provider": provider})
        return None if title is None or provisional(title) else title
    return None


def title_request(message: str, scope: str | None = None) -> ModelRequest:
    """The title's call: `TITLE_PROMPT`, the message cut to `MAX_TITLE_INPUT_CHARS`, no tools,
    no reasoning, at most `TITLE_MAX_TOKENS` of output."""
    return ModelRequest(
        (SystemMessage(TITLE_PROMPT), UserMessage(message.strip()[:MAX_TITLE_INPUT_CHARS])),
        purpose=TITLE_PURPOSE,
        scope=scope,
        max_output_tokens=TITLE_MAX_TOKENS,
        reasoning=False,
    )


def read_title(reply: str) -> str | None:
    """The title in a model's reply, cleaned; `None` when there is none, or it is an answer or
    one of the prompt's examples."""
    raw = reply.strip()
    fenced = _FENCED.match(raw)
    if fenced:
        raw = fenced.group(1).strip()
    found = _json_title(raw)
    if found is None:
        if raw.startswith(('{"', '["', "[{")) or raw.startswith("```"):
            return None  # JSON the output limit cut short: its fragment is no title
        found = _without_prefix(
            next((line.strip() for line in raw.splitlines() if line.strip()), "")
        )
    title = _without_prefix(" ".join(found.split()).strip("\"'")).rstrip(".!,;:")
    if len(title) > MAX_TITLE_CHARS:
        title = title[: MAX_TITLE_CHARS - 1].rstrip() + "…"
    if not title or len(title.split()) > MAX_TITLE_WORDS or _bare(title) in _ECHOES:
        return None
    return title


def provisional(title: str) -> bool:
    """The placeholder the prompt asks for a greeting, or its variant "in chat"."""
    return _bare(title) in (GREETING_TITLE.lower(), f"{GREETING_TITLE.lower()} in chat")


def _json_title(raw: str) -> str | None:
    """The `title` of a JSON object, or of a `"title": "..."` pair found in prose."""
    whole = _parsed(raw)
    if isinstance(whole, dict):
        value = cast("dict[str, object]", whole).get("title")
        if isinstance(value, str):
            return value.strip()
    pair = _JSON_TITLE.search(raw)
    if not pair:
        return None
    quoted = pair.group(1)
    unescaped = _parsed(f'"{quoted}"')
    return (unescaped if isinstance(unescaped, str) else quoted).strip()


def _parsed(text: str) -> object:
    """`text` read as JSON, or `None` when it is not JSON."""
    try:
        return json.loads(text)
    except ValueError:
        return None


def _without_prefix(text: str) -> str:
    """`text` without a leading `Title:`, in any case."""
    label, rest = text[:6], text[6:]
    return rest.strip() if label.lower() == "title:" else text


def _bare(title: str) -> str:
    """Compared without the quotes or brackets around it, so a wrapper never hides an echo."""
    return _EDGES.sub("", title.strip()).lower()
