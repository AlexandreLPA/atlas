"""Prove the agent map's background tasks against a real model. Spends almost nothing: two calls,
about US$ 0.001 on gpt-6-luna (prices of 14/10/2026).

    uv run python scripts/prove_tasks.py openai gpt-6-luna

On the user's real home (`ATLAS_HOME`), the keys from the vault and both calls in the call ledger,
without migrating it; the tools are the real ones over a temporary working folder. One turn asks
for a long build in the background, and proves:

1. The model starts it in the background with run_command and names it: the `description` the
   agent map shows (Claude Code's Bash), a few words, not the command itself.
2. The screen hears it start (`on_change`), named so, and the end of its output reads what the
   build printed.
3. Stopped from the screen, the screen hears it end as `stopped`.

Exit code 0 only when all three hold.
"""

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from proof_ledger import LEDGER_ROWS, ensure_ledger  # the scripts' folder, on the path when run

from atlas.agent import AgentLoop, SpillFolder, ToolFinished, Toolset, TurnEnded
from atlas.home import Database, Redactor, configure_logging, load_settings, open_home
from atlas.models import CallLedger, Message, Models, SystemMessage, load_catalog
from atlas.secrets import Vault
from atlas.tools import (
    BackgroundCommand,
    Commands,
    WebFetch,
    Workspace,
    file_tools,
    find_files,
    web_search,
)
from atlas.tools.keyless import KeylessSearch

SYSTEM = SystemMessage(
    "You are Atlas, a personal assistant working in the user's folder. Use your tools. Answer in "
    "one or two sentences."
)
ASK = (
    "Start the game build in the background: it is `python build.py` in this folder and takes a "
    "few minutes. Don't wait for it; just tell me it started."
)
BUILD = "import time\nprint('compiling level 1 of 40', flush=True)\ntime.sleep(300)\n"
#: More words than this is a sentence, not the few words the map shows.
MOST_WORDS = 12


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the agent map's background tasks.")
    parser.add_argument("provider")
    parser.add_argument("model")
    args = parser.parse_args()

    settings = load_settings(os.environ).settings
    home = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)
    database = Database(home.database_file)
    ensure_ledger(database)
    ledger = CallLedger(database)
    before = len(ledger.recent(LEDGER_ROWS))
    vault = Vault(home.secrets_folder, settings.vault, redactor)
    models = Models(load_catalog(home.models_file), vault, settings.model, ledger=ledger)
    with tempfile.TemporaryDirectory() as folder:
        try:
            proved = asyncio.run(
                prove(models, args.provider, args.model, Path(folder), home.spill_folder)
            )
        finally:
            calls = ledger.recent(LEDGER_ROWS)
            spent = calls[: len(calls) - before]
            cost = sum((call.cost.usd or 0) for call in spent)
            _say(f"calls: {len(spent)}, cost at list price: US$ {cost:.4f}")
    _say("PROVED: named, seen and stopped from the screen." if proved else "FAILED.")
    return 0 if proved else 1


async def prove(models: Models, provider: str, model: str, root: Path, spill: Path) -> bool:
    """The turn and the three checks over `root`, on the models given (a fake one, to rehearse)."""
    work = root / "work"
    work.mkdir()
    (work / "build.py").write_text(BUILD, encoding="utf-8")
    seen: list[BackgroundCommand] = []
    commands = Commands(Workspace(work), root / "commands", on_change=seen.append)
    toolset = Toolset(
        [
            *file_tools(Workspace(work)),
            find_files(Workspace(work)),
            commands.tool(),
            web_search(KeylessSearch()),
            WebFetch(SpillFolder(spill)).tool(),
        ]
    )
    try:
        loop = AgentLoop(models.client(provider, model), toolset, spill=SpillFolder(spill))
        history: list[Message] = [SYSTEM]
        _say(f"turn: {ASK}")
        async for event in loop.run(history, ASK):
            if isinstance(event, ToolFinished):
                _say(f"  tool {event.name}: {event.result[:200]!r}")
            elif isinstance(event, TurnEnded):
                _say(f"  ended {event.outcome.value}, {event.model_calls} calls: {event.text!r}")

        started = [view for view in seen if view.status == "running"]
        if not started:
            _say("1. FAILED: no command started in the background")
            return False
        view = started[0]
        words = len(view.description.split())
        named = 0 < words <= MOST_WORDS and view.description != view.command
        _say(f"1. {'ok' if named else 'FAILED'}: {view.id} named {view.description!r} ({words} "
             f"words) for {view.command!r}")  # fmt: skip

        output = ""
        for _ in range(100):
            output = commands.screen_output(view.id) or ""
            if "compiling" in output:
                break
            await asyncio.sleep(0.1)
        heard = "compiling" in output and any(row.id == view.id for row in commands.background())
        _say(f"2. {'ok' if heard else 'FAILED'}: running on the screen, its output {output!r}")

        said = await commands.stop(view.id)
        ended = [row for row in seen if row.id == view.id and row.status != "running"]
        stopped = [row.status for row in ended] == ["stopped"] and commands.background() == []
        _say(f"3. {'ok' if stopped else 'FAILED'}: {said!r}; ended as {[r.status for r in ended]}")
        return named and heard and stopped
    finally:
        await commands.close()
        await models.close()


def _say(line: str) -> None:
    # A model's answer may hold characters the Windows console's code page cannot write.
    sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
