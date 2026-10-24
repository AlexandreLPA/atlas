"""Record one short task on a real provider, for the trail's tests (step 9c). Spends some credit.

    uv run python scripts/record_turn.py anthropic claude-haiku-5-5
    uv run python scripts/record_turn.py openai gpt-6-luna
    uv run python scripts/record_turn.py gemini gemini-3.8-flash --pace 15
    uv run python scripts/record_turn.py ollama qwen3:4b-thinking-2507-q8_0
    uv run python scripts/record_turn.py openai gpt-6-luna --rehearse

The real server (gateway and assistant on uvicorn) runs on a temporary home with the task's files
(`tests/turn_support.py`), its key from the user's real home, and every call in that home's call
ledger. Between Atlas and the provider sits a small proxy that keeps, byte for byte, what the
provider answered to each call of the turn (a request that offers tools; a conversation's title is
not one), and nothing Atlas sent: no key, no header, no prompt. The working folder becomes
`<workspace>` in the answers, and the recording is refused if the user's name or home folder is
left in it. It goes to `tests/recordings/<provider>--<model>.json`, which
`tests/server/test_recordings.py` plays back through the same adapter and server.

The worst case is bounded: each call may write 8,000 tokens at most (`max_output` in a temporary
`models.toml`), and the proxy refuses a seventh call of the turn, which then fails.

`--rehearse` runs the same way against the fake model (tests/fake_model.py), spending nothing, and
writes the recording to the scratch folder given by `--out` (a temporary one by default).

Exit code 0 only when the turn answered and the recording was written.
"""

import argparse
import asyncio
import getpass
import json
import os
import sys
import tempfile
import threading
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from proof_ledger import LEDGER_ROWS, ensure_ledger  # the scripts' folder, on the path when run
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route

from atlas.__main__ import SCHEMAS
from atlas.home import (
    Database,
    ModelSettings,
    Redactor,
    Settings,
    VaultSettings,
    WorkspaceSettings,
    configure_logging,
    load_settings,
    migrate,
    open_home,
)
from atlas.models import CallLedger, Models, Wire, load_catalog
from atlas.secrets import Vault

TESTS = Path(__file__).resolve().parent.parent / "tests"
sys.path.insert(0, str(TESTS))

from fake_model import FakeCall, FakeModelServer, FakeReply  # noqa: E402 - the tests', by path
from server_support import AtlasServer  # noqa: E402
from turn_support import TASK, WORKSPACE, folders, lay_out, run_turn  # noqa: E402

RECORDINGS = TESTS / "recordings"
#: What stands for the rest of the temporary folder, and for the user's home folder.
SCRATCH = "<scratch>"
HOME = "<home>"
#: The most a recorded call may write, and the most calls a turn may make.
MAX_OUTPUT = 8_000
MAX_CALLS = 6
#: Request headers the proxy does not pass on.
_HOP = frozenset({"host", "content-length", "connection", "accept-encoding", "transfer-encoding"})


