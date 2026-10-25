"""The installation: the app built only when its code changed, the launcher, the update that goes
back when a step fails, and the app's update: the look for one and the hand-off as it closes.

Git is real (an origin and a clone in a temporary folder), and so are the files, the lock, the
process the hand-off waits on and the app it opens again (a program that writes it was opened).
Only npm and uv are recorded instead of run: what these tests prove is which steps run, in what
order, and the way back; the real build runs in the app's own gates and in daily use.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

from atlas.home import Home, Parent, locked, serving
from atlas.installation import (
    BUILD_COMMAND,
    BUILD_STAMP,
    LINUX_ICON,
    PACKAGES_STAMP,
    SYNC_COMMAND,
    WINDOWS_ICON,
    Checkout,
    HandedOff,
    InstallationBusyError,
    InstallationError,
    Runner,
    Toolchain,
    UpdateStatus,
    build_desktop,
    check,
    desktop_entry,
    hand_off,
    parse_toolchain,
    update,
    write_launcher,
)

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


class Recorder(Runner):
    """Runs git for real; records npm and uv, and fails the one named in `fail`."""

    def __init__(self, fail: str | None = None) -> None:
        super().__init__()
        self.ran: list[str] = []
        self.fail = fail

    def run(self, command: Sequence[str], cwd: Path) -> None:
        if command[0] in ("npm", "uv", "node"):
            said = "node install-electron" if command[0] == "node" else " ".join(command)
            self.ran.append(said)
            if self.fail is not None and said.startswith(self.fail):
                raise InstallationError(f"`{said}` failed (exit 1); its output is above.")
            if said == "npm run build":
                (cwd / "out").mkdir(exist_ok=True)
            if command[0] == "node":
                _with_electron(Checkout(cwd.parent.parent))
            return
        super().run(command, cwd)


#: Just enough of the desktop app for the build to find its sources, its tests and its packages.
_APP_FILES = {
    "src/main.tsx": "render()\n",
    "src/main.test.ts": "test()\n",
    "package.json": '{"name": "atlas-desktop"}\n',
    "package-lock.json": '{"lockfileVersion": 3}\n',
}


def _app(root: Path) -> Checkout:
    desktop = root / "apps" / "desktop"
    (desktop / "src").mkdir(parents=True)
    for name, text in _APP_FILES.items():
        (desktop / name).write_text(text, encoding="utf-8")
    return Checkout(root)


# -- the build -------------------------------------------------------------------------------------


def test_the_app_is_built_once_and_again_only_when_its_code_changes(tmp_path: Path) -> None:
    """A content stamp: an unchanged app opens without a build; a test file is not code."""
    checkout = _app(tmp_path)
    runner = Recorder()

    first = build_desktop(checkout, runner)
    again = build_desktop(checkout, runner)
    (checkout.desktop / "src" / "main.test.ts").write_text("test(2)\n", encoding="utf-8")
    test_only = build_desktop(checkout, runner)
    (checkout.desktop / "src" / "main.tsx").write_text("render(2)\n", encoding="utf-8")
    code = build_desktop(checkout, runner)
    (checkout.desktop / "package-lock.json").write_text('{"lockfileVersion": 4}\n', "utf-8")
    packages = build_desktop(checkout, runner)

    assert (first.installed, first.built) == (True, True)
    assert (again.installed, again.built) == (False, False)
    assert (test_only.installed, test_only.built) == (False, False)
    assert (code.installed, code.built) == (False, True)
    assert (packages.installed, packages.built) == (True, True)  # the lock is an input too
    assert runner.ran == [
        "npm ci",
        "node install-electron",  # Electron 43 fetches its program on first use only
        "npm run build",
        "npm run build",
        "npm ci",
        "npm run build",
    ]
    assert checkout.read_stamp(BUILD_STAMP) == checkout.stamp()
    assert checkout.read_stamp(PACKAGES_STAMP) is not None


def test_a_failed_build_leaves_no_stamp_so_the_next_run_builds_again(tmp_path: Path) -> None:
    checkout = _app(tmp_path)

    with pytest.raises(InstallationError, match="npm run build"):
        build_desktop(checkout, Recorder(fail="npm run build"))
    runner = Recorder()
    build_desktop(checkout, runner)

    assert runner.ran == ["npm run build"]  # the packages were installed by the first run


def test_a_build_under_another_build_or_update_is_refused(tmp_path: Path) -> None:
    checkout = _app(tmp_path)

    with locked(checkout.desktop / ".atlas-build.lock"), pytest.raises(InstallationBusyError):
        build_desktop(checkout, Recorder())


# -- the launcher ----------------------------------------------------------------------------------


def _with_electron(checkout: Checkout) -> Path:
    package = checkout.desktop / "node_modules" / "electron"
    (package / "dist").mkdir(parents=True, exist_ok=True)
    name = "electron.exe" if sys.platform == "win32" else "electron"
    (package / "path.txt").write_text(name, encoding="utf-8")
    (package / "dist" / name).write_bytes(b"")
    return package / "dist" / name


def test_the_linux_entry_opens_the_built_app() -> None:
    text = desktop_entry(
        '"/opt/atlas/electron" "/opt/atlas/apps/desktop"',
        "/opt/atlas/apps/desktop",
        "/opt/atlas/apps/desktop/resources/icon.png",
    )
    assert "Name=Atlas\n" in text
    assert 'Exec="/opt/atlas/electron" "/opt/atlas/apps/desktop"\n' in text
    assert "Icon=/opt/atlas/apps/desktop/resources/icon.png\n" in text
    assert "Terminal=false\n" in text


def test_the_launchers_icons_are_the_apps() -> None:
    """The icons the launchers name are the ones the app ships: an .ico and a PNG."""
    desktop = Path(__file__).parents[2] / "apps" / "desktop"

    assert (desktop / WINDOWS_ICON).read_bytes()[:4] == b"\x00\x00\x01\x00"
    assert (desktop / LINUX_ICON).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the app grid entry is Linux's")
def test_on_linux_the_launcher_is_an_app_grid_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = _app(tmp_path / "checkout")
    electron = _with_electron(checkout)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))

    written = write_launcher(checkout, tmp_path)

    assert written == tmp_path / "data" / "applications" / "atlas.desktop"
    assert written is not None
    text = written.read_text(encoding="utf-8")
    assert f'Exec="{electron}" "{checkout.desktop}"' in text
    assert f"Icon={checkout.desktop / LINUX_ICON}\n" in text


@pytest.mark.skipif(sys.platform != "win32", reason="the Start Menu shortcut is Windows's")
def test_on_windows_the_launcher_is_a_start_menu_shortcut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The desktop shortcuts, through WScript.Shell, into a Start Menu of the test's."""
    checkout = _app(tmp_path / "checkout")
    electron = _with_electron(checkout)
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))

    written = write_launcher(checkout, tmp_path)

    assert written == tmp_path / "roaming/Microsoft/Windows/Start Menu/Programs/Atlas.lnk"
    link = f"$link = (New-Object -ComObject WScript.Shell).CreateShortcut('{written}')"
    read = f"{link}; $link.TargetPath; $link.IconLocation"
    target, icon = subprocess.run(  # noqa: S603 - fixed tools, no user text
        ["powershell", "-NoProfile", "-Command", read],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()[:2]
    assert Path(target) == electron
    assert icon == f"{checkout.desktop / WINDOWS_ICON},0"


def test_without_the_app_packages_the_launcher_says_how_to_install_them(tmp_path: Path) -> None:
    checkout = _app(tmp_path)
    with pytest.raises(InstallationError, match="atlas desktop"):
        checkout.electron()


# -- the update ------------------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    """What git prints in `cwd`, trimmed; a command that fails fails the test."""
    command = ["git", *args]
    completed = subprocess.run(command, check=True, cwd=cwd, text=True, capture_output=True)  # noqa: S603
    return completed.stdout.strip()


def _repositories(tmp_path: Path) -> tuple[Path, Checkout]:
    """A development repository with one commit, and an installation cloned from it."""
    development = tmp_path / "development"
    development.mkdir()
    _git(development, "init", "-q", "-b", "main")
    _git(development, "config", "user.email", "dev@example.com")
    _git(development, "config", "user.name", "Dev")
    _app(development)
    # The repository's own ignores for what a build leaves in the checkout.
    (development / ".gitignore").write_text(
        "apps/desktop/out/\nnode_modules/\napps/desktop/.atlas-build.lock\n", encoding="utf-8"
    )
    _git(development, "add", ".")
    _git(development, "commit", "-q", "-m", "Step 10b closed")
    installation = tmp_path / "installation"
    _git(tmp_path, "clone", "-q", str(development), str(installation))
    return development, Checkout(installation)


def _commit(development: Path, name: str, message: str, body: str = "") -> None:
    (development / name).write_text(message + "\n", encoding="utf-8")
    _git(development, "add", name)
    _git(development, "commit", "-q", "-m", message, *(["-m", body] if body else []))


@needs_git
def test_an_update_brings_main_syncs_python_and_rebuilds_the_app(tmp_path: Path) -> None:
    development, checkout = _repositories(tmp_path)
    _commit(development, "apps/desktop/src/picker.tsx", "Step 11: the picker")
    _commit(development, "NOTES.md", "Step 11: a fix found in use")
    runner = Recorder()

    updated = update(checkout, runner, served=[])
    again = update(checkout, runner, served=[])

    assert updated.before != updated.after
    assert updated.after == _git(development, "rev-parse", "HEAD")
    assert [line.split(" ", 1)[1] for line in updated.commits] == [
        "Step 11: the picker",
        "Step 11: a fix found in use",
    ]
    assert (checkout.root / "NOTES.md").is_file()
    # The app is built by a new process, on the code the pull brought, every time.
    assert runner.ran == [
        "uv sync --locked --no-install-project --inexact --all-extras",
        "uv run --locked --no-sync python -m atlas desktop --build-only",
        "uv sync --locked --no-install-project --inexact --all-extras",
        "uv run --locked --no-sync python -m atlas desktop --build-only",
    ]
    assert (again.before, again.commits) == (again.after, [])


@needs_git
def test_a_failed_step_after_the_pull_goes_back_to_the_commit_before(tmp_path: Path) -> None:
    """The update rolls back with git reset --hard when its check fails."""
    development, checkout = _repositories(tmp_path)
    before = _git(checkout.root, "rev-parse", "HEAD")
    _commit(development, "apps/desktop/src/broken.tsx", "Step 11: broken")
    runner = Recorder(fail="uv run --locked --no-sync python -m atlas desktop")

    with pytest.raises(InstallationError, match="atlas desktop --build-only"):
        update(checkout, runner, served=[])

    assert _git(checkout.root, "rev-parse", "HEAD") == before
    assert not (checkout.root / "apps/desktop/src/broken.tsx").exists()
    # Back on the old commit, its Python and its app are made current again.
    assert runner.ran == [
        "uv sync --locked --no-install-project --inexact --all-extras",
        "uv run --locked --no-sync python -m atlas desktop --build-only",
        "uv sync --locked --no-install-project --inexact --all-extras",
        "uv run --locked --no-sync python -m atlas desktop --build-only",
    ]


@needs_git
def test_an_update_refuses_an_open_app_and_local_changes(tmp_path: Path) -> None:
    development, checkout = _repositories(tmp_path)
    _commit(development, "NOTES.md", "Step 11")
    before = _git(checkout.root, "rev-parse", "HEAD")

    with pytest.raises(InstallationError, match=r"The app is open \(a server serves default\)"):
        update(checkout, Recorder(), served=["default"])
    (checkout.desktop / "src" / "main.tsx").write_text("edited here\n", encoding="utf-8")
    with pytest.raises(InstallationError, match="has local changes"):
        update(checkout, Recorder(), served=[])

    assert _git(checkout.root, "rev-parse", "HEAD") == before


@needs_git
def test_an_update_under_a_build_is_refused(tmp_path: Path) -> None:
    _, checkout = _repositories(tmp_path)
    with locked(checkout.desktop / ".atlas-build.lock"), pytest.raises(InstallationBusyError):
        update(checkout, Recorder(), served=[])


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
def test_the_updates_sync_keeps_the_project_installed_and_its_build_runs_the_environments_python(
    tmp_path: Path,
) -> None:
    """The real uv on a small editable project, as the installation is (found in use on
    12/10/2026: an exact sync without the project uninstalled Atlas, and a bare `atlas` then ran
    Atlas V3's from the PATH)."""
    project = tmp_path / "demo"
    package = project / "src" / "demo"
    package.mkdir(parents=True)
    for module, source in (("__init__.py", ""), ("__main__.py", "print('demo here')\n")):
        (package / module).write_text(source, encoding="utf-8")
    (project / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.0.1"\nrequires-python = ">=3.13"\n\n'
        '[build-system]\nrequires = ["uv_build>=0.12,<0.13"]\nbuild-backend = "uv_build"\n',
        encoding="utf-8",
    )
    environ = {
        name: value
        for name, value in os.environ.items()
        if name not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
    }

    found = shutil.which("uv") or "uv"

    def uv(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 - the uv on the PATH, fixed arguments
            [found, *argv], cwd=project, env=environ, capture_output=True, text=True, check=False
        )

    assert uv("sync").returncode == 0
    installed = uv("pip", "list", "--format", "json")

    synced = uv(*SYNC_COMMAND[1:])
    after = uv("pip", "list", "--format", "json")
    ran = uv("run", "--locked", "--no-sync", "python", "-m", "demo")

    assert synced.returncode == 0, synced.stderr
    assert '"demo"' in installed.stdout
    assert '"demo"' in after.stdout, synced.stderr
    assert ran.stdout.strip() == "demo here", ran.stderr
    assert BUILD_COMMAND[:6] == ("uv", "run", "--locked", "--no-sync", "python", "-m")


# -- the toolchain ---------------------------------------------------------------------------------


def _program(folder: Path, name: str, says: str) -> str:
    """A real program that prints what it is and the first folder on its PATH."""
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        program = folder / f"{name}.cmd"
        program.write_text(f"@echo off\r\necho {says}\r\necho %PATH%\r\n", encoding="utf-8")
    else:
        program = folder / name
        program.write_text(f'#!/bin/sh\necho {says}\necho "$PATH"\n', encoding="utf-8")
        program.chmod(0o755)
    return program.name


def _installed(tmp_path: Path, programs: dict[str, str]) -> Checkout:
    """An installation as the installer leaves it: the checkout and `tools/` beside it."""
    tools = tmp_path / "atlas-install" / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    listed = ", ".join(f'"{name}": "{where}"' for name, where in programs.items())
    (tools / "toolchain.json").write_text(
        f'{{"version": 1, "programs": {{{listed}}}}}', encoding="utf-8"
    )
    return _app(tmp_path / "atlas-install" / "atlas")


#: A broken toolchain file, and the words its refusal must hold.
_BROKEN_TOOLCHAINS = [
    ("{", "damaged"),
    ("[]", "not an object"),
    ('{"version": 2, "programs": {}}', "version 2"),
    ('{"version": 1}', "no programs"),
    ('{"version": 1, "programs": {"UV": "uv"}}', "a program named 'UV'"),
    ('{"version": 1, "programs": {"uv": ""}}', "uv's path"),
    ('{"version": 1, "programs": {"uv": "uv\\\\uv.exe"}}', "uv's path"),
    ('{"version": 1, "programs": {"uv": "/usr/bin/uv"}}', "leaves the tools folder"),
    ('{"version": 1, "programs": {"uv": "../uv"}}', "leaves the tools folder"),
    ('{"version": 1, "programs": {"uv": "C:/uv.exe"}}', "leaves the tools folder"),
]


def test_the_toolchain_file_names_programs_under_the_tools_folder() -> None:
    programs = parse_toolchain(
        '{"version": 1, "programs": {"uv": "uv-0.12.5/uv.exe", "gh": "gh-2.101.0/bin/gh"}}'
    )
    assert sorted((name, str(programs[name])) for name in programs) == [
        ("gh", "gh-2.101.0/bin/gh"),
        ("uv", "uv-0.12.5/uv.exe"),
    ], programs


@pytest.mark.parametrize(("text", "said"), _BROKEN_TOOLCHAINS)
def test_a_damaged_toolchain_file_is_refused_and_says_to_run_the_installer(
    text: str, said: str
) -> None:
    with pytest.raises(InstallationError, match="run the installer again") as raised:
        parse_toolchain(text)
    assert said in str(raised.value)


def test_a_development_checkout_has_no_toolchain_and_runs_from_the_path(tmp_path: Path) -> None:
    toolchain = Toolchain.of(_app(tmp_path / "development"))

    assert (toolchain.folder, toolchain.programs) == (None, {})
    assert toolchain.environment({"PATH": "a"}) == {"PATH": "a"}


def test_a_program_the_toolchain_names_but_lacks_is_refused(tmp_path: Path) -> None:
    checkout = _installed(tmp_path, {"uv": "uv-0.12.5/uv.exe"})

    with pytest.raises(InstallationError, match="uv is missing"):
        Toolchain.of(checkout)


def test_the_installations_programs_run_before_the_paths_and_their_folders_lead_its_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The installer's uv wins over one on the PATH (a PATH uv 0.6 and a
    Chocolatey shim broke installs), and a program beside it (npx beside node) is found through
    the PATH it leads."""
    tools = tmp_path / "atlas-install" / "tools"
    uv = _program(tools / "uv-0.12.5", "uv", "pinned uv")
    node = _program(tools / "node-v24" / "bin", "node", "pinned node")
    _program(tools / "node-v24" / "bin", "npx", "pinned npx")
    _program(tmp_path / "machine", "uv", "the machine's uv")
    checkout = _installed(tmp_path, {"uv": f"uv-0.12.5/{uv}", "node": f"node-v24/bin/{node}"})
    monkeypatch.setenv("PATH", str(tmp_path / "machine") + os.pathsep + os.environ["PATH"])
    runner = Runner(Toolchain.of(checkout))

    said_uv = runner.output(["uv", "--version"], checkout.root).splitlines()
    said_npx = runner.output(["npx"], checkout.root).splitlines()

    assert said_uv[0].strip() == "pinned uv"
    assert said_npx[0].strip() == "pinned npx"
    first, second, third = said_uv[1].strip().split(os.pathsep)[:3]
    assert [Path(first), Path(second)] == [tools / "uv-0.12.5", tools / "node-v24" / "bin"]
    assert Path(third) == tmp_path / "machine"  # the user's PATH follows, unchanged


# -- the update from the app -------------------------------------------------------------------


def _installation(tmp_path: Path) -> tuple[Path, Checkout, Recorder]:
    """A development repository, an installation cloned from it with the installer's toolchain
    beside it (no program of its own: git comes from the PATH), and its runner, unattended."""
    development, checkout = _repositories(tmp_path)
    tools = checkout.root.parent / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "toolchain.json").write_text('{"version": 1, "programs": {}}', encoding="utf-8")
    runner = Recorder()
    runner.toolchain = Toolchain.of(checkout)
    runner.unattended = True
    return development, checkout, runner


@needs_git
def test_the_app_looks_for_an_update_only_in_an_installation_and_counts_what_main_brings(
    tmp_path: Path,
) -> None:
    """The source check: fetch, then how many commits the branch's upstream has that the
    installation lacks, the newest ones' subjects oldest first; a development checkout, with no
    toolchain beside it, is updated with git and offered nothing."""
    development, checkout, runner = _installation(tmp_path)
    assert check(checkout, Recorder()) == UpdateStatus(False, reason="development")
    assert not check(checkout, runner).available

    _commit(development, "NOTES.md", "Step 18: the button")
    _commit(
        development,
        "apps/desktop/src/update.tsx",
        "Step 18: the hand-off",
        "Notes-pt-BR: Passo 18: a passagem\nCo-Authored-By: Someone <someone@example.com>",
    )
    found = check(checkout, runner)

    assert (found.supported, found.available, found.behind, found.dirty) == (True, True, 2, False)
    assert found.commits == ("Step 18: the button", "Step 18: the hand-off")
    assert found.commits_pt_br == ("", "Passo 18: a passagem"), "one with none keeps its subject"
    assert found.current == _git(checkout.root, "rev-parse", "HEAD")
    assert found.target == _git(development, "rev-parse", "HEAD")
    assert json.loads(found.to_json())["available"] is True
    (checkout.desktop / "src" / "main.tsx").write_text("edited here\n", encoding="utf-8")
    assert check(checkout, runner).dirty, "the update would refuse it: the dialog says so"

    _git(checkout.root, "checkout", "-q", "-b", "alone")
    assert check(checkout, runner) == UpdateStatus(False, reason="no-upstream")


@needs_git
def test_a_look_that_cannot_reach_main_offers_nothing_and_says_why(tmp_path: Path) -> None:
    development, checkout, runner = _installation(tmp_path)
    shutil.rmtree(development, onexc=_writable)

    found = check(checkout, runner)

    assert (found.supported, found.available) == (True, False)
    assert "git fetch" in found.error


def _writable(function: Callable[[str], object], path: str, _error: BaseException) -> None:
    """Git's objects are read-only on Windows: made writable, then removed."""
    os.chmod(path, 0o700)  # noqa: PTH101 - the callback is given a str
    function(path)


