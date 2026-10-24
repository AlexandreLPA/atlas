"""Prove principle 4 against a real model: an empty container, an API key and a working
conversation, through the app. Spends a little credit.

    uv run python scripts/prove_docker.py openai
    uv run python scripts/prove_docker.py gemini gemini-3.7-flash --pace 15
    uv run python scripts/prove_docker.py --rehearse

The container is the real one, started from the repository's docker-compose.yml as a user starts it
(`docker compose up --build`), under a compose project of its own, so its volume starts empty and
its port is free; the app is the real one, reaching it with `ATLAS_DESKTOP_URL` and
`ATLAS_DESKTOP_TOKEN`, and Playwright types in its window. The steps are in
`apps/desktop/e2e/prove/docker.prove.ts`: the app reaches the empty container; a message with no
key says how to store one; the key enters with `docker compose exec atlas atlas secrets set`, the
server running; a tool runs inside the container; and a restart keeps the conversation and the file
the agent made.

Nothing chooses the model: Atlas takes the first provider with a key in its vault. The key is the
user's real one, read from their home (`ATLAS_HOME`) and given to `atlas secrets set` on standard
input; it never reaches the app or the command line. Naming a model writes it as that provider's
default in the volume's models.toml before the first start (Gemini's free tier counts per model).
After the run the container's model calls are copied into the real call ledger, and the project's
container and volume are removed.

`--rehearse` runs the same steps on the built image with the fake model in a container beside it
(tests/docker/fake-model.yml), scripted as the real model is expected to answer: no key, no credit.

Exit code 0 only when every step did what it should.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from atlas.home import Redactor, configure_logging, load_settings, open_home
from atlas.models import load_catalog
from atlas.secrets import Vault

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = ROOT / "apps" / "desktop"
sys.path.insert(0, str(ROOT / "tests"))

from prove_desktop import record_calls, say  # noqa: E402

from docker_support import Stack  # noqa: E402 - the image tests' compose project, by path

#: The rehearsal's provider: the one a fresh home takes first, pointed at the fake model.
REHEARSAL_PROVIDER = "openai"
REHEARSAL_KEY = "sk-rehearsal-0123456789"
DUMP_LEDGER = (
    "import json, sqlite3\n"
    "cursor = sqlite3.connect('/data/atlas.db').execute('SELECT * FROM models_calls')\n"
    "columns = [column[0] for column in cursor.description]\n"
    "print(json.dumps({'columns': columns, 'rows': cursor.fetchall()}))\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove an empty container, a key, a conversation.")
    parser.add_argument("provider", nargs="?", help="whose key goes in: openai, gemini...")
    parser.add_argument("model", nargs="?", help="that provider's default, written in models.toml")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds to wait between steps")
    parser.add_argument("--rehearse", action="store_true", help="the fake model, no credit")
    args = parser.parse_args()
    if not args.rehearse and not args.provider:
        parser.error("name a provider, or --rehearse")
    settings = load_settings(os.environ).settings
    real = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)

    if args.rehearse:
        provider, key_name, key = REHEARSAL_PROVIDER, "OPENAI_API_KEY", REHEARSAL_KEY
        say("building the image...")
        subprocess.run(["docker", "build", "-q", "-t", "atlas:local", "."], cwd=ROOT, check=True)  # noqa: S607
        stack = Stack("atlas:local", project="atlas-prove-rehearsal")
    else:
        provider = args.provider
        key_name = load_catalog(real.models_file).provider(provider).key
        if key_name is None:
            parser.error(f"{provider} takes no key; the proof is about a key")
        key = Vault(real.secrets_folder, settings.vault, redactor).get(key_name)
        if key is None:
            sys.stderr.write(f"There is no {key_name} in the vault of {real.root}.\n")
            return 1
        stack = Stack(project="atlas-prove")

    root = Path(tempfile.mkdtemp(prefix="atlas-prove-docker-"))
    signals = root / "signals"
    signals.mkdir()
    stack.down()  # what an interrupted run left
    try:
        if args.model:
            # Before the first start: the catalog is read when the server starts.
            stack.compose(
                "run", "--rm", "--no-deps", "-T", "atlas", "sh", "-c", "cat > /data/models.toml",
                stdin=f'[providers.{provider}]\ndefault_model = "{args.model}"\n',
            )  # fmt: skip
        say("starting the container from docker-compose.yml...")
        stack.up()
        environ = {
            **os.environ,
            "ATLAS_PROVE_URL": stack.url,
            "ATLAS_PROVE_TOKEN": stack.token(),
            "ATLAS_PROVE_SIGNALS": str(signals),
            "ATLAS_PROVE_PROFILE": str(root),
            "ATLAS_PROVE_PACE": str(args.pace),
            "ATLAS_PROVE_KEY_NAME": key_name,
        }
        if args.rehearse:
            environ["ATLAS_PROVE_REHEARSE"] = "1"
            environ["ATLAS_PROVE_FAKE_MODEL"] = stack.fake_model
        proof = run_with_signals(environ, signals, stack, key_name, key, rehearse=args.rehearse)
        kept = stack.exec("cat", "/data/home/notas.txt", check=False).strip()
        say(f"/data/home/notas.txt after the restart: {kept!r}")
        if not args.rehearse:
            carry_container_ledger(stack, real.database_file)
        if proof == 0 and "atlas" not in kept.lower():
            say("FAIL the file the agent made is not in the volume")
            proof = 1
    except BaseException:
        say(stack.logs()[-4000:])
        raise
    finally:
        stack.down()
        shutil.rmtree(root, ignore_errors=True)
    say("PROVED: every step did what it should." if proof == 0 else "FAILED.")
    return proof


def run_with_signals(
    environ: dict[str, str], signals: Path, stack: Stack, key_name: str, key: str, *, rehearse: bool
) -> int:
    """Run the window's steps, doing what they ask of the container when they ask: the key, given
    as a user gives it, and a restart. The app never holds the key."""
    npx = shutil.which("npx") or "npx"
    proof = subprocess.Popen(  # noqa: S603 - fixed tools, no user text
        [npx, "playwright", "test", "-c", "e2e/prove/playwright.config.ts", "docker.prove.ts"],
        cwd=DESKTOP,
        env=environ,
    )
    while proof.poll() is None:
        for asked in sorted(signals.glob("*.asked")):
            what = asked.stem
            if what == "key":
                stack.store_key(key_name, key)
                if rehearse:
                    # The stand-in's address is read at start: here alone the key costs a restart.
                    stack.point_at_fake_model(REHEARSAL_PROVIDER)
                    stack.restart()
                say(f"  [docker] {key_name} stored with `atlas secrets set`")
            elif what == "restart":
                stack.restart()
                say("  [docker] docker compose restart atlas")
            asked.rename(signals / f"{what}.done")
        time.sleep(0.5)
    return proof.returncode


def carry_container_ledger(stack: Stack, target: Path) -> None:
    """The container's model calls, read inside it, into the real call ledger."""
    dumped = json.loads(stack.exec("python", "-c", DUMP_LEDGER))
    record_calls(dumped["columns"], dumped["rows"], target)


if __name__ == "__main__":
    raise SystemExit(main())