class Proxy:
    """Passes Atlas's calls on to `upstream` and keeps the provider's answers to the turn's."""

    def __init__(self, upstream: str, pace: float) -> None:
        self.upstream = upstream.rstrip("/")
        self.pace = pace
        self.replies: list[dict[str, Any]] = []
        self.calls = 0
        self.port = 0
        self._server: uvicorn.Server | None = None

    def __enter__(self) -> "Proxy":
        app = Starlette(routes=[Route("/{path:path}", self._pass, methods=["GET", "POST"])])
        config = uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, access_log=False)
        self._server = uvicorn.Server(config)
        threading.Thread(target=self._server.run, daemon=True).start()
        deadline = time.monotonic() + 10
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("The recording proxy did not start.")
            time.sleep(0.01)
        self.port = self._server.servers[0].sockets[0].getsockname()[1]
        return self

    def __exit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.should_exit = True

    @property
    def address(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    async def _pass(self, request: Request) -> Response:
        raw = await request.body()
        body: Any = json.loads(raw) if raw else None
        recorded = isinstance(body, dict) and bool(body.get("tools"))  # pyright: ignore[reportUnknownMemberType]
        if recorded:
            self.calls += 1
            if self.calls > MAX_CALLS:
                return Response(
                    json.dumps({"error": {"message": f"More than {MAX_CALLS} calls: stopped."}}),
                    status_code=500,
                    media_type="application/json",
                )
            if self.pace:
                await asyncio.sleep(self.pace)
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
        headers["accept-encoding"] = "identity"
        url = f"{self.upstream}/{request.path_params['path']}"
        client = httpx.AsyncClient(timeout=httpx.Timeout(600, connect=30))
        started = time.monotonic()
        upstream = await client.send(
            client.build_request(
                request.method, url, headers=headers, content=raw, params=request.query_params
            ),
            stream=True,
        )
        call = str(self.calls) if recorded else "-"
        waited = time.monotonic() - started
        _say(f"  call {call}: HTTP {upstream.status_code} after {waited:.1f} s")
        kept: list[bytes] = []

        async def relay() -> AsyncIterator[bytes]:
            try:
                async for piece in upstream.aiter_raw():
                    kept.append(piece)
                    yield piece
            finally:
                await upstream.aclose()
                await client.aclose()
                if recorded:
                    self.replies.append(
                        {
                            "status": upstream.status_code,
                            "content_type": upstream.headers.get("content-type", ""),
                            "body": b"".join(kept).decode("utf-8"),
                        }
                    )

        return StreamingResponse(
            relay(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Record one short task on a real provider.")
    parser.add_argument("provider")
    parser.add_argument("model")
    parser.add_argument("--pace", type=float, default=0.0, help="seconds before each call")
    parser.add_argument("--rehearse", action="store_true", help="against the fake model")
    parser.add_argument("--out", type=Path, default=None, help="where a rehearsal writes")
    args = parser.parse_args()
    redactor = Redactor()
    configure_logging("WARNING", redactor)
    with tempfile.TemporaryDirectory() as scratch:
        root = Path(scratch)
        if args.rehearse:
            with FakeModelServer() as fake:
                fake.reply(*_rehearsal())
                vault = Vault(root / "keys", VaultSettings(), redactor)
                shipped = load_catalog(root / "none.toml").provider(args.provider)
                if shipped.key is not None:
                    vault.set(shipped.key, "fake-key")
                messages = shipped.wire is Wire.MESSAGES
                upstream = fake.root_url if messages else fake.base_url
                out = args.out or root / "recordings"
                return _record(root, args, vault, None, upstream, out)
        settings = load_settings(os.environ).settings
        real = open_home(settings.home)
        vault = Vault(real.secrets_folder, settings.vault, redactor)
        database = Database(real.database_file)
        ensure_ledger(database)
        upstream = load_catalog(real.models_file).provider(args.provider).base_url
        return _record(root, args, vault, CallLedger(database), upstream, RECORDINGS)


def _record(
    root: Path,
    args: argparse.Namespace,
    vault: Vault,
    ledger: CallLedger | None,
    upstream: str,
    out: Path,
) -> int:
    home = open_home(root / "home")
    work = root / "work"
    lay_out(work)
    migrate(Database(home.database_file), SCHEMAS)
    address = httpx.URL(upstream)
    with Proxy(f"{address.scheme}://{address.netloc.decode()}", args.pace) as proxy:
        shipped = load_catalog(home.models_file).provider(args.provider)
        # The provider's own path stays (`/v1`, `/v1beta/openai`, none for Anthropic, whose SDK
        # adds /v1/messages itself), so the proxy passes each call to the same place.
        base = f"{proxy.address}{address.path.rstrip('/')}"
        home.models_file.write_text(
            f'[providers.{args.provider}]\nbase_url = "{base}"\nmax_output = {MAX_OUTPUT}\n',
            encoding="utf-8",
        )
        chosen = ModelSettings(provider=args.provider, name=args.model)
        models = Models(load_catalog(home.models_file), vault, chosen, ledger=ledger)
        settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
        with AtlasServer(home, settings, models, vault) as server:
            events, _ = run_turn(server, TASK)
    done = events[-1]["payload"]
    _say(f"the turn: {done['status']}, {done['model_calls']} model calls")
    _say("trail: " + " ".join(_trail(events)))
    if ledger is not None:
        # The real home's ledger takes the calls of every Atlas the user runs meanwhile: only this
        # conversation's are the recording's (its turn and its title).
        conversation = events[0]["conversation_id"]
        spent = [call for call in ledger.recent(LEDGER_ROWS) if call.scope == conversation]
        cost = sum((call.cost.usd or 0) for call in spent)
        kinds = ", ".join(sorted({f"{call.purpose} on {call.model}" for call in spent}))
        _say(f"calls: {len(spent)} ({kinds}), at list price: US$ {cost:.4f}")
    if done["status"] != "answered" or not proxy.replies:
        _say(f"FAILED: the turn did not answer ({done.get('text')}); nothing is written.")
        return 1
    recording = {
        "provider": args.provider,
        "model": args.model,
        "wire": shipped.wire.value,
        "recorded_on": datetime.now(UTC).date().isoformat(),
        "task": TASK,
        "replies": proxy.replies,
    }
    text = json.dumps(recording, ensure_ascii=False, indent=1) + "\n"
    # The working folder, then the rest of the temporary folder (its home: the Responses API sends
    # back the instructions it was given, which name it), then the user's own home folder.
    for folder, marker in ((work, WORKSPACE), (root, SCRATCH), (Path.home(), HOME)):
        for spelling in folders(folder):
            text = text.replace(json.dumps(spelling)[1:-1], marker)
    personal = _personal(text)
    if personal:
        at = text.lower().index(personal.lower())
        _say(
            f"FAILED: the recording holds {personal!r} ({text[at - 60 : at + 60]!r}); not written."
        )
        return 1
    out.mkdir(parents=True, exist_ok=True)
    target = out / f"{args.provider}--{args.model.replace(':', '-').replace('/', '-')}.json"
    target.write_text(text, encoding="utf-8", newline="\n")
    _say(f"RECORDED: {target} ({len(proxy.replies)} replies)")
    return 0


def _personal(text: str) -> str | None:
    """The user's name or home folder, when the recording holds either."""
    home = str(Path.home())
    for spelling in (home, home.replace("\\", "/"), json.dumps(home)[1:-1], getpass.getuser()):
        if spelling and spelling.lower() in text.lower():
            return spelling
    return None


def _trail(events: list[dict[str, Any]]) -> list[str]:
    """The turn as the trail draws it, one word a part."""
    parts: list[str] = []
    for event in events:
        kind = event["type"]
        if kind in ("reasoning.delta", "message.delta"):
            word = "reasoning" if kind == "reasoning.delta" else "text"
            if not parts or parts[-1] != word:
                parts.append(word)
        elif kind == "message.interim":
            parts[-1] = "interim" if parts and parts[-1] == "text" else parts[-1]
        elif kind == "tool.complete":
            parts.append(f"{event['payload']['name']}:{event['payload']['status']}")
    return parts


def _rehearsal() -> list[FakeReply]:
    """The task as a model would do it: the log, the function, the fix, the answer."""
    fix = {
        "path": "src/shop/price.py",
        "old_string": "return sum(item.price * item.qty for item in items)",
        "new_string": (
            "return sum((Decimal(str(item.price)) * item.qty for item in items), Decimal(0))"
        ),
    }
    return [
        FakeReply(
            reasoning="The log first.",
            text="I'll read the log.",
            calls=[FakeCall("read_file", '{"path": "pytest.log"}')],
        ),
        FakeReply(calls=[FakeCall("read_file", '{"path": "src/shop/price.py"}')]),
        FakeReply(calls=[FakeCall("edit_file", json.dumps(fix))]),
        FakeReply(text="The total summed floats. It sums Decimal now."),
    ]


def _say(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    sys.exit(main())
