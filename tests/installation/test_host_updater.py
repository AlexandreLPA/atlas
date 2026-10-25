"""The host updater (docs/ARCHITECTURE.md, The update of a Compose host): the update of an Atlas
that Docker Compose runs, done on the host by root and asked for by the Atlas in the container.

Git is real, the user's checkout with its untracked homelab files and the updater's own mirror;
Docker is played (`host_updater_support.FakeDocker`), its `/data` volume a real folder that a
snapshot tars and a restore puts back. The kernel's peer credentials and the real socket are
proved on Linux.
"""

import ast
import io
import json
import os
import shutil
import socket
import sys
import tarfile
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

import pytest

from atlas.installation import InstallationError, SocketChannel, compose_check
from atlas.installation.host_updater import (
    APPLY_EVERY,
    CHECK_EVERY,
    REQUEST_BYTES,
    Gate,
    PeerCheck,
    Request,
    Service,
    UpdaterError,
    Verification,
    check_state_folder,
    compose_problems,
    judge_runs,
    owner_problem,
    parse_request,
    parse_settings,
    peer_credentials,
    read_line,
    read_process,
    request_line,
    respond,
    run_update,
)
from host_updater_support import OLD_IMAGE, Host, at_once, compose_config, git, make_host

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
linux = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux's own")

NEW_IMAGE = "sha256:" + "1" * 64


@pytest.fixture
def host(tmp_path: Path) -> Host:
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    return make_host(tmp_path)


def _texts(host: Host) -> list[str]:
    return [" ".join(argv) for argv in host.docker.ran]


# -- the look --------------------------------------------------------------------------------------


@needs_git
def test_the_look_says_what_main_brings_in_both_languages_and_reuses_itself(host: Host) -> None:
    updater = host.updater()
    first, second, third = host.checkout.commits

    status = updater.check()

    assert status["available"] is True
    assert status["kind"] == "compose"
    assert (status["current"], status["target"], status["behind"]) == (first, third, 2)
    assert status["commits"] == ["The voice plugin", "A plugin secret already stored"]
    assert status["commits_pt_br"] == ["O plugin de voz", ""]
    assert status["unverified"] == ""
    fetches = [t for t in _texts(host) if " fetch " in t]
    assert len(fetches) == 1
    # Within a minute the look is reused: GitHub allows 60 calls an hour.
    host.now[0] += CHECK_EVERY - 1
    assert updater.check() == status
    assert len([t for t in _texts(host) if " fetch " in t]) == 1
    host.now[0] += 2
    updater.check()
    assert len([t for t in _texts(host) if " fetch " in t]) == 2
    assert second in git(host.checkout.root, "log", "--format=%H")


@needs_git
def test_a_commit_ci_could_not_verify_is_offered_with_its_reason_and_one_it_failed_is_not(
    host: Host,
) -> None:
    reason = "GitHub Actions could not start its jobs (a billing or quota block)"
    host.verdict[0] = Verification("unverified", reason)
    status = host.updater().check(fresh=True)
    assert status["available"] is True
    assert status["unverified"] == reason

    host.verdict[0] = Verification("failed", "CI failed on this commit (checks)")
    status = host.updater().check(fresh=True)
    assert status["available"] is False
    assert "CI failed on this commit (checks)" in str(status["error"])


@needs_git
def test_the_updater_only_moves_forward(host: Host) -> None:
    root = host.checkout.root
    git(root, "checkout", "--quiet", "-b", "elsewhere", host.checkout.commits[0])
    (root / "other.txt").write_text("x", "utf-8")
    git(root, "add", "other.txt")
    git(root, "commit", "--quiet", "-m", "Not on main")
    host.deploy(git(root, "rev-parse", "HEAD"))
    git(root, "checkout", "--quiet", "main")

    status = host.updater().check(fresh=True)

    assert status["available"] is False
    assert "only moves forward" in str(status["error"])


