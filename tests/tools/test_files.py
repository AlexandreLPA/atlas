"""The file tools on real files in a temporary folder: read_file, write_file, edit_file."""

import asyncio
import os
import time
from pathlib import Path
from typing import Any

import pytest

from atlas.agent import Tool
from atlas.tools import Workspace, file_tools
from documents_support import write_docx, write_pdf
from spellings_support import spellings


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "work"
    root.mkdir()
    return Workspace(root, protected=(tmp_path / "home" / "secrets",))


def _call(workspace: Workspace, name: str, **arguments: Any) -> str:
    tools: dict[str, Tool] = {tool.name: tool for tool in file_tools(workspace)}

    async def call() -> object:
        return await tools[name].run(arguments)

    output = asyncio.run(call())
    assert isinstance(output, str)
    return output


def _touch_later(path: Path) -> None:
    """Make the file look changed even where the clock is coarse."""
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


# -- read_file -----------------------------------------------------------------------------------


def test_read_numbers_the_lines_like_cat_n(workspace: Workspace) -> None:
    (workspace.root / "notes.md").write_text("one\ntwo\nthree\n", encoding="utf-8")

    whole = _call(workspace, "read_file", path="notes.md")
    assert whole == "     1\tone\n     2\ttwo\n     3\tthree"
    assert _call(workspace, "read_file", path="notes.md", offset=2, limit=1) == (
        "     2\ttwo\n[Atlas: the file goes on; continue with offset=3.]"
    )


def test_read_stops_within_its_character_budget_and_says_where_to_go_on(
    workspace: Workspace,
) -> None:
    line = "x" * 1_999
    (workspace.root / "big.txt").write_text(f"{line}\n" * 200, encoding="utf-8")

    said = _call(workspace, "read_file", path="big.txt")

    shown = said.splitlines()
    assert len(shown) - 1 < 200
    assert shown[-1].startswith("[Atlas: the file goes on; continue with offset=")
    assert len(said) < 101_000


def test_read_cuts_a_very_long_line(workspace: Workspace) -> None:
    (workspace.root / "min.js").write_text("y" * 5_000, encoding="utf-8")

    said = _call(workspace, "read_file", path="min.js")

    assert said.endswith("… [line cut at 2,000 characters]")


def test_read_explains_what_it_cannot_read(workspace: Workspace) -> None:
    (workspace.root / "report.md").write_text("x", encoding="utf-8")
    (workspace.root / "image.png").write_bytes(b"\x89PNG\x00\x00")
    (workspace.root / "empty.txt").write_text("", encoding="utf-8")

    assert "Did you mean: report.md?" in _call(workspace, "read_file", path="reprot.md")
    assert "is not a text file" in _call(workspace, "read_file", path="image.png")
    assert "is a folder" in _call(workspace, "read_file", path=".")
    assert "is empty" in _call(workspace, "read_file", path="empty.txt")
    assert "fewer than 10 lines" in _call(workspace, "read_file", path="report.md", offset=10)
    assert "Neither does its folder" in _call(workspace, "read_file", path="nope/a.md")
    assert "A path is needed" in _call(workspace, "read_file", path="")


def test_read_takes_absolute_and_home_paths(workspace: Workspace) -> None:
    file = workspace.root / "a.txt"
    file.write_text("hi", encoding="utf-8")

    assert _call(workspace, "read_file", path=str(file)) == "     1\thi"


def test_the_vault_folder_is_out_of_reach(workspace: Workspace, tmp_path: Path) -> None:
    secrets = tmp_path / "home" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "vault.key").write_text("AGE-SECRET-KEY-1...", encoding="utf-8")

    for name, arguments in (
        ("read_file", {"path": str(secrets / "vault.key")}),
        ("write_file", {"path": str(secrets / "x"), "content": "x"}),
        ("edit_file", {"path": str(secrets / "vault.key"), "old_string": "A", "new_string": "B"}),
    ):
        said = _call(workspace, name, **arguments)
        assert "inside Atlas's vault" in said
    assert (secrets / "vault.key").read_text(encoding="utf-8") == "AGE-SECRET-KEY-1..."


