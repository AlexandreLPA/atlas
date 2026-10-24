"""Prove the app's update for real, on a temporary installation of this machine. No model is
called, so no credit is spent.

    uv run python scripts/prove_update.py

A bare repository stands for GitHub, and an installation is cloned from it as the installer
clones it, the machine's own installation's toolchain linked beside it, its Python synced and its
app built. Then `main` gets a commit that changes the app's packages and its code, so the update
installs the packages again and rebuilds the app while the app is closed: on Windows, Electron's
own program is among what `npm ci` replaces. The real app is opened on that installation, not
under the end-to-end flag, with a temporary home and a folder of its own (`--user-data-dir`:
Electron reads no `APPDATA`, and the user's app, open meanwhile, holds the single-instance lock of
the folder it would otherwise take; the first run of this proof found it, the app closing at
once). `APPDATA` is temporary too, so the update writes its Start Menu shortcut there, not into
the user's. Its button is clicked (`apps/desktop/e2e/prove/update.prove.ts`), and the real
hand-off takes over, opening the app again on the same folder.

Exit code 0 only when the installation is on the new commit, its packages were installed again
and its app rebuilt, the hand-off said ok, and the app opened again and read how it went.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from prove_desktop import run_proof, say  # the scripts' folder, on the path when run

from atlas.installation import (
    BUILD_STAMP,
    PACKAGES_STAMP,
    Checkout,
    Runner,
    Toolchain,
    build_desktop,
)

ROOT = Path(__file__).resolve().parent.parent
SUBJECT = "Prove the update: the app's packages and its code change"


def main() -> int:
    tools = _installed_tools()
    if tools is None:
        say("No installation of Atlas here to take its toolchain from: run the installer.")
        return 1
    root = Path(tempfile.mkdtemp(prefix="atlas-prove-update-"))
    say(f"the proof's folder: {root}")
    reopened: int | None = None
    try:
        checkout, before, after = _installation(root, tools)
        profile = root / "profile"
        environ = {
            name: value
            for name, value in os.environ.items()
            if not name.startswith("ATLAS_") and name != "ELECTRON_RUN_AS_NODE"
        }
        environ.update(
            APPDATA=str(root / "appdata"),
            ATLAS_HOME=str(root / "home"),
            ATLAS_PROVE_ELECTRON=str(checkout.electron()),
            ATLAS_PROVE_DESKTOP=str(checkout.desktop),
            ATLAS_PROVE_PROFILE=str(profile),
            ATLAS_PROVE_SUBJECT=SUBJECT,
        )
        packages = checkout.read_stamp(PACKAGES_STAMP)
        built = checkout.read_stamp(BUILD_STAMP)
        if run_proof("update.prove.ts", environ) != 0:
            return 1
        log = _one(profile, "update.log")
        ended = _ended(log)
        say(f"the hand-off said: {json.dumps(ended)}")
        result = log.parent / "update-result.json"
        reopened = _reopened(checkout)
        checks = {
            "the hand-off said ok": bool(ended.get("ok")),
            "the installation is on the new commit": _head(checkout.root) == after != before,
            "its packages were installed again": checkout.read_stamp(PACKAGES_STAMP) != packages,
            "its app was rebuilt": checkout.read_stamp(BUILD_STAMP) != built,
            "the app opened again": reopened is not None,
            "the app read how it went": _gone(result),
            "no marker is left": not (log.parent / "update-running.json").exists(),
        }
        for check, held in checks.items():
            say(f"  {'ok ' if held else 'NO '} {check}")
        return 0 if all(checks.values()) else 1
    finally:
        if reopened is not None:
            _stop(reopened)
        _remove(root)


def _installed_tools() -> Path | None:
    """The machine's own installation's tools (the installer's place for them)."""
    base = (
        Path(os.environ.get("LOCALAPPDATA", "")) / "atlas"
        if sys.platform == "win32"
        else Path.home() / ".local" / "share" / "atlas"
    )
    tools = base / "tools"
    return tools if (tools / "toolchain.json").is_file() else None


def _installation(root: Path, tools: Path) -> tuple[Checkout, str, str]:
    """The origin, the installation cloned from it and made ready, and `main` one commit on."""
    origin = root / "origin.git"
    head = _git(ROOT, "rev-parse", "HEAD")
    _git(root, "clone", "--quiet", "--bare", str(ROOT), str(origin))
    _git(origin, "branch", "--force", "main", head)
    _git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    installation = root / "inst"
    installation.mkdir()
    checkout = Checkout(installation / "atlas")
    _git(root, "clone", "--quiet", "-c", "core.longpaths=true", "-c", "core.autocrlf=false",
         "--branch", "main", str(origin), str(checkout.root))  # fmt: skip
    _link(installation / "tools", tools)
    runner = Runner(Toolchain.of(checkout))
    say("syncing the installation's Python…")
    runner.run(["uv", "sync", "--locked", "--all-extras"], checkout.root)
    say("building its app (its packages first)…")
    build_desktop(checkout, runner)
    development = root / "dev"
    _git(root, "clone", "--quiet", "-c", "core.autocrlf=false", str(origin), str(development))
    _git(development, "config", "user.email", "proof@example.com")
    _git(development, "config", "user.name", "Proof")
    lock = development / "apps" / "desktop" / "package-lock.json"
    lock.write_bytes(lock.read_bytes() + b"\n")
    entry = development / "apps" / "desktop" / "src" / "main.tsx"
    entry.write_bytes(entry.read_bytes() + b"// The proof's update.\n")
    _git(development, "commit", "--quiet", "-am", SUBJECT)
    _git(development, "push", "--quiet", "origin", "main")
    return checkout, head, _git(development, "rev-parse", "HEAD")


def _link(link: Path, target: Path) -> None:
    """The toolchain beside the installation, as the installer leaves it: a junction on Windows,
    which needs no administrator."""
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - fixed tools, the proof's own paths
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],  # noqa: S607
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(target, target_is_directory=True)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(  # noqa: S603 - fixed tools, the proof's own paths
        ["git", *args],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _head(checkout: Path) -> str:
    return _git(checkout, "rev-parse", "HEAD")


def _one(folder: Path, name: str, seconds: float = 900) -> Path:
    """The file `name` somewhere under `folder` (the app's own folder, named as Electron names
    it), once the hand-off wrote its last line to it."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for found in folder.rglob(name):
            if "Ended: " in found.read_text(encoding="utf-8", errors="replace"):
                return found
        time.sleep(1)
    raise SystemExit(f"The hand-off wrote no end to {name} in {seconds:.0f} s.")


def _ended(log: Path) -> dict[str, object]:
    line = next(
        line for line in log.read_text(encoding="utf-8").splitlines() if line.startswith("Ended: ")
    )
    found: dict[str, object] = json.loads(line.removeprefix("Ended: "))
    return found


def _reopened(checkout: Checkout, seconds: float = 60) -> int | None:
    """The process id of the app the hand-off opened again on this installation: its main
    process, whose children name their `--type`."""
    program = str(checkout.electron()).lower()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for pid, line in _processes():
            if program in line.lower() and "--type=" not in line:
                return pid
        time.sleep(1)
    return None


#: Each process as `<pid>\t<command line>`, from PowerShell.
_LISTED = 'Get-CimInstance Win32_Process | ForEach-Object { "$($_.ProcessId)`t$($_.CommandLine)" }'


def _processes() -> list[tuple[int, str]]:
    command = (
        ["powershell", "-NoProfile", "-Command", _LISTED]
        if sys.platform == "win32"
        else ["ps", "-eo", "pid=,args="]
    )
    listed = subprocess.run(  # noqa: S603 - fixed tools, a fixed line
        command, capture_output=True, text=True, check=False
    ).stdout
    found: list[tuple[int, str]] = []
    for row in listed.splitlines():
        parts = row.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            found.append((int(parts[0]), parts[1]))
    return found


def _gone(file: Path, seconds: float = 60) -> bool:
    deadline = time.monotonic() + seconds
    while file.exists():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.5)
    return True


def _stop(pid: int) -> None:
    """The app opened again, with its server: the proof's, not the user's."""
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603 - fixed tools, the app's own id
            ["taskkill", "/T", "/F", "/PID", str(pid)],  # noqa: S607
            capture_output=True,
            check=False,
        )
    else:
        subprocess.run(["kill", str(pid)], check=False)  # noqa: S603, S607


def _remove(root: Path) -> None:
    """The proof's folder. Its link to the machine's own toolchain goes first, alone: removing the
    tree must never reach through it into the installation's tools."""
    tools = root / "inst" / "tools"
    for _ in range(40):
        try:
            if tools.is_junction():
                os.rmdir(tools)  # noqa: PTH106 - a junction is removed as a folder, never its target
            elif tools.is_symlink():
                tools.unlink()
            if tools.is_junction() or tools.is_symlink():
                continue
            shutil.rmtree(root, onexc=_writable)
        except FileNotFoundError:
            return
        except PermissionError:
            time.sleep(0.5)
        else:
            return
    say(f"left behind: {root}")


def _writable(function: Callable[[str], object], path: str, _error: BaseException) -> None:
    """Git keeps its objects read-only, which Windows will not delete: writable, then removed (the
    first run of this proof left its folder behind for it)."""
    os.chmod(path, stat.S_IWRITE)  # noqa: PTH101 - the callback is given a str
    function(path)


if __name__ == "__main__":
    raise SystemExit(main())