@needs_git
def test_a_state_without_the_running_commit_offers_nothing(host: Host) -> None:
    (host.settings.state / "state.json").write_text("{}", "utf-8")

    status = host.updater().check(fresh=True)

    assert status["available"] is False
    assert "run its installer again" in str(status["error"])


# -- the update ------------------------------------------------------------------------------------


@needs_git
def test_an_update_builds_while_the_old_server_runs_then_snapshots_swaps_and_checks(
    host: Host,
) -> None:
    updater = host.updater()
    first, _, third = host.checkout.commits
    updater.check()

    ended = updater.finish(updater.begin(third, allow_unverified=False))

    assert ended["ok"] is True, ended
    assert (ended["before"], ended["after"]) == (first, third)
    assert [line.split(" ", 1)[1] for line in ended["commits"]] == [  # type: ignore[union-attr]
        "The voice plugin",
        "A plugin secret already stored",
    ]
    docker = host.docker
    built, stopped = docker.index("build"), docker.index("stop")
    snapshot, started = docker.index("--entrypoint", "tar"), docker.index("up")
    # The old server ran while the new image built: it stopped only after.
    assert built < stopped < snapshot < started
    assert any(f"org.atlas.revision={third}" in argv for argv in docker.said("build"))
    assert docker.images["atlas:local"] == NEW_IMAGE
    assert docker.container_image == NEW_IMAGE
    up = docker.said("up")[0]
    assert up[up.index("--pull") + 1] == "never"
    state = json.loads((host.settings.state / "state.json").read_text("utf-8"))
    assert state["deployed"] == third
    progress = updater.progress()
    assert progress["running"] is False
    assert progress["ended"] == ended
    snapshots = sorted((host.settings.state / "snapshots").glob("*.tar.gz"))
    assert len(snapshots) == 1
    with tarfile.open(snapshots[0]) as archive:
        assert "atlas.db" in archive.getnames()
    # Never a `down -v`, never `git clean`.
    assert not any(" down" in text or " clean" in text for text in _texts(host))
    assert "Ended:" in updater.log_tail()


@needs_git
def test_the_containers_are_made_from_the_compose_file_as_checked(host: Host) -> None:
    updater = host.updater()
    plan = updater.begin(host.checkout.commits[2], allow_unverified=False)
    checked = json.loads(updater.resolved.read_text("utf-8"))
    # A file the Compose files name, changed after the check, never reaches the container.
    host.docker.config = compose_config(privileged=True)

    assert updater.finish(plan)["ok"] is True

    assert checked == compose_config()
    up = host.docker.said("up")[0]
    assert up[up.index("-f") + 1] == str(updater.resolved)
    assert up.count("-f") == 1
    assert json.loads(updater.resolved.read_text("utf-8")) == checked


@needs_git
def test_the_users_checkout_and_its_untracked_files_are_never_touched(host: Host) -> None:
    root = host.checkout.root
    before = git(root, "status", "--porcelain", "--untracked-files=all")
    updater = host.updater()
    updater.check()

    updater.finish(updater.begin(host.checkout.commits[2], allow_unverified=False))

    assert git(root, "status", "--porcelain", "--untracked-files=all") == before
    assert "?? Dockerfile.homelab" in before
    assert (root / "Dockerfile.homelab").read_text("utf-8") == "FROM atlas\n# the homelab's own\n"
    assert (root / "docker-compose.homelab.yml").read_text("utf-8") == "services: {}\n"
    # The build saw the commit alone, exported: never the working tree and its untracked files.
    files, dockerfile = host.docker.built[0]
    assert files == ["Dockerfile", "secret.txt", "voice.txt"]
    assert dockerfile.endswith("Dockerfile")
    # Every git command ran on the updater's own mirror, never in the user's checkout.
    mirror = str(host.settings.state / "mirror.git")
    assert all(mirror in argv for argv in host.docker.ran if argv[0] == "git")
    assert all("core.hooksPath=/dev/null" in argv for argv in host.docker.ran if "fetch" in argv)
    # The export is gone once built.
    assert not any((host.settings.state / "build").iterdir())


