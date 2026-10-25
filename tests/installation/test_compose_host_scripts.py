"""The Compose host's files (docs/COMPOSE_HOST.md): `scripts/compose-host/atlas`, whose `atlas
update` is the host updater's and every other command Atlas's in its container;
`scripts/compose-host/install.sh`, which migrates the homelab's Compose file and Dockerfile into
root's files, leaving the originals; and the systemd unit.

The scripts run for real under sh and bash, staged with DESTDIR; sudo and docker are stand-ins on
the PATH that write down what they were asked.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from atlas.installation.host_updater import parse_settings

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the Compose host is Linux")

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts" / "compose-host"


def _tool(folder: Path, name: str, body: str) -> None:
    path = folder / name
    path.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    path.chmod(0o755)


def _run(
    argv: list[str], tools: Path, extra: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = {
        **os.environ,
        "PATH": f"{tools}{os.pathsep}{os.environ['PATH']}",
        "LOG": str(tools / "log"),
        **(extra or {}),
    }
    return subprocess.run(  # noqa: S603 - the test's own scripts
        argv, capture_output=True, text=True, env=environment, check=False, timeout=120
    )


def _log(tools: Path) -> list[str]:
    log = tools / "log"
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


# -- `atlas` on the host ---------------------------------------------------------------------------


@pytest.fixture
def tools(tmp_path: Path) -> Path:
    folder = tmp_path / "bin"
    folder.mkdir()
    _tool(
        folder,
        "sudo",
        'echo "sudo $*" >> "$LOG"\n'
        'if [ "$1 $2" = "docker ps" ]; then [ -n "${CONTAINER-}" ] && echo "$CONTAINER"; fi\n'
        "exit 0\n",
    )
    return folder


def _config(tmp_path: Path) -> Path:
    config = tmp_path / "config.toml"
    config.write_text('project = "atlas"\nservice = "atlas"\n', encoding="utf-8")
    return config


def test_atlas_update_on_the_host_is_the_host_updaters_run_as_root(
    tools: Path, tmp_path: Path
) -> None:
    done = _run(["sh", str(SCRIPTS / "atlas"), "update"], tools)

    assert done.returncode == 0, done.stderr
    assert _log(tools) == [
        "sudo /usr/bin/python3 -I /usr/local/lib/atlas-host-updater/host_updater.py update"
    ]

    more = _run(["sh", str(SCRIPTS / "atlas"), "update", "--force"], tools)
    assert more.returncode == 2
    assert "takes nothing more" in more.stderr


def test_every_other_command_is_atlass_in_its_container(tools: Path, tmp_path: Path) -> None:
    extra = {"ATLAS_HOST_UPDATER_CONFIG": str(_config(tmp_path)), "CONTAINER": "c0ffee"}

    done = _run(["sh", str(SCRIPTS / "atlas"), "server", "token"], tools, extra)

    assert done.returncode == 0, done.stderr
    assert _log(tools) == [
        "sudo docker ps --quiet --filter label=com.docker.compose.project=atlas "
        "--filter label=com.docker.compose.service=atlas",
        "sudo docker exec --interactive c0ffee atlas server token",
    ]


def test_the_host_command_says_what_is_missing(tools: Path, tmp_path: Path) -> None:
    gone = _run(
        ["sh", str(SCRIPTS / "atlas"), "version"],
        tools,
        {"ATLAS_HOST_UPDATER_CONFIG": str(tmp_path / "missing.toml")},
    )
    assert gone.returncode == 1
    assert "install.sh again" in gone.stderr

    stopped = _run(
        ["sh", str(SCRIPTS / "atlas"), "version"],
        tools,
        {"ATLAS_HOST_UPDATER_CONFIG": str(_config(tmp_path))},
    )
    assert stopped.returncode == 1
    assert "is not running" in stopped.stderr


# -- the installer ---------------------------------------------------------------------------------


def _checkout(tmp_path: Path) -> Path:
    """A homelab's checkout: the code committed, its Compose file and Dockerfile untracked."""
    root = tmp_path / "homelab" / "atlas"
    target = root / "src" / "atlas" / "installation"
    target.mkdir(parents=True)
    shutil.copy(ROOT / "src" / "atlas" / "installation" / "host_updater.py", target)
    identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
    for argv in (["init", "--quiet"], ["add", "."], ["commit", "--quiet", "-m", "Atlas"]):
        subprocess.run(["git", *identity, *argv], cwd=root, check=True)  # noqa: S603, S607
    (root / "docker-compose.homelab.yml").write_text("services:\n  atlas: {}\n", "utf-8")
    (root / "Dockerfile.homelab").write_text("FROM atlas\n", "utf-8")
    return root


