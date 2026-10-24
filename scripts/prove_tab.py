"""Prove step 16f through the app's window, against the user's own Claude Code and a real model.

    uv run python scripts/prove_tab.py openai gpt-6-luna
    uv run python scripts/prove_tab.py openai gpt-6-luna --steps 2
    uv run python scripts/prove_tab.py --rehearse

Two steps (`apps/desktop/e2e/prove/tab.prove.ts`):

1. **A session begun in a terminal goes on through Atlas.** `claude -p` is run in a fresh folder, as
   the user would in a terminal, and told a word to keep; the executors tab lists the session
   under its folder, it opens as a conversation, and a prompt from Atlas's box resumes it
   (`--resume`): Claude Code says the word back. It spends the user's Claude Code subscription,
   two short turns, and no Atlas credit ("Atlas answers" stays off).
2. **The play runs a failing test, and Atlas says why.** A JavaScript test that fails sits in the
   work folder; Atlas is asked for the command in a shell block, the play runs it in the side
   panel's terminal, and Atlas answers its output. A few calls of the model named.

The app is the real one and starts the real `atlas serve` on a temporary home; the model's key is
copied from the user's real home, and the calls it made are copied into the real call ledger after.
The Claude Code session the proof made is deleted from Claude Code's folder after the run.

`--rehearse` runs the same steps against the fake model and tests/fake_claude.py: no credit, no
subscription. Exit code 0 only when every step did what it should.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from prove_desktop import carry_ledger, remove_home, run_proof, say

from atlas.__main__ import SCHEMAS
from atlas.home import Database, Redactor, configure_logging, load_settings, migrate, open_home
from atlas.models import load_catalog
from atlas.plugins import Plugins
from atlas.secrets import Vault

ROOT = Path(__file__).resolve().parent.parent
FAKE_CLAUDE = ROOT / "tests" / "fake_claude.py"
#: The word Claude Code is told to keep in the terminal, and must say back through Atlas.
WORD = "girassol"
#: What the user tells Claude Code in the terminal.
TERMINAL_PROMPT = f"Guarde esta palavra para depois: {WORD}. Responda só: ok."
#: The failing test the play runs: `soma` subtracts.
FAILING_TEST = (
    "const assert = require('assert')\n"
    "const soma = (a, b) => a - b\n"
    "assert.strictEqual(soma(2, 3), 5)\n"
    "console.log('ok')\n"
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove step 16f through the app's window.")
    parser.add_argument("provider", nargs="?")
    parser.add_argument("model", nargs="?")
    parser.add_argument("--steps", default="1,2", help="which steps, e.g. 2")
    parser.add_argument("--rehearse", action="store_true", help="the fakes, no credit")
    args = parser.parse_args()
    if not args.rehearse and not (args.provider and args.model):
        parser.error("name a provider and a model, or --rehearse")
    settings = load_settings(os.environ).settings
    real = open_home(settings.home)
    redactor = Redactor()
    configure_logging("WARNING", redactor)

    root = Path(tempfile.mkdtemp(prefix="atlas-prove-16f-"))
    claude_home = root / "claude"
    project: Path | None = None
    try:
        home = root / "home"
        work = root / "work"
        project = work / f"proof16f-{root.name[-6:]}"
        project.mkdir(parents=True)
        (work / "soma.test.js").write_text(FAILING_TEST, encoding="utf-8")
        prepared = open_home(home)
        migrate(Database(prepared.database_file), SCHEMAS)
        vault = Vault(prepared.secrets_folder, settings.vault, redactor)
        plugins = Plugins(prepared, Database(prepared.database_file), vault)
        environ = {
            **os.environ,
            "ATLAS_PROVE_HOME": str(home),
            "ATLAS_PROVE_WORK": str(work),
            "ATLAS_PROVE_PROJECT": str(project),
            "ATLAS_PROVE_WORD": WORD,
            "ATLAS_PROVE_STEPS": args.steps,
        }
        if args.rehearse:
            environ["ATLAS_PROVE_REHEARSE"] = "1"
            scripts = root / "scripts"
            scripts.mkdir()
            environ.update(
                CLAUDE_CONFIG_DIR=str(claude_home),
                FAKE_CLAUDE_PROJECTS=str(claude_home / "projects"),
                FAKE_CLAUDE_SCRIPT=str(scripts),
                FAKE_CLAUDE_SESSION="0e0e0e0e-0000-4000-8000-000000016f16",
            )
            fake = [sys.executable, str(FAKE_CLAUDE)]
            plugins.set_setting("claude-code", "program", json.dumps(fake))
            (scripts / f"{project.name}.json").write_text(
                json.dumps([{"say": "ok"}, {"result": "ok"}]), encoding="utf-8"
            )
            (scripts / f"{project.name}.resume.json").write_text(
                json.dumps([{"say": WORD}, {"result": WORD}]), encoding="utf-8"
            )
        else:
            key = load_catalog(real.models_file).provider(args.provider).key
            if key is not None:
                value = Vault(real.secrets_folder, settings.vault, redactor).get(key)
                if value is None:
                    sys.stderr.write(f"There is no {key} in the vault of {real.root}.\n")
                    return 1
                vault.set(key, value)
            environ["ATLAS_PROVE_PROVIDER"] = args.provider
            environ["ATLAS_PROVE_MODEL"] = args.model
            fake = None
        manifest = plugins.view("claude-code").manifest
        if manifest is None:
            say("The claude-code plugin has no manifest.")
            return 1
        plugins.enable("claude-code", manifest.fingerprint())
        if "1" in args.steps and not begin_in_terminal(project, fake, environ):
            return 1
        proof = run_proof("tab.prove.ts", environ)
        if not args.rehearse:
            carry_ledger(prepared.database_file, real.database_file)
    finally:
        if project is not None and not args.rehearse:
            forget_session(project)
        remove_home(root)
    say("PROVED: every step did what it should." if proof == 0 else "FAILED.")
    return proof


def begin_in_terminal(project: Path, fake: list[str] | None, environ: dict[str, str]) -> bool:
    """The user's terminal: `claude -p` in the project's folder, told a word to keep."""
    clean = {k: v for k, v in environ.items() if not k.startswith(("ANTHROPIC_", "ATLAS_"))}
    if fake is None:
        program = shutil.which("claude.exe" if sys.platform == "win32" else "claude")
        if program is None:
            say("claude is not on the PATH.")
            return False
        line = [program, "-p", TERMINAL_PROMPT, "--output-format", "json"]
        given = None
    else:
        line = [*fake, "--output-format", "stream-json", "--input-format", "stream-json",
                "--permission-mode", "default"]  # fmt: skip
        message = {"role": "user", "content": TERMINAL_PROMPT}
        given = json.dumps({"type": "user", "message": message})
    done = subprocess.run(  # noqa: S603 - the user's own claude, or the fake
        line, input=given, capture_output=True, text=True, cwd=project, env=clean, timeout=300,
        check=False, encoding="utf-8",
    )  # fmt: skip
    said = re.sub(r"\s+", " ", done.stdout)[:300]
    say(f"terminal: claude answered ({done.returncode}): {said}")
    return done.returncode == 0


def forget_session(project: Path) -> None:
    """The Claude Code session the proof made, deleted from Claude Code's own folder: only the
    folder of the proof's own project, never another."""
    named = re.sub(r"[^a-zA-Z0-9]", "-", str(project))
    # The account the proof ran on: the user's own folder, or the one `CLAUDE_CONFIG_DIR` names
    # (another account, when the user's own is at its plan's limit).
    own = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    folder = Path(own) / "projects" / named
    if folder.is_dir() and "proof16f" in folder.name:
        shutil.rmtree(folder, ignore_errors=True)
        say(f"deleted the proof's Claude Code session folder: {folder.name}")


if __name__ == "__main__":
    raise SystemExit(main())
