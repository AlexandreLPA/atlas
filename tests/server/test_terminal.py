"""The server terminal through the real server (step 15): a shell on the server's machine, opened,
typed into and read over a real WebSocket, its own client's alone, closed with it."""

import os
import secrets
import shutil
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from atlas.terminals import Terminals
from server_support import Client, Serve

WINDOWS = sys.platform == "win32"
pytestmark = pytest.mark.skipif(
    not WINDOWS and shutil.which("bash") is None, reason="bash is not installed"
)


def _terminals(tmp_path: Path) -> Terminals:
    env = dict(os.environ)
    if WINDOWS:
        env.pop("ATLAS_TERMINAL_SHELL", None)
    else:
        env["ATLAS_TERMINAL_SHELL"] = "bash"
    return Terminals(env=env, scratch=tmp_path / "scratch")


def _output_until(client: Client, terminal: str, wanted: str, seconds: float = 20) -> str:
    seen = ""
    deadline = time.monotonic() + seconds
    while wanted not in seen:
        params = client.event("terminal.output", max(0.1, deadline - time.monotonic()))
        assert params["payload"]["id"] == terminal, "only this client's terminal"
        assert "conversation_id" not in params
        seen += params["payload"]["data"]
    return seen


def test_a_terminal_opens_on_the_servers_machine_and_carries_its_shell(
    serve: Serve, tmp_path: Path, work: Path
) -> None:
    server = serve(terminals=_terminals(tmp_path))
    nonce = secrets.token_hex(16)
    with server.client() as client:
        assert client.result("terminal.available") == {"available": True, "reason": ""}
        response = client.call(
            "terminal.open", {"cwd": str(work), "nonce": nonce, "cols": 100, "rows": 30}
        )
        # The answer that names it comes before any of its output.
        assert "result" in response, response
        opened: dict[str, Any] = response["result"]
        assert opened["marks"] is True
        terminal = opened["id"]
        _output_until(client, terminal, f"133;A;nonce={nonce}")

        client.result("terminal.resize", {"id": terminal, "cols": 120, "rows": 40})
        client.result("terminal.input", {"id": terminal, "data": "echo served-here\r"})
        said = _output_until(client, terminal, f"133;D;0;nonce={nonce}")
        assert "served-here" in said

        client.result("terminal.input", {"id": terminal, "data": "exit\r"})
        ended = client.event("terminal.exit", 20)
        assert ended["payload"]["id"] == terminal


def test_a_terminal_is_its_connections_alone_and_closes_with_it(
    serve: Serve, tmp_path: Path, work: Path
) -> None:
    terminals = _terminals(tmp_path)
    server = serve(terminals=terminals)
    nonce = secrets.token_hex(16)
    with server.client() as first, server.client() as second:
        opened = first.result("terminal.open", {"cwd": str(work), "nonce": nonce})
        terminal = opened["id"]
        _output_until(first, terminal, f"133;A;nonce={nonce}")

        refused = second.call("terminal.input", {"id": terminal, "data": "echo nope\r"})
        assert "gone" in refused["error"]["message"]
        assert second.result("terminal.close", {"id": terminal}) == {}
        bad = first.call("terminal.open", {"cwd": str(work), "nonce": "not hex"})
        assert "hexadecimal" in bad["error"]["message"]
        assert first.result("terminal.input", {"id": terminal, "data": "echo mine\r"}) == {}

    # The first client left: its shell went with it.
    deadline = time.monotonic() + 20
    while _open_count(terminals):
        assert time.monotonic() < deadline, "the terminal outlived its connection"
        time.sleep(0.1)


def _open_count(terminals: Terminals) -> int:
    return len(terminals._open)  # pyright: ignore[reportPrivateUsage]


def test_a_machine_that_cannot_open_terminals_says_why(serve: Serve, tmp_path: Path) -> None:
    missing = Terminals(scratch=tmp_path, missing=lambda: "The terminal needs pywinpty.")
    with serve(terminals=missing).client() as client:
        assert client.result("terminal.available") == {
            "available": False,
            "reason": "The terminal needs pywinpty.",
        }
