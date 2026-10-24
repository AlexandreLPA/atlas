"""Prove the memory end to end against a real model. Spends a little credit.

    uv run python scripts/prove_memory.py openai gpt-6-luna
    uv run python scripts/prove_memory.py gemini gemini-3.5-flash --pace 15

Keys come from the user's real home (`ATLAS_HOME`) and every call lands in its call ledger. The
memory, the conversations and the documents' texts live in a temporary folder, so nothing of the
proof reaches the user's real profile. Each conversation gets a fresh loop and fresh instructions,
as a new conversation would after a restart.

1. Memory: the user says who they are and asks to be remembered. The agent must save it with
   the memory tool, and USER.md must hold it.
2. The profile in the prompt: a new conversation asks the user's name. The instructions must
   carry it, and the agent must answer it without searching.
3. Conversation search: a fact said in an earlier stored conversation (and never saved to
   memory) is asked without its accent. conversation_search waits behind tool search: the
   agent must find it there and use it.
4. Documents: a figure that only a Word file holds. The agent must find it with find_files.
5. The memory review: a stored conversation of 10 user messages that reveal facts the agent
   never saved. The review must be due, and must save something with the memory tool, its calls
   named background_review in the ledger.

Then the conversations are read back from the database: what was said must be there.
Exit code 0 only when every step did what it should.
"""

import argparse
import asyncio
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

from proof_ledger import LEDGER_ROWS, ensure_ledger  # the scripts' folder, on the path when run

from atlas.agent import AgentLoop, SpillFolder, ToolFinished, Toolset, TurnEnded
from atlas.conversations import SCHEMA as CONVERSATIONS_SCHEMA
from atlas.conversations import Conversation, ConversationStore, fts_query
from atlas.home import Database, Redactor, configure_logging, load_settings, migrate, open_home
from atlas.memory import (
    REVIEW_PURPOSE,
    BackgroundReview,
    Instructions,
    MemoryFiles,
    Target,
    review_due,
)
from atlas.models import (
    AssistantMessage,
    CallLedger,
    ModelClient,
    Models,
    SystemMessage,
    TextDelta,
    UserMessage,
    load_catalog,
)
from atlas.secrets import Vault
from atlas.tools import (
    DocumentTexts,
    Workspace,
    conversation_search,
    file_tools,
    find_files,
    install_tool_search,
    memory,
)

TESTS = Path(__file__).resolve().parent.parent / "tests"
sys.path.insert(0, str(TESTS))

from documents_support import write_docx  # noqa: E402 - the tests' Word writer, by path

IDENTITY = (
    "You are Atlas, a personal assistant. The user's working folder is {work}. Use your tools; "
    "answer briefly, in the user's language."
)

#: A past conversation, stored as it was said: the fact lives only here.
PAST = [
    UserMessage("Decidimos hoje: o lançamento do projeto Órion passou para 14 de novembro."),
    AssistantMessage("Anotado: o lançamento do Órion fica em 14 de novembro."),
]

