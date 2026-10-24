"""Prove the server end to end against a real model. Spends a little credit.

    uv run python scripts/prove_server.py openai gpt-6-luna
    uv run python scripts/prove_server.py gemini gemini-3.7-flash --pace 15

The server is the real one (the gateway and the assistant on uvicorn, on a real port), on a
temporary home; the client talks to it over a real WebSocket, as the desktop will. Keys come from
the user's real home (`ATLAS_HOME`), and every model call lands in its call ledger. Conversations,
memory and skills stay in the temporary home.

1. A turn streams: the reply arrives in pieces, ends answered, and is stored.
2. ask_user: the agent asks which color the user prefers; the question reaches the client as a
   server request, the client answers, and the agent's reply carries the answer.
3. An approval: an MCP server marked untrusted (the tests' notes server) has a tool that writes;
   the agent finds it through tool search and calls it, the call reaches the client as an
   approval, the client allows it once, and the note is written.
4. Stopping: a long answer is stopped after its first pieces; the turn ends interrupted, and the
   next turn answers from a whole history.
5. Steering: a message sent while the agent works enters the turn (or starts the next one, when
   the model answered first), and the reply follows it.
6. Coming back: the client leaves in the middle of a turn; another connection asks for the events
   after the last one seen and gets the rest, with the same epoch and nothing truncated.
7. The review: a conversation of 9 earlier messages, with the user's name and taste, gets a 10th;
   after the turn, the background review keeps something, and `review.summary` reaches the client.
8. The curator: with the clock moved 3 hours on, the server asks the curator by itself. No model.

Exit code 0 only when every step did what it should.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import time
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, cast

from proof_ledger import LEDGER_ROWS, ensure_ledger  # the scripts' folder, on the path when run

from atlas.__main__ import SCHEMAS
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
    utc_now,
)
from atlas.memory import REVIEW_PURPOSE
from atlas.models import AssistantMessage, CallLedger, Models, UserMessage, load_catalog
from atlas.secrets import Vault

TESTS = Path(__file__).resolve().parent.parent / "tests"
sys.path.insert(0, str(TESTS))

from server_support import AtlasServer  # noqa: E402 - the tests' server and client, by path

MCP_SERVER = TESTS / "mcp_server.py"

EARLIER = [
    "Oi! Meu nome é Marina e trabalho com design gráfico.",
    "Prefiro respostas curtas, em tópicos.",
    "Estou montando um portfólio novo.",
    "Uso Figma no dia a dia.",
    "Quero publicar o portfólio até o fim do mês.",
    "Gosto de paletas com verde e cinza.",
    "Meu cliente principal é uma editora.",
    "Trabalho melhor de manhã.",
    "Vou precisar de ajuda com textos curtos para cada projeto.",
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the server end to end.")
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
            proved = prove(Path(folder), models, vault, chosen, args.pace)
        finally:
            calls = ledger.recent(LEDGER_ROWS)
            spent = calls[: len(calls) - before]
            cost = sum((call.cost.usd or 0) for call in spent)
            reviews = sum(1 for call in spent if call.purpose == REVIEW_PURPOSE)
            _say(f"calls: {len(spent)} ({reviews} {REVIEW_PURPOSE}), at list price: US$ {cost:.4f}")
    _say("PROVED: every step did what it should." if proved else "FAILED.")
    return 0 if proved else 1


def prove(root: Path, models: Models, vault: Vault, chosen: ModelSettings, pace: float) -> bool:
    """Every step on a server over `root`, on the models given (a fake one, to rehearse)."""
    home = open_home(root / "home")
    work = root / "work"
    work.mkdir()
    migrate(Database(home.database_file), SCHEMAS)
    servers = {
        "notes": {"command": sys.executable, "args": [str(MCP_SERVER)], "trust": "untrusted"}
    }
    home.mcp_file.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    clock = _Clock()
    served = cast("Models", _Paced(models, pace)) if pace else models
    with AtlasServer(home, settings, served, vault, clock=clock, housekeeping_seconds=1) as server:
        _wait_for_mcp(server)
        results = {
            "1 a turn streams": _turn_streams(server),
            "2 ask_user": _ask_user(server),
            "3 an approval": _approval(server),
            "4 stopping": _stopping(server),
            "5 steering": _steering(server, work),
            "6 coming back": _coming_back(server),
            "7 the review": _review(server, home),
            "8 the curator": _curator(clock, home),
        }
    for step, ok in results.items():
        _say(f"{'ok  ' if ok else 'FAIL'} {step}")
    return all(results.values())


# -- the steps -------------------------------------------------------------------------------------


def _turn_streams(server: AtlasServer) -> bool:
    _say("step 1: a turn streams")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result(
            "conversation.send",
            {"conversation_id": conversation, "text": "Em uma frase: qual a capital de Portugal?"},
        )
        events = client.events_until("message.complete", timeout=180)
        pieces = [e for e in events if e["type"] == "message.delta"]
        done = events[-1]["payload"]
        stored = client.result("conversation.open", {"conversation_id": conversation})["messages"]
    _say(f"  {len(pieces)} pieces; {done['status']}: {done['text'][:200]!r}")
    _say(f"  stored: {[(m['role'], m['text'][:40]) for m in stored]}")
    return (
        len(pieces) >= 1
        and done["status"] == "answered"
        and "lisboa" in done["text"].lower()
        and [m["role"] for m in stored] == ["user", "assistant"]
    )


def _ask_user(server: AtlasServer) -> bool:
    _say("step 2: ask_user")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result(
            "conversation.send",
            {
                "conversation_id": conversation,
                "text": "Vou pintar a parede. Antes de escolher, me pergunte com a ask_user qual "
                "cor eu prefiro, azul ou verde, e depois me diga em uma frase qual será a cor.",
            },
        )
        asked = client.request("ask_user", timeout=180)
        question = asked["params"]["questions"][0]
        _say(f"  asked: {question['text']!r}, choices {question['choices']}")
        verde = next((c for c in question["choices"] if "verde" in c.lower()), "Verde")
        client.answer(asked["id"], {"answers": {question["id"]: verde}})
        done = client.event("message.complete", timeout=180)["payload"]
    _say(f"  {done['status']}: {done['text'][:200]!r}")
    return done["status"] == "answered" and "verde" in done["text"].lower()


def _approval(server: AtlasServer) -> bool:
    _say("step 3: an approval")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        # A new conversation starts in "Auto", which would not ask.
        client.result("conversation.auto_mode", {"conversation_id": conversation, "enabled": False})
        client.result(
            "conversation.send",
            {
                "conversation_id": conversation,
                "text": "Use a ferramenta de notas (servidor MCP notes) para anotar exatamente: "
                "comprar tinta verde. Depois confirme em uma frase.",
            },
        )
        asked = client.request("approval", timeout=180)
        params = asked["params"]
        _say(f"  approval: {params['tool']} {params['target'][:80]!r} ({params['reason']})")
        client.answer(asked["id"], {"choice": "once"})
        events = client.events_until("message.complete", timeout=180)
    added = [
        e["payload"]
        for e in events
        if e["type"] == "tool.complete" and e["payload"]["name"] == "mcp__notes__add_note"
    ]
    done = events[-1]["payload"]
    _say(f"  add_note: {[a['result_text'][:80] for a in added]}; {done['text'][:160]!r}")
    return (
        params["tool"] == "mcp__notes__add_note"
        and bool(added)
        and not added[-1]["failed"]
        and "added note" in added[-1]["result_text"]
    )


def _stopping(server: AtlasServer) -> bool:
    _say("step 4: stopping")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result(
            "conversation.send",
            {
                "conversation_id": conversation,
                "text": "Escreva um texto de 400 palavras sobre a história do azulejo português.",
            },
        )
        client.event("message.delta", timeout=180)
        stopped = client.result("conversation.interrupt", {"conversation_id": conversation})
        first = client.event("message.complete", timeout=60)["payload"]
        client.result(
            "conversation.send",
            {"conversation_id": conversation, "text": "Esquece. Responda só: OK"},
        )
        second = client.event("message.complete", timeout=180)["payload"]
    _say(f"  stopped: {stopped}; first ended {first['status']}")
    _say(f"  next turn {second['status']}: {second['text'][:120]!r}")
    return (
        stopped["interrupted"]
        and first["status"] == "interrupted"
        and second["status"] == "answered"
        and "ok" in second["text"].lower()
    )


def _steering(server: AtlasServer, work: Path) -> bool:
    _say("step 5: steering")
    (work / "lista.txt").write_text("maçã\nbanana\nuva\n", encoding="utf-8")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result(
            "conversation.send",
            {
                "conversation_id": conversation,
                "text": "Leia o arquivo lista.txt com read_file e me diga as frutas.",
            },
        )
        client.event("message.start", timeout=60)
        steered = client.result(
            "conversation.send",
            {"conversation_id": conversation, "text": "Responda em inglês, por favor."},
        )
        events = client.events_until("message.complete", timeout=180)
        if not any(e["type"] == "message.steered" for e in events):
            events += client.events_until("message.complete", timeout=180)  # the next turn
    entered = [e["payload"]["text"] for e in events if e["type"] == "message.steered"]
    done = events[-1]["payload"]
    _say(f"  sent mid-turn: {steered['status']}; entered the turn: {bool(entered)}")
    _say(f"  {done['status']}: {done['text'][:160]!r}")
    english = any(word in done["text"].lower() for word in ("apple", "grape"))
    return steered["status"] == "steered" and done["status"] == "answered" and english


def _coming_back(server: AtlasServer) -> bool:
    _say("step 6: coming back")
    with server.client() as client:
        epoch = client.ready["params"]["payload"]["epoch"]
        conversation = client.result("conversation.create")["conversation_id"]
        client.result(
            "conversation.send",
            {"conversation_id": conversation, "text": "Liste cinco cidades portuguesas."},
        )
        seen = client.event("message.start", timeout=60)["seq"]
    deadline = time.monotonic() + 180
    while server.gateway.assistant.running(conversation) and time.monotonic() < deadline:
        time.sleep(0.2)
    with server.client() as back:
        replay = back.result(
            "conversation.events", {"conversation_id": conversation, "after": seen}
        )
    kinds = [e["type"] for e in replay["events"]]
    _say(f"  missed after #{seen}: {len(kinds)} events, ending {kinds[-1:]}")
    return (
        replay["epoch"] == epoch
        and not replay["truncated"]
        and kinds[-1:] == ["message.complete"]
        and replay["events"][0]["seq"] == seen + 1
    )


def _review(server: AtlasServer, home: Home) -> bool:
    _say("step 7: the review")
    store = server.gateway.assistant.conversations
    stored = store.create()
    for said in EARLIER:
        stored.history += [UserMessage(said), AssistantMessage("Anotado.")]
    stored.save()
    with server.client() as client:
        client.result("conversation.open", {"conversation_id": stored.id})
        client.result(
            "conversation.send",
            {"conversation_id": stored.id, "text": "Pode me sugerir um título para o portfólio?"},
        )
        client.event("message.complete", timeout=180)
        summary = client.event("review.summary", timeout=300)["payload"]
    kept = [
        (home.memory_folder / name).read_text(encoding="utf-8")
        for name in ("USER.md", "MEMORY.md")
        if (home.memory_folder / name).exists()
    ]
    _say(f"  review: {summary['text']!r}")
    _say(f"  kept: {[text[:200] for text in kept]}")
    return bool(summary["actions"]) and any("Marina" in text for text in kept)


def _curator(clock: "_Clock", home: Home) -> bool:
    _say("step 8: the curator, 3 hours on")
    clock.later(timedelta(hours=3))
    deadline = time.monotonic() + 30
    database = sqlite3.connect(home.database_file)
    try:
        while time.monotonic() < deadline:
            runs = database.execute("SELECT count(*) FROM skills_curator_runs").fetchone()[0]
            if runs:
                _say(f"  the server asked the curator: {runs} run(s) recorded")
                return True
            time.sleep(0.5)
    finally:
        database.close()
    _say("  the curator was never asked")
    return False


def _wait_for_mcp(server: AtlasServer) -> None:
    shared = server.gateway.assistant._shared  # pyright: ignore[reportPrivateUsage]
    deadline = time.monotonic() + 60
    while not shared.mcp_tools.deferred and time.monotonic() < deadline:
        time.sleep(0.1)
    _say(f"MCP tools: {sorted(t.name for t in shared.mcp_tools.deferred)}")


# -- helpers ---------------------------------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = utc_now()

    def later(self, step: timedelta) -> None:
        self.now += step

    def __call__(self) -> datetime:
        return self.now


class _Paced:
    """The models, each client waiting before each call: Gemini's free tier cuts replies and
    answers 429 past a few calls a minute (seen on 09/10/2026)."""

    def __init__(self, models: Models, seconds: float) -> None:
        self._models = models
        self._seconds = seconds

    def __getattr__(self, name: str) -> Any:
        return getattr(self._models, name)

    def client(self, *args: Any, **kwargs: Any) -> Any:
        return _PacedClient(self._models.client(*args, **kwargs), self._seconds)


class _PacedClient:
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
