"""Prove the agent loop end to end against a real model. Spends a little credit.

    uv run python scripts/prove_agent.py openai gpt-5.6-luna --effort medium

Four turns on the user's real home (`ATLAS_HOME`), keys from the vault, every call in the call
ledger. The model's window is declared small in a temporary models.toml (the user's own file is
not touched), so a compaction happens in a short session:

1. A turn with a tool: the model reads a note and answers, its readable reasoning streamed.
2. A turn stopped when its tool starts, as a user pressing stop: every call keeps a result.
3. A turn after the stop, answered from the history: the provider fields of the earlier turns
   (thought signatures, encrypted reasoning) go back and are taken.
4. A turn with a long message: the conversation is compacted by the model itself, and answered.

Exit code 0 only when every turn did what it should.
"""

import argparse
import asyncio
import os
import sys
import tempfile
from contextlib import aclosing
from pathlib import Path
from typing import Any

from proof_ledger import LEDGER_ROWS, ensure_ledger  # the scripts' folder, on the path when run

from atlas.agent import (
    STOPPED,
    AgentLoop,
    Compacted,
    SpillFolder,
    Tool,
    ToolFinished,
    ToolStarted,
    TurnEnded,
    TurnOutcome,
)
from atlas.home import Database, Redactor, configure_logging, load_settings, open_home
from atlas.models import (
    CallLedger,
    Message,
    Models,
    ReasoningDelta,
    SystemMessage,
    TextDelta,
    ToolResult,
    load_catalog,
)
from atlas.secrets import Vault

NOTES = {
    "today": (
        "1. Pay the electricity bill, due TODAY.\n2. Buy bread.\n3. Call the plumber this week."
    ),
    "week": "Monday: dentist. Wednesday: team review. Friday: send the report.",
}
SYSTEM = SystemMessage(
    "You are Atlas, a personal assistant. Use the tools you are given. Answer in one or two "
    "sentences."
)

#: The window declared for the proof: compaction starts past 85% of it.
WINDOW = 7_000


async def read_note(arguments: dict[str, Any]) -> str:
    name = str(arguments.get("name", ""))
    return NOTES.get(name, f"There is no note named {name!r}. Notes: {', '.join(NOTES)}.")


TOOLS = [
    Tool(
        "read_note",
        "Read one of the user's notes by its name, such as today or week.",
        {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "The note's name."}},
            "required": ["name"],
        },
        read_note,
        read_only=True,
    )
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the agent loop end to end.")
    parser.add_argument("provider")
    parser.add_argument("model")
    parser.add_argument("--effort")
    args = parser.parse_args()

    settings = load_settings(os.environ).settings
    home = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)
    database = Database(home.database_file)
    ensure_ledger(database)
    ledger = CallLedger(database)
    before = len(ledger.recent(LEDGER_ROWS))
    with tempfile.TemporaryDirectory() as folder:
        # The user's own models.toml, with the small window added for this proof only.
        small = Path(folder) / "models.toml"
        own = home.models_file.read_text(encoding="utf-8") if home.models_file.exists() else ""
        small.write_text(
            own + f'\n[[models]]\nprovider = "{args.provider}"\nid = "{args.model}"\n'
            f"limit = {{ context = {WINDOW + 2_000}, input = {WINDOW} }}\n",
            encoding="utf-8",
        )
        vault = Vault(home.secrets_folder, settings.vault, redactor)
        models = Models(load_catalog(small), vault, settings.model, ledger=ledger)
        try:
            proved = asyncio.run(_prove(models, args, SpillFolder(home.spill_folder)))
        finally:
            _ledger(ledger, before)
    _say("PROVED: all four turns did what they should." if proved else "FAILED.")
    return 0 if proved else 1


