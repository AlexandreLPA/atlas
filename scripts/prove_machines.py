"""Prove several machines with the user (step 15): a home always on and open on one machine, reached
from another of the user's machines over Tailscale, as the app reaches it. Spends no model credit
unless `--turn` is given (then one short turn on the home's own model).

On the machine that serves (its home always on and open: the app's Settings, or the terminal):

    uv run atlas always-on on
    uv run atlas always-on open
    uv run atlas server code              # the code, carried to the other machine

On the other machine, from Atlas's checkout (its own homes are not touched):

    uv run python scripts/prove_machines.py "atlas1...."
    uv run python scripts/prove_machines.py "atlas1...." --turn --backup shop.zip

1. The code reads: the home, the port, the tailnet's name or address (an open home's).
2. `/health` answers over the tailnet, with this checkout's protocol.
3. Without the token, and with a wrong one, the WebSocket is refused; with the code's, it opens.
4. `conversation.list` answers: the home's conversations, read there.
5. The server terminal: a shell opens on that machine, `echo` runs in it and its marks come back
   with exit code 0, and it closes.
6. `preview.read` reads there: a file that is not there says so.
7. A backup comes down the connection in parts, whole (`--backup` keeps it here).
8. With `--turn`: one turn on that home's model ("Reply with the single word: pong"), answered.

Exit code 0 only when every step did what it should. The code is a secret: it is never printed.
"""

import argparse
import base64
import json
import secrets
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from websockets.exceptions import InvalidStatus
from websockets.sync.client import ClientConnection, connect

from atlas.instances import CodeError, ConnectionCode, decode_code
from atlas.server import PROTOCOL, TOKEN_PREFIX

#: What the turn asks: one word, the cheapest answer there is.
PING = "Reply with the single word: pong"


class StepFailedError(Exception):
    """A step did not do what it should; the message says what happened."""


class Client:
    """A JSON-RPC client over the WebSocket, keeping the frames it read past."""

    def __init__(self, socket: ClientConnection) -> None:
        self.socket = socket
        self._next = 0
        self._kept: list[dict[str, Any]] = []
        ready = self._read(15)
        if ready.get("params", {}).get("type") != "server.ready":
            raise StepFailedError(f"The first frame was not server.ready: {ready}")

    def result(self, method: str, params: dict[str, Any] | None = None, seconds: float = 60) -> Any:
        self._next += 1
        wanted = self._next
        self.socket.send(
            json.dumps({"jsonrpc": "2.0", "id": wanted, "method": method, "params": params or {}})
        )
        frame = self._take(lambda f: f.get("id") == wanted and "method" not in f, seconds)
        if "error" in frame:
            raise StepFailedError(f"{method} failed: {frame['error'].get('message')}")
        return frame["result"]

    def event(self, kind: str, seconds: float = 30) -> dict[str, Any]:
        frame = self._take(
            lambda f: f.get("method") == "event" and f["params"]["type"] == kind, seconds
        )
        return frame["params"]

    def _take(self, wanted: Any, seconds: float) -> dict[str, Any]:
        for frame in list(self._kept):
            if wanted(frame):
                self._kept.remove(frame)
                return frame
        deadline = time.monotonic() + seconds
        while True:
            frame = self._read(deadline - time.monotonic())
            if wanted(frame):
                return frame
            self._kept.append(frame)

    def _read(self, seconds: float) -> dict[str, Any]:
        if seconds <= 0:
            raise StepFailedError("No answer came in time.")
        try:
            return json.loads(self.socket.recv(timeout=seconds))
        except TimeoutError as error:
            raise StepFailedError("No answer came in time.") from error


def base_of(code: ConnectionCode) -> str:
    if not code.tailnet:
        raise StepFailedError(
            "The code has no tailnet address: the home is not open to your machines. Open it "
            "there (`atlas always-on open`), and copy its code again."
        )
    host = f"[{code.tailnet}]" if ":" in code.tailnet else code.tailnet
    return f"http://{host}:{code.port}"


def dial(base: str, token: str) -> ClientConnection:
    return connect(
        base.replace("http", "ws", 1) + "/ws",
        subprotocols=[PROTOCOL, TOKEN_PREFIX + token],  # pyright: ignore[reportArgumentType]
        open_timeout=20,
        max_size=32 * 1024 * 1024,
    )


def step(number: int, text: str) -> None:
    sys.stdout.write(f"{number}. {text}\n")
    sys.stdout.flush()


def health(base: str) -> None:
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=20) as response:  # noqa: S310 - the code's own address
            body = json.loads(response.read())
    except OSError as error:
        raise StepFailedError(
            f"{base}/health did not answer ({error}). Is Tailscale up on both machines, and the "
            "home always on and open there?"
        ) from error
    if body.get("status") != "ok":
        raise StepFailedError(f"/health said {body}.")
    if body.get("protocol") != PROTOCOL:
        raise StepFailedError(
            f"That Atlas speaks {body.get('protocol')}, this checkout {PROTOCOL}."
        )


