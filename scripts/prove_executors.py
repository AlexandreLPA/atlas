"""Prove the outside executors against the user's own Claude Code and a real model. Spends a little
model credit for Atlas's turns, and some of the Claude Code plan's usage.

    uv run python scripts/prove_executors.py openai gpt-6-luna

The server is the real one (the gateway and the assistant on uvicorn, on a real port) over a
temporary home and a temporary project; the client talks to it over a real WebSocket, as the
desktop does, and plays the user: it answers Claude Code's cards. Atlas's keys come from the
user's real home (`ATLAS_HOME`), and its model calls land in its call ledger. Claude Code is the
`claude` on the PATH, with its own login, driven by the plugin Atlas ships (step 16c): the
temporary home turns the plugin on, and the server finds Claude Code through it as it does at
every start.

1. A fix: Atlas hands Claude Code a failing test; its permission requests reach the client as
   `executor.approval` cards, which allow them; the batch's notice says it is done, and the test
   passes.
2. A refusal: Claude Code is asked to write a file; every card is refused with a reason; the file
   is not there and the task still ends.

3. A question: Claude Code is told to ask the user a question first; the card's answer is in the
   code it writes.
4. Atlas decides: with the switch set, no card reaches the client; the guardian answers.
5. Stop: a long task is stopped from the client; it ends `interrupted`.
6. Another account (only with `--accounts own@… other@…`; the user signs in on the browser
   twice): a task on the user's account keeps a word; asked to, Atlas logs Claude Code into the
   other account, goes on with the same session, which still knows the word, and logs back into
   the user's account. A plan's limit cannot be reached on purpose; the tests prove it stops the
   task.
7. The workbench (step 16c): an instance of the user's, started from its card; a prompt that asks
   a question first, answered on the card, and a steer sent while it works, which it takes; its
   own automatic mode on, under which no card reaches the user; "Atlas answers" on, the guardian
   answering; a twin in another folder; then Atlas, asked what Claude Code did, answers from
   delegation_search.

Exit code 0 only when every step did what it should.
"""

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

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
from atlas.models import CallLedger, Models, load_catalog
from atlas.plugins import Plugins
from atlas.secrets import Vault

SCRIPTS = Path(__file__).resolve().parent
TESTS = SCRIPTS.parent / "tests"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(SCRIPTS))

from proof_ledger import ensure_ledger  # noqa: E402 - the scripts' folder
from prove_server import _say  # noqa: E402 - the other proof's helpers, by path

from claude_code_support import claude_code  # noqa: E402 - the plugin, as the tests load it
from server_support import AtlasServer, Client  # noqa: E402 - the tests' server and client

CLAUDE_CODE = "claude-code"
#: How long the server may take to find Claude Code through its plugin.
FIND_SECONDS = 90

