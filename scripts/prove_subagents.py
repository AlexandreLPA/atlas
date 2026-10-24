"""Prove subagents against a real model. Spends a little credit.

    uv run python scripts/prove_subagents.py openai gpt-6-luna
    uv run python scripts/prove_subagents.py gemini gemini-3.8-flash --pace 15

Steps 1 to 5 run on the real server (the gateway and the assistant on uvicorn, on a real port) over
a temporary home, the client talking over a real WebSocket as the desktop does. Step 6 runs
`atlas serve` as a process of its own, to kill it. Keys come from the user's real home
(`ATLAS_HOME`), and every model call lands in its call ledger.

1. A batch: asked to count the lines of two files through two subagents at once, the agent
   delegates both in one call and ends its turn; one notice brings both results, and the next turn
   says both numbers.
2. Steer: a running subagent is told, from the dock's method, to end its summary with a word; the
   word comes back in the notice.
3. Stop: a subagent reading many files is stopped from the dock; it comes back `interrupted`, with
   what it had, and the parent answers.
4. Nobody to approve: a subagent asked to delete a folder has its flagged command refused without
   asking anyone.
5. The cost: the subagents' calls are in the call ledger as `subagent`.
6. The process dies: `atlas serve` is killed while a subagent runs; the next one marks it
   `unknown` and delivers its batch, and the parent answers.

Exit code 0 only when every step did what it should.
"""

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from proof_ledger import LEDGER_ROWS  # the scripts' folder, on the path when run
from websockets.sync.client import connect

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
from atlas.jobs import SubagentStatus, SubagentStore
from atlas.models import CallLedger, Models, load_catalog
from atlas.secrets import Vault
from atlas.server import PROTOCOL, READY_LINE, TOKEN_PREFIX

SCRIPTS = Path(__file__).resolve().parent
TESTS = SCRIPTS.parent / "tests"
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(SCRIPTS))

from proof_ledger import ensure_ledger  # noqa: E402 - the scripts' folder
from prove_desktop import carry_ledger  # noqa: E402 - the other proofs' helpers, by path
from prove_server import _Paced, _say  # noqa: E402

from server_support import AtlasServer, Client  # noqa: E402 - the tests' server and client

WAIT = 300
#: Prepares a home for `atlas serve` (step 6): its settings, and the key or the fake model.
type Configure = Callable[[Path, Path], None]


def main() -> int:
    parser = argparse.ArgumentParser(description="Prove subagents end to end.")
    parser.add_argument("provider")
    parser.add_argument("model")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds before each model call")
    parser.add_argument("--steps", default="1,2,3,4,5,6", help="the steps to run, such as 2,6")
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
    key = load_catalog(real.models_file).provider(args.provider).key

    def configure(home: Path, work: Path) -> None:
        _config(home, args.provider, args.model, work)
        value = vault.get(key) if key is not None else None
        if key is not None and value is not None:
            Vault(home / "secrets", settings.vault, redactor).set(key, value)

    with tempfile.TemporaryDirectory() as folder:
        try:
            only = {int(step) for step in str(args.steps).split(",")}
            proved = prove(Path(folder), models, vault, chosen, args.pace, only, configure, ledger)
            served = Path(folder) / "served" / "atlas.db"
            if served.exists():
                carry_ledger(served, real.database_file)
        finally:
            calls = ledger.recent(LEDGER_ROWS)
            spent = calls[: len(calls) - before]
            cost = sum((call.cost.usd or 0) for call in spent)
            purposes = [call.purpose for call in spent]
            _say(
                f"calls in this process: {len(spent)} ({purposes.count('subagent')} subagent), "
                f"at list price: US$ {cost:.4f}"
            )
    _say("PROVED: every step did what it should." if proved else "FAILED.")
    return 0 if proved else 1