def test_the_vault_folder_is_out_of_reach_however_the_path_is_written(
    workspace: Workspace, tmp_path: Path
) -> None:
    # \\?\C:\..., \\localhost\C$\... and a hard link once read the vault (22/10/2026).
    secrets = tmp_path / "home" / "secrets"
    secrets.mkdir(parents=True)
    (secrets / "vault.key").write_text("AGE-SECRET-KEY-1...", encoding="utf-8")
    link = workspace.root / "notes.txt"
    os.link(secrets / "vault.key", link)

    for path in [str(link), *spellings(secrets / "vault.key")]:
        said = _call(workspace, "read_file", path=path)
        assert "inside Atlas's vault" in said, path
        assert "AGE-SECRET-KEY" not in said


# -- write_file ----------------------------------------------------------------------------------


def test_write_creates_a_file_and_its_folders(workspace: Workspace) -> None:
    said = _call(workspace, "write_file", path="a/b/new.md", content="# Title\nbody\n")

    assert said.startswith("Wrote 2 lines to ")
    assert (workspace.root / "a" / "b" / "new.md").read_bytes() == b"# Title\nbody\n"


def test_write_keeps_the_line_endings_it_was_given(workspace: Workspace) -> None:
    _call(workspace, "write_file", path="unix.txt", content="a\nb\n")

    assert (workspace.root / "unix.txt").read_bytes() == b"a\nb\n"


def test_an_existing_file_is_replaced_only_after_it_was_read(workspace: Workspace) -> None:
    file = workspace.root / "plan.md"
    file.write_text("the user's plan", encoding="utf-8")

    refused = _call(workspace, "write_file", path="plan.md", content="mine")
    assert "was not read in this conversation" in refused
    assert file.read_text(encoding="utf-8") == "the user's plan"

    _call(workspace, "read_file", path="plan.md")
    assert _call(workspace, "write_file", path="plan.md", content="new plan").startswith("Wrote")
    # What it wrote itself, it may write again.
    assert _call(workspace, "write_file", path="plan.md", content="newer").startswith("Wrote")
    assert file.read_text(encoding="utf-8") == "newer"


def test_a_file_changed_since_it_was_read_is_not_overwritten(workspace: Workspace) -> None:
    file = workspace.root / "plan.md"
    file.write_text("v1", encoding="utf-8")
    _call(workspace, "read_file", path="plan.md")
    file.write_text("v2 by someone else", encoding="utf-8")
    _touch_later(file)

    said = _call(workspace, "write_file", path="plan.md", content="mine")

    assert "changed since you read it" in said
    assert file.read_text(encoding="utf-8") == "v2 by someone else"


def test_write_refuses_a_folder_and_missing_content(workspace: Workspace) -> None:
    (workspace.root / "dir").mkdir()

    assert "is a folder" in _call(workspace, "write_file", path="dir", content="x")
    assert "needs `content`" in _call(workspace, "write_file", path="x.md")