WAIT = 600
#: A login on the browser takes as long as the user does (Claude Code waits 15 minutes).
LOGIN_WAIT = 20 * 60
#: Answers a card: the request's params; the result to send, or `None` to leave it unanswered.
type Player = Callable[[dict[str, Any]], dict[str, Any] | None]


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove the outside executors end to end.")
    parser.add_argument("provider")
    parser.add_argument("model")
    parser.add_argument("--steps", default="1,2,3,4,5,7", help="the steps to run, such as 1,4")
    parser.add_argument(
        "--accounts",
        nargs=2,
        metavar=("OWN", "OTHER"),
        help="step 6: the account Claude Code is logged in as, and the one to switch to",
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
    claude = _claude()
    if claude is None:
        sys.stderr.write("Claude Code is not on the PATH.\n")
        return 1
    with tempfile.TemporaryDirectory() as folder:
        try:
            only = {int(step) for step in str(args.steps).split(",")}
            if args.accounts:
                only.add(6)
            proved = prove(Path(folder), models, vault, chosen, claude, only, args.accounts)
        finally:
            calls = ledger.recent(LEDGER_ROWS)
            spent = calls[: len(calls) - before]
            cost = sum((call.cost.usd or 0) for call in spent)
            purposes = [call.purpose for call in spent]
            _say(
                f"Atlas's calls: {len(spent)} ({purposes.count('approval')} guardian, "
                f"{purposes.count('executor_answer')} answers), at list price: US$ {cost:.4f}"
            )
    _say("PROVED: every step did what it should." if proved else "FAILED.")
    return 0 if proved else 1


def prove(
    root: Path,
    models: Models,
    vault: Vault,
    chosen: ModelSettings,
    cli: list[str],
    only: set[int],
    accounts: list[str] | None = None,
) -> bool:
    """Every step over `root`, on the models and the `claude` given (fake ones, to rehearse)."""
    home = open_home(root / "home")
    work = root / "work"
    work.mkdir()
    migrate(Database(home.database_file), SCHEMAS)
    # The user's consent, as the Plugins screen gives it, and the program it runs.
    plugins = Plugins(home, Database(home.database_file), vault)
    manifest = plugins.view(CLAUDE_CODE).manifest
    if manifest is None:
        _say(f"  the plugin cannot be read: {plugins.view(CLAUDE_CODE).problem}")
        return False
    plugins.enable(CLAUDE_CODE, manifest.fingerprint())
    plugins.set_setting(CLAUDE_CODE, "program", json.dumps(cli))
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    results: dict[str, bool] = {}
    with AtlasServer(home, settings, models, vault) as server:
        deadline = time.monotonic() + FIND_SECONDS
        while CLAUDE_CODE not in server.gateway.assistant.executors():
            if time.monotonic() > deadline:
                _say(f"  the server did not find Claude Code: {server.gateway.detections}")
                return False
            time.sleep(0.2)
        _say(f"  found through its plugin: {server.gateway.detections[CLAUDE_CODE]}")
        if 1 in only:
            _step(results, "1 a fix", lambda: _fix(server, work))
        if 2 in only:
            _step(results, "2 a refusal", lambda: _refusal(server, work))
        if 3 in only:
            _step(results, "3 a question", lambda: _question(server, work))
        if 4 in only:
            _step(results, "4 Atlas decides", lambda: _atlas_decides(server, work))
        if 5 in only:
            _step(results, "5 stop", lambda: _stop(server, work))
        if 6 in only and accounts:
            own, other = accounts
            _step(results, "6 another account", lambda: _accounts(server, work, cli, own, other))
        if 7 in only:
            _step(results, "7 the workbench", lambda: _workbench(server, work))
    for step, ok in results.items():
        _say(f"{'ok  ' if ok else 'FAIL'} {step}")
    return all(results.values())


# -- the steps -------------------------------------------------------------------------------------


def _project(work: Path, name: str) -> Path:
    project = work / name
    project.mkdir()
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (project / "test_calc.py").write_text(
        "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    return project


def _ask(project: Path, task: str) -> str:
    return (
        "Use a ferramenta delegate com executor='claude-code', approval='manual' e uma tarefa só, "
        f"com folder='{project}': {task} Depois de delegar, termine o turno; quando o resultado "
        "chegar, diga em uma frase o que aconteceu."
    )


def _fix(server: AtlasServer, work: Path) -> bool:
    _say("step 1: a fix")
    project = _project(work, "conserto")
    asked: list[dict[str, Any]] = []

    def allow(params: dict[str, Any]) -> dict[str, Any]:
        asked.append(params)
        return {"allow": True}

    notice = _delegate(
        server,
        _ask(
            project,
            "conserte a função add em calc.py para o teste test_calc.py passar, e rode "
            "'python -m pytest -q' para confirmar.",
        ),
        allow,
    )
    fixed = "a + b" in (project / "calc.py").read_text(encoding="utf-8")
    _say(f"  cards: {[(p['tool'], p['summary'][:60]) for p in asked]}; fixed: {fixed}")
    return bool(asked) and "status=completed" in notice and fixed


def _refusal(server: AtlasServer, work: Path) -> bool:
    _say("step 2: a refusal")
    project = _project(work, "recusa")
    created = project / "saudacao.txt"
    asked: list[dict[str, Any]] = []

    def deny(params: dict[str, Any]) -> dict[str, Any]:
        asked.append(params)
        return {"allow": False, "reason": "não quero esse arquivo agora"}

    # On 12/10/2026 Claude Code, asked to delete a file, asked for confirmation in words and tried
    # nothing; a harmless write still asks for permission, and the refusal is the user's.
    notice = _delegate(
        server,
        _ask(project, "crie o arquivo saudacao.txt com o texto 'oi', na pasta."),
        deny,
    )
    _say(f"  cards: {[p['summary'][:60] for p in asked]}; created: {created.exists()}")
    return bool(asked) and not created.exists() and "BATCH COMPLETE" in notice


def _question(server: AtlasServer, work: Path) -> bool:
    _say("step 3: a question")
    project = _project(work, "pergunta")
    questions: list[dict[str, Any]] = []

    def play(params: dict[str, Any]) -> dict[str, Any]:
        if "questions" in params:
            questions.append(params)
            first = params["questions"][0]
            return {"answers": {first["question"]: "multiplicar"}}
        return {"allow": True}

    notice = _delegate(
        server,
        _ask(
            project,
            "antes de mudar qualquer coisa, use a ferramenta AskUserQuestion para me perguntar o "
            "nome de uma nova função em calc.py, com as opções 'multiplicar' e 'vezes'; depois "
            "crie essa função, que multiplica a por b.",
        ),
        play,
    )
    code = (project / "calc.py").read_text(encoding="utf-8")
    named = "def multiplicar" in code
    _say(f"  questions: {len(questions)}; 'def multiplicar' in the code: {named}")
    return bool(questions) and named and "BATCH COMPLETE" in notice


def _atlas_decides(server: AtlasServer, work: Path) -> bool:
    _say("step 4: Atlas decides")
    project = _project(work, "atlas")
    reached: list[dict[str, Any]] = []

    def record(params: dict[str, Any]) -> dict[str, Any]:
        reached.append(params)
        return {"allow": True}

    notice = _delegate(
        server,
        _ask(project, "rode 'python -m pytest -q' na pasta e diga quantos testes falharam."),
        record,
        decides="atlas",
    )
    _say(f"  cards that reached the user: {len(reached)}")
    return "BATCH COMPLETE" in notice and not reached


def _stop(server: AtlasServer, work: Path) -> bool:
    _say("step 5: stop")
    project = _project(work, "parar")
    stopped: list[bool] = []

    def stop_at_first(params: dict[str, Any]) -> dict[str, Any] | None:
        stopped.append(True)
        return None

    notice = _delegate(
        server,
        _ask(
            project,
            "rode 'python -m pytest -q', depois escreva dez arquivos de exemplo, um de cada vez, "
            "rodando os testes depois de cada um.",
        ),
        stop_at_first,
        stop_on_card=True,
    )
    return bool(stopped) and "status=interrupted" in notice


def _accounts(server: AtlasServer, work: Path, cli: list[str], own: str, other: str) -> bool:
    _say("step 6: another account")
    if _login(cli).lower() != own.lower():
        _say(f"  Claude Code is not logged in as {own}; log in first.")
        return False
    project = _project(work, "contas")
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        first = _talk(
            client,
            conversation,
            _ask(
                project,
                "guarde nesta sessão a palavra-chave 'girassol' e responda só 'guardado'. Não "
                "crie nem mude arquivo nenhum.",
            ),
            until_notice=True,
        )
        stopped = first.subagent
        _say(f"  first task: {stopped} on {first.accounts}")
        _say("  >>> sign in on the browser as the OTHER account when it opens")
        _talk(
            client,
            conversation,
            f"Troque o Claude Code para a conta {other} com delegate(action='account').",
        )
        switched = _login(cli)
        resumed = _talk(
            client,
            conversation,
            f"Continue a tarefa {stopped} com delegate(action='resume') e a mensagem: 'Qual foi "
            "a palavra-chave que eu pedi para guardar? Responda só a palavra.' Depois de "
            "retomar, termine o turno; quando o resultado chegar, diga a palavra.",
            until_notice=True,
        )
        _say("  >>> sign in on the browser as YOUR account when it opens")
        _talk(
            client,
            conversation,
            f"Troque o Claude Code de volta para a conta {own} com delegate(action='account').",
        )
    back = _login(cli)
    remembered = "girassol" in resumed.notice.lower()
    _say(
        f"  switched to: {switched}; resumed on: {resumed.accounts}; remembered: {remembered}; "
        f"back on: {back}"
    )
    return (
        bool(stopped)
        and switched.lower() == other.lower()
        and other.lower() in [a.lower() for a in resumed.accounts]
        and remembered
        and back.lower() == own.lower()
    )


def _workbench(server: AtlasServer, work: Path) -> bool:
    _say("step 7: the workbench")
    project = _project(work, "bancada")
    twin = _project(work, "gemea")
    with server.client() as client:
        card = client.result(
            "workbench.start", {"executor": CLAUDE_CODE, "folder": str(project), "auto": False}
        )
        instance = str(card["instance_id"])
        _say(f"  card: {card['title']} · {card['account']} · {card['state']}")
        asked: list[str] = []

        def play(frame: dict[str, Any]) -> dict[str, Any]:
            params = cast("dict[str, Any]", frame["params"])
            if frame["method"] == "executor.question":
                first = params["questions"][0]
                asked.append(str(first["question"]))
                return {"answers": {first["question"]: "notas"}}
            asked.append(str(params["tool"]))
            return {"allow": True}

        client.result(
            "workbench.prompt",
            {
                "instance_id": instance,
                "text": (
                    "Antes de mudar qualquer coisa, use a ferramenta AskUserQuestion para me "
                    "perguntar o nome de um arquivo, com as opções 'notas' e 'ideias'. Depois "
                    "crie esse arquivo, com a extensão .txt, contendo a palavra 'bancada'."
                ),
            },
        )
        steered = False
        for frame in _bench(client, instance, play):
            if not steered and frame.get("method") == "executor.question":
                steer = {
                    "instance_id": instance,
                    "text": "Também crie o arquivo extra.txt com a palavra 'steer'.",
                }
                steered = client.result("workbench.prompt", steer)["steered"]
        named = (project / "notas.txt").exists()
        extra = (project / "extra.txt").exists()
        _say(f"  asked: {asked}; steered: {steered}; notas.txt: {named}; extra.txt: {extra}")

        client.result("workbench.auto", {"instance_id": instance, "enabled": True})
        before = len(asked)
        client.result(
            "workbench.prompt",
            {"instance_id": instance, "text": "Crie o arquivo auto.txt com a palavra 'ok'."},
        )
        list(_bench(client, instance, play))
        automatic = (project / "auto.txt").exists() and len(asked) == before
        _say(f"  auto: auto.txt made with no card: {automatic}")

        client.result("workbench.auto", {"instance_id": instance, "enabled": False})
        client.result("workbench.atlas_answers", {"instance_id": instance, "enabled": True})
        before = len(asked)
        client.result(
            "workbench.prompt",
            {"instance_id": instance, "text": "Rode 'python -m pytest -q' e diga o resultado."},
        )
        list(_bench(client, instance, play))
        answered = len(asked) == before
        _say(f"  Atlas answers: no card reached the user: {answered}")

        copy = client.result("workbench.duplicate", {"instance_id": instance, "folder": str(twin)})
        twinned = copy["state"] == "idle" and copy["folder"] == str(twin)
        _say(f"  twin: {copy['folder']} · {copy['state']}")

        conversation = client.result("conversation.create")["conversation_id"]
        talk = _talk(
            client,
            conversation,
            "O que o Claude Code fez na pasta 'bancada'? Use a ferramenta delegation_search "
            "(procure por ela com tool_search) e diga os arquivos que ele criou.",
        )
        recalled = "notas" in talk.answer.lower()
        for found in client.result("workbench.list")["instances"]:
            client.result("workbench.stop", {"instance_id": found["instance_id"]})
    return named and extra and steered and automatic and answered and twinned and recalled


def _bench(
    client: Client, instance: str, play: Callable[[dict[str, Any]], dict[str, Any]]
) -> Iterator[dict[str, Any]]:
    """Answer the instance's cards with `play` until its turn ends; each card's frame, as it
    comes, for the caller to act on."""
    deadline = time.monotonic() + WAIT
    worked = False
    while time.monotonic() < deadline:
        frame = _next(client, deadline)
        method = frame.get("method")
        if method == "event":
            event = cast("dict[str, Any]", frame["params"])
            kind, payload = event["type"], event["payload"]
            if kind == "workbench.activity" and payload["instance_id"] == instance:
                _say(f"  {payload['kind']}: {payload['text'][:100]!r}")
            elif kind == "workbench.update" and payload["instance_id"] == instance:
                worked = worked or payload["state"] == "working"
                if worked and payload["state"] == "idle":
                    return
        elif method in ("executor.approval", "executor.question"):
            yield frame
            client.answer(str(frame["id"]), play(frame))
    raise TimeoutError("the instance's turn never ended")


class _Talk:
    def __init__(self) -> None:
        self.subagent = ""
        self.accounts: list[str] = []
        self.notice = ""
        self.answer = ""


def _talk(client: Client, conversation: str, text: str, *, until_notice: bool = False) -> _Talk:
    """Send `text` and allow every card, until the turn ends, or the batch's notice comes."""
    talk = _Talk()
    client.result("conversation.send", {"conversation_id": conversation, "text": text})
    deadline = time.monotonic() + LOGIN_WAIT
    while time.monotonic() < deadline:
        frame = _next(client, deadline)
        method = frame.get("method")
        if method == "event":
            event = cast("dict[str, Any]", frame["params"])
            kind, payload = event["type"], event["payload"]
            if kind == "subagent.update" and payload.get("executor"):
                talk.subagent = talk.subagent or str(payload["subagent_id"])
                if payload.get("account") and payload["account"] not in talk.accounts:
                    talk.accounts.append(str(payload["account"]))
            elif kind == "tool.complete":
                _say(f"  {payload['name']}: {payload['result_text'][:200]!r}")
            elif kind == "message.start" and payload.get("kind") == "notice":
                talk.notice = str(payload["text"])
                _say(f"  notice: {talk.notice[-300:]!r}")
            elif kind == "message.complete" and (not until_notice or talk.notice):
                talk.answer = str(payload.get("text", ""))
                _say(f"  answer: {talk.answer!r}")
                return talk
        elif method in ("executor.approval", "executor.question"):
            client.answer(str(frame["id"]), {"allow": True})
    raise TimeoutError("the turn never ended")


# -- helpers ---------------------------------------------------------------------------------------


def _delegate(
    server: AtlasServer,
    text: str,
    play: Player,
    *,
    decides: str | None = None,
    stop_on_card: bool = False,
) -> str:
    """Send `text`, answer every card with `play` until the batch's notice comes; the notice."""
    deadline = time.monotonic() + WAIT
    with server.client() as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result("conversation.send", {"conversation_id": conversation, "text": text})
        decided = decides is None
        subagent = ""
        while time.monotonic() < deadline:
            frame = _next(client, deadline)
            method = frame.get("method")
            if method == "event":
                event = cast("dict[str, Any]", frame["params"])
                kind, payload = event["type"], event["payload"]
                if kind == "subagent.update" and payload.get("executor"):
                    subagent = subagent or str(payload["subagent_id"])
                if kind == "subagent.update" and not decided and payload.get("executor"):
                    client.result(
                        "subagent.decides",
                        {
                            "conversation_id": conversation,
                            "subagent_id": payload["subagent_id"],
                            "decides": decides,
                        },
                    )
                    decided = True
                elif kind == "workbench.activity":
                    _say(f"  {payload['kind']}: {payload['text'][:100]!r}")
                elif kind == "message.start" and payload.get("kind") == "notice":
                    _say(f"  notice: {payload['text'][-300:]!r}")
                    return str(payload["text"])
            elif method in ("executor.approval", "executor.question"):
                params = cast("dict[str, Any]", frame["params"])
                answer = play(params)
                if stop_on_card:
                    client.result(
                        "subagent.interrupt",
                        {"conversation_id": conversation, "subagent_id": subagent},
                    )
                elif answer is not None:
                    client.answer(str(frame["id"]), answer)
    raise TimeoutError("the batch's notice never came")


def _next(client: Client, deadline: float) -> dict[str, Any]:
    """The next frame, whatever it is."""
    frame = client._take(lambda _frame: True, deadline - time.monotonic())
    return cast("dict[str, Any]", frame)


def _login(cli: list[str]) -> str:
    """Who Claude Code is logged in as, as its plugin reads it."""
    found = asyncio.run(claude_code(cli).detect()).account
    return "" if found is None else found.login


def _claude() -> list[str] | None:
    """The user's Claude Code, as Atlas finds it."""
    found = shutil.which("claude.exe" if sys.platform == "win32" else "claude")
    return None if found is None else [found]


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