@needs_git
def test_a_root_owned_dockerfile_builds_the_exported_commit(tmp_path: Path) -> None:
    host = make_host(tmp_path)
    dockerfile = tmp_path / "etc" / "Dockerfile"
    dockerfile.parent.mkdir(parents=True)
    dockerfile.write_text("FROM python:3.13-slim\n", "utf-8")
    host.settings = type(host.settings)(**{**host.settings.__dict__, "dockerfile": dockerfile})
    updater = host.updater()
    updater.check()

    assert updater.finish(updater.begin(host.checkout.commits[2], allow_unverified=False))["ok"]
    assert host.docker.built[0][1] == str(dockerfile)


@needs_git
def test_a_failed_build_changes_nothing_and_the_old_server_never_stops(host: Host) -> None:
    host.docker.fail_build = True
    updater = host.updater()
    first, _, third = host.checkout.commits
    updater.check()

    ended = updater.finish(updater.begin(third, allow_unverified=False))

    assert ended["ok"] is False
    assert "did not build" in str(ended["error"])
    assert f"kept running {first[:12]}" in str(ended["error"])
    assert host.docker.said("stop") == []
    assert host.docker.said("up") == []
    assert host.docker.said("--entrypoint", "tar") == []
    assert (host.docker.running, host.docker.container) == (True, "c0")
    assert host.docker.images["atlas:local"] == OLD_IMAGE
    state = json.loads((host.settings.state / "state.json").read_text("utf-8"))
    assert state["deployed"] == first
    assert updater.progress()["ended"] == ended


@needs_git
def test_an_unhealthy_new_container_goes_back_to_the_old_image_and_data(host: Host) -> None:
    host.docker.unhealthy = True
    updater = host.updater()
    first, _, third = host.checkout.commits
    updater.check()

    ended = updater.finish(updater.begin(third, allow_unverified=False))

    assert ended["ok"] is False
    error = str(ended["error"])
    assert "unhealthy" in error
    assert f"back on {first[:12]} and /data as it was" in error
    volume = host.docker.volume
    # The new version had migrated the data; the snapshot put it back as it was.
    assert not (volume / "migrated-by-new-version").exists()
    assert (volume / "atlas.db").read_text("utf-8") == "conversations"
    assert (volume / "home" / "notes.md").read_text("utf-8") == "the user's files"
    assert host.docker.images["atlas:local"] == OLD_IMAGE
    assert host.docker.container_image == OLD_IMAGE
    assert host.docker.running
    restore = host.docker.said("--entrypoint", "sh")
    assert len(restore) == 1
    assert restore[0][restore[0].index("--network") + 1] == "none"
    state = json.loads((host.settings.state / "state.json").read_text("utf-8"))
    assert state["deployed"] == first


@needs_git
def test_a_way_back_that_fails_says_where_the_snapshot_is(host: Host) -> None:
    host.docker.unhealthy = True
    host.docker.fail_restore = True
    updater = host.updater()
    updater.check()

    ended = updater.finish(updater.begin(host.checkout.commits[2], allow_unverified=False))

    error = str(ended["error"])
    assert "failed too" in error
    assert str(host.settings.state / "snapshots") in error
    assert updater.progress()["running"] is False


@needs_git
def test_the_way_back_returns_to_the_image_the_old_container_ran(host: Host) -> None:
    # The tag was rebuilt by hand since the old container started: the way back is to what ran.
    host.docker.images["atlas:local"] = "sha256:" + "2" * 64
    host.docker.unhealthy = True
    updater = host.updater()
    updater.check()

    ended = updater.finish(updater.begin(host.checkout.commits[2], allow_unverified=False))

    assert ended["ok"] is False
    assert host.docker.images["atlas:local"] == OLD_IMAGE
    assert host.docker.container_image == OLD_IMAGE
    snapshot = host.docker.said("--entrypoint", "tar")[0]
    assert OLD_IMAGE in snapshot


