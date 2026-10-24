"""Prove bots against a real model. Spends a little credit.

    uv run python scripts/prove_bots.py openai gpt-6-luna
    uv run python scripts/prove_bots.py gemini gemini-3.8-flash --pace 15

Every step runs on the real server (the gateway and the assistant on uvicorn, on a real port) over
a temporary home, the client talking over a real WebSocket as the desktop does. Keys come from the
user's real home (`ATLAS_HOME`), and every model call lands in its call ledger. The screen itself
(the Bots tab, the dialog) is proved by the app's e2e scenario, `apps/desktop/e2e/bots.spec.ts`.

1. A bot answers as itself: made as the dialog makes it (`bot.create`), with a rule in its mission
   (end every reply with a word), it is opened and asked; the reply keeps the rule, and a second
   click opens the same conversation.
2. One profile: a second bot's conversation is already open when the first is told the user
   changed jobs and saves it; the second, asked in its open conversation, knows the new job. What
   the first notes of its own work is in its own memory, never in the home's.
3. A bot's skill is its own: asked to keep a procedure as a skill, the bot creates it in its
   folder, recorded as its making, and the home's library does not get it.
4. A bot finds only its own conversations: a word said to the main agent is not found by a bot
   that searches for it, and the main agent, searching, finds what was said to a bot.
5. A bot's routine: run now, it runs as the bot, and its answer enters the bot's fixed
   conversation as a message the bot answers.
6. The main agent asks a bot from an ordinary conversation: it writes with message_agent and ends
   its turn; the bot answers in its own conversation; the answer comes back as a notice, and the
   main agent tells the user, naming the bot. The ledger then says what each bot cost.

Exit code 0 only when every step did what it should.
"""

import argparse
import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from proof_ledger import LEDGER_ROWS  # the scripts' folder, on the path when run

from atlas.__main__ import SCHEMAS
from atlas.conversations import ConversationStore
from atlas.home import (
    Database,
    Home,
    ModelSettings,
    Redactor,
    Settings,
    WorkspaceSettings,
    configure_logging,
    load_settings,
    migrate,
    open_home,
)
from atlas.models import CallLedger, Models, load_catalog
from atlas.secrets import Vault
from atlas.skills import SkillUsage

SCRIPTS = Path(__file__).resolve().parent
TESTS = SCRIPTS.parent / "tests"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(SCRIPTS))

from proof_ledger import ensure_ledger  # noqa: E402 - the scripts' folder
from prove_server import _Paced, _say  # noqa: E402 - the other proofs' helpers, by path

from server_support import AtlasServer, Client  # noqa: E402 - the tests' server and client

WAIT = 300
FINANCE_MISSION = (
    "Você é o bot de Finanças do usuário: cuida das contas, dos vencimentos e dos impostos dele. "
    "Regra permanente: termine toda resposta com a palavra CAIXA, em maiúsculas."
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove bots end to end.")
    parser.add_argument("provider")
    parser.add_argument("model")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds before each model call")
    parser.add_argument("--steps", default="1,2,3,4,5,6", help="the steps to run, such as 2,6")
    args = parser.parse_args()
    settings = load_settings(os.environ).settings
    real = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)
    database = Database(real.database_file)
    ensure_ledger(database)
    ledger = CallLedger(database)
    before = len(ledger.recent(LEDGER_ROWS))
    vault = Vault(real.secrets_folder, settings.vault, redactor)
    chosen = ModelSettings(provider=args.provider, name=args.model)
    models = Models(load_catalog(real.models_file), vault, chosen, ledger=ledger)
    with tempfile.TemporaryDirectory() as folder:
        try:
            only = {int(step) for step in str(args.steps).split(",")}
            proved = prove(Path(folder), models, vault, chosen, args.pace, only, ledger)
        finally:
            calls = ledger.recent(LEDGER_ROWS)
            spent = calls[: len(calls) - before]
            cost = sum((call.cost.usd or 0) for call in spent)
            _say(f"calls in this process: {len(spent)}, at list price: US$ {cost:.4f}")
    _say("PROVED: every step did what it should." if proved else "FAILED.")
    return 0 if proved else 1


