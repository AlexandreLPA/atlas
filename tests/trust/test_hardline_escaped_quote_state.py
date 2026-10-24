# SPDX-FileCopyrightText: 2025 Nous Research
# SPDX-FileCopyrightText: 2026 the Atlas contributors
# SPDX-License-Identifier: MIT
"""The floor follows quoting on the command as written, not on the text it matches patterns on.

Before matching, the detector drops backslash escapes, so `r\\m` cannot pass for something other
than `rm`. Tracking quotes on that text is wrong: `\\"` turns into `"` and flips every quote after
it. It failed both ways. A valid `grep -o "[^\\"]*" f` looked unterminated and was stopped (118 of
125 floor stops in a week of real use, every one a harmless grep). And in
`cat "f\\"n.txt"; rm -rf --no-preserve-root /` the `; rm` fell inside a quote that did not exist,
so no command start was seen and the wipe passed. Quotes are now read from the raw command, with
only the newlines inside them masked.
"""

import pytest

from atlas.trust.detection import detect_dangerous_command, detect_hardline_command

ROOT = "recursive delete of root filesystem"
HOME = "recursive delete of home directory"
POWER = "system shutdown/reboot"
MALFORMED = "command parser limit or malformed executable payload"


@pytest.mark.parametrize(
    "command",
    [
        pytest.param('grep -o "[^\\"]*" f', id="escaped-quote-in-class"),
        pytest.param(
            r'grep -n "^from\|^__all__\|^    \"" tools/environments/__init__.py | head -15',
            id="escaped-quote-in-alternation",
        ),
        pytest.param(r'egrep -n "alpha\"beta|gamma" input.txt', id="escaped-quote-mid-word"),
        pytest.param(
            r'grep -v "^./tests/\|def \|\"\"\"" x.py | grep -v "read_only=True"',
            id="three-escaped-quotes",
        ),
        pytest.param('grep -n "; reboot" f.txt', id="floor-words-in-pattern"),
        pytest.param('echo "value is ${HOME}/x"', id="braced-home-in-text"),
    ],
)
def test_an_escaped_quote_in_a_valid_command_is_not_taken_for_open_quoting(command: str) -> None:
    assert detect_hardline_command(command) == (False, None)
    assert detect_dangerous_command(command) == (False, None, None)


@pytest.mark.parametrize(
    ("command", "reason"),
    [
        pytest.param(r'cat "f\"n.txt"; rm -rf --no-preserve-root /', ROOT, id="root-after-cat"),
        pytest.param(r'echo "a\"b"; reboot', POWER, id="reboot-after-echo"),
        pytest.param(r'echo "a\"b" && rm -rf ~', HOME, id="home-after-and"),
        pytest.param(r'echo "a\"b"; rm${IFS}-rf${IFS}/', ROOT, id="root-spaced-with-ifs"),
        pytest.param(
            r'grep -n "prefix \"quoted\" suffix" input.txt; reboot', POWER, id="reboot-after-grep"
        ),
        pytest.param("printf \\\\\nreboot", POWER, id="escaped-backslash-then-newline"),
        pytest.param("grep 'unterminated", MALFORMED, id="really-unterminated"),
    ],
)
def test_an_escaped_quote_cannot_hide_the_floor_command_after_it(command: str, reason: str) -> None:
    assert detect_hardline_command(command) == (True, reason)
