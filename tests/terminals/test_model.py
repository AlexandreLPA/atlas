"""The server terminal's rules: the shell it picks, how each shell is made to write its marks, and
the environment it gets."""

import base64

import pytest

from atlas.terminals import (
    ShellFamily,
    bash_script,
    choose_shell,
    family_of,
    launch_for,
    powershell_script,
    shell_environment,
    shell_name,
    zsh_script,
)

NONCE = "0123456789abcdef0123456789abcdef"


@pytest.mark.parametrize(
    ("program", "family"),
    [
        ("C:\\Program Files\\PowerShell\\7\\pwsh.exe", ShellFamily.POWERSHELL),
        ("C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe", ShellFamily.POWERSHELL),
        ("C:\\Windows\\System32\\cmd.exe", ShellFamily.CMD),
        ("/bin/bash", ShellFamily.BASH),
        ("/usr/bin/zsh", ShellFamily.ZSH),
        ("/usr/bin/fish", ShellFamily.OTHER),
    ],
)
def test_a_shell_is_known_by_its_name(program: str, family: ShellFamily) -> None:
    assert family_of(program) is family


def test_the_tab_shows_the_shell_without_its_folder_or_exe() -> None:
    assert shell_name("C:\\Program Files\\PowerShell\\7\\pwsh.exe") == "pwsh"
    assert shell_name("/bin/bash") == "bash"


def test_each_shell_starts_so_it_writes_its_marks() -> None:
    powershell = launch_for("pwsh.exe", NONCE, "C:\\Temp\\atlas-terminal\\n")
    assert powershell.marks
    assert powershell.args[:3] == ("-NoLogo", "-NoExit", "-EncodedCommand")
    decoded = base64.b64decode(powershell.args[3]).decode("utf-16-le")
    assert decoded == powershell_script(NONCE)
    assert f"$global:__atlasNonce = '{NONCE}'" in decoded

    bash = launch_for("/bin/bash", NONCE, "/home/u/.cache/atlas-terminal/n")
    assert bash.args == ("--rcfile", "/home/u/.cache/atlas-terminal/n/bashrc", "-i")
    assert bash.files == {"bashrc": bash_script(NONCE)}
    # printf's own escapes, as the shell reads them.
    assert r"printf '\033]133;D;%s;nonce=%s\007'" in bash.files["bashrc"]
    assert (
        'PROMPT_COMMAND="__atlas_begin${PROMPT_COMMAND:+;$PROMPT_COMMAND};__atlas_end"'
        in (bash.files["bashrc"])
    )

    zsh = launch_for("/bin/zsh", NONCE, "/home/u/.cache/atlas-terminal/n")
    assert dict(zsh.env) == {"ZDOTDIR": "/home/u/.cache/atlas-terminal/n"}
    assert zsh.files == {".zshrc": zsh_script(NONCE)}

    assert not launch_for("cmd.exe", NONCE, "x").marks
    assert launch_for("/usr/bin/fish", NONCE, "x").args == ("-i",)


@pytest.mark.parametrize("nonce", ["", "short", "'; rm -rf ~; '" + "0" * 16, NONCE.upper()])
def test_a_nonce_is_only_hex_since_it_is_written_into_the_script(nonce: str) -> None:
    with pytest.raises(ValueError, match="hexadecimal"):
        launch_for("/bin/bash", nonce, "/home/u/x")


def test_the_shell_named_wins_when_it_is_there() -> None:
    def which(name: str) -> str | None:
        return {"fish": "/usr/bin/fish", "pwsh.exe": "C:\\pwsh\\pwsh.exe"}.get(name)

    def exists(path: str) -> bool:
        return path in {"/bin/bash", "/bin/zsh"}

    assert (
        choose_shell({"ATLAS_TERMINAL_SHELL": "fish"}, "linux", exists=exists, which=which)
        == "/usr/bin/fish"
    )
    assert choose_shell({"SHELL": "/bin/bash"}, "linux", exists=exists, which=which) == "/bin/bash"
    assert choose_shell({"SHELL": "/nope"}, "linux", exists=exists, which=which) == "/bin/zsh"
    assert choose_shell({}, "linux", exists=lambda _: False, which=which) == "/bin/sh"
    # On Windows $SHELL (Git Bash's) is not the user's choice: PowerShell 7, then 5.1, then cmd.
    windows = {"SHELL": "/usr/bin/bash", "SystemRoot": "C:\\Windows", "COMSPEC": "C:\\cmd.exe"}
    assert choose_shell(windows, "win32", exists=exists, which=which) == "C:\\pwsh\\pwsh.exe"
    builtin = "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
    assert (
        choose_shell(windows, "win32", exists=lambda path: path == builtin, which=lambda _: None)
        == builtin
    )
    assert choose_shell(windows, "win32", exists=exists, which=lambda _: None) == "C:\\cmd.exe"


def test_the_shell_gets_the_users_environment_without_the_servers_token_or_environment() -> None:
    """Found moving the terminal into the server: the server runs under `uv run`, so its PATH
    starts with Atlas's environment, and a container's server carries its token."""
    given = {
        "PATH": "/home/u/atlas/.venv/bin:/usr/bin:/bin",
        "VIRTUAL_ENV": "/home/u/atlas/.venv",
        "ATLAS_SERVER_TOKEN": "secret",
        "ATLAS_HOME": "/home/u/.atlas",
        "npm_config_cache": "x",
        "FORCE_COLOR": "0",
        "LANG": "pt_BR.UTF-8",
    }

    kept = shell_environment(given, "linux", "/home/u/atlas/.venv/bin/")

    assert kept["PATH"] == "/usr/bin:/bin"
    assert "VIRTUAL_ENV" not in kept
    assert "ATLAS_SERVER_TOKEN" not in kept
    assert kept["ATLAS_HOME"] == "/home/u/.atlas"
    assert "npm_config_cache" not in kept
    assert "FORCE_COLOR" not in kept
    assert (kept["TERM"], kept["COLORTERM"], kept["TERM_PROGRAM"]) == (
        "xterm-256color",
        "truecolor",
        "Atlas",
    )
    assert kept["LC_CTYPE"] == "pt_BR.UTF-8"

    windows = shell_environment(
        {"Path": "C:\\A\\.venv\\Scripts;C:\\Windows"}, "win32", "c:\\a\\.venv\\scripts"
    )
    assert windows["Path"] == "C:\\Windows"
    assert (windows["MSYS_NO_PATHCONV"], windows["MSYS2_ARG_CONV_EXCL"]) == ("1", "*")
    assert shell_environment({}, "darwin")["LC_CTYPE"] == "UTF-8"