def prove(
    root: Path,
    models: Models,
    vault: Vault,
    chosen: ModelSettings,
    pace: float,
    only: set[int],
    ledger: CallLedger,
) -> bool:
    """Every step over `root`, on the models given (a fake one, to rehearse), whose calls land in
    `ledger`. The steps build on one another's bots, so each makes what it needs when run alone."""
    before = len(ledger.recent(LEDGER_ROWS))
    home = open_home(root / "home")
    work = root / "work"
    work.mkdir()
    migrate(Database(home.database_file), SCHEMAS)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    served = cast("Models", _Paced(models, pace)) if pace else models
    results: dict[str, bool] = {}
    steps: list[tuple[int, str, Callable[[AtlasServer, Home], bool]]] = [
        (1, "1 a bot answers as itself", _answers_as_itself),
        (2, "2 one profile", _one_profile),
        (3, "3 a bot's skill is its own", _own_skill),
        (4, "4 a bot finds only its own conversations", _own_conversations),
        (5, "5 a bot's routine", _routine),
        (6, "6 the main agent asks a bot", lambda s, h: _asks_a_bot(s, h, ledger, before)),
    ]
    with AtlasServer(home, settings, served, vault) as server:
        for number, name, step in steps:
            if number in only:
                _step(results, name, lambda step=step: step(server, home))
    for step_name, ok in results.items():
        _say(f"{'ok  ' if ok else 'FAIL'} {step_name}")
    return bool(results) and all(results.values())


# -- the steps -----------------------------------------------------------------------------------


def _answers_as_itself(server: AtlasServer, home: Home) -> bool:
    _say("step 1: a bot answers as itself")
    with server.client() as client:
        _bot(client, "finance")
        heard = client.result("bot.list")["bots"]
        fixed = client.result("bot.open", {"name": "finance"})["conversation_id"]
        again = client.result("bot.open", {"name": "finance"})["conversation_id"]
        done = _turn(client, fixed, "Em uma frase: com o que você me ajuda?")
        listed = client.result("conversation.list")["conversations"]
    text = done["text"].strip()
    _say(f"  roster: {[bot['name'] for bot in heard]}; {done['status']}: {text[-160:]!r}")
    return (
        (home.bots_folder / "finance" / "BOT.md").is_file()
        and again == fixed
        and done["status"] == "answered"
        and text.rstrip(" .!*_`").endswith("CAIXA")
        and all(row["id"] != fixed for row in listed)
    )


def _one_profile(server: AtlasServer, home: Home) -> bool:
    _say("step 2: one profile")
    profile = home.memory_folder / "USER.md"
    with server.client() as client:
        _bot(client, "finance")
        _bot(client, "produto")
        produto = client.result("bot.open", {"name": "produto"})["conversation_id"]
        finance = client.result("bot.open", {"name": "finance"})["conversation_id"]
        # The second bot's conversation is open, with its instructions as they were, before the
        # first is told anything.
        _turn(client, produto, "Oi. Responda só: pronto.")
        saved = _turn(
            client,
            finance,
            "Mudei de emprego: agora trabalho na Padaria Aurora. Guarde isso no meu perfil, com a "
            "ferramenta memory, target user.",
        )
        noted = _turn(
            client,
            finance,
            "Guarde nas suas notas, com a ferramenta memory, target memory: o aluguel vence todo "
            "dia 5.",
        )
        asked = _turn(client, produto, "Onde eu trabalho hoje? Responda em uma frase.")
    user = profile.read_text(encoding="utf-8") if profile.is_file() else ""
    notes = home.bots_folder / "finance" / "MEMORY.md"
    own = notes.read_text(encoding="utf-8") if notes.is_file() else ""
    main_notes = home.memory_folder / "MEMORY.md"
    _say(f"  profile: {user[:120]!r}; finance's notes: {own[:120]!r}")
    _say(f"  {saved['status']}, {noted['status']}; produto: {asked['text'][:160]!r}")
    return (
        "Aurora" in user
        and "Aurora" in asked["text"]
        and "5" in own
        and not (main_notes.is_file() and "aluguel" in main_notes.read_text(encoding="utf-8"))
        and not (home.bots_folder / "produto" / "MEMORY.md").is_file()
    )


def _own_skill(server: AtlasServer, home: Home) -> bool:
    _say("step 3: a bot's skill is its own")
    with server.client() as client:
        _bot(client, "finance")
        fixed = client.result("bot.open", {"name": "finance"})["conversation_id"]
        done, events = _turn_events(
            client,
            fixed,
            "Guarde como skill, com a ferramenta skill_manage, o nosso fechamento do mês. Nome da "
            "skill: fechamento-do-mes. Os passos: 1) listar as contas pagas; 2) conferir o saldo; "
            "3) anotar o que vence no mês seguinte.",
        )
    mine = home.bots_folder / "finance" / "skills" / "fechamento-do-mes" / "SKILL.md"
    record = SkillUsage(Database(home.database_file), "finance").record("fechamento-do-mes")
    creator = None if record is None else record.creator
    _say(f"  skill_manage: {[r[:100] for r in _results(events, 'skill_manage')]}")
    _say(f"  in the bot's folder: {mine.is_file()}; creator: {creator}; {done['status']}")
    return (
        mine.is_file()
        and not (home.skills_folder / "fechamento-do-mes").exists()
        and creator == "finance"
    )