# -- what the update refuses -----------------------------------------------------------------------


@needs_git
def test_the_update_is_to_the_commit_the_user_was_shown(host: Host) -> None:
    updater = host.updater()
    updater.check()

    with pytest.raises(UpdaterError, match="moved since the look"):
        updater.begin(host.checkout.commits[1], allow_unverified=False)
    with pytest.raises(UpdaterError, match="40 hexadecimal"):
        updater.begin("main", allow_unverified=False)
    assert host.docker.said("build") == []


@needs_git
def test_an_unverified_commit_needs_the_users_explicit_yes(host: Host) -> None:
    host.verdict[0] = Verification("unverified", "GitHub Actions has no run for this commit")
    updater = host.updater()
    target = host.checkout.commits[2]

    with pytest.raises(UpdaterError, match="confirm to update anyway"):
        updater.begin(target, allow_unverified=False)
    plan = updater.begin(target, allow_unverified=True)
    assert updater.finish(plan)["ok"] is True


@needs_git
def test_a_failed_ci_and_an_installation_up_to_date_are_refused(host: Host) -> None:
    host.verdict[0] = Verification("failed", "CI failed on this commit (checks)")
    with pytest.raises(UpdaterError, match="CI failed"):
        host.updater().begin(host.checkout.commits[2], allow_unverified=True)

    host.verdict[0] = Verification("verified")
    host.deploy(host.checkout.commits[2])
    with pytest.raises(UpdaterError, match="up to date"):
        host.updater().begin(host.checkout.commits[2], allow_unverified=False)


@needs_git
def test_one_update_at_a_time_and_none_twice_in_a_row(host: Host) -> None:
    host.docker.fail_build = True
    target = host.checkout.commits[2]
    first = host.updater()
    plan = first.begin(target, allow_unverified=False)

    # The lock: another updater, the host's command or the socket's, waits its turn.
    with pytest.raises(UpdaterError, match="running already"):
        host.updater().begin(target, allow_unverified=False)
    first.finish(plan)

    # The rate: a new one only after a while, whatever the last one did.
    host.now[0] += 10
    with pytest.raises(UpdaterError, match="try again in"):
        host.updater().begin(target, allow_unverified=False)
    host.now[0] += APPLY_EVERY
    host.docker.fail_build = False
    again = host.updater()
    assert again.finish(again.begin(target, allow_unverified=False))["ok"] is True


@needs_git
def test_an_unsafe_compose_file_is_refused_before_anything_runs(host: Host) -> None:
    host.docker.config = compose_config(privileged=True)

    with pytest.raises(UpdaterError, match="privileged"):
        host.updater().begin(host.checkout.commits[2], allow_unverified=False)
    assert host.docker.said("build") == []
    assert host.updater().progress()["running"] is False


# -- the Compose file's rules ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        (
            {"volumes": [{"type": "bind", "source": "/var/run/docker.sock", "target": "/s"}]},
            "Docker socket",
        ),
        (
            {"volumes": [{"type": "volume", "source": "x", "target": "/var/run/docker.sock"}]},
            "Docker socket",
        ),
        ({"privileged": True}, "privileged"),
        ({"cap_add": ["SYS_ADMIN"]}, "adds capabilities"),
        ({"devices": ["/dev/sda:/dev/sda"]}, "host devices"),
        ({"network_mode": "host"}, "host's network_mode"),
        ({"pid": "host"}, "host's pid"),
        ({"security_opt": ["seccomp=unconfined"]}, "security profile"),
        ({"image": "evil:latest"}, "not atlas:local"),
        ({"environment": {}}, "ATLAS_INSTALLATION=compose"),
        ({"security_opt": ["label:disable"]}, "security profile"),
        ({"device_cgroup_rules": ["b 8:* rmw"]}, "host devices"),
        ({"volumes_from": ["other"]}, "another container's volumes"),
        ({"env_file": [{"path": "/home/user/atlas/.env"}]}, "reads a host file (env_file)"),
        ({"label_file": ["/home/user/labels"]}, "reads a host file (label_file)"),
        (
            {"volumes": [{"type": "bind", "source": "/run/atlas-updater", "target": "/run/x"}]},
            "writable",
        ),
        ({"volumes": [{"type": "npipe", "source": "x", "target": "/x"}]}, "a npipe"),
        ({"build": {"context": "/home/user/atlas"}}, "built by Compose from a host folder"),
    ],
)
def test_the_compose_file_never_reaches_the_host(changes: dict[str, object], said: str) -> None:
    host_settings = parse_settings(_SETTINGS)

    problems = compose_problems(compose_config(**changes), host_settings)

    assert any(said in problem for problem in problems), problems


