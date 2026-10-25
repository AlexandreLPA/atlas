"""Prove that Atlas answers about itself from its documentation, against a real model. Spends a
little credit.

    uv run python scripts/prove_docs.py openai gpt-6-luna
    uv run python scripts/prove_docs.py gemini gemini-3.5-flash --pace 15

Keys come from the user's real home (`ATLAS_HOME`), and every call lands in its call ledger. The
skills and the working folder live in a temporary folder, with the skills Atlas ships synced into
it, the `atlas` skill among them, so nothing of the proof reaches the user's real home.

1. A guide's question: "how do I add a second Claude Code account?". The agent must read the
   documentation with `atlas docs` (through `run_command`, as it would in a conversation) and
   answer with what the page says: the Executors tab and adding an account.
2. A reference's question: "which setting changes how long an approval waits?". The agent must
   read the documentation again and name `approval_timeout`.

Exit code 0 only when both answers came from the documentation and hold what it says.
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

from atlas.agent import AgentLoop, SpillFolder, ToolFinished, Toolset, ToolStarted, TurnEnded
from atlas.home import (
    Database,
    Redactor,
    configure_logging,
    load_settings,
    migrate,
    open_home,
)
from atlas.memory import Instructions, MemoryFiles
from atlas.models import CallLedger, Message, ModelClient, Models, TextDelta, load_catalog
from atlas.secrets import Vault
from atlas.skills import SCHEMA as SKILLS_SCHEMA
from atlas.skills import SkillLibrary, SkillUsage, sync_bundled
from atlas.tools import Commands, SkillUse, Workspace, file_tools

IDENTITY = (
    "You are Atlas, a personal agent. The user's working folder is {work}. Use your tools; "
    "answer briefly, in the user's language."
)

QUESTIONS = (
    (
        "Como eu ligo uma segunda conta do Claude Code no Atlas?",
        ("execut",),
        ("conta", "account"),
    ),
    (
        "No Atlas, qual configuração muda quanto tempo uma aprovação espera antes de ser recusada?",
        ("approval_timeout",),
        (),
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove Atlas answers about itself from its docs.")
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
        try:
            proved = asyncio.run(_prove(models, args, Path(folder), SpillFolder(home.spill_folder)))
        finally:
            calls = ledger.recent(LEDGER_ROWS)
            spent = calls[: len(calls) - before]
            cost = sum((call.cost.usd or 0) for call in spent)
            _say(f"calls: {len(spent)}, at list price: US$ {cost:.4f}")
    _say("PROVED: both answers came from the documentation." if proved else "FAILED.")
    return 0 if proved else 1


async def _prove(models: Models, args: argparse.Namespace, root: Path, spill: SpillFolder) -> bool:
    work = root / "work"
    work.mkdir()
    database = Database(root / "atlas.db")
    migrate(database, (SKILLS_SCHEMA,))
    usage = SkillUsage(database)
    library = SkillLibrary(root / "skills", usage)
    synced = sync_bundled(library)
    _say(f"skills shipped and synced: {sorted(synced.copied)}")
    if library.catalog.get("atlas") is None:
        _say("  the atlas skill did not sync")
        return False
    files = MemoryFiles(root / "memory")
    client = models.client(args.provider, args.model)
    if args.pace:
        client = cast("ModelClient", _Paced(client, args.pace))
    instructions = Instructions(files, IDENTITY.format(work=work), conversation_search=False)
    window = client.info.context_window if client.info is not None else None

    def tools() -> Toolset:
        listed = [*file_tools(Workspace(work)), Commands(Workspace(work), root / "commands").tool()]
        use = SkillUse(library.catalog, window, usage=usage).tool()
        return Toolset([*listed, use] if use is not None else listed)

    ok = True
    try:
        for number, (question, must, either) in enumerate(QUESTIONS, start=1):
            commands, said = await _turn(
                AgentLoop(client, tools(), spill=spill, instructions=instructions),
                [],
                str(number),
                question,
            )
            read = [command for command in commands if "atlas docs" in command]
            lowered = said.lower()
            holds = all(word in lowered for word in must) and (
                not either or any(word in lowered for word in either)
            )
            _say(f"  read the documentation: {read or 'no'}")
            _say(f"  the answer holds {[*must, *either]}: {holds}")
            ok &= bool(read) and holds
        return ok
    finally:
        await models.close()


async def _turn(
    loop: AgentLoop, history: list[Message], number: str, text: str
) -> tuple[list[str], str]:
    _say(f"turn {number}: {text}")
    commands: list[str] = []
    said = ""
    async for event in loop.run(history, text):
        if isinstance(event, ToolStarted) and event.name == "run_command":
            command = str(json.loads(event.arguments or "{}").get("command", ""))
            commands.append(command)
        elif isinstance(event, ToolFinished):
            _say(f"  tool {event.name} ({event.status.value}): {event.result[:160]!r}")
        elif isinstance(event, TextDelta):
            continue
        elif isinstance(event, TurnEnded):
            said = event.text
            _say(f"  ended {event.outcome.value}, {event.model_calls} calls:\n{event.text}")
    return commands, said


class _Paced:
    """The model client, waiting before each call: Gemini's free tier cuts replies and answers
    429 past a few calls a minute."""

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
    raise SystemExit(main())
