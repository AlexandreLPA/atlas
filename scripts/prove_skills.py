"""Prove skills that learn end to end against a real model. Spends a little credit.

    uv run python scripts/prove_skills.py openai gpt-6-luna
    uv run python scripts/prove_skills.py gemini gemini-3.5-flash --pace 15

Keys come from the user's real home (`ATLAS_HOME`) and every call lands in its call ledger. The
skills, the memory and the database of usage live in a temporary folder, so nothing of the proof
reaches the user's real profile.

1. A skill made in a conversation: the user asks for a skill for a weekly summary. The agent must
   write it with skill_manage, the skill must load, and it must be the user's.
2. The skill in use: a new conversation asks for the weekly summary. The agent must find the skill
   in use_skill's listing and load it; the load counts as a use.
3. The skill review: a conversation where the skill was used and the user corrected the result,
   with 10 model calls since the last skill change. The review must be due, and must change the
   skills: patch the skill in use (after loading it, as the guard demands) or create one. A
   changed skill keeps its previous version and stays the user's; a created one is the review's.
   Its calls are background_review in the ledger.
4. The curator, on the skills the proof made, with the clock moved: the first pass only records
   itself; 40 days on, a skill of the review that nobody used is archived, the user's skill is
   not, and a restore brings the archived one back. No model call.

Exit code 0 only when every step did what it should.
"""

import argparse
import asyncio
import os
import sys
import tempfile
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from proof_ledger import LEDGER_ROWS, ensure_ledger  # the scripts' folder, on the path when run

from atlas.agent import AgentLoop, SpillFolder, ToolFinished, Toolset, TurnEnded
from atlas.home import (
    Database,
    Redactor,
    configure_logging,
    load_settings,
    migrate,
    open_home,
    utc_now,
)
from atlas.memory import (
    REVIEW_PURPOSE,
    BackgroundReview,
    Instructions,
    MemoryFiles,
    skill_review_due,
)
from atlas.models import (
    AssistantMessage,
    CallLedger,
    Message,
    ModelClient,
    Models,
    TextDelta,
    ToolCall,
    ToolResult,
    UserMessage,
    load_catalog,
)
from atlas.secrets import Vault
from atlas.skills import SCHEMA as SKILLS_SCHEMA
from atlas.skills import (
    Curator,
    CuratorClaim,
    Origin,
    SkillLibrary,
    SkillOperation,
    SkillUsage,
)
from atlas.tools import SkillUse, Workspace, file_tools, memory, skill_manage

IDENTITY = (
    "You are Atlas, a personal assistant. The user's working folder is {work}. Use your tools; "
    "answer briefly, in the user's language."
)

NOTES = (
    "Notas da semana:\n"
    "- Vendas: 132 pedidos (semana passada: 118).\n"
    "- Suporte: 41 chamados, 3 em aberto.\n"
    "- Lançamento do app adiado para 20/11.\n"
    "- Contratamos uma pessoa para o financeiro.\n"
    "- Reunião com o fornecedor de embalagens na terça.\n"
    "- Site ficou fora do ar 2 horas na quarta.\n"
    "- Campanha de e-mail teve 31% de abertura.\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove skills that learn end to end.")
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
    work.mkdir()
    database = Database(root / "atlas.db")
    migrate(database, (SKILLS_SCHEMA,))
    usage = SkillUsage(database)
    library = SkillLibrary(root / "skills", usage)
    files = MemoryFiles(root / "memory")
    client = models.client(args.provider, args.model)
    if args.pace:
        client = cast("ModelClient", _Paced(client, args.pace))
    instructions = Instructions(files, IDENTITY.format(work=work), conversation_search=False)
    window = client.info.context_window if client.info is not None else None

    def tools() -> Toolset:
        listed = [*file_tools(Workspace(work)), memory(files), skill_manage(library)]
        use = SkillUse(library.catalog, window, usage=usage).tool()
        return Toolset([*listed, use] if use is not None else listed)

    try:
        history: list[Message] = []
        used, _ = await _turn(
            AgentLoop(client, tools(), spill=spill, instructions=instructions),
            history,
            "1",
            "Toda sexta eu te peço um resumo semanal das minhas notas. Crie uma skill chamada "
            "resumo-semanal para isso: o resumo tem um título com a semana, os destaques em "
            "tópicos e, no fim, o que ficou pendente.",
        )
        skill = library.catalog.get("resumo-semanal")
        record = usage.record("resumo-semanal")
        ok = "skill_manage" in used and skill is not None
        ok &= record is not None and record.origin is Origin.USER
        _say(f"  skill made: {skill is not None}; origin: {record.origin if record else None}")
        if skill is None:
            return False

        history = []
        used, said = await _turn(
            AgentLoop(client, tools(), spill=spill, instructions=instructions),
            history,
            "2",
            f"Faça o resumo semanal destas notas:\n{NOTES}",
        )
        record = usage.record("resumo-semanal")
        ok &= "use_skill" in used and record is not None and record.use_count >= 1
        _say(f"  uses recorded: {record.use_count if record else 0}")

        reviewed = _corrected(history, said)
        due = skill_review_due(reviewed)
        before = {
            s.name: (s.folder / "SKILL.md").read_text(encoding="utf-8")
            for s in library.catalog.skills()
        }
        calls = sum(isinstance(m, AssistantMessage) for m in reviewed)
        _say(f"review: {calls} model calls, due: {due}")
        ended = await BackgroundReview(client, tools(), spill=spill).run(
            reviewed, memory=False, skills=True, scope="prove-skills"
        )
        after = {
            s.name: (s.folder / "SKILL.md").read_text(encoding="utf-8")
            for s in library.catalog.skills()
        }
        changed = sorted(name for name in after if before.get(name) != after[name])
        _say(
            f"  ended {ended.turn.outcome.value}, {ended.turn.model_calls} calls: "
            f"{ended.turn.text[:300]!r}"
        )
        _say(f"  skills changed or made: {changed}")
        ok &= due and bool(changed)
        for name in changed:
            record = usage.record(name)
            origin = record.origin if record else None
            versions = library.versions(name)
            _say(f"  {name}: origin {origin}, versions kept {len(versions)}")
            if name in before:
                ok &= origin is Origin.USER and len(versions) >= 1
            else:
                ok &= origin is Origin.REVIEW
        if "resumo-semanal" in changed:
            _say("  resumo-semanal now:\n" + after["resumo-semanal"])

        ok &= _curator(library, usage)
        return ok
    finally:
        await models.close()


