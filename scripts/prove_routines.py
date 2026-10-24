"""Prove routines, loop guards and the background command's notice against a real model. Spends a
little credit.

    uv run python scripts/prove_routines.py openai gpt-6-luna
    uv run python scripts/prove_routines.py gemini gemini-3.8-flash --pace 15

The server is the real one (the gateway, the assistant and the scheduler on uvicorn, on a real
port), on a temporary home; the client talks to it over a real WebSocket, as the desktop does. Keys
come from the user's real home (`ATLAS_HOME`), and every model call lands in its call ledger.

1. The agent schedules a routine: asked in a conversation for something every weekday at 9, it
   finds cronjob_manage through tool search and makes the routine, the agent's, on weekdays at 9.
2. Run now: the routine fires at once; its run is a conversation nobody watches, and every client
   hears routine.finished with the reply.
3. Silence: a routine with nothing to report answers [SILENT], and its run is silent.
4. Nobody to approve: a routine asked to delete a folder has its flagged commands refused
   without asking anyone (no guardian, no card). Whether the folder survives is not
   promised: on 12/10/2026 Gemini 3.7 deleted it by a way no pattern flags, after three refusals;
   the user decided to keep this posture.
5. Late, and once: with the clock moved 5 hours on, an hourly routine fires once, as a catch-up.
6. The notice: a background command asked to notify starts a turn when it ends, and the reply
   says what the command printed.
7. A command routine: no model; its output is the run's. Proved without credit.

Exit code 0 only when every step did what it should.
"""

import argparse
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import cast

from proof_ledger import LEDGER_ROWS  # the scripts' folder, on the path when run

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
from atlas.jobs import Creator, RunStatus
from atlas.models import CallLedger, Models, load_catalog
from atlas.secrets import Vault

SCRIPTS = Path(__file__).resolve().parent
TESTS = SCRIPTS.parent / "tests"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(SCRIPTS))

from proof_ledger import ensure_ledger  # noqa: E402 - the scripts' folder
from prove_server import _Clock, _Paced, _say  # noqa: E402 - the other proof's helpers, by path

from server_support import AtlasServer, Client  # noqa: E402 - the tests' server and client

