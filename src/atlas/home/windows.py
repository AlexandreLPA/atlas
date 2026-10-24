"""Windows adapter: folders that only their owner can open, through access control lists.

`os.chmod` does not do this on Windows: it only flips the read-only attribute. What does it is
removing inheritance and leaving a single entry, the owner's. Atlas V3 learned why it matters: on
one machine `%TEMP%` gave Modify to a sandbox group by inheritance, so every file created there
was open to that group.

The owner is named by security identifier (SID), taken from the process token by `whoami`, and not
by the user name in the environment: the SID does not depend on the language of the system or on
which variables the process inherited.
"""

import csv
import subprocess
import sys
from pathlib import Path

from atlas.home.errors import HomeFolderError

assert sys.platform == "win32"  # noqa: S101 - this module exists only on Windows

#: `icacls` and `whoami` write in the console's code page, which names principals in the system
#: language ("AUTORIDADE NT\SISTEMA" on a Portuguese Windows).
_CONSOLE_ENCODING = "oem"


def _run(arguments: list[str]) -> str:
    try:
        # Fixed system tools found on PATH; no argument comes from user text.
        completed = subprocess.run(  # noqa: S603
            arguments,
            check=True,
            capture_output=True,
            text=True,
            encoding=_CONSOLE_ENCODING,
            errors="replace",
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise HomeFolderError(f"Could not run {arguments[0]}: {error}.") from error
    return completed.stdout


def _current_user() -> tuple[str, str]:
    """The name and the security identifier of the account running this process."""
    output = _run(["whoami", "/user", "/fo", "csv", "/nh"])
    for row in csv.reader(output.splitlines()):
        if len(row) == 2 and row[1].startswith("S-1-"):
            return row[0], row[1]
    raise HomeFolderError(f"whoami did not report this account's SID: {output!r}.")


def current_user_sid() -> str:
    """The security identifier of the account running this process."""
    return _current_user()[1]


def close_folder(folder: Path) -> None:
    """Leave the owner as the only principal of `folder` and of everything created in it.

    Removing inheritance is not always enough: a folder whose parent passes nothing down is made
    with the account's default access list as entries of its own (the owner, SYSTEM,
    Administrators and OWNER RIGHTS), found by CI on 13/10/2026 on GitHub's Windows runner. Those
    are removed by the names icacls itself printed, so the system's language does not matter."""
    name, sid = _current_user()
    _run(["icacls", str(folder), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F"])
    for other in principals(folder):
        if other.casefold() != name.casefold():
            _run(["icacls", str(folder), "/remove:g", other])


#: The policies only a mandatory label carries (no write, read or execute up). A folder made by an
#: elevated process gets one, `Mandatory Label\High Mandatory Level:(OI)(NP)(IO)(NW)`: an
#: integrity level, not access for anyone. Its name is in the system language ("Rótulo
#: Obrigatório" on a Portuguese Windows), the flags are not. Found by CI on 13/10/2026, whose
#: runner runs elevated: every private folder read as open to a second principal.
_LABEL_POLICIES = ("(NW)", "(NR)", "(NX)")


def principals(path: Path) -> list[str]:
    """Who has any access to `path`, so that tests prove the access list instead of assuming it."""
    found: list[str] = []
    for line in _run(["icacls", str(path)]).splitlines():
        rest = line.replace(str(path), "", 1).strip()
        if ":" in rest and "(" in rest and not any(flag in rest for flag in _LABEL_POLICIES):
            found.append(rest.split(":", 1)[0].strip())
    return found