def test_a_volume_config_or_secret_that_reaches_a_host_file_is_refused() -> None:
    settings = parse_settings(_SETTINGS)
    bound = compose_config()
    bound["volumes"] = {
        "atlas-home": {
            "name": "atlas_atlas-home",
            "driver_opts": {"type": "none", "o": "bind", "device": "/"},
        }
    }
    assert any("driver's options" in p for p in compose_problems(bound, settings))
    other_driver = compose_config()
    other_driver["volumes"] = {"atlas-home": {"name": "atlas_atlas-home", "driver": "x"}}
    assert any("driver's options" in p for p in compose_problems(other_driver, settings))
    files = compose_config()
    files["configs"] = {"shadow": {"file": "/etc/shadow"}}
    files["secrets"] = {"key": {"file": "/root/.ssh/id_ed25519"}, "ok": {"environment": "X"}}
    problems = compose_problems(files, settings)
    assert "The config shadow is read from a host file." in problems
    assert "The secret key is read from a host file." in problems
    assert not any("secret ok" in p for p in problems)


def test_a_host_folder_or_another_data_volume_is_refused() -> None:
    settings = parse_settings(_SETTINGS)
    home = compose_config(
        volumes=[
            {"type": "volume", "source": "atlas-home", "target": "/data"},
            {"type": "bind", "source": "/home/user", "target": "/mnt"},
            {"type": "bind", "source": "/run/atlas-updater", "target": "/run/atlas-updater"},
        ]
    )
    assert any("host folder /home/user" in p for p in compose_problems(home, settings))
    elsewhere = compose_config(volumes=[{"type": "volume", "source": "other", "target": "/data"}])
    problems = compose_problems(elsewhere, settings)
    assert any("does not keep /data" in p for p in problems)
    assert any("does not mount /run/atlas-updater" in p for p in problems)
    assert compose_problems(compose_config(), settings) == []
    assert compose_problems({"services": {}}, settings) == [
        "The Compose file has no service atlas."
    ]


# -- the settings ----------------------------------------------------------------------------------

_SETTINGS = """
repository = "https://github.com/example/atlas.git"
github = "example/atlas"
project = "atlas"
service = "atlas"
image = "atlas:local"
compose_files = ["/etc/atlas-updater/compose.yml", "/etc/atlas-updater/compose.updater.yml"]
dockerfile = "/etc/atlas-updater/Dockerfile"
volume = "atlas_atlas-home"
container_uid = 10001
"""