def _app_for(seconds: float) -> Parent:
    """A real process standing in for the app, gone after `seconds`, and reaped so it is not left
    a zombie."""
    process = subprocess.Popen(  # noqa: S603 - this interpreter, a fixed line
        [sys.executable, "-c", f"import time; time.sleep({seconds})"]
    )
    threading.Thread(target=process.wait, daemon=True).start()
    return Parent(process.pid)


def _opener(checkout: Checkout, opened: Path) -> None:
    """An Electron that, opened, only writes what it was opened with to `opened`: the folder,
    then the flags (cmd's `%*` keeps a flag's `=`, which `%1` would split)."""
    package = checkout.desktop / "node_modules" / "electron"
    (package / "dist").mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        name = "electron.cmd"
        script = f'@echo off\r\necho %*> "{opened}"\r\n'
    else:
        name = "electron"
        script = f'#!/bin/sh\necho "$@" > "{opened}"\n'
    (package / "dist" / name).write_text(script, encoding="utf-8")
    (package / "dist" / name).chmod(0o755)
    (package / "path.txt").write_text(name, encoding="utf-8")


def _opened(opened: Path, seconds: float = 20) -> list[str]:
    """What the app was opened again with, once it was: its folder first."""
    deadline = time.monotonic() + seconds
    while not opened.exists() or not opened.read_text(encoding="utf-8").strip():
        if time.monotonic() > deadline:
            return []
        time.sleep(0.05)
    return opened.read_text(encoding="utf-8").split()


