"""The detector answers at once on inputs shaped to make a regular expression backtrack."""

import time

import pytest

from atlas.trust import detect_dangerous_command


@pytest.mark.parametrize(
    "command",
    [
        "install ./.env" + ".ab" * 300 + ")",
        "cat config/.env" + ".x" * 300 + "-",
        "cp a ../.env" + ".local" * 200 + "\x1c",
    ],
    ids=["closing-paren", "trailing-dash", "control-char"],
)
def test_a_long_dotted_env_name_is_judged_without_freezing(command: str) -> None:
    # Before the fix, 22 dotted parts took two seconds and each four more multiplied it by 14.
    started = time.perf_counter()
    detect_dangerous_command(command)
    assert time.perf_counter() - started < 2


def test_a_write_to_a_long_dotted_env_name_is_still_flagged() -> None:
    flagged, _, reason = detect_dangerous_command("install ./.env" + ".ab" * 300 + ")")
    assert flagged
    assert reason == "overwrite project env/config file"