def test_the_settings_are_read_whole_and_anything_out_of_shape_is_refused() -> None:
    settings = parse_settings(_SETTINGS)
    assert settings.branch == "main"
    assert settings.image_name == "atlas"
    assert settings.dockerfile == Path("/etc/atlas-updater/Dockerfile")
    assert (
        parse_settings(_SETTINGS.replace('"/etc/atlas-updater/Dockerfile"', '""')).dockerfile
        is None
    )

    for broken, said in [
        (_SETTINGS + 'command = "rm -rf /"\n', "unknown keys: command"),
        (_SETTINGS.replace("/etc/atlas-updater/compose.yml", "compose.yml"), "absolute paths"),
        (_SETTINGS.replace("/etc/atlas-updater/compose.yml", "/etc/../home/x"), "absolute paths"),
        (_SETTINGS.replace("10001", "0"), "container_uid"),
        (_SETTINGS.replace('"atlas:local"', '"atlas; rm"'), "image"),
        (_SETTINGS.replace("https://github.com/example/atlas.git", "ssh://x"), "repository"),
        (_SETTINGS + 'health_url = "http://example.com/"\n', "loopback"),
        (_SETTINGS + 'health_url = "http://127.0.0.1:1@example.com/"\n', "loopback"),
        (_SETTINGS + 'health_url = "http://localhost.example.com:80/"\n', "loopback"),
        ("not = toml = at all", "not TOML"),
    ]:
        with pytest.raises(UpdaterError, match=said):
            parse_settings(broken)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX owners")
def test_root_trusts_only_files_that_are_roots_alone(tmp_path: Path) -> None:
    mine = tmp_path / "compose.yml"
    mine.write_text("services: {}\n", "utf-8")
    if sys.platform != "win32" and os.geteuid() != 0:
        assert "is not root's" in owner_problem(mine)
    assert "under /home" in owner_problem(Path("/home/user/homelab/atlas/docker-compose.yml"))
    assert "does not exist" in owner_problem(tmp_path / "missing")


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="POSIX, not root")
def test_a_state_folder_another_user_writes_is_refused(tmp_path: Path) -> None:
    # Root unpacks, deletes and builds in it: a folder of the user's could be swapped for links.
    with pytest.raises(UpdaterError, match="state folder"):
        check_state_folder(tmp_path / "state")


# -- the requests ----------------------------------------------------------------------------------


def test_a_request_is_one_of_four_shapes_and_nothing_more() -> None:
    target = "a" * 40
    assert parse_request(b'{"version": 1, "action": "check"}') == Request("check")
    assert parse_request(request_line(Request("apply", target, True)).strip()) == Request(
        "apply", target, True
    )
    for line, said in [
        (b'{"version": 1, "action": "check", "command": "sh"}', "exactly action, version"),
        (b'{"version": 1, "action": "shell"}', "nothing the updater does"),
        (b'{"version": 2, "action": "check"}', "version is not 1"),
        (b'{"version": 1, "action": "apply", "target": "main", "allow_unverified": false}', "40"),
        (
            b'{"version": 1, "action": "apply", "target": "' + b"a" * 40 + b'", '
            b'"allow_unverified": "yes"}',
            "true or false",
        ),
        (
            b'{"version": 1, "action": "apply", "target": "' + b"a" * 40 + b'", '
            b'"allow_unverified": false, "compose_files": ["/tmp/x.yml"]}',
            "exactly",
        ),
        (b"rm -rf /", "not JSON"),
        (b"[1, 2]", "nothing the updater does"),
        (b" " * (REQUEST_BYTES + 1), "too long"),
    ]:
        with pytest.raises(UpdaterError, match=said):
            parse_request(line)


def test_the_socket_takes_so_many_requests_a_minute() -> None:
    now = [0.0]
    gate = Gate(most=2, clock=lambda: now[0])
    assert gate.admit()
    assert gate.admit()
    assert not gate.admit()
    now[0] += 61
    assert gate.admit()