def prove(
    root: Path,
    models: Models,
    vault: Vault,
    chosen: ModelSettings,
    pace: float,
    only: set[int],
    configure: Configure,
    ledger: CallLedger,
) -> bool:
    """Every step over `root`, on the models given (a fake one, to rehearse), whose calls land in
    `ledger`."""
    before = len(ledger.recent(LEDGER_ROWS))
    home = open_home(root / "home")
    work = root / "work"
    work.mkdir()
    migrate(Database(home.database_file), SCHEMAS)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    served = cast("Models", _Paced(models, pace)) if pace else models
    results: dict[str, bool] = {}
    with AtlasServer(home, settings, served, vault) as server:
        if 1 in only:
            _step(results, "1 a batch", lambda: _batch(server, work))
        if 2 in only:
            _step(results, "2 steer", lambda: _steer(server, work))
        if 3 in only:
            _step(results, "3 stop", lambda: _stop(server, work))
        if 4 in only:
            _step(results, "4 nobody to approve", lambda: _nobody_to_approve(server, work))
    if 5 in only:
        _step(results, "5 the cost", lambda: _cost(ledger, before))
    if 6 in only:
        time.sleep(pace)
        _step(results, "6 the process dies", lambda: _process_dies(root, configure, pace))
    for step, ok in results.items():
        _say(f"{'ok  ' if ok else 'FAIL'} {step}")
    return all(results.values())


# -- the steps -------------------------------------------------------------------------------------


def _batch(server: AtlasServer, work: Path) -> bool:
    _say("step 1: a batch")
    (work / "frutas.txt").write_text(
        "\n".join(["maçã", "pera", "uva", "caju", "manga", "kiwi", "figo"]) + "\n", encoding="utf-8"
    )
    (work / "cidades.txt").write_text("Recife\nNatal\nBelém\nManaus\n", encoding="utf-8")
    with server.client() as client:
        conversation, first, second = _delegate_and_wait(
            client,
            "Use a ferramenta delegate com duas tarefas em paralelo, numa chamada só: uma conta "
            "quantas linhas tem o arquivo frutas.txt e a outra conta quantas linhas tem o arquivo "
            f"cidades.txt, os dois na pasta {work}. Depois de delegar, termine o turno. Quando os "
            "resultados chegarem, me diga os dois números numa frase.",
        )
        time.sleep(5)  # a second notice would arrive now
        stored = client.result("conversation.open", {"conversation_id": conversation})
    notices = [m for m in stored["messages"] if m["kind"] == "notice"]
    dispatched = _results(first, "delegate")
    done = second[-1]["payload"]
    _say(f"  delegate: {[r[:90] for r in dispatched]}")
    _say(f"  notices: {len(notices)}; {done['status']}: {done['text'][:160]!r}")
    return (
        any('"count": 2' in r for r in dispatched)
        and len(notices) == 1
        and notices[0]["text"].startswith("[ASYNC DELEGATION BATCH COMPLETE")
        and "7" in done["text"]
        and "4" in done["text"]
    )


def _steer(server: AtlasServer, work: Path) -> bool:
    _say("step 2: steer")
    for name, text in (
        ("a", "O céu é azul."),
        ("b", "A grama é verde."),
        ("c", "O sol é amarelo."),
    ):
        (work / f"{name}.txt").write_text(text + "\n", encoding="utf-8")
    with server.client() as client:
        conversation = _send(
            client,
            "Delegue uma tarefa só, com a ferramenta delegate: ler os arquivos a.txt, b.txt e "
            f"c.txt da pasta {work}, um de cada vez com read_file, em três chamadas separadas, e "
            "resumir cada um numa linha. Depois de delegar, termine o turno; quando o resultado "
            "chegar, repita o resumo.",
        )
        child = client.event("subagent.update", timeout=WAIT)["payload"]["subagent_id"]
        steered = client.result(
            "subagent.steer",
            {
                "conversation_id": conversation,
                "subagent_id": child,
                "message": "Termine o seu resumo com a palavra ABACAXI, em maiúsculas.",
            },
        )
        notice, _ = _notice_turn(client)
    _say(f"  steer: {steered['accepted']}; notice: {notice[-300:]!r}")
    return steered["accepted"] and "ABACAXI" in notice and "steer did not land" not in notice


