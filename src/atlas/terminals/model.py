"""The server terminal's rules (step 15, docs/ARCHITECTURE.md, Several machines): which shell it
runs, how that shell is made to write its marks, and the environment it gets. Pure.

They moved here from the app (its `electron/terminal.ts` and `shell-integration.ts` of step 16f),
which keeps reading the marks: the shell now starts on the server's machine, whichever machine the
app is on.

- **The shell** is `ATLAS_TERMINAL_SHELL` when set, else PowerShell 7, then Windows PowerShell 5.1,
  then cmd on Windows; the user's `$SHELL`, then zsh, bash, sh elsewhere.
- **The marks** are OSC 133 with a nonce the app chose: PowerShell wraps `prompt` and PSReadLine's
  `PSConsoleHostReadLine`; bash reads an rc file that hooks the prompt and a `DEBUG` trap that
  skips the prompt's own commands and completion; zsh gets `preexec` and `precmd` hooks through its
  own `ZDOTDIR`; cmd has no hooks, and its commands end when it goes quiet.
- **The environment**: npm's variables and the color switches an
  agent's runner sets taken out, a real terminal's said, Git Bash's path rewriting off on Windows.
  Two more, found moving it into the server: the server's own token never reaches the shell, and
  the environment Atlas runs in is left (`VIRTUAL_ENV` and its folder on the PATH), or every
  terminal's `python` would be Atlas's.
"""

import base64
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PureWindowsPath

#: What a nonce may be: it is written into the shell's start-up script.
NONCE = re.compile(r"^[0-9a-f]{16,64}$")
#: The variable that names the shell.
SHELL_VARIABLE = "ATLAS_TERMINAL_SHELL"
#: Variables that never reach the shell: the server's token.
_SECRET = frozenset({"ATLAS_SERVER_TOKEN"})
_DROPPED = frozenset(
    {"COLORFGBG", "ELECTRON_RUN_AS_NODE", "FORCE_COLOR", "NO_COLOR", "VIRTUAL_ENV"}
)


class ShellFamily(StrEnum):
    POWERSHELL = "powershell"
    BASH = "bash"
    ZSH = "zsh"
    CMD = "cmd"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class Launch:
    """How to start a shell so it writes its marks: its arguments, the environment it adds, the
    files it reads first (by name, in the folder given), and whether it writes marks at all."""

    args: tuple[str, ...]
    marks: bool
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    files: Mapping[str, str] = field(default_factory=dict[str, str])


def program_name(program: str) -> str:
    """The program's own name, without its folder, on any system's path."""
    return PureWindowsPath(program).name if "\\" in program else program.rsplit("/", 1)[-1]


def family_of(program: str) -> ShellFamily:
    name = program_name(program).lower()
    if name.startswith(("pwsh", "powershell")):
        return ShellFamily.POWERSHELL
    if name.startswith("cmd"):
        return ShellFamily.CMD
    if name.startswith("bash"):
        return ShellFamily.BASH
    return ShellFamily.ZSH if name.startswith("zsh") else ShellFamily.OTHER


def shell_name(program: str) -> str:
    """The shell as the tab shows it: `pwsh`, `bash`, `cmd`."""
    name = program_name(program)
    return name[:-4] if name.lower().endswith(".exe") else name


def launch_for(program: str, nonce: str, folder: str) -> Launch:
    """How to start `program` so it writes its marks with `nonce`; `folder` holds the files bash
    and zsh read first (`files` says what to write there)."""
    if not NONCE.match(nonce):
        raise ValueError("A nonce is 16 to 64 lowercase hexadecimal characters.")
    family = family_of(program)
    if family is ShellFamily.POWERSHELL:
        encoded = base64.b64encode(powershell_script(nonce).encode("utf-16-le")).decode()
        return Launch(("-NoLogo", "-NoExit", "-EncodedCommand", encoded), marks=True)
    if family is ShellFamily.BASH:
        rc = _join(folder, "bashrc")
        return Launch(("--rcfile", rc, "-i"), marks=True, files={"bashrc": bash_script(nonce)})
    if family is ShellFamily.ZSH:
        return Launch(
            ("-i",), marks=True, env={"ZDOTDIR": folder}, files={".zshrc": zsh_script(nonce)}
        )
    if family is ShellFamily.CMD:
        return Launch((), marks=False)
    return Launch(("-i",), marks=False)


def _join(folder: str, name: str) -> str:
    separator = "\\" if "\\" in folder and "/" not in folder else "/"
    return folder.rstrip("\\/") + separator + name


def choose_shell(
    env: Mapping[str, str],
    platform: str,
    *,
    exists: Callable[[str], bool],
    which: Callable[[str], str | None],
) -> str:
    """The shell the terminal runs: the one named, when it is there, else the best installed."""
    windows = platform == "win32"
    named = (env.get(SHELL_VARIABLE) or ("" if windows else env.get("SHELL", ""))).strip()
    if named:
        found = named if _absolute(named) and exists(named) else which(named)
        if found:
            return found
    if windows:
        system = (env.get("SystemRoot") or env.get("windir") or "C:\\Windows") + "\\System32"
        builtin = system + "\\WindowsPowerShell\\v1.0\\powershell.exe"
        return (
            which("pwsh.exe")
            or (builtin if exists(builtin) else None)
            or env.get("COMSPEC")
            or system + "\\cmd.exe"
        )
    return next(
        (shell for shell in ("/bin/zsh", "/bin/bash", "/bin/sh") if exists(shell)), "/bin/sh"
    )


def _absolute(path: str) -> bool:
    return path.startswith("/") or bool(re.match(r"^[A-Za-z]:[\\/]", path))