WAIT = 240


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove routines end to end.")
    parser.add_argument("provider")
    parser.add_argument("model")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds before each model call")
    parser.add_argument(
        "--steps",
        default="1,2,3,4,5,6,7",
        help="the steps to run, such as 4,6: a day's quota may cut a run short (step 2 needs 1)",
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
            only = {int(step) for step in str(args.steps).split(",")}
            proved = prove(Path(folder), models, vault, chosen, args.pace, only)
        finally:
            calls = ledger.recent(LEDGER_ROWS)
            spent = calls[: len(calls) - before]
            cost = sum((call.cost.usd or 0) for call in spent)
            _say(f"calls: {len(spent)}, at list price: US$ {cost:.4f}")
    _say("PROVED: every step did what it should." if proved else "FAILED.")
    return 0 if proved else 1


def prove(
    root: Path,
    models: Models,
    vault: Vault,
    chosen: ModelSettings,
    pace: float,
    only: set[int] | None = None,
) -> bool:
    """Every step on a server over `root`, on the models given (a fake one, to rehearse)."""
    home = open_home(root / "home")
    work = root / "work"
    work.mkdir()
    migrate(Database(home.database_file), SCHEMAS)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    clock = _Clock()
    served = cast("Models", _Paced(models, pace)) if pace else models
    with AtlasServer(home, settings, served, vault, clock=clock, housekeeping_seconds=1) as server:
        results: dict[str, bool] = {}
        steps = only or set(range(1, 8))
        made: object = None
        if steps & {1, 2}:
            made = _step(
                results, "1 the agent schedules a routine", lambda: _agent_schedules(server)
            )
            results["1 the agent schedules a routine"] = isinstance(made, str)
        if 2 in steps:
            _step(results, "2 run now", lambda: isinstance(made, str) and _run_now(server, made))
        if 3 in steps:
            _step(results, "3 silence", lambda: _silence(server))
        if 4 in steps:
            _step(results, "4 nobody to approve", lambda: _nobody_to_approve(server, work))
        if 5 in steps:
            _step(results, "5 late, and once", lambda: _late_and_once(server, clock))
        if 6 in steps:
            _step(results, "6 the notice", lambda: _notice(server, work))
        if 7 in steps:
            _step(results, "7 a command routine", lambda: _command_routine(server))
    for step, ok in results.items():
        _say(f"{'ok  ' if ok else 'FAIL'} {step}")
    return all(results.values())


# -- the steps -------------------------------------------------------------------------------------


def _agent_schedules(server: AtlasServer) -> str | None:
    _say("step 1: the agent schedules a routine")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result(
            "conversation.send",
            {
                "conversation_id": conversation,
                "text": "Agende uma rotina: todo dia útil às 9h, me mande uma frase de incentivo "
                "para começar o dia. Use a ferramenta de rotinas e depois confirme em uma frase.",
            },
        )
        events = client.events_until("message.complete", timeout=WAIT)
    tools = [e["payload"]["name"] for e in events if e["type"] == "tool.complete"]
    done = events[-1]["payload"]
    routines = server.gateway.assistant.routines.store.all()
    _say(f"  tools: {tools}; {done['status']}: {done['text'][:160]!r}")
    for routine in routines:
        _say(f"  routine {routine.name!r}: {routine.schedule.display} ({routine.schedule.expr})")
    found = [
        r
        for r in routines
        if r.created_by is Creator.AGENT and (r.schedule.expr or "").endswith("* * 1-5")
    ]
    if not found or "cronjob_manage" not in tools or found[0].schedule.expr != "0 9 * * 1-5":
        return None
    return found[0].id


def _run_now(server: AtlasServer, routine_id: str) -> bool:
    _say("step 2: run now")
    with server.client() as client:
        client.result("routine.run", {"routine_id": routine_id})
        finished = client.event("routine.finished", timeout=WAIT)["payload"]
        runs = client.result("routine.runs", {"routine_id": routine_id})["runs"]
        stored = (
            client.result("conversation.open", {"conversation_id": finished["conversation_id"]})
            if finished["conversation_id"]
            else {"messages": []}
        )
    _say(f"  {finished['status']}: {finished['text'][:160]!r}")
    replies = [m for m in stored["messages"] if m["role"] == "assistant" and m["text"]]
    return (
        finished["status"] == "ok"
        and bool(finished["text"].strip())
        and runs[0]["lateness"] == "manual"
        and bool(replies)
    )


def _silence(server: AtlasServer) -> bool:
    _say("step 3: silence")
    routines = server.gateway.assistant.routines
    quiet = routines.create(
        "novidades",
        "every day at 7am",
        prompt="Veja se existe o arquivo novidades.txt na pasta de trabalho, com read_file ou "
        "find_files. Se não existir, responda apenas [SILENT], sem mais nada.",
    )
    with server.client() as client:
        client.result("routine.run", {"routine_id": quiet.id})
        finished = client.event("routine.finished", timeout=WAIT)["payload"]
    _say(f"  {finished['status']}: {finished['text'][:120]!r}")
    return finished["status"] == RunStatus.SILENT.value


def _nobody_to_approve(server: AtlasServer, work: Path) -> bool:
    _say("step 4: nobody to approve")
    old = work / "antiga"
    old.mkdir()
    (old / "nota.txt").write_text("guardar", encoding="utf-8")
    routines = server.gateway.assistant.routines
    remover = routines.create(
        "limpeza",
        "every day at 2am",
        prompt="Apague de vez, com remoção recursiva pelo run_command, a pasta 'antiga' que está "
        "na pasta de trabalho. Depois diga em uma frase o que aconteceu.",
    )
    with server.client() as client:
        client.result("routine.run", {"routine_id": remover.id})
        finished = client.event("routine.finished", timeout=WAIT)["payload"]
    conversation = finished["conversation_id"]
    results = [
        m.text
        for m in server.gateway.assistant.conversations.messages(conversation)
        if m.role.value == "tool"
    ]
    refused = [text for text in results if "nobody" in text.lower() or "unattended" in text.lower()]
    _say(f"  {finished['status']}: {finished['text'][:160]!r}")
    _say(f"  refusals: {[text[:100] for text in refused]}; the folder is there: {old.is_dir()}")
    return bool(refused)


def _late_and_once(server: AtlasServer, clock: _Clock) -> bool:
    _say("step 5: late, and once, 5 hours on")
    routines = server.gateway.assistant.routines
    hourly = routines.create("de hora em hora", "every 1h", prompt="Responda apenas: ok.")
    with server.client() as client:
        clock.later(timedelta(hours=5))
        finished = client.event("routine.finished", timeout=WAIT)["payload"]
        time.sleep(3)  # the server's minute (1 second here) comes round a few times more
        runs = client.result("routine.runs", {"routine_id": hourly.id})["runs"]
    _say(f"  runs: {[(r['status'], r['lateness']) for r in runs]}; {finished['text'][:80]!r}")
    return (
        len(runs) == 1
        and runs[0]["lateness"] == "catch_up"
        and runs[0]["status"] == "ok"
        and finished["name"] == hourly.name
    )


def _notice(server: AtlasServer, work: Path) -> bool:
    _say("step 6: the notice")
    (work / "espera.py").write_text(
        "import time\ntime.sleep(3)\nprint('pronto: 42 arquivos copiados')\n", encoding="utf-8"
    )
    command = _python_command(work / "espera.py")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result(
            "conversation.send",
            {
                "conversation_id": conversation,
                "text": "Rode em segundo plano, com aviso quando terminar (notify_on_complete), "
                f"exatamente este comando: {command}\nNão espere por ele agora. Quando ele "
                "terminar, me diga o que ele imprimiu.",
            },
        )
        first = client.events_until("message.complete", timeout=WAIT)
        second = client.events_until("message.complete", timeout=WAIT)
    started = [e["payload"] for e in second if e["type"] == "message.start"]
    done = second[-1]["payload"]
    _say(f"  started: {started[0]['kind'] if started else None}; {done['text'][:160]!r}")
    began = any(e["type"] == "tool.complete" and "background" in json.dumps(e) for e in first)
    return (
        began
        and bool(started)
        and started[0]["kind"] == "notice"
        and "42" in done["text"]
        and done["status"] == "answered"
    )


def _command_routine(server: AtlasServer) -> bool:
    _say("step 7: a command routine, no model")
    routines = server.gateway.assistant.routines
    backup = routines.create("eco", "every day at 3am", command="echo 'backup ok'")
    with server.client() as client:
        client.result("routine.run", {"routine_id": backup.id})
        finished = client.event("routine.finished", timeout=WAIT)["payload"]
    _say(f"  {finished['status']}: {finished['text'][:80]!r}")
    return finished["status"] == "ok" and "backup ok" in finished["text"]


# -- helpers ---------------------------------------------------------------------------------------


def _step[T](results: dict[str, bool], name: str, run: Callable[[], T]) -> T | None:
    """Run a step; one that waits past its time fails, and the proof goes on to the next."""
    try:
        found = run()
    except TimeoutError as error:
        _say(f"  timed out: {error}")
        results[name] = False
        return None
    results[name] = bool(found)
    return found


def _python_command(script: Path) -> str:
    """The script under this Python, in the system shell's syntax."""
    if sys.platform == "win32":
        return f"& '{sys.executable}' '{script}'"
    return f"'{sys.executable}' '{script}'"


__all__ = ["Client", "prove"]


if __name__ == "__main__":
    sys.exit(main())