def _corrected(history: list[Message], said: str) -> list[Message]:
    """The second conversation, then the user's correction, then work enough to make 10 model
    calls since the skill was made."""
    corrected: list[Message] = [
        *history,
        UserMessage(
            "Ficou longo e os números se perderam no meio. Daqui em diante, no resumo semanal, "
            "comece sempre pelos números da semana e use no máximo 5 tópicos."
        ),
        AssistantMessage("Entendido: números primeiro e no máximo 5 tópicos."),
        UserMessage("Refaça agora, assim."),
    ]
    count = sum(isinstance(message, AssistantMessage) for message in corrected)
    for n in range(max(0, 9 - count)):
        call = ToolCall(f"pad{n}", "read_file", '{"path": "notas.txt"}')
        corrected += [AssistantMessage(tool_calls=(call,)), ToolResult(call.id, NOTES)]
    corrected.append(
        AssistantMessage(
            "# Semana\n- Vendas: 132 pedidos (+12%)\n- Suporte: 41 chamados, 3 abertos\n"
            "- E-mail: 31% de abertura\n- App adiado para 20/11\n- Pendente: reunião de terça"
        )
    )
    return corrected


def _curator(library: SkillLibrary, usage: SkillUsage) -> bool:
    """The curator with the clock moved, on a skill the review would have made."""
    _say("curator: a review skill nobody uses, 40 days on")
    text = "---\nname: unused-trick\ndescription: Use when testing the curator.\n---\nStep.\n"
    library.apply([SkillOperation("create", "unused-trick", content=text)], Origin.REVIEW)
    clock = _Clock()
    curator = Curator(library, usage, clock)
    first = curator.run()
    clock.later(timedelta(days=40))
    second = curator.run()
    restored = library.restore("unused-trick") if second and second.archived else None
    ok = first is not None and first.claim is CuratorClaim.SEEDED
    ok &= second is not None and second.archived == ("unused-trick",)
    ok &= library.catalog.get("resumo-semanal") is not None and restored is not None
    _say(f"  first pass: {first.claim.value if first else None}")
    _say(f"  40 days on, archived: {second.archived if second else None}")
    stayed = library.catalog.get("resumo-semanal") is not None
    _say(f"  the user's skill stayed: {stayed}; restored: {restored is not None}")
    return ok


class _Clock:
    def __init__(self) -> None:
        self.now = utc_now()

    def later(self, step: timedelta) -> None:
        self.now += step

    def __call__(self) -> datetime:
        return self.now


async def _turn(
    loop: AgentLoop, history: list[Message], number: str, text: str
) -> tuple[set[str], str]:
    _say(f"turn {number}: {text[:120]}")
    used: set[str] = set()
    said = ""
    async for event in loop.run(history, text):
        if isinstance(event, ToolFinished):
            used.add(event.name)
            flag = " (failed)" if event.failed else ""
            _say(f"  tool {event.name}{flag}: {event.result[:200]!r}")
        elif isinstance(event, TextDelta):
            continue
        elif isinstance(event, TurnEnded):
            said = event.text
            _say(f"  ended {event.outcome.value}, {event.model_calls} calls: {event.text[:300]!r}")
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