def shell_environment(
    env: Mapping[str, str], platform: str, environment_folder: str | None = None
) -> dict[str, str]:
    """The user's environment for their shell. `environment_folder`: the scripts folder of the
    environment Atlas runs in, taken off the PATH."""
    kept = {
        name: value
        for name, value in env.items()
        if not (
            name.startswith(("npm_config_", "npm_package_")) or name in _DROPPED or name in _SECRET
        )
    }
    if environment_folder:
        for name in ("PATH", "Path"):
            if name in kept:
                kept[name] = _without(kept[name], environment_folder, platform)
    kept["COLORTERM"] = "truecolor"
    kept["TERM"] = "xterm-256color"
    kept["TERM_PROGRAM"] = "Atlas"
    kept["LC_CTYPE"] = env.get("LC_CTYPE") or (
        "UTF-8" if platform == "darwin" else env.get("LANG", "C.UTF-8")
    )
    if platform == "win32":
        kept["MSYS_NO_PATHCONV"] = env.get("MSYS_NO_PATHCONV", "1")
        kept["MSYS2_ARG_CONV_EXCL"] = env.get("MSYS2_ARG_CONV_EXCL", "*")
    return kept


def _without(path: str, folder: str, platform: str) -> str:
    separator = ";" if platform == "win32" else ":"

    def same(entry: str) -> bool:
        left, right = entry.rstrip("\\/"), folder.rstrip("\\/")
        return left.lower() == right.lower() if platform == "win32" else left == right

    return separator.join(entry for entry in path.split(separator) if not same(entry))


# -- the scripts that write the marks -------------------------------------------------------------


def powershell_script(nonce: str) -> str:
    """PowerShell's: the exit code is 0 when `$?` holds, 1 when a new error was recorded (a cmdlet
    failed), else `$LASTEXITCODE` (a program's). Without PSReadLine there is no start mark."""
    return _POWERSHELL.replace("@NONCE@", nonce)


def bash_script(nonce: str) -> str:
    return _BASH.replace("@NONCE@", nonce)


def zsh_script(nonce: str) -> str:
    return _ZSH.replace("@NONCE@", nonce)


_POWERSHELL = r"""
$global:__atlasNonce = '@NONCE@'
$global:__atlasRan = $false
$global:__atlasErrors = $Error.Count
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$global:__atlasPrompt = $function:prompt
function global:prompt {
  $ok = $?
  $code = $global:LASTEXITCODE
  $e = [char]27
  $b = [char]7
  $marks = ''
  if ($global:__atlasRan) {
    if ($ok) { $exit = 0 }
    elseif ($Error.Count -gt $global:__atlasErrors) { $exit = 1 }
    elseif ($null -ne $code) { $exit = $code }
    else { $exit = 1 }
    $marks += "$e]133;D;$exit;nonce=$($global:__atlasNonce)$b"
    $global:__atlasRan = $false
  }
  $global:__atlasErrors = $Error.Count
  $marks += "$e]133;A;nonce=$($global:__atlasNonce)$b"
  $shown = & $global:__atlasPrompt
  $marks + $shown
}
if (Get-Command PSConsoleHostReadLine -ErrorAction SilentlyContinue) {
  $global:__atlasReadLine = $function:PSConsoleHostReadLine
  function global:PSConsoleHostReadLine {
    $line = & $global:__atlasReadLine
    if ($line.Trim().Length -gt 0) {
      $global:__atlasRan = $true
      $global:__atlasErrors = $Error.Count
      [Console]::Write("$([char]27)]133;C;nonce=$($global:__atlasNonce)$([char]7)")
    }
    $line
  }
}
"""

_BASH = r"""
[ -f ~/.bashrc ] && . ~/.bashrc
__atlas_nonce='@NONCE@'
__atlas_ran=0
__atlas_inprompt=1
__atlas_begin() {
  __atlas_status=$?
  __atlas_inprompt=1
}
__atlas_end() {
  if [ "$__atlas_ran" = 1 ]; then
    printf '\033]133;D;%s;nonce=%s\007' "$__atlas_status" "$__atlas_nonce"
    __atlas_ran=0
  fi
  printf '\033]133;A;nonce=%s\007' "$__atlas_nonce"
  __atlas_inprompt=0
}
__atlas_preexec() {
  [ "$__atlas_inprompt" = 1 ] && return
  [ -n "$COMP_LINE" ] && return
  case "$BASH_COMMAND" in __atlas_*) return ;; esac
  [ "$__atlas_ran" = 1 ] && return
  __atlas_ran=1
  printf '\033]133;C;nonce=%s\007' "$__atlas_nonce"
}
PROMPT_COMMAND="__atlas_begin${PROMPT_COMMAND:+;$PROMPT_COMMAND};__atlas_end"
trap '__atlas_preexec' DEBUG
"""

_ZSH = r"""
[ -f "$HOME/.zshrc" ] && ZDOTDIR="$HOME" . "$HOME/.zshrc"
__atlas_nonce='@NONCE@'
__atlas_ran=0
__atlas_preexec() {
  __atlas_ran=1
  printf '\033]133;C;nonce=%s\007' "$__atlas_nonce"
}
__atlas_precmd() {
  local code=$?
  if [ "$__atlas_ran" = 1 ]; then
    printf '\033]133;D;%s;nonce=%s\007' "$code" "$__atlas_nonce"
    __atlas_ran=0
  fi
  printf '\033]133;A;nonce=%s\007' "$__atlas_nonce"
}
autoload -Uz add-zsh-hook
add-zsh-hook preexec __atlas_preexec
# First, so the exit code it reads is the command's, not another hook's.
precmd_functions=(__atlas_precmd ${precmd_functions[@]})
"""
