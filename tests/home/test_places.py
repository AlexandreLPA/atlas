"""Whether a path leads into a place, however it is written."""

import os
import sys
from pathlib import Path

import pytest

from atlas.home import inside
from spellings_support import spellings


@pytest.fixture
def secrets(tmp_path: Path) -> Path:
    folder = tmp_path / "home" / "secrets"
    folder.mkdir(parents=True)
    (folder / "vault.key").write_text("key", encoding="utf-8")
    return folder


def test_a_path_inside_a_folder_and_one_beside_it(secrets: Path) -> None:
    assert inside(secrets / "vault.key", secrets)
    assert inside(secrets / "new" / "file.txt", secrets)
    assert inside(secrets, secrets)
    assert not inside(secrets.parent / "secrets2" / "vault.key", secrets)
    assert not inside(secrets.parent / "notes.md", secrets)


def test_a_file_that_does_not_exist_yet(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    assert inside(home / "mcp.json", home / "mcp.json")
    assert inside(home / "plugin-data" / "x" / "y.py", home / "plugin-data")
    assert not inside(home / "mcp.json.bak", home / "mcp.json")


def test_every_way_windows_writes_the_same_folder(secrets: Path) -> None:
    written = spellings(secrets / "vault.key")
    if not written:
        pytest.skip("only Windows has other ways of writing a path")

    for spelled in written:
        assert inside(Path(spelled), secrets), spelled
        assert inside(Path(spelled).parent / "new.txt", secrets), spelled


def test_every_way_windows_writes_a_file_that_does_not_exist_yet(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    written = spellings(home / "mcp.json")
    if not written:
        pytest.skip("only Windows has other ways of writing a path")

    for spelled in written:
        assert inside(Path(spelled), home / "mcp.json"), spelled


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ignores these in a name")
def test_windows_names_ignore_case_trailing_dots_and_streams(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    for name in ("MCP.JSON", "mcp.json.", "mcp.json ", "mcp.json::$DATA", "mcp.json:x"):
        assert inside(home / name, home / "mcp.json"), name


def test_a_hard_link_to_a_file_of_the_folder(secrets: Path, tmp_path: Path) -> None:
    link = tmp_path / "work" / "innocent.txt"
    link.parent.mkdir()
    os.link(secrets / "vault.key", link)

    assert inside(link, secrets)
    assert not inside(tmp_path / "work" / "other.txt", secrets)
