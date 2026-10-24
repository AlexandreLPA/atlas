"""The server terminal: shells the server opens on its own machine for a client, the shell chosen
and made to write its marks here, carried over the connection."""

from atlas.terminals.errors import TerminalError, TerminalUnavailableError
from atlas.terminals.model import (
    NONCE,
    SHELL_VARIABLE,
    Launch,
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
from atlas.terminals.service import MOST_PER_CLIENT, Opened, Terminals, environment_folder
from atlas.terminals.store import Pty, Spawn, spawn_pty, unavailable

__all__ = [
    "MOST_PER_CLIENT",
    "NONCE",
    "SHELL_VARIABLE",
    "Launch",
    "Opened",
    "Pty",
    "ShellFamily",
    "Spawn",
    "TerminalError",
    "TerminalUnavailableError",
    "Terminals",
    "bash_script",
    "choose_shell",
    "environment_folder",
    "family_of",
    "launch_for",
    "powershell_script",
    "shell_environment",
    "shell_name",
    "spawn_pty",
    "unavailable",
    "zsh_script",
]
