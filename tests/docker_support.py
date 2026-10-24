"""Atlas's container as a user runs it, for the image's tests (tests/docker) and its proof
(scripts/prove_docker.py): the real `docker-compose.yml` under a compose project of its own, so its
own volume and its own port, and, when asked, the fake model in a container beside it
(`tests/docker/fake-model.yml`).

These drive `docker compose`, the way Atlas's compose file tells a user to start it.
"""

import json
import os
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from websockets.sync.client import connect

from atlas.server import PROTOCOL, TOKEN_PREFIX
from server_support import Client

ROOT = Path(__file__).resolve().parent.parent
FAKE_MODEL_FILE = Path("tests") / "docker" / "fake-model.yml"
#: Where Atlas's container reaches the fake model, on the compose project's network.
FAKE_MODEL_URL = "http://fake-model:8000/v1"


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


class ComposeError(RuntimeError):
    pass


class Stack:
    """One compose project: Atlas's container and its volume, and the fake model when asked.

    With `image`, that built image runs and the fake model sits beside it; without, compose builds
    `atlas:local` from the repository, as a user's `docker compose up --build` does.
    """

    def __init__(self, image: str | None = None, *, project: str | None = None) -> None:
        self.image = image
        self.project = project or f"atlas-test-{secrets.token_hex(4)}"
        self.port = free_port()
        self._files = ["-f", "docker-compose.yml"]
        if image is not None:
            self._files += ["-f", FAKE_MODEL_FILE.as_posix()]

    # -- compose ---------------------------------------------------------------------------------

    def compose(
        self, *args: str, stdin: str | None = None, check: bool = True, timeout: float = 300
    ) -> str:
        """Run `docker compose` on this project; its standard output."""
        environ = {**os.environ, "ATLAS_PORT": str(self.port)}
        if self.image is not None:
            environ["ATLAS_TEST_IMAGE"] = self.image
        done = subprocess.run(  # noqa: S603 - fixed tools, no user text
            ["docker", "compose", "-p", self.project, *self._files, *args],  # noqa: S607
            cwd=ROOT,
            env=environ,
            # Bytes, so Windows does not turn the line breaks of a secret or a file into CRLF.
            input=None if stdin is None else stdin.encode("utf-8"),
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        out = done.stdout.decode("utf-8", "replace")
        if check and done.returncode != 0:
            err = done.stderr.decode("utf-8", "replace")
            raise ComposeError(f"docker compose {' '.join(args)} ({done.returncode}):\n{out}{err}")
        return out

    def up(self) -> None:
        """Start from nothing: a new volume, the server, and the fake model when there is one."""
        if self.image is None:
            self.compose("up", "-d", "--build", timeout=1800)
        else:
            self.compose("build", "fake-model", timeout=600)
            self.compose("up", "-d", "--no-build")
        self.wait_ready()

    def down(self) -> None:
        """Stop everything and remove the volume."""
        self.compose("down", "-v", "--remove-orphans", check=False)

    def restart(self) -> None:
        self.compose("restart", "atlas")
        self.wait_ready()

    def exec_bytes(self, *argv: str) -> bytes:
        """A command inside Atlas's container whose output is binary, such as a backup."""
        environ = {**os.environ, "ATLAS_PORT": str(self.port)}
        if self.image is not None:
            environ["ATLAS_TEST_IMAGE"] = self.image
        done = subprocess.run(  # noqa: S603 - fixed tools, no user text
            ["docker", "compose", "-p", self.project, *self._files, "exec", "-T", "atlas", *argv],  # noqa: S607
            cwd=ROOT,
            env=environ,
            capture_output=True,
            timeout=300,
            check=False,
        )
        if done.returncode != 0:
            raise ComposeError(done.stderr.decode("utf-8", "replace"))
        return done.stdout

    def exec(self, *argv: str, stdin: str | None = None, check: bool = True) -> str:
        """A command inside Atlas's container, as `docker compose exec` runs it: the image's user,
        in its working folder."""
        return self.compose("exec", "-T", "atlas", *argv, stdin=stdin, check=check)

    def logs(self) -> str:
        return self.compose("logs", "--no-color", "atlas", check=False)

    def health(self) -> str:
        """What Docker's health check says of Atlas's container."""
        container = self.compose("ps", "-q", "atlas").strip()
        done = subprocess.run(  # noqa: S603 - fixed tools, no user text
            ["docker", "inspect", "-f", "{{.State.Health.Status}}", container],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return done.stdout.strip()

    def wait_ready(self, timeout: float = 120) -> None:
        """Until `/health` answers through the published port."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2):
                    return
            except (OSError, urllib.error.URLError) as error:
                if time.monotonic() > deadline:
                    raise ComposeError(f"/health never answered: {error}\n{self.logs()}") from error
                time.sleep(0.5)

    # -- Atlas -----------------------------------------------------------------------------------

    def token(self) -> str:
        """The server token, as a user gets it: `atlas server token`."""
        return self.exec("atlas", "server", "token").strip()

    def store_key(self, name: str, value: str) -> None:
        """A secret into the vault, from standard input, as a user stores it."""
        self.exec("atlas", "secrets", "set", name, stdin=value)

    def point_at_fake_model(self, provider: str) -> None:
        """The one stand-in: the provider's address, in the volume's models.toml."""
        text = f'[providers.{provider}]\nbase_url = "{FAKE_MODEL_URL}"\n'
        self.exec("sh", "-c", "cat > /data/models.toml", stdin=text)

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ws"

    @contextmanager
    def client(self, token: str) -> Iterator[Client]:
        socket = connect(self.url, subprotocols=[PROTOCOL, TOKEN_PREFIX + token])  # pyright: ignore[reportArgumentType]
        try:
            yield Client(socket)
        finally:
            socket.close()

    # -- the fake model --------------------------------------------------------------------------

    @property
    def fake_model(self) -> str:
        """The fake model's address on the host, for scripting it."""
        return f"http://{self.compose('port', 'fake-model', '8000').strip()}"

    def reply(self, *replies: dict[str, Any]) -> None:
        """Queue what the fake model answers next (`reply_from_json`'s shape)."""
        request = urllib.request.Request(  # noqa: S310 - the fake model on loopback
            f"{self.fake_model}/_fake/reply",
            data=json.dumps(list(replies)).encode("utf-8"),
            headers={"content-type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10):  # noqa: S310
            pass

    def model_requests(self) -> list[dict[str, Any]]:
        """What the fake model received: each request's path, body and authorization."""
        with urllib.request.urlopen(f"{self.fake_model}/_fake/requests", timeout=10) as response:  # noqa: S310
            found: list[dict[str, Any]] = json.loads(response.read())["requests"]
            return found