def _own_conversations(server: AtlasServer, home: Home) -> bool:
    _say("step 4: a bot finds only its own conversations")
    with server.client() as client:
        _bot(client, "finance")
        _bot(client, "produto")
        mine = client.result("conversation.create")["conversation_id"]
        _turn(client, mine, "Anote só nesta conversa: o código do projeto secreto é ORQUIDEA-42.")
        finance = client.result("bot.open", {"name": "finance"})["conversation_id"]
        _turn(client, finance, "O plano de saúde agora custa 480 reais por mês. Responda: anotado.")
        produto = client.result("bot.open", {"name": "produto"})["conversation_id"]
        blind, searched = _turn_events(
            client,
            produto,
            "Use a ferramenta conversation_search para procurar ORQUIDEA nas conversas passadas e "
            "me diga, em uma frase, o que achou.",
        )
        other = client.result("conversation.create")["conversation_id"]
        seeing, found = _turn_events(
            client,
            other,
            "Use a ferramenta conversation_search para procurar 'plano de saúde' nas conversas "
            "passadas e me diga quanto custa e com quem foi a conversa.",
        )
    by_bot = _results(searched, "conversation_search")
    by_main = _results(found, "conversation_search")
    _say(f"  the bot's search: {[r[:120] for r in by_bot]}; it said: {blind['text'][:140]!r}")
    _say(f"  the main agent's: {[r[:120] for r in by_main]}; it said: {seeing['text'][:140]!r}")
    return (
        bool(by_bot)
        and all("42" not in result for result in by_bot)
        and "42" not in blind["text"]
        and any("480" in result and "(with the bot finance)" in result for result in by_main)
        and "480" in seeing["text"]
    )


def _routine(server: AtlasServer, home: Home) -> bool:
    _say("step 5: a bot's routine")
    with server.client() as client:
        _bot(client, "finance")
        fixed = client.result("bot.open", {"name": "finance"})["conversation_id"]
        routine = server.gateway.assistant.routines.create(
            "vencimentos",
            "every 1h",
            prompt="Responda apenas com esta frase: A conta de luz vence na sexta.",
            bot="finance",
        )
        client.result("routine.run", {"routine_id": routine.id})
        finished = client.event("routine.finished", timeout=WAIT)["payload"]
        if finished["status"] != "ok":
            _say(f"  the run did not answer: {finished['status']}: {finished['text'][:160]!r}")
            return False
        deadline = time.monotonic() + WAIT
        while True:
            started = client.event("message.start", timeout=deadline - time.monotonic())
            if started["conversation_id"] == fixed and started["payload"]["kind"] == "notice":
                break
        done = _complete(client, fixed, started["seq"])
        runs = client.result("routine.runs", {"routine_id": routine.id})["runs"]
    delivered = started["payload"]["text"]
    conversations = ConversationStore(Database(home.database_file))
    run = conversations.info(runs[0]["conversation_id"])
    _say(f"  run: {finished['status']}, as {run.bot}; delivered: {delivered[:110]!r}")
    _say(f"  the bot answered: {done['status']}: {done['text'][:140]!r}")
    return (
        finished["status"] == "ok"
        and run.bot == "finance"
        and delivered.startswith('[Cronjob "vencimentos" output')
        and "luz" in delivered
        and done["status"] == "answered"
    )