@needs_git
def test_the_handed_off_update_waits_for_the_app_and_its_servers_then_updates_and_reopens(
    tmp_path: Path,
) -> None:
    """The checkout hand-off: the app hands the update off as it closes; it waits for the
    app to be gone and the homes to be let go, updates, writes how it went before anything
    claims it, and opens the app again. Its marker says an update runs while it does, with its
    process's id."""
    development, checkout, runner = _installation(tmp_path)
    _commit(development, "NOTES.md", "Step 18: the hand-off")
    homes = tmp_path / "homes"
    homes.mkdir()
    marker, result, opened = tmp_path / "running.json", tmp_path / "result.json", tmp_path / "o"
    _opener(checkout, opened)
    app = _app_for(1.5)
    held = threading.Event()

    def hold_the_home() -> None:
        with serving(Home(homes)):
            held.set()
            time.sleep(2.5)

    threading.Thread(target=hold_the_home, daemon=True).start()
    held.wait(5)
    done: list[HandedOff] = []
    profile = f"--user-data-dir={tmp_path / 'profile'}"
    with (tmp_path / "update.log").open("w", encoding="utf-8") as log:
        runner.log = log
        worker = threading.Thread(
            target=lambda: done.append(
                hand_off(
                    checkout,
                    runner,
                    app,
                    homes,
                    marker=marker,
                    result=result,
                    reopen_flags=(profile, "--lang=pt-BR"),
                )
            )
        )
        worker.start()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert json.loads(marker.read_text(encoding="utf-8"))["pid"] == os.getpid()
        worker.join(60)

    [ended] = done
    assert (ended.ok, ended.error, ended.reopen) == (True, "", "")
    assert ended.after == _git(development, "rev-parse", "HEAD")
    assert [line.split(" ", 1)[1] for line in ended.commits] == ["Step 18: the hand-off"]
    assert json.loads(result.read_text(encoding="utf-8")) == json.loads(ended.to_json())
    assert not marker.exists()
    # Opened again on the updated app, as it was opened.
    folder, *flags = _opened(opened)
    assert (Path(folder), flags) == (checkout.desktop, [profile, "--lang=pt-BR"])
    logged = (tmp_path / "update.log").read_text(encoding="utf-8")
    assert "The app closed." in logged
    assert "$ git pull --ff-only" in logged
    assert runner.ran == [
        "uv sync --locked --no-install-project --inexact --all-extras",
        "uv run --locked --no-sync python -m atlas desktop --build-only",
    ]


