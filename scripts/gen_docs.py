"""Write the documentation's generated reference from the code (docs/ARCHITECTURE.md, The
documentation):

- the commands, from the command line's own parser (`atlas.__main__.build_parser`): each command,
  what its help says, and what it takes;
- the settings, from their dataclasses (`atlas.home.Settings`): each section of `config.toml`, each
  key with its default, the `ATLAS_*` variable that wins over it, and the `#:` comment above its
  field, which every setting must have.

`tests/docs/test_reference.py` fails when a committed page differs from what this renders, so a
command or a setting that changes fails there before the documentation drifts from the code.

Run with `uv run python scripts/gen_docs.py`; `--check` only says whether the pages are current.
"""

import argparse
import ast
import dataclasses
import inspect
import sys
import types
import typing
from pathlib import Path
from typing import Any, Literal, TypeAliasType, get_args, get_origin

from atlas.__main__ import build_parser
from atlas.docs import PAGES_FOLDER
from atlas.home import EDITABLE, SecretText, Settings

REPOSITORY = Path(__file__).resolve().parents[1]
COMMANDS = PAGES_FOLDER / "reference" / "commands.md"
SETTINGS = PAGES_FOLDER / "reference" / "settings.md"
NOTICE = (
    "<!-- Written by scripts/gen_docs.py from the code. Change the code and run it again; an edit "
    "here is lost. -->"
)

_SubParsers = argparse._SubParsersAction  # pyright: ignore[reportPrivateUsage]


def _quoted(value: str) -> str:
    """A YAML string in double quotes: the site refuses a bare `: ` or a leading `` ` ``."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _header(title: str, description: str, order: int) -> list[str]:
    return [
        "---",
        f"title: {_quoted(title)}",
        f"description: {_quoted(description)}",
        "sidebar:",
        f"  order: {order}",
        "---",
        "",
        NOTICE,
        "",
    ]


# -- commands --------------------------------------------------------------------------------------


def _subcommands(parser: argparse.ArgumentParser) -> list[tuple[str, str, argparse.ArgumentParser]]:
    """A parser's commands, in the order it declares them, each with its help."""
    for action in parser._actions:  # pyright: ignore[reportPrivateUsage]
        if isinstance(action, _SubParsers):
            subparsers = typing.cast("Any", action)
            helps = {choice.dest: choice.help or "" for choice in subparsers._choices_actions}
            return [(name, helps.get(name, ""), sub) for name, sub in subparsers.choices.items()]
    return []


def _arguments(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    return [
        action
        for action in parser._actions  # pyright: ignore[reportPrivateUsage]
        if not isinstance(action, argparse._HelpAction | _SubParsers)  # pyright: ignore[reportPrivateUsage]
    ]


def _value(action: argparse.Action) -> str:
    """How an argument's value is written: `NAME`, `[NAME]`, `NAME ...`."""
    name = action.metavar if isinstance(action.metavar, str) else None
    name = name or (action.dest.upper() if action.option_strings else action.dest)
    match action.nargs:
        case "?":
            return f"[{name}]"
        case "*":
            return f"[{name} ...]"
        case "+":
            return f"{name} ..."
        case _:
            return name


def _usage_of(action: argparse.Action) -> str:
    if not action.option_strings:
        return _value(action)
    names = ", ".join(action.option_strings)
    return names if action.nargs == 0 else f"{names} {_value(action)}"


def _synopsis(path: str, parser: argparse.ArgumentParser) -> str:
    words = [path]
    for action in _arguments(parser):
        if not action.option_strings:
            words.append(_value(action))
            continue
        written = action.option_strings[-1]
        if action.nargs != 0:
            written = f"{written} {_value(action)}"
        words.append(written if action.required else f"[{written}]")
    return " ".join(words)


def _exclusive(parser: argparse.ArgumentParser) -> list[str]:
    notes: list[str] = []
    for group in parser._mutually_exclusive_groups:  # pyright: ignore[reportPrivateUsage]
        names = [f"`{action.option_strings[-1]}`" for action in group._group_actions]  # pyright: ignore[reportPrivateUsage]
        joined = " or ".join(names) if len(names) == 2 else ", ".join(names)
        if group.required:
            notes.append(f"One of {joined} is required.")
        else:
            notes.append(f"{' and '.join(names)} do not go together.")
    return notes


def _command(path: str, help_text: str, parser: argparse.ArgumentParser, depth: int) -> list[str]:
    lines = [f"{'#' * depth} `{path}`", ""]
    if help_text:
        lines += [_sentence(help_text), ""]
    children = _subcommands(parser)
    arguments = _arguments(parser)
    if not children:
        lines += ["```", _synopsis(path, parser), "```", ""]
    if arguments:
        lines += ["| Argument | What it does |", "|---|---|"]
        for action in arguments:
            text = _sentence(action.help or "")
            if action.required and action.option_strings:
                text = f"Required. {text}".strip()
            lines.append(f"| `{_usage_of(action)}` | {_cell(text)} |")
        lines.append("")
        lines += [*_exclusive(parser), ""] if _exclusive(parser) else []
    for name, child_help, child in children:
        lines += _command(f"{path} {name}", child_help, child, min(depth + 1, 4))
    return lines


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    return text if not text or text.endswith((".", "!", "?", ")")) else f"{text}."


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_commands() -> str:
    parser = build_parser()
    lines = _header(
        "Commands",
        "Every `atlas` command and what it takes, written from the command line's own parser.",
        1,
    )
    lines += [
        "Every command runs in the home `atlas home use` chose, unless `--home NAME` comes before "
        "it: `atlas --home work usage`. A plugin that is on may add commands of its own, which "
        "`atlas plugins list` shows.",
        "",
    ]
    for name, help_text, sub in _subcommands(parser):
        lines += _command(f"atlas {name}", help_text, sub, 2)
    return "\n".join(lines).rstrip() + "\n"


# -- settings --------------------------------------------------------------------------------------


def _comments(cls: type) -> dict[str, str]:
    """The `#:` comment above each field of a dataclass, read from its module's source."""
    source = Path(inspect.getfile(cls)).read_text(encoding="utf-8")
    lines = source.splitlines()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ClassDef) and node.name == cls.__name__:
            found: dict[str, str] = {}
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    above: list[str] = []
                    k = statement.lineno - 2
                    while k >= 0 and lines[k].strip().startswith("#:"):
                        above.insert(0, lines[k].strip().removeprefix("#:").strip())
                        k -= 1
                    found[statement.target.id] = " ".join(above)
            return found
    raise LookupError(f"{cls.__name__} is not in {inspect.getfile(cls)}.")


