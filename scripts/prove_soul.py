"""Prove the soul against a real model. Spends a little credit: 11 calls on gpt-6-luna, 3 of them
the conversations' titles, US$ 0.0013 (15/10/2026); at worst, a model that loops on its tools
until the loop guards stop it, some 30 calls, about US$ 0.01.

    uv run python scripts/prove_soul.py openai gpt-6-luna
    uv run python scripts/prove_soul.py gemini gemini-3.8-flash --pace 15

Every step runs on the real server (the gateway and the assistant on uvicorn, on a real port) over
a temporary home, the client talking over a real WebSocket as the desktop does. Keys come from the
user's real home (`ATLAS_HOME`), and every model call lands in its call ledger. The screen itself
is proved by the app's e2e scenario, `apps/desktop/e2e/soul.spec.ts`.

1. The soul is who Atlas is: the server seeded it with Atlas's default; the screen's method
   (`soul.write`) writes a rule into it (end every reply with a word), and a new conversation's
   reply keeps the rule.
2. The agent writes its own soul when asked: told to change the word and to save that in its
   soul, it edits `SOUL.md` with the file tools and the file holds the new word. Nobody is asked:
   this client answers no approval, so one would hold the turn until the step timed out. On
   15/10/2026, before the leave of `atlas.assistant.SOUL_NOTE`, gpt-6-luna refused here three
   times of three; its tool results and reasoning are printed for that.
3. The next conversation follows the new text, and nobody asked, nobody changes the soul.

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

SCRIPTS = Path(__file__).resolve().parent
TESTS = SCRIPTS.parent / "tests"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(SCRIPTS))

from proof_ledger import ensure_ledger  # noqa: E402 - the scripts' folder
from prove_server import _Paced, _say  # noqa: E402 - the other proofs' helpers, by path

from server_support import AtlasServer, Client  # noqa: E402 - the tests' server and client

WAIT = 300
SOUL = (
    "Você é o Atlas, o assistente pessoal do usuário. Fale português, de forma breve e direta.\n\n"
    "Termine toda resposta com a palavra FAROL, em maiúsculas."
)
FILE_TOOLS = {"edit_file", "write_file"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the soul end to end.")
    parser.add_argument("provider")
    parser.add_argument("model")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds before each model call")
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
            _say(f"calls in this process: {len(spent)}, at list price: US$ {cost:.4f}")
    _say("PROVED: every step did what it should." if proved else "FAILED.")
    return 0 if proved else 1


def prove(root: Path, models: Models, vault: Vault, chosen: ModelSettings, pace: float) -> bool:
    """Every step over `root`, on the models given (a fake one, to rehearse). Each step builds on
    the one before."""
    home = open_home(root / "home")
    work = root / "work"
    work.mkdir()
    migrate(Database(home.database_file), SCHEMAS)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    served = cast("Models", _Paced(models, pace)) if pace else models
    results: dict[str, bool] = {}
    with AtlasServer(home, settings, served, vault) as server:
        _step(results, "1 the soul is who Atlas is", lambda: _is_who_it_is(server))
        _step(results, "2 the agent writes its soul when asked", lambda: _writes_it(server, home))
        _step(results, "3 the next conversation follows it", lambda: _follows_it(server, home))
    for name, ok in results.items():
        _say(f"{'ok  ' if ok else 'FAIL'} {name}")
    return bool(results) and all(results.values())


# -- the steps -----------------------------------------------------------------------------------


def _is_who_it_is(server: AtlasServer) -> bool:
    _say("step 1: the soul is who Atlas is")
    with server.client() as client:
        seeded = client.result("soul.read")
        client.result("soul.write", {"text": SOUL})
        conversation = client.result("conversation.create")["conversation_id"]
        done = _turn(client, conversation, "Em uma frase: com o que você pode me ajudar?")
    text = done["text"].strip()
    _say(f"  seeded: {seeded['text'][:60]!r}; {done['status']}: {text[-160:]!r}")
    return (
        seeded["text"].startswith("You are Atlas, a personal agent.")
        and done["status"] == "answered"
        and _ends_with(text, "FAROL")
    )


def _writes_it(server: AtlasServer, home: Home) -> bool:
    _say("step 2: the agent writes its soul when asked")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        done, events = _turn_events(
            client,
            conversation,
            "Daqui para a frente, quero que você termine toda resposta com a palavra ÂNCORA, no "
            "lugar de FAROL. Grave essa mudança no seu SOUL.md, para valer nas próximas conversas.",
        )
    used = [e["payload"]["name"] for e in events if e["type"] == "tool.complete"]
    soul = home.soul_file.read_text(encoding="utf-8")
    for event in events:
        if event["type"] == "tool.complete":
            result = event["payload"]["result_text"]
            _say(f"  {event['payload']['name']} returned: {result[:300]!r}")
    reasoning = "".join(
        str(e["payload"].get("text", "")) for e in events if e["type"] == "reasoning.delta"
    )
    if reasoning:
        _say(f"  reasoning: {reasoning[:600]!r}")
    _say(f"  tools: {used}; {done['status']}: {done['text'][-160:]!r}")
    _say(f"  SOUL.md now: {soul!r}")
    # Answered means nobody was asked: this client answers no approval, which would hold the turn.
    return (
        done["status"] == "answered"
        and bool(FILE_TOOLS & set(used))
        and "ÂNCORA" in soul
        and "Fale português" in soul  # an edit, not a soul written from nothing
    )


def _follows_it(server: AtlasServer, home: Home) -> bool:
    _say("step 3: the next conversation follows it, and nobody changes it unasked")
    before = home.soul_file.read_text(encoding="utf-8")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        done = _turn(client, conversation, "Em uma frase: qual é a capital de Portugal?")
    text = done["text"].strip()
    after = home.soul_file.read_text(encoding="utf-8")
    _say(f"  {done['status']}: {text[-160:]!r}; soul unchanged: {after == before}")
    return done["status"] == "answered" and _ends_with(text, "ÂNCORA") and after == before


# -- helpers -------------------------------------------------------------------------------------


def _ends_with(text: str, word: str) -> bool:
    return text.rstrip(" .!*_`\"'").upper().endswith(word)


def _turn(client: Client, conversation: str, text: str) -> dict[str, Any]:
    return _turn_events(client, conversation, text)[0]


def _turn_events(
    client: Client, conversation: str, text: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Say `text` in a conversation; how its turn ended, and the turn's events."""
    client.result("conversation.send", {"conversation_id": conversation, "text": text})
    seen: list[dict[str, Any]] = []
    deadline = time.monotonic() + WAIT
    while True:
        for event in client.events_until("message.complete", timeout=deadline - time.monotonic()):
            if event.get("conversation_id") == conversation:
                seen.append(event)
        if seen and seen[-1]["type"] == "message.complete":
            return seen[-1]["payload"], seen


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