async def _prove(models: Models, args: argparse.Namespace, spill: SpillFolder) -> bool:
    try:
        check = await models.check_key(args.provider)
        _say(f"key check: {check.status.value}, {check.detail}")
        if not check.usable:
            return False
        client = models.client(args.provider, args.model, effort=args.effort)
        loop = AgentLoop(client, TOOLS, spill=spill)
        history: list[Message] = [SYSTEM]

        first, _ = await _turn(
            loop, history, "1", "Read my note 'today': which task is most urgent?"
        )
        ok = first.outcome is TurnOutcome.ANSWERED and any(
            isinstance(m, ToolResult) for m in history
        )

        _say("turn 2: stopped when its tool starts")
        async with aclosing(loop.run(history, "Read my note 'week' and summarize it.")) as events:
            async for event in events:
                if isinstance(event, ToolStarted):
                    _say(f"  stopped as {event.name}({event.arguments}) started")
                    break
        stopped = history[-1]
        ok &= isinstance(stopped, ToolResult) and stopped.text == STOPPED
        _say(f"  the history ends with: {stopped!r}")

        third, _ = await _turn(
            loop, history, "3", "Never mind that note. In one sentence: what was the most urgent?"
        )
        ok &= third.outcome is TurnOutcome.ANSWERED

        # About 9,000 tokens: well past 85% of the declared window, so the turn compacts first.
        filler = "\n".join(f"Line {i}: nothing to do here, just context." for i in range(800))
        fourth, compacted = await _turn(
            loop,
            history,
            "4",
            f"Here is a long text; ignore what it says.\n{filler}\nNow reply 'noted' and name the "
            "most urgent task again.",
        )
        ok &= fourth.outcome is TurnOutcome.ANSWERED and compacted
        ok &= history[0] == SYSTEM  # the system prompt survived the compaction
        _say(f"  the history now holds {len(history)} messages; the second is the summary:")
        _say(f"  {history[1].text[:600]!r}")
        return ok
    finally:
        await models.close()


async def _turn(
    loop: AgentLoop, history: list[Message], label: str, text: str
) -> tuple[TurnEnded, bool]:
    _say(f"turn {label}: {text[:80]!r}")
    thought = written = 0
    compacted = False
    end: TurnEnded | None = None
    async with aclosing(loop.run(history, text, scope="prove-agent")) as events:
        async for event in events:
            match event:
                case ReasoningDelta(text=piece):
                    thought += len(piece)
                case TextDelta(text=piece):
                    written += len(piece)
                case ToolStarted(name=name, arguments=arguments):
                    _say(f"  tool started: {name}({arguments})")
                case ToolFinished(result=result):
                    _say(f"  tool finished: {result[:80]!r}")
                case Compacted(tokens_before=before, tokens_after=after):
                    compacted = True
                    _say(f"  compacted: about {before} tokens became {after}")
                case TurnEnded():
                    end = event
    if end is None:
        raise RuntimeError("The turn ended without TurnEnded.")
    _say(f"  readable reasoning streamed: {thought} characters; answer streamed: {written}")
    _say(
        f"  ended: {end.outcome.value} after {end.model_calls} calls, cost {end.cost_usd} USD: "
        f"{end.text[:200]!r}"
    )
    return end, compacted


def _ledger(ledger: CallLedger, before: int) -> None:
    calls = ledger.recent(LEDGER_ROWS)
    new = list(reversed(calls[: len(calls) - before]))
    _say(f"call ledger, {len(new)} new rows:")
    for call in new:
        tokens = (
            "-"
            if call.usage is None
            else (
                f"{call.usage.input_tokens} in / {call.usage.output_tokens} out "
                f"({call.usage.reasoning_tokens} reasoning)"
            )
        )
        _say(
            f"  {call.started_at} {call.model} {call.purpose} {call.outcome.value} "
            f"{call.duration_ms} ms, {tokens}, {call.cost.usd} USD ({call.cost.source.value})"
        )


def _say(line: str) -> None:
    # A model's answer may hold characters the Windows console's code page cannot write.
    sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
