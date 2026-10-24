"""Prove the desktop app end to end against a real model. Spends a little credit.

    uv run python scripts/prove_desktop.py openai gpt-6-luna
    uv run python scripts/prove_desktop.py gemini gemini-3.7-flash --pace 15
    uv run python scripts/prove_desktop.py --rehearse

The app is the real one (Electron, built), and it starts the real `atlas serve` by itself, on a
temporary home, as it does on the user's machine; Playwright clicks and types in its window. The
steps are in `apps/desktop/e2e/prove/desktop.prove.ts`, and mirror step 8's proof through the
screen: a turn in pieces, ask_user's card, an approval card for an untrusted MCP tool, the ⚡,
stopping with the unsent message back in the box, a message sent mid-turn, a dropped connection,
the review's summary, the system notification, and the server leaving with the app.

The provider's key is copied from the user's real home (`ATLAS_HOME`) into the temporary one, and
after the run every model call the temporary home recorded is copied into the real call ledger, so
the day's count per model (Gemini's free tier) stays true. Conversations, memory and skills stay in
the temporary home.

`--rehearse` runs the same steps against the fake model server, scripted as the real model is
expected to answer: no key, no credit.

Exit code 0 only when every step did what it should.
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path

from proof_ledger import ensure_ledger  # the scripts' folder, on the path when run

from atlas.__main__ import SCHEMAS
from atlas.conversations import ConversationStore
from atlas.home import Database, Redactor, configure_logging, load_settings, migrate, open_home
from atlas.memory import REVIEW_PURPOSE
from atlas.models import AssistantMessage, UserMessage, load_catalog
from atlas.secrets import Vault

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "apps" / "desktop"
MCP_SERVER = ROOT / "tests" / "mcp_server.py"

#: The review's conversation: nine earlier messages, kept in the store as step 8's proof keeps them,
#: so the tenth, sent from the screen, makes the review due without nine more model calls.
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
REVIEW_TITLE = "Portfólio da Marina"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the desktop app end to end.")
    parser.add_argument("provider", nargs="?")
    parser.add_argument("model", nargs="?")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds to wait between steps")
    parser.add_argument("--rehearse", action="store_true", help="the fake model, no credit")
    args = parser.parse_args()
    if not args.rehearse and not (args.provider and args.model):
        parser.error("name a provider and a model, or --rehearse")
    settings = load_settings(os.environ).settings
    real = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)

    root = Path(tempfile.mkdtemp(prefix="atlas-prove-desktop-"))
    try:
        home = root / "home"
        work = root / "work"
        work.mkdir()
        prepared = open_home(home)
        migrate(Database(prepared.database_file), SCHEMAS)
        servers = {
            "notes": {"command": sys.executable, "args": [str(MCP_SERVER)], "trust": "untrusted"}
        }
        prepared.mcp_file.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
        seed_conversation(prepared.database_file, EARLIER, REVIEW_TITLE)
        environ = {
            **os.environ,
            "ATLAS_PROVE_HOME": str(home),
            "ATLAS_PROVE_WORK": str(work),
            "ATLAS_PROVE_PACE": str(args.pace),
            "ATLAS_PROVE_REVIEW_TITLE": REVIEW_TITLE,
        }
        if args.rehearse:
            environ["ATLAS_PROVE_REHEARSE"] = "1"
        else:
            vault = Vault(real.secrets_folder, settings.vault, redactor)
            key = load_catalog(real.models_file).provider(args.provider).key
            if key is not None:
                value = vault.get(key)
                if value is None:
                    sys.stderr.write(f"There is no {key} in the vault of {real.root}.\n")
                    return 1
                Vault(prepared.secrets_folder, settings.vault, redactor).set(key, value)
            environ["ATLAS_PROVE_PROVIDER"] = args.provider
            environ["ATLAS_PROVE_MODEL"] = args.model
        proof = run_proof("desktop.prove.ts", environ)
        if not args.rehearse:
            carry_ledger(prepared.database_file, real.database_file)
    finally:
        remove_home(root)
    say("PROVED: every step did what it should." if proof == 0 else "FAILED.")
    return proof


def run_proof(spec: str, environ: dict[str, str]) -> int:
    """Run one proof's steps (`apps/desktop/e2e/prove/<spec>`) through the app's window."""
    npx = shutil.which("npx") or "npx"
    proof = subprocess.run(  # noqa: S603 - fixed tools, no user text
        [npx, "playwright", "test", "-c", "e2e/prove/playwright.config.ts", spec],
        cwd=DESKTOP,
        env=environ,
        check=False,
    )
    return proof.returncode


def remove_home(root: Path) -> None:
    """The temporary home, once the server under the app let go of its files: on Windows a killed
    process releases them a moment after it is gone."""
    for _ in range(20):
        try:
            shutil.rmtree(root)
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.5)
        else:
            return
    say(f"left behind: {root}")


def seed_conversation(database_file: Path, earlier: list[str], title: str) -> None:
    """A stored conversation of earlier user messages, each answered "Anotado.", under a title."""
    store = ConversationStore(Database(database_file))
    stored = store.create()
    for said in earlier:
        stored.history += [UserMessage(said), AssistantMessage("Anotado.")]
    stored.save()
    with closing(sqlite3.connect(database_file)) as connection, connection:
        connection.execute(
            "UPDATE conversations_threads SET title = ? WHERE id = ?", (title, stored.id)
        )


def carry_ledger(source: Path, target: Path) -> None:
    """The temporary home's model calls, into the real call ledger."""
    with closing(sqlite3.connect(source)) as connection:
        cursor = connection.execute("SELECT * FROM models_calls")
        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
    record_calls(columns, rows, target)


def record_calls(columns: Sequence[str], rows: Sequence[Sequence[object]], target: Path) -> None:
    """Model calls made elsewhere (a temporary home, a container), into the real call ledger."""
    ensure_ledger(Database(target))
    kept = [column for column in columns if column != "id"]
    places = [columns.index(column) for column in kept]
    carried = [tuple(row[place] for place in places) for row in rows]
    with closing(sqlite3.connect(target)) as connection, connection:
        marks = ", ".join("?" for _ in kept)
        connection.executemany(
            f"INSERT INTO models_calls ({', '.join(kept)}) VALUES ({marks})",  # noqa: S608
            carried,
        )
    purposes = [row[kept.index("purpose")] for row in carried]
    costs = [row[kept.index("cost_usd")] for row in carried]
    cost = sum(float(str(value)) for value in costs if value is not None)
    reviews = purposes.count(REVIEW_PURPOSE)
    say(f"calls: {len(carried)} ({reviews} {REVIEW_PURPOSE}), at list price: US$ {cost:.4f}")


def say(text: str) -> None:
    sys.stdout.write(f"{text}\n")
    sys.stdout.flush()


if __name__ == "__main__":
    raise SystemExit(main())
