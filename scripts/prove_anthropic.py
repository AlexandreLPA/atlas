"""Prove the Anthropic adapter on a long conversation against the real model. Spends credit:
about US$ 1 to 1.5 on Claude Opus 5.5 (estimated on 14/10/2026), most of it the hour-long cache
written as the conversation grows past 100,000 tokens.

    uv run python scripts/prove_anthropic.py
    uv run python scripts/prove_anthropic.py --model claude-sonnet-5-5 --chapters 12

On the user's real home (`ATLAS_HOME`), the key from the vault, every call in the call ledger:
run it after the user's own Atlas served the home once with this code, so the ledger has its
hour-long cache column (migration 2). One turn reads twelve chapters of about 9,000 tokens each,
one tool call at a time, so the conversation crosses the 100,000 tokens where Anthropic's server
clears old results (docs/ARCHITECTURE.md, Anthropic). It proves:

1. Every call was taken: the reasoning went back sealed on every round, and no request was
   refused (a 400 would end the turn).
2. The cache was read: the calls after the first read what the earlier ones wrote, an hour's
   cache since a person paces the turn.
3. The server cleared: the last call's input is smaller than everything the chapters added.
4. The model answered from the last chapter it read.

Exit code 0 only when all four hold.
"""

import argparse
import asyncio
import os
import sys
from contextlib import aclosing
from decimal import Decimal
from itertools import pairwise
from typing import Any

from proof_ledger import ensure_ledger  # the scripts' folder, on the path when run

from atlas.agent import AgentLoop, Tool, ToolStarted, TurnEnded, TurnOutcome
from atlas.home import Database, Redactor, configure_logging, load_settings, open_home
from atlas.models import CallLedger, Message, Models, SystemMessage, load_catalog
from atlas.secrets import Vault

SCOPE = "prove-anthropic"
#: About 9,000 tokens each, at four characters a token.
CHAPTER_CHARS = 36_000
WORDS = (
    "amber", "birch", "cobalt", "dune", "ember", "fjord", "garnet", "harbor",
    "indigo", "juniper", "kestrel", "lagoon", "marble", "nectar", "onyx", "pumice",
)  # fmt: skip


def _chapter(number: int) -> str:
    word = WORDS[(number - 1) % len(WORDS)]
    head = f"Chapter {number}. The key word of this chapter is {word}.\n"
    line = f"In chapter {number} nothing else matters; this line only fills the chapter.\n"
    return head + line * ((CHAPTER_CHARS - len(head)) // len(line))


async def _read_chapter(arguments: dict[str, Any]) -> str:
    return _chapter(int(arguments.get("number", 1)))


READ_CHAPTER = Tool(
    "read_chapter",
    "Read one chapter of the book, by its number.",
    {
        "type": "object",
        "properties": {"number": {"type": "integer", "description": "The chapter, from 1."}},
        "required": ["number"],
    },
    _read_chapter,
    read_only=True,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the Anthropic adapter on a long turn.")
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--chapters", type=int, default=12)
    parser.add_argument("--effort")
    args = parser.parse_args()

    settings = load_settings(os.environ).settings
    home = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)
    database = Database(home.database_file)
    ensure_ledger(database)
    ledger = CallLedger(database)
    vault = Vault(home.secrets_folder, settings.vault, redactor)
    models = Models(load_catalog(home.models_file), vault, settings.model, ledger=ledger)
    proved = asyncio.run(_prove(models, args))
    proved &= _report(ledger, args.chapters)
    _say("PROVED: every call taken, the cache read, the server cleared, the answer right."
         if proved else "FAILED.")  # fmt: skip
    return 0 if proved else 1


async def _prove(models: Models, args: argparse.Namespace) -> bool:
    try:
        check = await models.check_key("anthropic")
        _say(f"key check: {check.status.value}, {check.detail}")
        if not check.usable:
            return False
        client = models.client("anthropic", args.model, effort=args.effort)
        loop = AgentLoop(client, [READ_CHAPTER])
        history: list[Message] = [SystemMessage("You are Atlas, reading a book for the user.")]
        last = args.chapters
        ask = (
            f"Read chapters 1 to {last} with read_chapter, one call at a time and in order, "
            f"without skipping any. Then reply with one line: the key word of chapter {last} and "
            "how many chapters you read."
        )
        end: TurnEnded | None = None
        calls = 0
        async with aclosing(loop.run(history, ask, scope=SCOPE)) as events:
            async for event in events:
                if isinstance(event, ToolStarted):
                    calls += 1
                    _say(f"  read_chapter({event.arguments})")
                elif isinstance(event, TurnEnded):
                    end = event
        if end is None:
            return False
        _say(f"ended: {end.outcome.value} after {end.model_calls} calls: {end.text[:300]!r}")
        word = WORDS[(last - 1) % len(WORDS)]
        return end.outcome is TurnOutcome.ANSWERED and calls >= last and word in end.text.lower()
    finally:
        await models.close()


def _report(ledger: CallLedger, chapters: int) -> bool:
    """The calls of this proof, and whether the cache was read and the server cleared."""
    rows = [record for record in reversed(ledger.recent(200)) if record.scope == SCOPE][-60:]
    _say("calls: input / cache read / cache write (of it the hour's) / output / cost")
    total = Decimal(0)
    for record in rows:
        usage = record.usage
        cost = record.cost.usd or Decimal(0)
        total += cost
        if usage is None:
            _say(f"  {record.outcome.value}: no usage")
            continue
        _say(
            f"  {usage.input_tokens:>7} / {usage.cache_read_tokens:>7} / "
            f"{usage.cache_write_tokens:>6} ({usage.cache_write_long_tokens:>6}) / "
            f"{usage.output_tokens:>5} / {cost:.4f}"
        )
    _say(f"total: US$ {total:.4f}")
    # A call should read what the one before it cached, unless that one cached nothing (Anthropic
    # caches nothing under 512 tokens) or the server cleared in between (the input shrank, and
    # the cache from the cleared point on was written again). The first run, on 14/10/2026, was
    # marked failed for both, wrongly.
    due = [
        (before.usage, after.usage)
        for before, after in pairwise(rows)
        if before.usage is not None
        and after.usage is not None
        and before.usage.cache_read_tokens + before.usage.cache_write_tokens > 0
        and after.usage.input_tokens >= before.usage.input_tokens
    ]
    read = sum(1 for _, after in due if after.cache_read_tokens > 0)
    added = chapters * CHAPTER_CHARS // 4
    last = rows[-1].usage.input_tokens if rows and rows[-1].usage is not None else 0
    _say(f"calls that should read the cache and did: {read} of {len(due)}")
    _say(f"the chapters added about {added:,} tokens; the last call sent {last:,}")
    cleared = 0 < last < added
    _say("the server cleared." if cleared else "the server did not clear.")
    return cleared and bool(due) and read == len(due)


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