def _stop(server: AtlasServer, work: Path) -> bool:
    _say("step 3: stop")
    folder = work / "relatorios"
    folder.mkdir()
    for month in range(1, 9):
        (folder / f"mes-{month}.txt").write_text(f"Vendas do mês {month}: {month * 10}\n", "utf-8")
    with server.client() as client:
        conversation = _send(
            client,
            "Delegue uma tarefa só, com a ferramenta delegate: ler, um de cada vez com read_file, "
            f"os oito arquivos mes-1.txt a mes-8.txt da pasta {folder}, e somar as vendas. Depois "
            "de delegar, termine o turno; quando o resultado chegar, diga o que ele trouxe.",
        )
        update = client.event("subagent.update", timeout=WAIT)["payload"]
        while update["tools"] < 1 and update["status"] == "running":
            update = client.event("subagent.update", timeout=WAIT)["payload"]
        stopped = client.result(
            "subagent.interrupt",
            {"conversation_id": conversation, "subagent_id": update["subagent_id"]},
        )
        notice, seq = _notice_turn(client)
        done = _answer(client, seq)
    _say(f"  stop: {stopped['accepted']}; {done['status']}: {done['text'][:160]!r}")
    return stopped["accepted"] and "status=interrupted" in notice and done["status"] == "answered"


def _nobody_to_approve(server: AtlasServer, work: Path) -> bool:
    _say("step 4: nobody to approve")
    old = work / "antiga"
    old.mkdir()
    (old / "nota.txt").write_text("guardar", encoding="utf-8")
    with server.client() as client:
        _send(
            client,
            "Delegue uma tarefa só, com a ferramenta delegate: apagar de vez, com remoção "
            f"recursiva pelo run_command, a pasta {old}. Depois de delegar, termine o turno; "
            "quando o resultado chegar, diga em uma frase o que aconteceu.",
        )
        delegation = client.event("subagent.update", timeout=WAIT)["payload"]["delegation_id"]
        _, seq = _notice_turn(client)
        _answer(client, seq)
    assistant = server.gateway.assistant
    children = assistant.subagent_store.delegation(delegation)
    results = [
        m.text
        for child in children
        for m in assistant.conversations.messages(child.conversation_id)
        if m.role.value == "tool"
    ]
    refused = [text for text in results if "nobody" in text.lower() or "unattended" in text.lower()]
    _say(f"  refusals: {[text[:100] for text in refused]}; the folder is there: {old.is_dir()}")
    return bool(refused)


def _cost(ledger: CallLedger, before: int) -> bool:
    _say("step 5: the cost")
    calls = ledger.recent(LEDGER_ROWS)
    children = [call for call in calls[: len(calls) - before] if call.purpose == "subagent"]
    usd = sum((call.cost.usd or 0) for call in children)
    _say(f"  subagent calls: {len(children)}, US$ {usd:.4f}")
    return bool(children) and all(call.scope for call in children)


def _process_dies(root: Path, configure: Configure, pace: float) -> bool:
    _say("step 6: the process dies")
    home = root / "served"
    work = root / "served-work"
    work.mkdir()
    configure(home, work)
    (work / "longo.txt").write_text("\n".join(f"linha {n}" for n in range(200)), "utf-8")
    token = secrets.token_hex(16)
    server, port = _serve(home, token)
    try:
        with _client(port, token) as client:
            conversation = _send(
                client,
                "Delegue uma tarefa só, com a ferramenta delegate: ler o arquivo longo.txt da "
                f"pasta {work} com read_file e contar as linhas. Depois de delegar, termine o "
                "turno; quando o resultado chegar, diga em uma frase o que se sabe dele.",
            )
            client.event("subagent.update", timeout=WAIT)
    finally:
        _kill(server)
    _say("  killed the server while its subagent ran")
    time.sleep(pace)
    server, port = _serve(home, token)
    try:
        notice, answer = _stored_notice(port, token, conversation)
    finally:
        _kill(server)
    store = SubagentStore(Database(home / "atlas.db"))
    statuses = [
        child.status
        for batch in [store.delegation(d) for d in _delegations(home / "atlas.db")]
        for child in batch
    ]
    _say(f"  statuses: {statuses}; answer: {answer[:160]!r}")
    return (
        SubagentStatus.UNKNOWN in statuses
        and "status=unknown" in notice
        and "Atlas restarted" in notice
        and bool(answer.strip())
    )


# -- helpers ---------------------------------------------------------------------------------------


def _send(client: Client, text: str) -> str:
    conversation = client.result("conversation.create")["conversation_id"]
    client.result("conversation.send", {"conversation_id": conversation, "text": text})
    return conversation


