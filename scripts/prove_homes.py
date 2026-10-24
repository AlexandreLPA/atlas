"""Prove step 10b through the app against a real model: several homes, and a backup that moves one.
Spends a little credit.

    uv run python scripts/prove_homes.py openai gpt-6-luna
    uv run python scripts/prove_homes.py gemini gemini-3.7-flash --pace 15
    uv run python scripts/prove_homes.py --rehearse

The app is the real one, on a temporary default home, starting a server per home it opens; the
steps are in `apps/desktop/e2e/prove/homes.prove.ts`: the agent writes the user's profile; a home
cloned in the terminal (`atlas home create trabalho --clone`) shows in the app; the clone knows the
user and has the keys, but none of the conversations; a turn there goes on while the default home
is on screen, and notifies when it ends; a backup of the default home restored into a new one
(`atlas backup`, `atlas home create copia`, `atlas --home copia restore`) brings its conversations
and memory, and no key, so a message there says how to store one; and with the key stored, the
restored home answers from the memory it brought. The terminal steps run here, as a user types
them, when the window asks.

The provider's key is copied from the user's real home (`ATLAS_HOME`) into the temporary default
home, and given to the restored home on standard input; after the run every model call of the
temporary homes is copied into the real call ledger.

`--rehearse` runs the same steps against the fake model server, as OpenAI: no key, no credit.

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

from prove_desktop import DESKTOP, carry_ledger, remove_home, say

from atlas.home import Redactor, configure_logging, load_settings, open_home
from atlas.models import load_catalog
from atlas.secrets import Vault

REHEARSAL_KEY = "sk-rehearsal-0123456789"


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove several homes and a backup that moves one.")
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
    provider, model = ("openai", "gpt-6-luna") if args.rehearse else (args.provider, args.model)
    key_name = load_catalog(real.models_file).provider(provider).key
    if key_name is None:
        parser.error(f"{provider} takes no key; the proof is about keys")
    if args.rehearse:
        key = REHEARSAL_KEY
    else:
        found = Vault(real.secrets_folder, settings.vault, redactor).get(key_name)
        if found is None:
            sys.stderr.write(f"There is no {key_name} in the vault of {real.root}.\n")
            return 1
        key = found

    root = Path(tempfile.mkdtemp(prefix="atlas-prove-homes-"))
    try:
        home = root / "atlas"
        work = root / "work"
        signals = root / "signals"
        work.mkdir()
        signals.mkdir()
        prepared = open_home(home)
        Vault(prepared.secrets_folder, settings.vault, redactor).set(key_name, key)
        toml = json.dumps  # a JSON string is a TOML basic string
        prepared.config_file.write_text(
            f"[model]\nprovider = {toml(provider)}\nname = {toml(model)}\n\n"
            f"[workspace]\nfolder = {toml(str(work))}\n",
            encoding="utf-8",
        )
        environ = {
            **os.environ,
            "ATLAS_PROVE_HOME": str(home),
            "ATLAS_PROVE_WORK": str(work),
            "ATLAS_PROVE_SIGNALS": str(signals),
            "ATLAS_PROVE_PACE": str(args.pace),
            "ATLAS_PROVE_KEY_NAME": key_name,
        }
        if args.rehearse:
            environ["ATLAS_PROVE_REHEARSE"] = "1"
        proof = run_with_terminal(environ, signals, home, root / "backups", key_name, key)
        if not args.rehearse:
            for each in (home, home / "homes" / "trabalho", home / "homes" / "copia"):
                if (each / "atlas.db").is_file():
                    carry_ledger(each / "atlas.db", real.database_file)
    finally:
        remove_home(root)
    say("PROVED: every step did what it should." if proof == 0 else "FAILED.")
    return proof


def atlas(home: Path, *argv: str, stdin: str | None = None) -> str:
    """An `atlas` command, as a user types it in a terminal."""
    done = subprocess.run(  # noqa: S603 - fixed tools, no user text
        [sys.executable, "-m", "atlas", *argv],
        env={**os.environ, "ATLAS_HOME": str(home)},
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"atlas {' '.join(argv)}: {done.stdout}{done.stderr}")
    return done.stdout


def run_with_terminal(
    environ: dict[str, str], signals: Path, home: Path, backups: Path, key_name: str, key: str
) -> int:
    """Run the window's steps, typing in the terminal what they ask for when they ask."""
    npx = shutil.which("npx") or "npx"
    proof = subprocess.Popen(  # noqa: S603 - fixed tools, no user text
        [npx, "playwright", "test", "-c", "e2e/prove/playwright.config.ts", "homes.prove.ts"],
        cwd=DESKTOP,
        env=environ,
    )
    while proof.poll() is None:
        for asked in sorted(signals.glob("*.asked")):
            what = asked.stem
            if what == "clone":
                say(f"  [terminal] {atlas(home, 'home', 'create', 'trabalho', '--clone').strip()}")
            elif what == "backup":
                said = atlas(home, "backup", "-o", str(backups))
                say(f"  [terminal] {said.splitlines()[0]}")
                (written,) = backups.glob("*.zip")
                atlas(home, "home", "create", "copia")
                restored = atlas(home, "--home", "copia", "restore", str(written))
                say(f"  [terminal] {' '.join(restored.split())}")
            elif what == "key":
                atlas(home, "--home", "copia", "secrets", "set", key_name, stdin=key)
                say(f"  [terminal] {key_name} stored in copia with `atlas secrets set`")
            asked.rename(signals / f"{what}.done")
        time.sleep(0.5)
    return proof.returncode


if __name__ == "__main__":
    raise SystemExit(main())