def _unwrap(kind: Any) -> Any:
    if isinstance(kind, TypeAliasType):
        return _unwrap(kind.__value__)
    if get_origin(kind) in (types.UnionType, typing.Union):
        rest = [arg for arg in get_args(kind) if arg is not type(None)]
        return _unwrap(rest[0]) if len(rest) == 1 else kind
    return kind


def _kind(kind: Any) -> str:
    kind = _unwrap(kind)
    if kind is SecretText:
        return "secret"
    if get_origin(kind) is Literal:
        return "one of " + ", ".join(f"`{value}`" for value in get_args(kind))
    if get_origin(kind) is tuple:
        return "list of text"
    return {str: "text", int: "whole number", bool: "`true` or `false`", Path: "path"}.get(
        kind, str(kind)
    )


def _default(field: dataclasses.Field[Any]) -> str:
    if field.default is not dataclasses.MISSING:
        value = field.default
    elif field.default_factory is not dataclasses.MISSING:
        value = field.default_factory()
    else:
        return "required"
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return f"`{str(value).lower()}`"
    if isinstance(value, tuple | list):
        items = typing.cast("tuple[object, ...]", tuple(value))
        return "`[]`" if not items else "`[" + ", ".join(f'"{item}"' for item in items) + "]`"
    if isinstance(value, str):
        return f'`"{value}"`'
    return f"`{value}`"


def render_settings() -> str:
    lines = _header(
        "Settings",
        "Every setting of `config.toml`: its default, the `ATLAS_*` variable that wins over it, "
        "and what it does, written from the code.",
        2,
    )
    lines += [
        "Atlas reads its settings once, when it starts, from four places, each winning over the "
        "one before: the default, the home's `config.toml`, an `ATLAS_*` variable, and a "
        "command-line flag. A variable is named after the setting's place in the file: "
        "`[server] port` is `ATLAS_SERVER_PORT`, and a blank variable counts as unset.",
        "",
        "An unknown key in `config.toml` stops Atlas with an error, so a typo never passes in "
        "silence; an unknown `ATLAS_*` variable is only reported. A secret never goes in the "
        "file: it comes from its variable, which Atlas removes from its environment once read, so "
        "no command the agent runs inherits it. The settings marked *on the settings screen* "
        "also change from the app, with no restart. A plugin keeps its own settings, changed "
        "with `atlas plugins set`.",
        "",
    ]
    editable = {(setting.section, setting.key) for setting in EDITABLE}
    hints = typing.get_type_hints(Settings)
    for section in dataclasses.fields(Settings):
        if section.name != "home":
            lines += render_section(
                section.name, typing.cast("type", hints[section.name]), editable
            )
    return "\n".join(lines).rstrip() + "\n"


def render_section(name: str, cls: type, editable: set[tuple[str, str]]) -> list[str]:
    """One section of `config.toml`: a row per field of its dataclass. A field with no `#:` comment
    above it raises, so every setting says what it does where it is declared."""
    comments = _comments(cls)
    kinds = typing.get_type_hints(cls)
    lines = [f"## [{name}]", ""]
    lines += ["| Key | Value | Default | Variable | What it does |", "|---|---|---|---|---|"]
    for field in dataclasses.fields(cls):
        about = comments.get(field.name, "")
        if not about:
            raise ValueError(
                f"[{name}] {field.name} has no `#:` comment above its field in "
                f"{inspect.getfile(cls)}: every setting says what it does where it is declared."
            )
        variable = f"ATLAS_{name}_{field.name}".upper()
        if (name, field.name) in editable:
            about = f"{about} *On the settings screen.*"
        lines.append(
            f"| `{field.name}` | {_kind(kinds[field.name])} | {_default(field)} | "
            f"`{variable}` | {_cell(about)} |"
        )
    return [*lines, ""]


# -- the pages -------------------------------------------------------------------------------------

PAGES = {COMMANDS: render_commands, SETTINGS: render_settings}


def main(argv: list[str]) -> int:
    stale: list[Path] = []
    for path, render in PAGES.items():
        text = render()
        if path.exists() and path.read_text(encoding="utf-8") == text:
            continue
        stale.append(path)
        if "--check" not in argv:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            sys.stdout.write(f"wrote {path.relative_to(REPOSITORY)}\n")
    if stale and "--check" in argv:
        names = ", ".join(str(path.relative_to(REPOSITORY)) for path in stale)
        sys.stderr.write(f"stale: {names}; run scripts/gen_docs.py\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