def refused(base: str, token: str) -> None:
    try:
        with dial(base, token):
            pass
    except InvalidStatus:
        return
    raise StepFailedError("A WebSocket with a wrong token was let in.")


def terminal(client: Client) -> str:
    available = client.result("terminal.available")
    if not available["available"]:
        raise StepFailedError(f"That machine opens no terminal: {available['reason']}")
    nonce = secrets.token_hex(16)
    opened = client.result("terminal.open", {"cwd": "", "nonce": nonce, "cols": 100, "rows": 30})
    seen = ""
    deadline = time.monotonic() + 60
    typed = False
    wanted = f"133;D;0;nonce={nonce}" if opened["marks"] else "atlas-proof\r\n"
    while wanted not in seen.split("echo atlas-proof", 1)[-1] or not typed:
        if not typed and (f"133;A;nonce={nonce}" in seen or not opened["marks"]):
            client.result("terminal.input", {"id": opened["id"], "data": "echo atlas-proof\r"})
            typed = True
        params = client.event("terminal.output", max(0.1, deadline - time.monotonic()))
        if params["payload"]["id"] == opened["id"]:
            seen += params["payload"]["data"]
    client.result("terminal.close", {"id": opened["id"]})
    return opened["shell"]


def backup(client: Client, keep: Path | None) -> int:
    made = client.result("home.backup_download", seconds=300)
    taken = bytearray()
    while True:
        part = client.result(
            "home.backup_part", {"id": made["id"], "offset": len(taken)}, seconds=120
        )
        taken += base64.b64decode(part["data"])
        if part["done"]:
            break
    if len(taken) != made["size"] or taken[:2] != b"PK":
        raise StepFailedError(f"The backup came down cut: {len(taken)} of {made['size']} bytes.")
    if keep is not None:
        keep.write_bytes(bytes(taken))
    return len(taken)


def turn(client: Client) -> str:
    conversation = client.result("conversation.create")["conversation_id"]
    client.result("conversation.send", {"conversation_id": conversation, "text": PING})
    while True:
        event = client.event("message.complete", 180)
        if event.get("conversation_id") == conversation:
            payload = event["payload"]
            if payload.get("status") != "answered":
                raise StepFailedError(f"The turn ended {payload.get('status')}: {payload}")
            return str(payload.get("text", "")).strip()


def prove(code_text: str, *, with_turn: bool, keep: Path | None) -> None:
    try:
        code = decode_code(code_text)
    except CodeError as error:
        raise StepFailedError(str(error)) from error
    base = base_of(code)
    step(1, f"The code reads: home {code.home}, port {code.port}, tailnet {code.tailnet}.")

    health(base)
    step(2, f"{base}/health answers, speaking {PROTOCOL}.")

    refused(base, "")
    refused(base, secrets.token_urlsafe(24))
    try:
        opened = dial(base, code.token)
    except InvalidStatus as error:
        raise StepFailedError(
            f"The code's token was refused ({error}): a new token was made there since. Copy "
            "its code again (`atlas server code`)."
        ) from error
    with opened as socket:
        client = Client(socket)
        step(3, "Without the token and with a wrong one it is refused; with the code's, it opens.")

        listed = client.result("conversation.list", {"limit": 5})
        step(4, f"conversation.list answers: {len(listed['conversations'])} recent there.")

        shell = terminal(client)
        step(5, f"A shell ({shell}) opened there, ran echo, ended with exit code 0, and closed.")

        missing = f"/atlas-proof-{secrets.token_hex(4)}/nothing.txt"
        read = client.result("preview.read", {"path": missing})
        if read["kind"] != "error":
            raise StepFailedError(f"A missing file was read: {read}")
        step(6, f"preview.read reads there: {read['content']}")

        size = backup(client, keep)
        where = f", kept as {keep}" if keep else ""
        step(7, f"A backup came down whole: {size:,} bytes{where}.")

        if with_turn:
            said = turn(client)
            step(8, f"A turn on that home's model answered: {said!r}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("code", help="The code `atlas server code` printed there (a secret).")
    parser.add_argument("--turn", action="store_true", help="One short turn on that home's model.")
    parser.add_argument("--backup", type=Path, help="Keep the backup that came down here.")
    args = parser.parse_args(argv)
    try:
        prove(args.code, with_turn=args.turn, keep=args.backup)
    except StepFailedError as error:
        sys.stdout.write(f"FAILED: {error}\n")
        return 1
    sys.stdout.write("Every step did what it should.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
