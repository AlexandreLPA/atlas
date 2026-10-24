# SPDX-FileCopyrightText: 2025 Nous Research
# SPDX-FileCopyrightText: 2026 the Atlas contributors
# SPDX-License-Identifier: MIT
"""A grep inside a command substitution is read as a command of its own, ending where it ends.

To tell which quoted operand of a grep is only a pattern, the detector splits the grep into
words. It used to read on to the end of the whole segment, so in `"$(grep … | cut …)"` it ate the
closing quote of the outer command, found the quoting unbalanced, and stopped the command at the
floor. Every one of those stops was wrong (546 in one run). The words now end with the simple
command: at an unquoted `;`, `|`, `&` or newline, or at the `)` or backtick that closes the
substitution.
"""

import asyncio
from typing import Any

import pytest

from atlas.agent import Tool
from atlas.trust import ApprovalGate, ApprovalRequest
from atlas.trust.detection import (
    _quoted_grep_pattern_spans,
    _shell_tokens_with_spans,
    detect_hardline_command,
)

# A newline ends the grep inside the substitution, and what follows it runs.
_HIDDEN_AFTER_A_NEWLINE = "echo \"$(grep -P 'safe' /dev/null\nreboot)\""


@pytest.mark.parametrize(
    "command",
    [
        pytest.param('sed -n "$(grep -n X f | cut -d: -f1),+3p" f', id="sed-range-start"),
        pytest.param(
            "sed -n \"$(grep -n '^### 1.' d.md | cut -d: -f1),$(grep -n '^### 3.' d.md | cut -d: -f1)p\" d.md",
            id="sed-range-from-two-greps",
        ),
        pytest.param('echo "$(grep -c x f)"', id="count-in-echo"),
        pytest.param('x="$(grep -n foo bar | head -1)"; echo $x', id="assigned-then-echoed"),
        pytest.param(
            "for f in a b; do echo \"$f sig=$(grep -cE '^x' $f)\"; done", id="inside-a-loop"
        ),
        pytest.param("echo `grep -c x f`", id="backticks"),
    ],
)
def test_a_grep_in_a_substitution_leaves_the_quoting_balanced(command: str) -> None:
    _, malformed = _quoted_grep_pattern_spans(command)

    assert malformed is False
    assert detect_hardline_command(command) == (False, None)


def test_quoting_that_is_really_open_is_still_refused() -> None:
    assert _shell_tokens_with_spans("grep 'unterminated", 0) is None
    assert detect_hardline_command("grep 'unterminated")[0] is True


@pytest.mark.parametrize(
    "command",
    [
        pytest.param("rm -rf /", id="root-wipe"),
        pytest.param('echo "$(rm -rf /)"', id="root-wipe-in-substitution"),
        pytest.param("sudo shutdown -h now", id="shutdown-under-sudo"),
    ],
)
def test_the_floor_still_stops_its_own_commands(command: str) -> None:
    assert detect_hardline_command(command)[0] is True, command


# -- what a substitution runs is code, even inside double quotes ------------------------------------

# With the grep bounded, the old "malformed" refusal no longer covers these. The masking of
# quoted newlines has to treat a substitution's body as code; otherwise a floor command after
# a newline in it would pass as part of an argument. An outside review found this shape: once
# refused as malformed, then let through by the first version of the fix.


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(_HIDDEN_AFTER_A_NEWLINE, id="newline"),
        pytest.param('echo "$(grep x f; reboot)"', id="semicolon"),
        pytest.param('echo "$(grep x f && reboot)"', id="and"),
        pytest.param('echo "$(grep x f | shutdown -h now)"', id="pipe"),
        pytest.param('echo "$(echo $(grep x f)\nreboot)"', id="nested-substitution"),
        pytest.param('echo "`grep x f\nreboot`"', id="backticks"),
    ],
)
def test_a_floor_command_in_a_quoted_substitution_is_stopped_for_what_it_is(command: str) -> None:
    stopped, reason = detect_hardline_command(command)

    assert stopped and reason == "system shutdown/reboot"


def test_the_gate_refuses_such_a_command_without_asking_the_user() -> None:
    asked: list[ApprovalRequest] = []

    async def user(request: ApprovalRequest) -> Any:
        asked.append(request)
        raise AssertionError("the user was asked about a floor command")

    async def run(arguments: dict[str, Any]) -> str:
        return ""

    gate = ApprovalGate(approver=user)
    tool = Tool("run_command", "Runs a command.", {}, run)

    refusal = asyncio.run(gate(tool, {"command": _HIDDEN_AFTER_A_NEWLINE}))

    assert refusal is not None and "hardline" in refusal.lower() and asked == []


@pytest.mark.parametrize(
    "command",
    [
        # the backtick opens after the grep, so it is an operand, not the end of a substitution
        pytest.param("grep -e `echo needle` file", id="backtick-operand"),
        pytest.param("grep -F 'sudo reboot' notes.md", id="floor-words-as-pattern"),
        pytest.param('git commit -m "fix\nsudo reboot handling"', id="newline-in-message"),
    ],
)
def test_a_harmless_shape_near_these_passes(command: str) -> None:
    assert detect_hardline_command(command) == (False, None)