def _asks_a_bot(server: AtlasServer, home: Home, ledger: CallLedger, before: int) -> bool:
    _say("step 6: the main agent asks a bot")
    notes = home.bots_folder / "finance" / "MEMORY.md"
    with server.client() as client:
        _bot(client, "finance")
        if not notes.is_file():
            notes.write_text("O aluguel vence todo dia 5.\n", encoding="utf-8")
        fixed = client.result("bot.open", {"name": "finance"})["conversation_id"]
        mine = client.result("conversation.create")["conversation_id"]
        client.result(
            "conversation.send",
            {
                "conversation_id": mine,
                "text": "Pergunte ao bot de finanças em que dia vence o aluguel e me diga o que "
                "ele respondeu.",
            },
        )
        first = _events_of(client, mine)
        deadline = time.monotonic() + WAIT
        while True:
            started = client.event("message.start", timeout=deadline - time.monotonic())
            if started["conversation_id"] == mine and started["payload"]["kind"] == "notice":
                break
        told = _complete(client, mine, started["seq"])
        time.sleep(10)  # a second answer would arrive now
        bot_side = client.result("conversation.open", {"conversation_id": fixed})["messages"]
        asked = client.result("conversation.open", {"conversation_id": mine})["messages"]
    # The answer reaches the conversation that asked once: by message_agent or as the turn's.
    answers = [
        m["text"]
        for m in asked
        if m["text"].startswith(("[IMPORTANT: @finance", "Message from \U0001f916 Finanças"))
    ]
    once = len(answers) == 1
    sent = _results(first, "message_agent")
    back = started["payload"]["text"]
    received = [m["text"] for m in bot_side if m["text"].startswith("Message from ")]
    owners = ConversationStore(Database(home.database_file)).bots()
    calls = ledger.recent(LEDGER_ROWS)
    spent = calls[: len(calls) - before]
    by_bot: dict[str, int] = {}
    for call in spent:
        owner = owners.get(call.scope or "")
        if owner is not None:
            by_bot[owner] = by_bot.get(owner, 0) + 1
    _say(f"  message_agent: {[r[:90] for r in sent]}")
    _say(f"  the bot read: {[r[:110] for r in received[-1:]]}; back: {back[:110]!r}")
    _say(f"  answers that came back: {len(answers)}")
    _say(f"  {told['status']}: {told['text'][:160]!r}; calls by bot: {by_bot}")
    return (
        any('"status": "queued"' in result for result in sent)
        and bool(received)
        and "(@atlas)" in received[-1]
        and (
            back.startswith("[IMPORTANT: @finance answered")
            or back.startswith("Message from \U0001f916 Finanças (@finance)")
        )
        and once
        and "5" in told["text"]
        and by_bot.get("finance", 0) > 0
    )


# -- what the steps share -------------------------------------------------------------------------


def _bot(client: Client, name: str) -> None:
    """The bot, made as the dialog makes it, unless a step before made it."""
    if any(bot["name"] == name for bot in client.result("bot.list")["bots"]):
        return
    definitions: dict[str, dict[str, Any]] = {
        "finance": {
            "name": "finance",
            "title": "Finanças",
            "description": "Contas, vencimentos e impostos.",
            "mission": FINANCE_MISSION,
        },
        "produto": {
            "name": "produto",
            "title": "Produto",
            "description": "Uso, erros e métricas do produto.",
        },
    }
    client.result("bot.create", definitions[name])


def _turn(client: Client, conversation: str, text: str) -> dict[str, Any]:
    return _turn_events(client, conversation, text)[0]


def _turn_events(
    client: Client, conversation: str, text: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Say `text` in a conversation; how its turn ended, and the turn's events."""
    client.result("conversation.send", {"conversation_id": conversation, "text": text})
    events = _events_of(client, conversation)
    return events[-1]["payload"], events


def _events_of(client: Client, conversation: str) -> list[dict[str, Any]]:
    """A conversation's events up to the end of its turn; other conversations' are let by."""
    seen: list[dict[str, Any]] = []
    deadline = time.monotonic() + WAIT
    while True:
        for event in client.events_until("message.complete", timeout=deadline - time.monotonic()):
            if event.get("conversation_id") == conversation:
                seen.append(event)
        if seen and seen[-1]["type"] == "message.complete":
            return seen


def _complete(client: Client, conversation: str, after: int) -> dict[str, Any]:
    """How the turn of a conversation that started after event `after` ended."""
    deadline = time.monotonic() + WAIT
    while True:
        complete = client.event("message.complete", timeout=deadline - time.monotonic())
        if complete["conversation_id"] == conversation and complete["seq"] > after:
            return complete["payload"]


def _results(events: list[dict[str, Any]], tool: str) -> list[str]:
    return [
        e["payload"]["result_text"]
        for e in events
        if e["type"] == "tool.complete" and e["payload"]["name"] == tool
    ]


def _step(results: dict[str, bool], name: str, run: Callable[[], bool]) -> None:
    """Run a step; one that waits past its time fails, and the proof goes on to the next."""
    try:
        results[name] = run()
    except TimeoutError as error:
        _say(f"  timed out: {error}")
        results[name] = False


__all__ = ["prove"]


if __name__ == "__main__":
    sys.exit(main())