@pytest.fixture
def docker(tmp_path: Path) -> Path:
    folder = tmp_path / "docker-bin"
    folder.mkdir()
    _tool(
        folder,
        "docker",
        'echo "docker $*" >> "$LOG"\n'
        'case "$1 $2" in\n'
        '  "compose version") echo "Docker Compose version v2.30.0" ;;\n'
        '  "compose --project-name")\n'
        '    echo \'{"name": "atlas", "services": {"atlas": {"image": "atlas:local", '
        '"build": {"context": "/home/user/homelab/atlas"}, '
        '"env_file": [{"path": "/home/user/homelab/atlas/.env"}]}}}\' ;;\n'
        '  "image inspect") exit 1 ;;\n'
        "esac\n",
    )
    return folder


def test_the_installer_migrates_the_homelab_files_into_roots_and_leaves_them(
    docker: Path, tmp_path: Path
) -> None:
    checkout = _checkout(tmp_path)
    stage = tmp_path / "stage"
    install = ["bash", str(SCRIPTS / "install.sh"), "--checkout", str(checkout)]

    done = _run(install, docker, {"DESTDIR": str(stage)})

    assert done.returncode == 0, done.stdout + done.stderr
    helper = stage / "usr/local/lib/atlas-host-updater/host_updater.py"
    assert helper.read_bytes() == (checkout / "src/atlas/installation/host_updater.py").read_bytes()
    migrated = yaml.safe_load((stage / "etc/atlas-updater/compose.yml").read_text("utf-8"))
    # It stands alone, and never names the checkout: the host updater builds the commit itself.
    assert migrated == {"name": "atlas", "services": {"atlas": {"image": "atlas:local"}}}
    assert any("config --format json" in line for line in _log(docker))
    assert (stage / "etc/atlas-updater/compose.yml").stat().st_mode & 0o777 == 0o600
    assert (stage / "etc/atlas-updater/Dockerfile").read_text("utf-8") == "FROM atlas\n"
    # The originals stay where they were, as they were.
    assert (checkout / "docker-compose.homelab.yml").read_text(
        "utf-8"
    ) == "services:\n  atlas: {}\n"
    assert (checkout / "Dockerfile.homelab").read_text("utf-8") == "FROM atlas\n"
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=checkout,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert (stage / "var/lib/atlas-updater/state.json").read_text("utf-8").strip() == (
        f'{{"deployed": "{commit}"}}'
    )
    settings = parse_settings((stage / "etc/atlas-updater/config.toml").read_text("utf-8"))
    assert settings.dockerfile == Path("/etc/atlas-updater/Dockerfile")
    assert settings.volume == "atlas_atlas-home"
    assert settings.repository == "https://github.com/marqueskaio/atlas.git"
    override = yaml.safe_load((stage / "etc/atlas-updater/compose.updater.yml").read_text("utf-8"))
    service = override["services"]["atlas"]
    assert service["environment"] == {"ATLAS_INSTALLATION": "compose"}
    assert service["volumes"] == [
        {
            "type": "bind",
            "source": "/run/atlas-updater",
            "target": "/run/atlas-updater",
            "read_only": True,
        }
    ]
    staged = [p.read_text("utf-8") for p in stage.rglob("*") if p.is_file() and p != helper]
    assert not any("docker.sock" in text for text in staged)
    assert "atlas-compose-host-shim" in (stage / "usr/local/bin/atlas").read_text("utf-8")
    assert (stage / "etc/systemd/system/atlas-host-updater.service").is_file()
    # The socket's folder is made at boot, before Docker restarts the container that mounts it.
    tmpfiles = (stage / "etc/tmpfiles.d/atlas-updater.conf").read_text("utf-8")
    assert "d /run/atlas-updater 0755 root root -" in tmpfiles.splitlines()
    # Root ran no git in the checkout, and nothing was started from a stage.
    assert not any("up --detach" in line for line in _log(docker))

    again = _run(install, docker, {"DESTDIR": str(stage)})
    assert again.returncode == 0, again.stderr
    assert "Kept /etc/atlas-updater/compose.yml" in again.stdout
    assert "Kept /etc/atlas-updater/config.toml" in again.stdout