def test_a_failed_write_leaves_the_old_file_and_no_temporary(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = workspace.root / "keep.md"
    file.write_text("old", encoding="utf-8")
    _call(workspace, "read_file", path="keep.md")

    def broken_fsync(_: int) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", broken_fsync)
    with pytest.raises(OSError, match="disk full"):
        _call(workspace, "write_file", path="keep.md", content="new")

    assert file.read_text(encoding="utf-8") == "old"
    assert [p.name for p in workspace.root.iterdir()] == ["keep.md"]


# -- edit_file -----------------------------------------------------------------------------------


def test_edit_replaces_text_found_once_and_shows_the_diff(workspace: Workspace) -> None:
    file = workspace.root / "app.py"
    file.write_text("def main():\n    return 1\n", encoding="utf-8")

    said = _call(
        workspace, "edit_file", path="app.py", old_string="return 1", new_string="return 2"
    )

    assert said.splitlines()[0].endswith("app.py: 1 replacement(s).")
    assert "-    return 1\n+    return 2" in said
    assert file.read_text(encoding="utf-8") == "def main():\n    return 2\n"


def test_edit_needs_unique_text_or_replace_all(workspace: Workspace) -> None:
    file = workspace.root / "a.txt"
    file.write_text("x = 1\nx = 1\n", encoding="utf-8")

    refused = _call(workspace, "edit_file", path="a.txt", old_string="x = 1", new_string="x = 2")
    assert "appears 2 times" in refused
    assert file.read_text(encoding="utf-8") == "x = 1\nx = 1\n"

    said = _call(
        workspace, "edit_file", path="a.txt", old_string="x = 1", new_string="x=2", replace_all=True
    )
    assert "2 replacement(s)" in said
    assert file.read_text(encoding="utf-8") == "x=2\nx=2\n"


def test_edit_matches_exactly_whitespace_included(workspace: Workspace) -> None:
    file = workspace.root / "a.py"
    file.write_text("    indented\n", encoding="utf-8")

    said = _call(workspace, "edit_file", path="a.py", old_string="  indented", new_string="x")
    assert said.startswith("Edited")
    missed = _call(workspace, "edit_file", path="a.py", old_string="\tindented", new_string="x")
    assert "was not found" in missed


def test_edit_keeps_a_windows_files_line_endings(workspace: Workspace) -> None:
    file = workspace.root / "win.txt"
    file.write_bytes(b"first\r\nsecond\r\nthird\r\n")

    _call(
        workspace,
        "edit_file",
        path="win.txt",
        old_string="first\nsecond",
        new_string="one\ntwo",
    )

    assert file.read_bytes() == b"one\r\ntwo\r\nthird\r\n"


def test_edit_says_why_it_did_not_change_anything(workspace: Workspace) -> None:
    (workspace.root / "a.txt").write_text("text", encoding="utf-8")
    (workspace.root / "latin1.txt").write_bytes("caf\xe9".encode("latin-1"))

    def edit(path: str, old: str, new: str) -> str:
        return _call(workspace, "edit_file", path=path, old_string=old, new_string=new)

    assert "is not a file" in edit("missing.txt", "a", "b")
    assert "old_string is empty" in edit("a.txt", "", "b")
    assert "are the same" in edit("a.txt", "text", "text")
    assert "is not UTF-8 text" in edit("latin1.txt", "caf", "b")
    assert "needs `old_string`" in _call(workspace, "edit_file", path="a.txt")


def test_edit_of_a_file_changed_since_it_was_read_says_so(workspace: Workspace) -> None:
    file = workspace.root / "a.txt"
    file.write_text("alpha beta", encoding="utf-8")
    _call(workspace, "read_file", path="a.txt")
    time.sleep(0.01)
    file.write_text("alpha beta gamma", encoding="utf-8")
    _touch_later(file)

    said = _call(workspace, "edit_file", path="a.txt", old_string="beta", new_string="BETA")
    missed = _call(workspace, "edit_file", path="a.txt", old_string="delta", new_string="x")

    assert "had changed since you last read it" in said
    assert file.read_text(encoding="utf-8") == "alpha BETA gamma"
    assert "was not found" in missed


# -- documents -----------------------------------------------------------------------------------


def test_a_pdf_and_a_word_file_are_read_as_markdown(workspace: Workspace) -> None:
    write_pdf(workspace.root / "bill.pdf", ["Electricity bill", "Due: TODAY"])
    write_docx(workspace.root / "notes.docx", ["Meeting notes", "Ship on Friday"])

    pdf = _call(workspace, "read_file", path="bill.pdf")
    docx = _call(workspace, "read_file", path="notes.docx", offset=3)

    assert pdf == "     1\tElectricity bill Due: TODAY"
    assert docx == "     3\tShip on Friday"


def test_a_document_is_converted_once_while_it_stays_the_same(workspace: Workspace) -> None:
    write_docx(workspace.root / "notes.docx", ["First"])
    _call(workspace, "read_file", path="notes.docx")
    cached = workspace.documents[workspace.root / "notes.docx"]

    _call(workspace, "read_file", path="notes.docx")
    assert workspace.documents[workspace.root / "notes.docx"] is cached

    write_docx(workspace.root / "notes.docx", ["Second", "version"])
    _touch_later(workspace.root / "notes.docx")
    assert "Second" in _call(workspace, "read_file", path="notes.docx")


def test_a_broken_document_says_why(workspace: Workspace) -> None:
    (workspace.root / "broken.pdf").write_bytes(b"%PDF-1.4\n" + b"\x00" * 64)

    said = _call(workspace, "read_file", path="broken.pdf")

    assert said.startswith(f"[Atlas: {workspace.root / 'broken.pdf'} is a")
    assert "could not be read" in said or "not a text file" in said