@needs_git
def test_a_handed_off_update_that_fails_goes_back_says_why_and_reopens_the_app(
    tmp_path: Path,
) -> None:
    development, checkout, _ = _installation(tmp_path)
    before = _git(checkout.root, "rev-parse", "HEAD")
    _commit(development, "apps/desktop/src/broken.tsx", "Step 18: broken")
    runner = Recorder(fail="uv run --locked --no-sync python -m atlas desktop")
    runner.toolchain = Toolchain.of(checkout)
    result, opened = tmp_path / "result.json", tmp_path / "o"
    _opener(checkout, opened)
    (tmp_path / "homes").mkdir()

    ended = hand_off(
        checkout,
        runner,
        _app_for(0.2),
        tmp_path / "homes",
        marker=tmp_path / "running.json",
        result=result,
    )

    assert not ended.ok
    assert "atlas desktop --build-only" in ended.error
    assert _git(checkout.root, "rev-parse", "HEAD") == before, "back where it was"
    assert json.loads(result.read_text(encoding="utf-8"))["ok"] is False
    assert Path(_opened(opened)[0]) == checkout.desktop, "the app opens again, on what it was"


class _AlwaysOn:
    """The always-on homes' servers, holding a home until stopped, as a closed app leaves them."""

    def __init__(self, homes: Path, runner: Recorder) -> None:
        self.runner = runner
        self.said: list[str] = []
        self._serving = serving(Home(homes))
        self._serving.__enter__()

    def stop(self) -> list[str]:
        self.said.append(f"stop, after {len(self.runner.ran)} steps")
        self._serving.__exit__(None, None, None)
        return ["default"]

    def start(self) -> None:
        self.said.append(f"start, after {len(self.runner.ran)} steps")


