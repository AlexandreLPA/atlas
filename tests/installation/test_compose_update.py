"""Which way an installation updates (docs/ARCHITECTURE.md, The update of a Compose host): native,
as it always did, or by a Compose host's updater, which the Atlas in the container asks through
`atlas.installation`'s compose functions, and whose `atlas update` is the host's own.

The host updater is real here, its Docker played (`host_updater_support`); the requests go
through their JSON lines as the socket carries them, the socket itself proved on Linux by
`test_host_updater.py`.
"""

import io
import json
import shutil
from collections.abc import Mapping
from pathlib import Path

import pytest

import atlas.__main__
from atlas.__main__ import main
from atlas.installation import (
    COMPOSE_UPDATE_ELSEWHERE,
    HostRequest,
    InstallationError,
    SocketChannel,
    UpdateStatus,
    compose_apply,
    compose_check,
    compose_log,
    compose_progress,
    host_channel,
    installation_kind,
)
from atlas.installation.host_updater import Service
from atlas.installation.model import handed_off_from_host, status_from_host
from host_updater_support import Host, Through, at_once, make_host


@pytest.fixture
def host(tmp_path: Path) -> Host:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    return make_host(tmp_path)


def test_the_installation_kind_is_said_never_guessed() -> None:
    assert installation_kind({}) == "native"
    assert installation_kind({"ATLAS_INSTALLATION": "native"}) == "native"
    assert installation_kind({"ATLAS_INSTALLATION": " Compose "}) == "compose"
    with pytest.raises(InstallationError, match="no installation kind"):
        installation_kind({"ATLAS_INSTALLATION": "docker"})
    assert host_channel({}) is None
    found = host_channel({"ATLAS_INSTALLATION": "compose"})
    assert isinstance(found, SocketChannel)
    assert found.path == "/run/atlas-updater/updater.sock"


def test_a_native_status_keeps_its_shape_and_says_its_kind() -> None:
    status = UpdateStatus(True, current="a", target="b", behind=1, commits=("One",))
    said = json.loads(status.to_json())
    assert said["kind"] == "native"
    assert said["unverified"] == ""
    assert said["available"] is True


def test_what_the_host_says_out_of_shape_is_a_failed_look() -> None:
    assert status_from_host(None).error == "The host updater answered no status."
    assert status_from_host({"supported": True, "behind": True}).behind == 0
    assert handed_off_from_host({"error": "no ok"}) is None


def test_the_container_asks_the_host_to_look_update_and_tell(host: Host) -> None:
    channel = Through(Service(host.updater(), start=at_once))
    target = host.checkout.commits[2]

    status = compose_check(channel)
    assert (status.kind, status.behind, status.target) == ("compose", 2, target)
    assert status.commits_pt_br == ("O plugin de voz", "")
    assert status.available

    compose_apply(channel, target, allow_unverified=False)
    progress = compose_progress(channel)
    assert progress.running is False
    assert progress.ended is not None
    assert progress.ended.ok
    assert progress.ended.after == target
    assert "Ended:" in compose_log(channel)
    assert [request.action for request in channel.asked] == ["check", "apply", "status", "log"]

    with pytest.raises(InstallationError, match="moments ago"):
        compose_apply(channel, target, allow_unverified=False)


def test_a_host_updater_that_cannot_be_reached_is_a_failed_look_not_a_crash() -> None:
    class Gone:
        def ask(self, request: HostRequest) -> Mapping[str, object]:
            raise InstallationError("The host updater did not answer (refused).")

    status = compose_check(Gone())
    assert status.supported
    assert not status.available
    assert status.error == "The host updater did not answer (refused)."


# -- `atlas update` --------------------------------------------------------------------------------


def test_atlas_update_in_a_compose_container_points_to_the_host_and_touches_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = {"ATLAS_HOME": str(tmp_path), "ATLAS_INSTALLATION": "compose"}

    assert main(["update"], environ, io.StringIO("")) == 1

    said = capsys.readouterr()
    assert COMPOSE_UPDATE_ELSEWHERE in said.err
    assert "`atlas update` on the host" in said.err
    # No setting warns about the variable: it is the installation's.
    assert "ATLAS_INSTALLATION" not in said.err


@pytest.mark.parametrize("kind", [None, "native"])
def test_atlas_update_on_a_native_installation_goes_the_way_it_always_went(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    kind: str | None,
) -> None:
    """The dispatch alone: the native update itself is proved by test_installation.py, on real
    git; here its first step says it was reached, and nothing runs after it."""

    def reached() -> object:
        raise InstallationError("the native update was reached")

    monkeypatch.setattr(atlas.__main__.Checkout, "running", staticmethod(reached))
    environ = {"ATLAS_HOME": str(tmp_path)}
    if kind is not None:
        environ["ATLAS_INSTALLATION"] = kind

    assert main(["update"], environ, io.StringIO("")) == 1
    assert "the native update was reached" in capsys.readouterr().err


def test_atlas_update_refuses_an_unknown_installation_kind(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    environ = {"ATLAS_HOME": str(tmp_path), "ATLAS_INSTALLATION": "kubernetes"}

    assert main(["update"], environ, io.StringIO("")) == 1
    assert "no installation kind Atlas knows" in capsys.readouterr().err