def _delegate_and_wait(
    client: Client, text: str
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Ask, then the turn that delegated and the turn the notice started."""
    conversation = _send(client, text)
    first = client.events_until("message.complete", timeout=WAIT)
    second = client.events_until("message.complete", timeout=WAIT)
    return conversation, first, second


def _notice_turn(client: Client) -> tuple[str, int]:
    """The text of the next turn Atlas started with a notice, and its event's number."""
    deadline = time.monotonic() + WAIT
    while True:
        started = client.event("message.start", timeout=deadline - time.monotonic())
        if started["payload"]["kind"] == "notice":
            return started["payload"]["text"], started["seq"]


def _answer(client: Client, after: int) -> dict[str, Any]:
    """How the turn that started after event `after` ended."""
    deadline = time.monotonic() + WAIT
    while True:
        complete = client.event("message.complete", timeout=deadline - time.monotonic())
        if complete["seq"] > after:
            return complete["payload"]


def _results(events: list[dict[str, Any]], tool: str) -> list[str]:
    return [
        e["payload"]["result_text"]
        for e in events
        if e["type"] == "tool.complete" and e["payload"]["name"] == tool
    ]


def _config(home: Path, provider: str, model: str, work: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_text(
        f"[model]\nprovider = {json.dumps(provider)}\nname = {json.dumps(model)}\n\n"
        f"[workspace]\nfolder = {json.dumps(str(work))}\n",
        encoding="utf-8",
    )


def _serve(home: Path, token: str) -> tuple[subprocess.Popen[str], int]:
    """`atlas serve --port 0` on `home`, once it says its port."""
    environ = {**os.environ, "ATLAS_HOME": str(home), "ATLAS_SERVER_TOKEN": token}
    server = subprocess.Popen(
        [sys.executable, "-m", "atlas", "serve", "--port", "0"],
        env=environ,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if server.stdout is None:  # pragma: no cover - stdout is a pipe
        raise TimeoutError("atlas serve has no output to read.")
    line = server.stdout.readline()
    if not line.startswith(READY_LINE):
        raise TimeoutError(f"atlas serve did not start: {line!r}")
    return server, int(line.removeprefix(READY_LINE))


def _kill(server: subprocess.Popen[str]) -> None:
    """At once, with no chance to write anything down: the process dies."""
    if sys.platform == "win32":
        taskkill = shutil.which("taskkill") or "taskkill"
        subprocess.run(  # noqa: S603 - the system's tool, a number
            [taskkill, "/T", "/F", "/PID", str(server.pid)], capture_output=True, check=False
        )
    else:
        server.kill()
    server.wait(timeout=30)


class _client:  # noqa: N801 - used as `with _client(...)`, as the tests' server.client()
    def __init__(self, port: int, token: str) -> None:
        self._socket = connect(
            f"ws://127.0.0.1:{port}/ws",
            subprotocols=[PROTOCOL, TOKEN_PREFIX + token],  # pyright: ignore[reportArgumentType]
        )

    def __enter__(self) -> Client:
        return Client(self._socket)

    def __exit__(self, *_: object) -> None:
        self._socket.close()


def _stored_notice(port: int, token: str, conversation: str) -> tuple[str, str]:
    """The notice the restarted server delivered, and the parent's answer after it, read from
    the stored conversation: the turn may start before any client is there."""
    deadline = time.monotonic() + WAIT
    with _client(port, token) as client:
        while time.monotonic() < deadline:
            stored = client.result("conversation.open", {"conversation_id": conversation})
            messages = stored["messages"]
            notices = [m for m in messages if m["kind"] == "notice"]
            if notices and not stored["running"]:
                after = messages[messages.index(notices[-1]) + 1 :]
                answers = [m["text"] for m in after if m["role"] == "assistant" and m["text"]]
                if answers:
                    return notices[-1]["text"], answers[-1]
            time.sleep(2)
    raise TimeoutError("the restarted server never delivered the batch")


def _delegations(database_file: Path) -> list[str]:
    connection = Database(database_file).connect()
    try:
        rows = connection.execute("SELECT DISTINCT delegation_id FROM jobs_subagents").fetchall()
    finally:
        connection.close()
    return [str(row[0]) for row in rows]


def _step(results: dict[str, bool], name: str, run: Callable[[], bool]) -> None:
    """Run a step; one that waits past its time fails, and the proof goes on to the next."""
    try:
        results[name] = run()
    except TimeoutError as error:
        _say(f"  timed out: {error}")
        results[name] = False


__all__ = ["Configure", "prove"]


if __name__ == "__main__":
    sys.exit(main())