@needs_git
@pytest.mark.parametrize("fails", [False, True])
def test_a_handed_off_update_stops_the_always_on_homes_and_starts_them_after(
    tmp_path: Path, fails: bool
) -> None:
    """An always-on home outlives the app: left serving, the update would wait for it and then
    refuse (found writing step 15, 19/10/2026). It is stopped before, and started after the update
    whatever it did, rolled back too."""
    development, checkout, _ = _installation(tmp_path)
    _commit(development, "NOTES.md", "Step 15")
    runner = Recorder(fail="uv run" if fails else None)
    runner.toolchain = Toolchain.of(checkout)
    homes = tmp_path / "homes"
    homes.mkdir()
    services = _AlwaysOn(homes, runner)
    _opener(checkout, tmp_path / "o")

    with (tmp_path / "update.log").open("w", encoding="utf-8") as log:
        runner.log = log
        ended = hand_off(
            checkout,
            runner,
            _app_for(0.2),
            homes,
            marker=tmp_path / "running.json",
            result=tmp_path / "result.json",
            free_seconds=1,
            services=services,
        )

    assert ended.ok is not fails
    assert services.said == ["stop, after 0 steps", f"start, after {len(runner.ran)} steps"]
    assert len(runner.ran) == (4 if fails else 2)
    assert "Stopped the always-on homes: default." in (tmp_path / "update.log").read_text(
        encoding="utf-8"
    )


