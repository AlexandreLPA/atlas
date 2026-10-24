"""Prove the secrets tool against a real model (step 16d). Spends a little credit: about 4 calls on
gpt-6-luna, a fraction of a cent (at worst the loop guards stop a model that loops, some 30 calls).

    uv run python scripts/prove_secrets_tool.py openai gpt-6-luna

A temporary home holds an `mcp.json` with one server, `notes`, that needs `${NOTES_TOKEN}` and has
none in the vault, so it is left out. The user asks, in Portuguese and without naming any tool,
for the server to come up. The model must find the way to ask for the key by name, with a reason,
and the proof plays the app: it answers the card with a fake token.

1. The model asks for the key with the secrets tool, under the name the server needs and with a
   reason, and it does not ask for it in the chat.
2. The app's answer lands in the vault exactly, the model hears only `stored`, and the value is in
   neither the reply nor the tool's result.
3. The server that was left out comes up with the key, without a restart.

Exit code 0 only when every step did what it should.
"""

import argparse
import json
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

from proof_ledger import ensure_ledger  # noqa: E402 - the scripts' folder, on the path when run
from prove_server import _Paced, _say  # noqa: E402 - the other proofs' helpers, by path

from server_support import AtlasServer, Client  # noqa: E402 - the tests' server and client

WAIT = 300
KEY = "NOTES_TOKEN"
#: A fake token: the notes server does not check it, and nothing real is pasted into a proof.
FAKE_TOKEN = "notes-token-fake-" + "x" * 24
ASK = (
    "O servidor de notas (notes) do meu mcp.json não está subindo, porque falta a chave "
    "NOTES_TOKEN. Resolva isso para eu usar as minhas notas."
)
NOTES_SERVER = TESTS / "mcp_server.py"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the secrets tool end to end.")
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
    """Every step over `root`, on the models given (a fake one, to rehearse)."""
    home = open_home(root / "home")
    work = root / "work"
    work.mkdir()
    migrate(Database(home.database_file), SCHEMAS)
    _write_mcp(home)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    served = cast("Models", _Paced(models, pace)) if pace else models
    results: dict[str, bool] = {}
    with AtlasServer(home, settings, served, vault) as server, server.client() as client:
        _step(
            results,
            "1 the model asks for the key by name, with a reason",
            lambda: _asks(client, vault),
        )
        _step(
            results,
            "2 the key goes to the vault; the model hears only stored",
            lambda: _stored(client, vault),
        )
        _step(results, "3 the server left out comes up with the key", lambda: _comes_up(server))
    for name, ok in results.items():
        _say(f"{'ok  ' if ok else 'FAIL'} {name}")
    return bool(results) and all(results.values())


# -- the steps -----------------------------------------------------------------------------------

#: What the steps share: the card the model asked for, and how its turn ended.
_seen: dict[str, Any] = {}


def _asks(client: Client, vault: Vault) -> bool:
    _say("step 1: the model asks for the key")
    conversation = client.result("conversation.create")["conversation_id"]
    client.result("conversation.send", {"conversation_id": conversation, "text": ASK})
    try:
        card = client.request("secret", timeout=WAIT)
    except TimeoutError:
        # No card came: the turn may have ended without the tool. Say how it ended.
        _say("  no card in the time: the model did not ask for the key with the tool")
        return False
    _seen["card"] = card
    _seen["conversation"] = conversation
    params = card["params"]
    _say(f"  card: name={params['name']!r} reason={params['reason']!r} exists={params['exists']}")
    # The card is answered in step 2; the chat's text is read there.
    return params["name"] == KEY and bool(params["reason"].strip()) and vault.get(KEY) is None


def _stored(client: Client, vault: Vault) -> bool:
    _say("step 2: the app answers the card with a fake token")
    if "card" not in _seen:
        _say("  nothing to answer: step 1 found no card")
        return False
    client.answer(_seen["card"]["id"], {"value": FAKE_TOKEN})
    done, events = _until_done(client, _seen["conversation"])
    text = str(done.get("text", ""))
    results = [e["payload"].get("result_text", "") for e in events if e["type"] == "tool.complete"]
    secrets_results = [r for r in results if '"status"' in r]
    _say(f"  reply: {text[-200:]!r}")
    _say(f"  tool results: {secrets_results}")
    stored_ok = vault.get(KEY) == FAKE_TOKEN
    told_stored = any(
        '"status": "stored"' in r or '"status":"stored"' in r for r in secrets_results
    )
    leaked = any(FAKE_TOKEN in str(e) for e in events) or FAKE_TOKEN in text
    _seen["reply"] = text
    return done.get("status") == "answered" and stored_ok and told_stored and not leaked


def _comes_up(server: AtlasServer) -> bool:
    _say("step 3: the notes server comes up with the key")
    shared = server.gateway.assistant._shared  # pyright: ignore[reportPrivateUsage]
    deadline = time.monotonic() + 30
    names: list[str] = []
    while time.monotonic() < deadline:
        names = [tool.name for tool in (*shared.mcp_tools.always, *shared.mcp_tools.deferred)]
        if "mcp__notes__list_notes" in names:
            break
        time.sleep(0.2)
    _say(f"  notes tools: {[n for n in names if n.startswith('mcp__notes__')]}")
    return "mcp__notes__list_notes" in names


# -- helpers -------------------------------------------------------------------------------------


def _write_mcp(home: Home) -> None:
    """The home's mcp.json: one stdio server that needs the key, which the vault lacks."""
    servers = {
        "notes": {
            "command": sys.executable,
            "args": [str(NOTES_SERVER)],
            "env": {KEY: f"${{{KEY}}}"},
        }
    }
    home.mcp_file.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def _until_done(client: Client, conversation: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The turn's events until its message.complete, and that event's payload."""
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