#: A conversation of ten user messages whose facts nobody saved.
UNREVIEWED = [
    ("Bom dia! Vou te contar como trabalho, para você me ajudar melhor.", "Bom dia! Pode contar."),
    ("Eu trabalho com Python e TypeScript.", "Entendido."),
    ("Em casa uso Linux, e no escritório uso Windows.", "Certo."),
    ("Para pacotes JavaScript eu sempre uso o pnpm, nunca o npm.", "Anotado mentalmente."),
    ("Não gosto de respostas longas: vá direto ao ponto.", "Combinado."),
    ("Minhas reuniões são sempre de manhã, então prefiro tarefas à tarde.", "Ok."),
    ("Meu fuso horário é o de Brasília.", "Certo."),
    ("Uso o VS Code como editor.", "Ok."),
    ("Faço deploy só às terças.", "Entendido."),
    ("Era isso por hoje.", "Até mais!"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the memory end to end.")
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
            reviews = sum(1 for call in spent if call.purpose == REVIEW_PURPOSE)
            _say(f"calls: {len(spent)} ({reviews} {REVIEW_PURPOSE}), at list price: US$ {cost:.4f}")
    _say("PROVED: every step did what it should." if proved else "FAILED.")
    return 0 if proved else 1


async def _prove(models: Models, args: argparse.Namespace, root: Path, spill: SpillFolder) -> bool:
    work = root / "work"
    (work / "contracts").mkdir(parents=True)
    write_docx(
        work / "contracts" / "fornecimento.docx",
        ["Contrato de fornecimento", "A cláusula girassol fixa a multa em 7% ao mês."],
    )
    conversations_db = Database(root / "atlas.db")
    migrate(conversations_db, (CONVERSATIONS_SCHEMA,))
    store = ConversationStore(conversations_db)
    files = MemoryFiles(root / "memory")
    texts = DocumentTexts(root / "documents")
    workspace = Workspace(work)
    client = models.client(args.provider, args.model)
    if args.pace:
        client = cast("ModelClient", _Paced(client, args.pace))
    instructions = Instructions(files, IDENTITY.format(work=work))

    def tools(conversation: Conversation) -> Toolset:
        toolset = Toolset(
            [
                *file_tools(workspace),
                find_files(workspace, texts=texts),
                memory(files),
                conversation_search(store, conversation.id),
            ]
        )
        install_tool_search(toolset)  # conversation_search waits behind it
        return toolset

    def conversation_loop(conversation: Conversation) -> AgentLoop:
        return AgentLoop(client, tools(conversation), spill=spill, instructions=instructions)

    try:
        first = store.create()
        used, _ = await _turn(
            conversation_loop(first),
            first,
            "1",
            "Meu nome é Marina e prefiro respostas curtas, em tópicos. Guarde isso para as "
            "próximas conversas.",
        )
        profile = files.entries(Target.USER) + files.entries(Target.MEMORY)
        ok = "memory" in used and any("Marina" in entry for entry in profile)
        _say(f"  profile and memory now: {profile}")

        past = store.create()
        past.history += PAST
        past.save()

        second = store.create()
        loop = conversation_loop(second)
        used, said = await _turn(loop, second, "2", "Como eu me chamo? Responda só o nome.")
        carried = "Marina" in second.history[0].text if second.history else False
        ok &= carried and "Marina" in said and not {"conversation_search", "memory"} & used
        _say(f"  instructions carry the name: {carried}")

        used, said = await _turn(
            loop,
            second,
            "3",
            "Em que data ficou o lançamento do projeto Orion? Já falamos disso em outra conversa.",
        )
        ok &= "conversation_search" in used and "14" in said

        used, said = await _turn(
            loop,
            second,
            "4",
            "Na pasta de trabalho há um contrato em Word. Qual multa a cláusula girassol fixa?",
        )
        ok &= "find_files" in used and "7" in said

        reviewed = store.create()
        reviewed.history.append(SystemMessage(instructions()))
        for text, reply in UNREVIEWED:
            reviewed.history += [UserMessage(text), AssistantMessage(reply)]
        reviewed.save()
        due = review_due(reviewed.history)
        before = files.entries(Target.USER) + files.entries(Target.MEMORY)
        _say("review: 10 user messages, nothing saved")
        ended = await BackgroundReview(client, tools(reviewed), spill=spill).run(
            reviewed.history, scope=reviewed.id
        )
        after = files.entries(Target.USER) + files.entries(Target.MEMORY)
        added = [entry for entry in after if entry not in before]
        _say(f"  due: {due}; ended {ended.turn.outcome.value}, {ended.turn.model_calls} calls")
        _say(f"  saved by the review: {added}")
        ok &= due and bool(added)

        reopened = store.open(second.id).history
        query = fts_query("girassol")
        found = store.search(query, limit=3) if query else []
        ok &= len(reopened) == len(second.history) and any(
            hit.conversation.id == second.id for hit in found
        )
        _say(f"  conversation 2 read back: {len(reopened)} of {len(second.history)} messages")
        return ok
    finally:
        await models.close()


async def _turn(
    loop: AgentLoop, conversation: Conversation, number: str, text: str
) -> tuple[set[str], str]:
    _say(f"turn {number}: {text}")
    used: set[str] = set()
    said = ""
    async for event in loop.run(conversation.history, text, scope=conversation.id):
        if isinstance(event, ToolFinished):
            used.add(event.name)
            flag = " (failed)" if event.failed else ""
            _say(f"  tool {event.name}{flag}: {event.result[:200]!r}")
        elif isinstance(event, TextDelta):
            continue
        elif isinstance(event, TurnEnded):
            said = event.text
            _say(f"  ended {event.outcome.value}, {event.model_calls} calls: {event.text[:300]!r}")
        conversation.save()
    return used, said


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
