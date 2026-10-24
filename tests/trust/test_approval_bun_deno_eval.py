# SPDX-FileCopyrightText: 2025 Nous Research
# SPDX-FileCopyrightText: 2026 the Atlas contributors
# SPDX-License-Identifier: MIT
"""Inline scripts run by Bun and Deno ask for approval the way `node -e` and `python -c` do.

Bun takes the script after `-e` or `--eval`; Deno also has an `eval` subcommand, which may follow
Deno's global options. None of these were in the interpreter tables, so the script ran unasked.
"""

import pytest

from atlas.trust.detection import detect_dangerous_command

INLINE = "script execution via -e/-c flag"
HEREDOC = "script execution via heredoc"
MALFORMED = "command parser limit or malformed executable payload"


@pytest.mark.parametrize(
    ("command", "rule"),
    [
        pytest.param('bun -e "console.log(1)"', INLINE, id="bun-e"),
        pytest.param('bun --eval "console.log(1)"', INLINE, id="bun-eval-flag"),
        pytest.param('deno eval "console.log(1)"', INLINE, id="deno-eval-subcommand"),
        pytest.param('deno -e "console.log(1)"', INLINE, id="deno-e"),
        pytest.param('deno --eval "console.log(1)"', INLINE, id="deno-eval-flag"),
        # a wrapper in front of the interpreter hides nothing
        pytest.param('sudo bun -e "console.log(1)"', INLINE, id="bun-under-sudo"),
        pytest.param('sudo deno eval "console.log(1)"', INLINE, id="deno-under-sudo"),
        # Deno's global options come before its subcommand, and `-L` takes its value apart or
        # glued; deno 2.9.6 runs the script in each of these
        pytest.param('deno -q eval "console.log(1)"', INLINE, id="deno-quiet-short"),
        pytest.param('deno --quiet eval "console.log(1)"', INLINE, id="deno-quiet-long"),
        pytest.param('deno -L debug eval "console.log(1)"', INLINE, id="deno-log-level-apart"),
        pytest.param('deno -Ldebug eval "console.log(1)"', INLINE, id="deno-log-level-glued"),
        pytest.param(
            'deno --log-level=debug eval "console.log(1)"', INLINE, id="deno-log-level-long"
        ),
        # Windows matches executable names in any case, and so does the interpreter lookup
        pytest.param('BUN.EXE -e "console.log(1)"', INLINE, id="bun-exe-upper-case"),
        pytest.param('Deno.exe eval "console.log(1)"', INLINE, id="deno-exe-mixed-case"),
        pytest.param('bun << "EOF"\nconsole.log("pwned")\nEOF', HEREDOC, id="bun-heredoc"),
        pytest.param("deno << 'EOF'\nconsole.log(\"pwned\")\nEOF", HEREDOC, id="deno-heredoc"),
        # a quote left open is refused, as for every other interpreter
        pytest.param('bun -e "console.log(1)', MALFORMED, id="bun-unclosed-quote"),
    ],
)
def test_an_inline_bun_or_deno_script_asks_under_the_interpreter_rule(
    command: str, rule: str
) -> None:
    dangerous, key, _ = detect_dangerous_command(command)

    assert dangerous is True, command
    assert key == rule, command


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("bun install", id="bun-install"),
        pytest.param("bun run build", id="bun-run"),
        pytest.param("deno run server.ts", id="deno-run"),
        pytest.param("deno test", id="deno-test"),
        pytest.param("deno lint", id="deno-lint"),
        # a file called eval.ts is what `run` runs, not the eval subcommand
        pytest.param("deno run eval.ts", id="run-a-file-named-eval"),
        pytest.param("deno -q run eval.ts", id="run-a-file-named-eval-quietly"),
        pytest.param("deno -L debug run eval.ts", id="run-a-file-named-eval-with-log-level"),
        # Deno stops reading subcommands at `--` and opens its REPL; no script runs
        pytest.param('deno -- eval "console.log(1)"', id="eval-after-double-dash"),
        # quoted, `deno eval` is text for echo
        pytest.param("echo 'use deno eval for that'", id="eval-in-prose"),
    ],
)
def test_everyday_bun_and_deno_commands_ask_for_nothing(command: str) -> None:
    assert detect_dangerous_command(command) == (False, None, None), command