class _Broken(Recorder):
    """A runner whose Python sync breaks in a way nobody expected."""

    def run(self, command: Sequence[str], cwd: Path) -> None:
        if command[0] == "uv":
            raise RuntimeError("the disk went away")
        super().run(command, cwd)


@needs_git
def test_whatever_breaks_the_handed_off_update_says_so_and_still_reopens_the_app(
    tmp_path: Path,
) -> None:
    """The hand-off is the edge: nobody else would hear a failure it raised, and the app, closed
    for it, would stay closed."""
    development, checkout, _ = _installation(tmp_path)
    _commit(development, "NOTES.md", "Step 18")
    runner = _Broken()
    runner.toolchain = Toolchain.of(checkout)
    result, opened = tmp_path / "result.json", tmp_path / "o"
    _opener(checkout, opened)
    (tmp_path / "homes").mkdir()

    ended = hand_off(
        checkout,
        runner,
        _app_for(0.2),
        tmp_path / "homes",
        marker=tmp_path / "running.json",
        result=result,
    )

    assert (ended.ok, ended.error) == (False, "RuntimeError: the disk went away")
    assert json.loads(result.read_text(encoding="utf-8"))["error"] == ended.error
    assert Path(_opened(opened)[0]) == checkout.desktop


@needs_git
def test_a_handed_off_update_leaves_an_app_that_does_not_close_alone(tmp_path: Path) -> None:
    development, checkout, runner = _installation(tmp_path)
    before = _git(checkout.root, "rev-parse", "HEAD")
    _commit(development, "NOTES.md", "Step 18")
    marker, opened = tmp_path / "running.json", tmp_path / "o"
    _opener(checkout, opened)
    (tmp_path / "homes").mkdir()

    ended = hand_off(
        checkout,
        runner,
        _app_for(30),
        tmp_path / "homes",
        marker=marker,
        result=tmp_path / "result.json",
        close_seconds=0.5,
    )

    assert (ended.ok, ended.error) == (False, "The app did not close, so nothing was updated.")
    assert _git(checkout.root, "rev-parse", "HEAD") == before
    assert not marker.exists()
    assert _opened(opened, seconds=1) == [], "it is still open: nothing opens another"