def test_the_program_root_runs_comes_from_the_installers_tree_never_the_checkout(
    docker: Path, tmp_path: Path
) -> None:
    checkout = _checkout(tmp_path)
    # The checkout is its user's, and the agent's through them: a change there never runs as root.
    tampered = checkout / "src/atlas/installation/host_updater.py"
    tampered.write_text("import os\nos.system('id')\n", "utf-8")
    stage = tmp_path / "stage"

    done = _run(
        ["bash", str(SCRIPTS / "install.sh"), "--checkout", str(checkout)],
        docker,
        {"DESTDIR": str(stage)},
    )

    assert done.returncode == 0, done.stdout + done.stderr
    helper = stage / "usr/local/lib/atlas-host-updater/host_updater.py"
    assert helper.read_bytes() == (ROOT / "src/atlas/installation/host_updater.py").read_bytes()


def test_the_installer_never_replaces_an_atlas_command_it_did_not_write(
    docker: Path, tmp_path: Path
) -> None:
    checkout = _checkout(tmp_path)
    stage = tmp_path / "stage"
    (stage / "usr/local/bin").mkdir(parents=True)
    (stage / "usr/local/bin/atlas").write_text("#!/bin/sh\necho mine\n", "utf-8")

    done = _run(
        ["bash", str(SCRIPTS / "install.sh"), "--checkout", str(checkout)],
        docker,
        {"DESTDIR": str(stage)},
    )

    assert done.returncode == 1
    assert "is not this installer's" in done.stderr
    assert (stage / "usr/local/bin/atlas").read_text("utf-8") == "#!/bin/sh\necho mine\n"
    # Refused before anything was written.
    assert not (stage / "etc").exists()
    assert not (stage / "usr/local/lib").exists()


def test_the_installer_parses_and_refuses_what_is_not_a_compose_name(
    docker: Path, tmp_path: Path
) -> None:
    syntax = _run(["bash", "-n", str(SCRIPTS / "install.sh")], docker)
    assert syntax.returncode == 0, syntax.stderr
    checkout = _checkout(tmp_path)

    done = _run(
        ["bash", str(SCRIPTS / "install.sh"), "--checkout", str(checkout), "--service", "a b"],
        docker,
        {"DESTDIR": str(tmp_path / "stage")},
    )
    assert done.returncode == 1
    assert "is not a Compose name" in done.stderr


@pytest.mark.parametrize(
    ("option", "value", "said"),
    [
        ("--image", 'atlas:local"\nprivileged = true', "is not an image's name:tag"),
        ("--image", "atlas", "is not an image's name:tag"),
        ("--github", 'owner/name" x', "is not OWNER/NAME"),
        ("--repository", "http://example.com/atlas.git", "is not an https:// address"),
        ("--repository", 'https://example.com/a"b', "is not an https:// address"),
    ],
)
def test_the_installer_writes_into_roots_files_only_values_of_their_shape(
    docker: Path, tmp_path: Path, option: str, value: str, said: str
) -> None:
    checkout = _checkout(tmp_path)
    stage = tmp_path / "stage"

    done = _run(
        ["bash", str(SCRIPTS / "install.sh"), "--checkout", str(checkout), option, value],
        docker,
        {"DESTDIR": str(stage)},
    )

    assert done.returncode == 1
    assert said in done.stderr
    assert not stage.exists()


def test_the_installer_refuses_to_run_from_a_tree_root_does_not_own(
    docker: Path, tmp_path: Path
) -> None:
    clone = tmp_path / "clone"
    scripts = clone / "scripts" / "compose-host"
    scripts.mkdir(parents=True)
    for name in ("install.sh", "atlas", "atlas-host-updater.service"):
        shutil.copy(SCRIPTS / name, scripts / name)
    helper = clone / "src" / "atlas" / "installation"
    helper.mkdir(parents=True)
    shutil.copy(ROOT / "src/atlas/installation/host_updater.py", helper)
    # Its check alone, from a copy in the test's folder: the user's, or under /tmp, which all write.
    check = f'source "{scripts / "install.sh"}"; trusted_source'

    done = _run(["bash", "-c", check], docker)

    assert done.returncode == 1
    root = os.geteuid() == 0  # pyright: ignore[reportAttributeAccessIssue,reportUnknownMemberType]
    said = "is written by others than root" if root else "is not root's"
    assert said in done.stderr
    assert "run the installer from" in done.stderr


def test_the_service_runs_roots_copy_and_reads_nothing_under_home() -> None:
    unit = (SCRIPTS / "atlas-host-updater.service").read_text("utf-8")

    assert (
        "ExecStart=/usr/bin/python3 -I /usr/local/lib/atlas-host-updater/host_updater.py serve"
        in unit
    )
    for line in ("ProtectHome=yes", "ProtectSystem=strict", "NoNewPrivileges=yes"):
        assert line in unit
    assert "RuntimeDirectoryPreserve=yes" in unit
    settings = [line for line in unit.splitlines() if line and not line.startswith("#")]
    assert not any("/home" in line for line in settings)
