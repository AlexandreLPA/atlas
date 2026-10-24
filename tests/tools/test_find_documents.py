"""find_files inside documents: real Word and PDF files, the real converter and the real ripgrep,
with the texts kept in a temporary home's `documents/`."""

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from atlas.agent import Tool
from atlas.home import Home
from atlas.tools import DocumentTexts, Workspace, file_tools, find_files
from documents_support import write_docx, write_pdf

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep is not installed")


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "work"
    (root / "reports").mkdir(parents=True)
    (root / "node_modules").mkdir()
    write_docx(root / "reports" / "contract.docx", ["Preamble", "The sunflower clause applies."])
    write_pdf(root / "reports" / "invoice.pdf", ["Invoice 42", "Pay by sunflower day"])
    write_docx(root / "node_modules" / "skip.docx", ["sunflower in a dependency"])
    (root / "notes.md").write_text("A sunflower note.\n", encoding="utf-8")
    return Workspace(root)


@pytest.fixture
def texts(home: Home) -> DocumentTexts:
    return DocumentTexts(home.documents_folder)


def _find(tool: Tool, **arguments: Any) -> str:
    async def call() -> object:
        return await tool.run(arguments)

    output = asyncio.run(call())
    assert isinstance(output, str)
    return output.replace("\\", "/")


def test_without_texts_documents_stay_unread(workspace: Workspace) -> None:
    said = _find(find_files(workspace), pattern="sunflower clause")

    assert said.startswith("Nothing matches")


def test_words_inside_word_and_pdf_files_are_found_once_after_the_files(
    workspace: Workspace, texts: DocumentTexts
) -> None:
    said = _find(find_files(workspace, texts=texts), pattern="sunflower")

    lines = said.splitlines()[1:]
    assert lines[0] == "notes.md:1:A sunflower note."
    assert [line.split(":")[0] for line in lines[1:]] == [
        "reports/contract.docx",
        "reports/invoice.pdf",
    ]
    assert "node_modules" not in said


def test_a_documents_line_numbers_are_read_files(
    workspace: Workspace, texts: DocumentTexts
) -> None:
    said = _find(find_files(workspace, texts=texts), pattern="sunflower clause")
    _, line, _ = said.splitlines()[1].split(":", 2)
    read = {tool.name: tool for tool in file_tools(workspace)}["read_file"]

    shown = _find(read, path="reports/contract.docx", offset=int(line), limit=1)

    assert "The sunflower clause applies." in shown


def test_names_counts_and_globs_work_on_documents(
    workspace: Workspace, texts: DocumentTexts
) -> None:
    tool = find_files(workspace, texts=texts)

    assert "reports/invoice.pdf" in _find(tool, pattern="Invoice", output_mode="files_only")
    assert "reports/contract.docx:1" in _find(tool, pattern="clause", output_mode="count")
    assert _find(tool, pattern="clause", file_glob="*.md").startswith("Nothing matches")
    assert "contract.docx" in _find(tool, pattern="clause", file_glob="*.docx")


def test_a_text_is_kept_and_made_again_only_when_the_document_changes(
    workspace: Workspace, texts: DocumentTexts
) -> None:
    tool = find_files(workspace, texts=texts)
    contract = workspace.root / "reports" / "contract.docx"
    _find(tool, pattern="sunflower")
    kept = texts.folder / texts.name(contract)
    assert kept.stat().st_mtime_ns == contract.stat().st_mtime_ns

    write_docx(contract, ["The tulip clause applies."])
    later = contract.stat().st_mtime_ns + 5_000_000_000
    os.utime(contract, ns=(later, later))

    assert "contract.docx" in _find(tool, pattern="tulip")
    assert "contract.docx" not in _find(tool, pattern="sunflower clause")


def test_a_file_named_as_a_document_that_is_text_is_still_found(
    workspace: Workspace, texts: DocumentTexts
) -> None:
    (workspace.root / "exported.pdf").write_text("plain text marigold\n", encoding="utf-8")

    said = _find(find_files(workspace, texts=texts), pattern="marigold")

    assert said.splitlines()[1] == "exported.pdf:1:plain text marigold"


def test_a_document_with_no_text_is_not_tried_again(
    workspace: Workspace, texts: DocumentTexts
) -> None:
    broken = workspace.root / "broken.docx"
    broken.write_bytes(b"PK\x03\x04 not really a zip \x00\x01")
    tool = find_files(workspace, texts=texts)

    _find(tool, pattern="anything")

    kept = texts.folder / texts.name(broken)
    assert kept.stat().st_size == 0
    assert kept.stat().st_mtime_ns == broken.stat().st_mtime_ns


def test_texts_not_ready_in_time_are_left_for_the_next_search(
    workspace: Workspace, texts: DocumentTexts
) -> None:
    said = _find(find_files(workspace, texts=texts, seconds=0.5), pattern="note")

    # Listing the documents may already take the half second; either way, nothing is lost.
    if "not read yet" in said:
        assert "Search again to include them." in said
    said = _find(find_files(workspace, texts=texts), pattern="sunflower clause")
    assert "contract.docx" in said
