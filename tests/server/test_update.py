"""The update of the server's machine from the app (docs/ARCHITECTURE.md, The update of a Compose
host): the same button, with a Compose host's server on screen, looks and updates through the
server's `update.*` methods, which only the owner's client may call; the server that restarted
tells each client how the update ended.

Over the real server and a real socket; the host updater is real too, its Docker played
(`host_updater_support`), reached through its JSON lines.
"""

import asyncio
import shutil
import time
from pathlib import Path

import pytest

from atlas.installation.host_updater import Service
from atlas.server import Connection, Gateway, ProtocolError
from atlas.server.channels import Bridge
from atlas.server.protocol import FORBIDDEN
from host_updater_support import Host, Through, at_once, make_host
from server_support import Serve


@pytest.fixture
def host(tmp_path: Path) -> Host:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    return make_host(tmp_path / "host")


def test_a_native_server_offers_no_update_to_a_remote_app(serve: Serve) -> None:
    server = serve()
    with server.client() as client:
        info = client.result("update.check")
        assert (info["supported"], info["available"], info["kind"]) == (False, False, "native")
        refused = client.result("update.apply", {"target": "a" * 40})
        assert refused["ok"] is False
        assert "its own machine" in refused["error"]
        assert client.result("update.status") == {
            "running": False,
            "step": "",
            "started_at": "",
            "target": "",
            "ended": None,
        }
        assert client.result("update.log") == {"text": ""}


def test_the_owners_app_looks_updates_and_reads_how_it_went(serve: Serve, host: Host) -> None:
    server = serve(host_updater=Through(Service(host.updater(), start=at_once)))
    first, second, third = host.checkout.commits
    with server.client() as client:
        info = client.result("update.check")
        assert (info["kind"], info["available"], info["behind"]) == ("compose", True, 2)
        assert info["commits"] == ["The voice plugin", "A plugin secret already stored"]
        assert info["commits_pt_br"] == ["O plugin de voz", ""]
        assert info["unverified"] == ""

        moved = client.result("update.apply", {"target": second})
        assert moved["ok"] is False
        assert "moved since the look" in moved["error"]

        assert client.result("update.apply", {"target": third}) == {"ok": True, "error": ""}
        progress = client.result("update.status")
        assert progress["running"] is False
        assert progress["ended"]["ok"] is True
        assert (progress["ended"]["before"], progress["ended"]["after"]) == (first, third)
        assert "Ended:" in client.result("update.log")["text"]

        again = client.result("update.apply", {"target": third})
        assert again["ok"] is False


def test_the_server_that_restarted_tells_each_client_how_the_update_ended(
    serve: Serve, host: Host
) -> None:
    host.now[0] = time.time()
    updater = host.updater()
    updater.check()
    updater.finish(updater.begin(host.checkout.commits[2], allow_unverified=False))

    # The new server, as the update started it: each client that connects hears how it went.
    server = serve(host_updater=Through(Service(host.updater(), start=at_once)))
    with server.client() as client:
        ended = client.event("update.ended")["payload"]
        assert ended["ok"] is True
        assert ended["after"] == host.checkout.commits[2]
        assert client.result("update.status")["ended"]["ended_at"] == ended["ended_at"]


def test_an_update_that_ended_long_ago_is_not_told_again(serve: Serve, host: Host) -> None:
    host.now[0] = time.time() - 3600
    updater = host.updater()
    updater.check()
    updater.finish(updater.begin(host.checkout.commits[2], allow_unverified=False))

    server = serve(host_updater=Through(Service(host.updater(), start=at_once)))
    with server.client() as client:
        client.result("ping")
        with pytest.raises(TimeoutError):
            client.event("update.ended", timeout=1)


def test_only_the_owners_client_may_update_never_a_channels_bridge(
    serve: Serve, host: Host
) -> None:
    """A channel's bridge (a bot's chat) is a connection of the gateway too; the update refuses
    anything but a client that presented the server token. No tool, bot, routine or plugin
    imports the installation at all (pyproject.toml's import contracts)."""
    server = serve(host_updater=Through(Service(host.updater(), start=at_once)))
    gateway: Gateway = server.gateway
    assert issubclass(Bridge, Connection)

    class NotTheOwner(Connection):
        pass

    async def call(method: str, params: dict[str, object]) -> object:
        return await gateway._call(  # pyright: ignore[reportPrivateUsage]
            NotTheOwner(peer=None),  # pyright: ignore[reportArgumentType]
            {"method": method, "params": params},
        )

    for method, params in [
        ("update.check", {}),
        ("update.apply", {"target": host.checkout.commits[2], "allow_unverified": True}),
        ("update.status", {}),
        ("update.log", {}),
    ]:
        with pytest.raises(ProtocolError) as refused:
            asyncio.run(call(method, params))
        assert refused.value.code == FORBIDDEN
    assert host.docker.ran == []
