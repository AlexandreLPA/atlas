"""`python -m atlas.installation`: what the app's update button runs, apart from the rest of Atlas.

- `check` writes, as one JSON object, whether the installation's branch has commits it lacks
  (`UpdateStatus`), for the button.
- `handoff` is the update the app hands off as it closes: it waits for the app, updates, writes
  how it went to `--result`, and opens the app again (`hand_off`).

Only the standard library and Atlas's own installation, home and instances (the always-on homes,
stopped for the update) are loaded here, never the rest of Atlas and its packages: on Windows
`uv sync` cannot replace a package a running process loaded (`atlas update`, from a terminal,
imports them all), and this process runs while it syncs.
"""

import argparse
import os
import sys
from pathlib import Path

from atlas.home import Parent, home_root, resolve_home
from atlas.installation.errors import InstallationError
from atlas.installation.model import UpdateStatus
from atlas.installation.service import check, hand_off
from atlas.installation.store import Checkout, Runner, Toolchain
from atlas.instances import AlwaysOnServices


def arguments() -> argparse.ArgumentParser:
    """What the app passes: the end-to-end tests' stand-in reads them with this very parser, so it
    cannot drift from what the app sends."""
    parser = argparse.ArgumentParser(prog="python -m atlas.installation")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check", help="Say, as JSON, whether main has commits this lacks.")
    off = commands.add_parser(
        "handoff", help="Wait for the app to close, update, write how it went, open the app."
    )
    off.add_argument("--app-pid", type=int, required=True, help="The app to wait for.")
    off.add_argument("--marker", type=Path, required=True, help="Says an update runs.")
    off.add_argument("--result", type=Path, required=True, help="How it went, for the app.")
    off.add_argument("--log", type=Path, required=True, help="The steps' output.")
    off.add_argument("--no-reopen", action="store_true", help="Do not open the app again.")
    off.add_argument(
        "--reopen-flag",
        action="append",
        default=[],
        help="A flag the app was opened with, given to it again (`--reopen-flag=--lang=en`).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = arguments().parse_args(argv)
    if args.command == "check":
        sys.stdout.write(_status().to_json() + "\n")
        return 0
    app = Parent(args.app_pid)
    try:
        checkout = Checkout.running()
        with args.log.open("w", encoding="utf-8") as log:
            runner = Runner(Toolchain.of(checkout), log=log, unattended=True)
            homes = home_root(resolve_home(os.environ))
            ended = hand_off(
                checkout,
                runner,
                app,
                homes,
                marker=args.marker,
                result=args.result,
                reopen=not args.no_reopen,
                reopen_flags=tuple(args.reopen_flag),
                services=AlwaysOnServices(homes),
            )
    finally:
        app.close()
    return 0 if ended.ok else 1


def _status() -> UpdateStatus:
    try:
        checkout = Checkout.running()
        return check(checkout, Runner(Toolchain.of(checkout), unattended=True))
    except InstallationError as error:
        return UpdateStatus(True, error=str(error))


if __name__ == "__main__":
    sys.exit(main())
