"""The pseudo-terminal starts a program with exactly the arguments it is given."""

import os
import sys
import time
from pathlib import Path

import pytest

from atlas.terminals.store import spawn_pty


@pytest.mark.skipif(sys.platform != "win32", reason="ConPTY is Windows's")
def test_a_windows_program_gets_its_arguments_once_with_its_own_name_first(tmp_path: Path) -> None:
    # pywinpty puts the program in front of the arguments itself; naming it again made PowerShell 7
    # take its own path for a script to run (seen on GitHub's Windows runners, 24/10/2026).
    program = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "cmd.exe"
    pty = spawn_pty(
        str(program),
        ["/c", "echo", "LINE=%CMDCMDLINE%"],
        cwd=str(tmp_path),
        env=dict(os.environ),
        cols=200,
        rows=30,
    )
    output, deadline = "", time.monotonic() + 15
    try:
        while "\n" not in output.partition("LINE=")[2] and time.monotonic() < deadline:
            chunk = pty.read()
            if not chunk:
                break
            output += chunk
    finally:
        pty.close()
    line = output[output.index("LINE=") + len("LINE=") :].splitlines()[0]
    assert line.lower().count("cmd.exe") == 1, line
    assert line.lower().replace('"', "").startswith(str(program).lower()), line
    assert line.endswith("/c echo LINE=%CMDCMDLINE%"), line
