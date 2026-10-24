"""Prove step 16g's attachments against real models. Spends a little credit: a turn on a model that
sees images and one on a model that does not, a description of the image and the titles, some
US$ 0.01; at worst, models that loop on their tools until the loop guards stop them, about US$ 0.10.

    uv run python scripts/prove_attachments.py openai gpt-6-luna openai o3-mini

Every step runs on the real server over a temporary home, the client sending its files over a real
WebSocket as the desktop does (`attachment.add`, then `conversation.send` with their ids). Keys
come from the user's real home, and every model call lands in its call ledger.

1. **A PDF and an image on a model that sees images.** A PDF holds a code word, a PNG is one color:
   the model says both, the image read as itself and the PDF as Atlas gives it.
2. **The same image on a model that does not see images.** Its conversation runs on the second
   model; before the turn, a model that sees images describes the image, and the conversation's
   model says the color from the description alone.

Exit code 0 only when every step did what it should.
"""

import argparse
import base64
import os
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from atlas.__main__ import SCHEMAS
from atlas.home import (
    Database,
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

from proof_ledger import LEDGER_ROWS, ensure_ledger  # noqa: E402 - the scripts' folder

from documents_support import write_pdf, write_png  # noqa: E402 - the tests' file makers
from server_support import AtlasServer, Client  # noqa: E402 - the tests' server and client

WAIT = 300
#: The code word the PDF holds, and the color of the image.
CODE = "ARARA-77"
RED = (220, 30, 30)
BOTH = (
    "Qual é o código secreto escrito no PDF anexo, e qual é a cor do quadrado da imagem anexa? "
    "Responda em uma linha, sem usar ferramentas."
)
COLOR = "Qual é a cor do quadrado da imagem anexa? Responda com uma palavra."


def say(text: str) -> None:
    sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove step 16g's attachments end to end.")
    parser.add_argument("provider", help="a provider whose model sees images")
    parser.add_argument("model")
    parser.add_argument("blind_provider", help="a provider whose model does not see images")
    parser.add_argument("blind_model")
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
            proved = prove(
                Path(folder),
                models,
                vault,
                chosen,
                (args.blind_provider, args.blind_model),
                ledger,
            )
        finally:
            calls = ledger.recent(LEDGER_ROWS)
            spent = calls[: len(calls) - before]
            cost = sum((call.cost.usd or 0) for call in spent)
            purposes = sorted({call.purpose for call in spent})
            say(f"calls: {len(spent)} ({', '.join(purposes)}), at list price: US$ {cost:.4f}")
    say("PROVED: every step did what it should." if proved else "FAILED.")
    return 0 if proved else 1


def prove(
    root: Path,
    models: Models,
    vault: Vault,
    chosen: ModelSettings,
    blind: tuple[str, str],
    ledger: CallLedger,
) -> bool:
    """Every step over `root`, on the models given (a fake one, to rehearse)."""
    home = open_home(root / "home")
    work = root / "work"
    work.mkdir()
    migrate(Database(home.database_file), SCHEMAS)
    pdf, png = work / "relatorio.pdf", work / "quadrado.png"
    write_pdf(pdf, ["Relatorio trimestral", f"O codigo secreto e {CODE}."])
    write_png(png, RED, size=64)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    results: dict[str, bool] = {}
    with AtlasServer(home, settings, models, vault) as server:
        _step(results, "1 a PDF and an image, seen", lambda: _seen(server, pdf, png))
        _step(results, "2 the image, described", lambda: _described(server, png, blind, ledger))
    for name, ok in results.items():
        say(f"{'ok  ' if ok else 'FAIL'} {name}")
    return bool(results) and all(results.values())


def _seen(server: AtlasServer, pdf: Path, png: Path) -> bool:
    say("step 1: a PDF and an image on a model that sees images")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        ids = [_attach(client, pdf), _attach(client, png)]
        done = _turn(client, conversation, BOTH, ids)
        kept = client.result("conversation.open", {"conversation_id": conversation})
    text = done["text"]
    attached = _attached(kept)
    say(f"  {done['status']}: {text[-200:]!r}; kept with the message: {attached}")
    return (
        done["status"] == "answered"
        and CODE in text.upper()
        and "VERMELH" in text.upper()
        and attached == ["relatorio.pdf", "quadrado.png"]
    )


def _described(server: AtlasServer, png: Path, blind: tuple[str, str], ledger: CallLedger) -> bool:
    provider, model = blind
    say(f"step 2: the image on {provider} {model}, which does not see images")
    before = len(ledger.recent(LEDGER_ROWS))
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result(
            "conversation.model",
            {"conversation_id": conversation, "provider": provider, "model": model},
        )
        done = _turn(client, conversation, COLOR, [_attach(client, png)])
    calls = ledger.recent(LEDGER_ROWS)
    spent = calls[: len(calls) - before]
    described = [call for call in spent if call.purpose == "image_description"]
    on_blind = [call for call in spent if call.model == model]
    say(
        f"  {done['status']}: {done['text'][-120:]!r}; descriptions: {len(described)} on "
        f"{sorted({c.model for c in described})}; calls on {model}: {len(on_blind)}"
    )
    return (
        done["status"] == "answered"
        and "VERMELH" in done["text"].upper()
        and bool(described)
        and bool(on_blind)
    )


# -- helpers -------------------------------------------------------------------------------------


def _step(results: dict[str, bool], name: str, run: Callable[[], bool]) -> None:
    try:
        results[name] = run()
    except TimeoutError as error:
        say(f"  timed out: {error}")
        results[name] = False


def _attach(client: Client, path: Path) -> str:
    """A file attached as the desktop sends it, in one part: its attachment's id."""
    data = path.read_bytes()
    added = client.result(
        "attachment.add",
        {"name": path.name, "size": len(data), "data": base64.b64encode(data).decode()},
    )
    attachment = added["attachment"]
    if attachment is None:
        raise TimeoutError(f"{path.name} was not taken whole: {added}")
    return str(attachment["id"])


def _attached(opened: dict[str, Any]) -> list[str]:
    """The names of the files the conversation's first message keeps."""
    for message in opened.get("messages", []):
        names = [str(a["name"]) for a in message.get("attachments", [])]
        if names:
            return names
    return []


def _turn(client: Client, conversation: str, text: str, attachments: list[str]) -> dict[str, Any]:
    """Say `text` with the files, and how its turn ended."""
    client.result(
        "conversation.send",
        {"conversation_id": conversation, "text": text, "attachments": attachments},
    )
    deadline = time.monotonic() + WAIT
    while time.monotonic() < deadline:
        events = client.events_until("message.complete", timeout=deadline - time.monotonic())
        done = [e for e in events if e.get("conversation_id") == conversation]
        if done and done[-1]["type"] == "message.complete":
            return done[-1]["payload"]
    raise TimeoutError("The turn took too long.")


if __name__ == "__main__":
    sys.exit(main())