@needs_git
def test_the_service_answers_each_request_and_starts_an_admitted_update(host: Host) -> None:
    service = Service(host.updater(), start=at_once)
    target = host.checkout.commits[2]

    def ask(request: Request, refusal: str = "") -> dict[str, object]:
        return json.loads(json.dumps(service.answer(request_line(request).strip(), refusal)))

    assert ask(Request("check"), "Only Atlas in its container may ask.") == {
        "ok": False,
        "error": "Only Atlas in its container may ask.",
    }
    assert host.docker.ran == []
    status = ask(Request("check"))["status"]
    assert status["target"] == target  # type: ignore[index]
    assert ask(Request("apply", target, False)) == {"ok": True}
    progress = ask(Request("status"))["progress"]
    assert progress["ended"]["ok"] is True  # type: ignore[index]
    assert "Ended:" in str(ask(Request("log"))["log"])
    refused = ask(Request("apply", target, False))
    assert refused["ok"] is False
    assert service.answer(b"not json") == {"ok": False, "error": "The request is not JSON."}


# -- CI's verification -----------------------------------------------------------------------------


def test_ci_verifies_a_commit_only_when_every_run_ended_green() -> None:
    def jobs(steps: list[object]) -> Callable[[int], Sequence[Mapping[str, object]]]:
        return lambda run_id: [{"name": "checks", "conclusion": "failure", "steps": steps}]

    green = {"id": 1, "status": "completed", "conclusion": "success", "name": "CI"}
    red = {"id": 2, "status": "completed", "conclusion": "failure", "name": "CI"}
    assert judge_runs([], jobs([])).state == "unverified"
    assert judge_runs([{**green, "status": "in_progress"}], jobs([])).state == "unverified"
    assert judge_runs([green, {**green, "conclusion": "skipped"}], jobs([])).state == "verified"
    failed = judge_runs([green, red], jobs([{"name": "Tests", "conclusion": "failure"}]))
    assert (failed.state, failed.reason) == ("failed", "CI failed on this commit (checks)")
    # Jobs that never ran a step did not run at all: a billing block is not a failure of the code.
    blocked = judge_runs([red], jobs([]))
    assert blocked.state == "unverified"
    assert "could not start its jobs" in blocked.reason


# -- who may ask -----------------------------------------------------------------------------------


def _process(proc: Path, pid: int, name: str, parent: int, started: int) -> None:
    folder = proc / str(pid)
    folder.mkdir(parents=True)
    rest = ["S", str(parent), *["0"] * 17, str(started), "0", "0", "0"]
    (folder / "stat").write_text(f"{pid} ({name}) {' '.join(rest)}\n", "utf-8")


