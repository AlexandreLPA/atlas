"""Prove the tools end to end against a real model. Spends a little credit.

    uv run python scripts/prove_tools.py openai gpt-6-luna

Three turns on the user's real home (`ATLAS_HOME`), keys from the vault, every call in the call
ledger. The tools are the real ones, over a temporary working folder, and a real MCP server (the
test suite's notes server, over stdio) is connected, so its tools are deferred behind tool search:

1. Files and a command: find the file that mentions the electricity bill, change its due date
   with edit_file, and show it with run_command. The file on disk must have changed.
2. Tool search and MCP: note something in the notes app. The model must find the MCP tool with
   tool_search (OpenAI's own tool search on the Responses API, the tool list elsewhere) and call
   it. The note must be in the server.
3. The plan: plan three steps. The plan must hold three items.
4. An image: read a red square with read_file and say its color. The answer must say red.

Exit code 0 only when every turn did what it should.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from proof_ledger import LEDGER_ROWS, ensure_ledger  # the scripts' folder, on the path when run

from atlas.agent import AgentLoop, SpillFolder, ToolFinished, Toolset, TurnEnded
from atlas.home import Database, Redactor, configure_logging, load_settings, open_home
from atlas.mcp import McpClient
from atlas.models import (
    AssistantMessage,
    CallLedger,
    Message,
    ModelClient,
    Models,
    SystemMessage,
    TextDelta,
    load_catalog,
)
from atlas.secrets import Vault
from atlas.tools import (
    Commands,
    Plan,
    WebFetch,
    Workspace,
    file_tools,
    find_files,
    install_tool_search,
    web_search,
)
from atlas.tools.keyless import KeylessSearch

TESTS = Path(__file__).resolve().parent.parent / "tests"
SERVER = TESTS / "mcp_server.py"
sys.path.insert(0, str(TESTS))

from documents_support import write_png  # noqa: E402 - the tests' PNG writer, by path

SYSTEM = SystemMessage(
    "You are Atlas, a personal assistant working in the user's folder. Use your tools; find a "
    "tool you lack with tool_search. Answer in one or two sentences."
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the tools end to end.")
    parser.add_argument("provider")
    parser.add_argument("model")
    parser.add_argument(
        "--pace",
        type=float,
        default=0.0,
        help="seconds to wait before each model call, for a per-minute limit (Gemini's free tier)",
    )
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
        root = Path(folder)
        try:
            proved = asyncio.run(_prove(models, args, root, SpillFolder(home.spill_folder)))
        finally:
            calls = ledger.recent(LEDGER_ROWS)
            spent = calls[: len(calls) - before]
            cost = sum((call.cost.usd or 0) for call in spent)
            _say(f"calls: {len(spent)}, cost at list price: US$ {cost:.4f}")
    _say("PROVED: all four turns did what they should." if proved else "FAILED.")
    return 0 if proved else 1


async def _prove(models: Models, args: argparse.Namespace, root: Path, spill: SpillFolder) -> bool:
    work = root / "work"
    (work / "bills").mkdir(parents=True)
    (work / "bills" / "electricity.md").write_text(
        "# Electricity\n\nDue: TODAY\nAmount: 120\n", encoding="utf-8"
    )
    (work / "bills" / "water.md").write_text("# Water\n\nDue: Friday\n", encoding="utf-8")
    (root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"notes": {"command": "python", "args": [str(SERVER)]}}}),
        encoding="utf-8",
    )
    workspace = Workspace(work)
    commands = Commands(workspace, root / "commands")
    plan = Plan()
    toolset = Toolset(
        [
            *file_tools(workspace),
            find_files(workspace),
            commands.tool(),
            web_search(KeylessSearch()),
            WebFetch(spill).tool(),
            plan.tool(),
        ]
    )

    def changed() -> None:
        install_tool_search(toolset, mcp.sources)

    mcp = McpClient(root / "mcp.json", lambda _: None, toolset, changed)
    await mcp.start()
    try:
        client = models.client(args.provider, args.model)
        if args.pace:
            client = cast("ModelClient", _Paced(client, args.pace))
        loop = AgentLoop(client, toolset, spill=spill)
        history: list[Message] = [SYSTEM]

        used = await _turn(
            loop,
            history,
            "1",
            "Find the bill in this folder that mentions electricity, change its due date from "
            "TODAY to tomorrow, then run a command that prints the file.",
        )
        edited = (work / "bills" / "electricity.md").read_text(encoding="utf-8")
        # "Tomorrow" may come back as a date (gpt-6-luna wrote 2026-10-10 on 09/10); what must hold
        # is that TODAY is gone and the amount was left alone.
        ok = "TODAY" not in edited and "Amount: 120" in edited and "Due:" in edited
        ok &= {"edit_file", "run_command"} <= used
        _say(f"  file now: {edited.splitlines()[2]!r}; tools: {sorted(used)}")

        used = await _turn(loop, history, "2", "Add 'buy bread' to my notes app.")
        lister = toolset.get("mcp__notes__list_notes")
        notes = str(await lister.run({})) if lister is not None else ""
        found = "tool_search" in used and "mcp__notes__add_note" in used
        ok &= found and "bread" in notes
        _say(f"  notes server holds: {notes!r}; tools: {sorted(used)}")

        used = await _turn(
            loop,
            history,
            "3",
            "Use the plan tool to plan these three steps: pay the electricity bill, pay the "
            "water bill, file both receipts.",
        )
        ok &= "plan" in used and len(plan.items) == 3
        _say(f"  plan: {[item['content'] for item in plan.items]}")

        write_png(work / "swatch.png", (220, 30, 30))
        used = await _turn(
            loop, history, "4", "Look at swatch.png in this folder: what color is it? One word."
        )
        answer = history[-1]
        said = answer.text if isinstance(answer, AssistantMessage) else ""
        ok &= "read_file" in used and "red" in said.lower()
        _say(f"  answer: {said!r}")
        return ok
    finally:
        await mcp.close()
        await commands.close()
        await models.close()


async def _turn(loop: AgentLoop, history: list[Message], number: str, text: str) -> set[str]:
    _say(f"turn {number}: {text}")
    used: set[str] = set()
    async for event in loop.run(history, text):
        if isinstance(event, ToolFinished):
            used.add(event.name)
            flag = " (failed)" if event.failed else ""
            _say(f"  tool {event.name}{flag}: {event.result[:160]!r}")
        elif isinstance(event, TextDelta):
            continue
        elif isinstance(event, TurnEnded):
            _say(f"  ended {event.outcome.value}, {event.model_calls} calls: {event.text[:200]!r}")
    return used


class _Paced:
    """The model client, waiting before each call: Gemini's free tier cuts replies and answers
    429 past a few calls a minute (seen on 09/10/2026)."""

    def __init__(self, client: Any, seconds: float) -> None:
        self._client = client
        self._seconds = seconds

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

    async def stream(self, request: Any) -> AsyncIterator[Any]:
        await asyncio.sleep(self._seconds)
        async for event in self._client.stream(request):
            yield event

    async def reply(self, request: Any) -> Any:
        await asyncio.sleep(self._seconds)
        return await self._client.reply(request)


def _say(line: str) -> None:
    # A model's answer may hold characters the Windows console's code page cannot write.
    sys.stdout.buffer.write((line + "\n").encode("utf-8"))
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
