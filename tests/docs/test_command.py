"""`atlas docs`: the list, a page, a search, every page, and what it refuses. It opens no home."""

from pathlib import Path

import pytest

from atlas.__main__ import main


def _docs(
    capsys: pytest.CaptureFixture[str], *argv: str, home: Path | None = None
) -> tuple[int, str, str]:
    environ = {} if home is None else {"ATLAS_HOME": str(home)}
    code = main(["docs", *argv], environ)
    out, err = capsys.readouterr()
    return code, out, err


def test_with_no_page_it_lists_the_pages_with_what_each_covers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, out, err = _docs(capsys)

    assert (code, err) == (0, "")
    assert out.startswith("# Atlas's documentation\n")
    assert "- `install`: **Install**." in out
    assert "- `reference/settings`: **Settings**." in out


def test_a_page_prints_whole_with_its_links_as_commands(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _docs(capsys, "guides/approval-gate")

    assert code == 0
    assert out.startswith("# The approval gate\n\n> How Atlas checks each command")
    assert "(`atlas docs reference/settings`)" in out
    assert "](/docs" not in out


def test_a_page_is_found_by_the_end_of_its_name(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _docs(capsys, "machines")

    assert code == 0
    assert out.startswith("# Several machines\n")


def test_a_search_names_the_page_and_section_to_read(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _docs(capsys, "--search", "add", "an", "account")

    assert code == 0
    assert "`atlas docs guides/coding-agents`: Coding agents and accounts, Several accounts" in out


def test_a_search_can_stay_in_one_page(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _docs(capsys, "memory", "--search", "limit")

    assert code == 0
    assert "`atlas docs guides/memory`" in out
    assert "`atlas docs guides/background-review`" not in out


def test_all_prints_every_page(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _docs(capsys, "--all")

    assert code == 0
    for title in ("# What Atlas is", "# Install", "# Commands", "# Writing a plugin"):
        assert f"\n{title}\n" in f"\n{out}"


@pytest.mark.parametrize(
    ("argv", "said"),
    [
        (("nowhere",), "There is no page 'nowhere'. The pages are: index, install"),
        (("install", "--all"), "--all prints every page"),
    ],
)
def test_what_it_cannot_do_is_said_on_stderr(
    capsys: pytest.CaptureFixture[str], argv: tuple[str, ...], said: str
) -> None:
    code, out, err = _docs(capsys, *argv)

    assert (code, out) == (1, "")
    assert err.startswith(f"atlas: {said}")


def test_it_opens_no_home(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    home = tmp_path / "never-made"

    code, _, _ = _docs(capsys, "install", home=home)

    assert code == 0
    assert not home.exists()