def test_the_hand_off_loads_nothing_but_the_standard_library_and_atlas() -> None:
    """On Windows `uv sync` cannot replace a package a running process loaded, and the hand-off
    runs while it syncs: `python -m atlas.installation` must load nothing from the environment
    `uv sync` replaces (`atlas update`, from a terminal, loads every package there). The standard
    library and Atlas's own code live elsewhere; what the interpreter loads before (an
    environment's `.pth` files) is the interpreter's, not the hand-off's. Read by where each module
    comes from: a name list misses the standard library's own generated modules (Linux's
    `_sysconfigdata_*`, found by the CI on 18/10/2026)."""
    code = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import atlas.installation.__main__\n"
        "environment = sys.prefix if sys.prefix != sys.base_prefix else ''\n"
        "added = [sys.modules[name] for name in set(sys.modules) - before]\n"
        "files = [getattr(module, '__file__', None) or '' for module in added]\n"
        "print(sorted(file for file in files if environment and file.startswith(environment)))\n"
    )
    said = subprocess.run(  # noqa: S603 - this interpreter, a fixed script
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert sys.prefix != sys.base_prefix, "the tests run in the project's environment"
    assert said.stdout.strip() == "[]"


def test_the_check_runs_as_a_module_and_offers_a_development_checkout_nothing() -> None:
    said = subprocess.check_output([sys.executable, "-m", "atlas.installation", "check"], text=True)
    status = json.loads(said)
    assert (status["supported"], status["reason"], status["available"]) == (
        False,
        "development",
        False,
    )