@needs_git
def test_only_the_containers_main_process_may_ask(host: Host, tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    _process(proc, 4242, "tini", 4000, 100)
    _process(proc, 4300, "atlas", 4242, 105)
    # A command the agent runs is the server's child; one that left it for the init started later.
    _process(proc, 4400, "bash", 4300, 500)
    _process(proc, 4500, "python3 (evil) x", 4242, 900)
    (proc / "self").mkdir()
    peers = PeerCheck(host.updater(), proc)

    assert peers.refusal(4300, 10001) == ""
    assert peers.refusal(4300, 0) == "Only Atlas in its container may ask."
    assert peers.refusal(4400, 10001) == "Only the server in Atlas's container may ask."
    assert peers.refusal(4500, 10001) == "Only the server in Atlas's container may ask."
    assert peers.refusal(9999, 10001) == "Only the server in Atlas's container may ask."
    assert read_process(proc, 4500) is not None
    host.docker.running = False
    assert peers.refusal(4300, 10001) == "Atlas's container is not running."


@needs_git
def test_a_request_that_fails_unforeseen_is_refused_and_the_socket_keeps_answering(
    host: Host, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc = tmp_path / "proc"
    _process(proc, 4242, "tini", 4000, 100)
    _process(proc, 4300, "atlas", 4242, 105)
    peers = PeerCheck(host.updater(), proc)
    service = Service(host.updater(), start=at_once)
    line = request_line(Request("status")).strip()

    # The peer read, then gone before its siblings were: no child of the init is left.
    monkeypatch.setattr(PeerCheck, "_processes", lambda self: iter(()))
    assert respond(service, peers, 4300, 10001, line) == {
        "ok": False,
        "error": "Only the server in Atlas's container may ask.",
    }

    def broken(self: PeerCheck, pid: int, uid: int) -> str:
        raise KeyError("unforeseen")

    monkeypatch.setattr(PeerCheck, "refusal", broken)
    assert respond(service, peers, 4300, 10001, line) == {
        "ok": False,
        "error": "The host updater failed on this request; see its journal.",
    }


@linux
@needs_git
def test_the_container_reaches_the_updater_on_its_socket_and_the_kernel_names_the_peer(
    host: Host, tmp_path: Path
) -> None:
    service = Service(host.updater(), start=at_once)
    path = tmp_path / "updater.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType,reportUnknownArgumentType]
    listener.bind(str(path))
    listener.listen(1)
    seen: list[tuple[int, int]] = []

    def one() -> None:
        connection, _ = listener.accept()
        with connection:
            seen.append(peer_credentials(connection))
            answer = service.answer(read_line(connection))
            connection.sendall(json.dumps(answer).encode("utf-8") + b"\n")

    thread = threading.Thread(target=one)
    thread.start()
    status = compose_check(SocketChannel(str(path), seconds=10))
    thread.join(10)
    listener.close()

    assert status.behind == 2
    assert status.kind == "compose"
    assert status.commits_pt_br == ("O plugin de voz", "")
    assert seen == [(os.getpid(), os.getuid())]  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]


@linux
def test_a_server_without_the_updater_says_how_to_install_it(tmp_path: Path) -> None:
    with pytest.raises(InstallationError, match=r"not installed on this server"):
        SocketChannel(str(tmp_path / "missing.sock")).ask(Request("check"))


# -- the host's own `atlas update` -----------------------------------------------------------------


@needs_git
def test_atlas_update_on_the_host_looks_asks_and_updates_on_a_yes(host: Host) -> None:
    out = io.StringIO()
    assert run_update(host.updater(), out, lambda prompt: "n") == 1
    assert "brings 2 commits" in out.getvalue()
    assert "Nothing was updated." in out.getvalue()
    assert host.docker.said("build") == []

    out = io.StringIO()
    assert run_update(host.updater(), out, lambda prompt: "y") == 0
    said = out.getvalue()
    assert "CI verified" in said
    assert "Building atlas:" in said
    assert f"to {host.checkout.commits[2][:12]}." in said

    host.now[0] += APPLY_EVERY + 1
    out = io.StringIO()
    assert run_update(host.updater(), out, lambda prompt: "y") == 0
    assert "Already up to date" in out.getvalue()


@needs_git
def test_atlas_update_on_the_host_wants_the_words_for_an_unverified_commit(host: Host) -> None:
    host.verdict[0] = Verification("unverified", "GitHub could not be reached (offline)")
    out = io.StringIO()
    assert run_update(host.updater(), out, lambda prompt: "y") == 1
    assert "CI did not verify" in out.getvalue()
    assert host.docker.said("build") == []

    out = io.StringIO()
    assert run_update(host.updater(), out, lambda prompt: "update anyway") == 0


@needs_git
def test_atlas_update_on_the_host_says_a_failure_and_its_log(host: Host) -> None:
    host.docker.fail_build = True
    out = io.StringIO()
    assert run_update(host.updater(), out, lambda prompt: "yes") == 1
    assert "did not build" in out.getvalue()
    assert "The log:" in out.getvalue()


# -- the file itself -------------------------------------------------------------------------------


def test_the_host_updater_runs_on_the_hosts_python_alone() -> None:
    """The installer copies it to the host, where systemd runs it with the system's Python 3.11 or
    newer and nothing else: no newer syntax, and only the standard library."""
    source = Path(__file__).resolve().parents[2] / "src" / "atlas" / "installation"
    tree = ast.parse((source / "host_updater.py").read_text("utf-8"), feature_version=(3, 11))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported <= set(sys.stdlib_module_names), imported - set(sys.stdlib_module_names)
