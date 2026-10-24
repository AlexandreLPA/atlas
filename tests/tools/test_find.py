"""find_files on a real tree with the real ripgrep, as installed on the machine (and in CI)."""

import asyncio
import shutil
from pathlib import Path
from typing import Any

import pytest

from atlas.agent import Tool
from atlas.tools import Workspace, find_files

pytestmark = pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep is not installed")


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "work"
    files = {
        "README.md": "# Atlas\nA personal agent.\n",
        "src/app.py": "def main():\n    return 'TODO: ship'\n",
        "src/util.py": "# todo later\nVALUE = 1\n",
        "docs/guide.md": "Read the guide.\nTODO: write it\n",
        "node_modules/pkg/index.js": "// TODO in a dependency\n",
        "build/out.txt": "TODO generated\n",
        "secret/.hidden.md": "TODO hidden\n",
    }
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return Workspace(root)


def _find(workspace: Workspace, tool: Tool | None = None, **arguments: Any) -> str:
    chosen = tool or find_files(workspace)

    async def call() -> object:
        return await chosen.run(arguments)

    output = asyncio.run(call())
    assert isinstance(output, str)
    return output.replace("\\", "/")


def test_content_search_gives_path_line_and_text_in_path_order(workspace: Workspace) -> None:
    said = _find(workspace, pattern="TODO")

    lines = said.splitlines()
    assert lines[0] == f"In {workspace.root.as_posix()}:"
    assert lines[1:] == ["docs/guide.md:2:TODO: write it", "src/app.py:2:    return 'TODO: ship'"]


def test_folders_that_are_never_the_answer_are_skipped_unless_the_search_starts_there(
    workspace: Workspace,
) -> None:
    everywhere = _find(workspace, pattern="TODO", ignore_case=True)
    inside = _find(workspace, pattern="TODO", path="node_modules")

    assert "node_modules" not in everywhere
    assert "build/" not in everywhere
    assert ".hidden" not in everywhere
    assert "src/util.py:1:# todo later" in everywhere
    assert "pkg/index.js:1:// TODO in a dependency" in inside


def test_gitignore_is_followed_in_a_repository(workspace: Workspace) -> None:
    (workspace.root / ".git").mkdir()
    (workspace.root / ".gitignore").write_text("docs/\n", encoding="utf-8")

    said = _find(workspace, pattern="TODO")

    assert "docs/guide.md" not in said
    assert "src/app.py" in said


def test_files_by_name(workspace: Workspace) -> None:
    said = _find(workspace, pattern="*.md", target="files")
    nested = _find(workspace, pattern="src/**/*.py", target="files")

    # A glob that names a hidden file finds it, as ripgrep's globs do: `.env*` finds .env.
    assert said.splitlines()[1:] == ["README.md", "docs/guide.md", "secret/.hidden.md"]
    assert nested.splitlines()[1:] == ["src/app.py", "src/util.py"]
    assert _find(workspace, pattern="*.MD", target="files", ignore_case=True).count(".md") == 3


def test_output_modes_context_and_a_file_filter(workspace: Workspace) -> None:
    files = _find(workspace, pattern="TODO", output_mode="files_only")
    counts = _find(workspace, pattern="(?i)todo", output_mode="count")
    around = _find(workspace, pattern="TODO: ship", context=1)
    only_md = _find(workspace, pattern="TODO", file_glob="guide.md")

    assert files.splitlines()[1:] == ["docs/guide.md", "src/app.py"]
    assert "src/util.py:1" in counts
    assert "src/app.py-1-def main():" in around
    assert only_md.splitlines()[1:] == ["docs/guide.md:2:TODO: write it"]


def test_one_file_can_be_searched(workspace: Workspace) -> None:
    said = _find(workspace, pattern="VALUE", path="src/util.py")

    assert said.splitlines()[1:] == ["util.py:2:VALUE = 1"]


def test_results_are_paged(workspace: Workspace) -> None:
    for i in range(30):
        (workspace.root / "many" / f"f{i:02}.txt").parent.mkdir(exist_ok=True)
        (workspace.root / "many" / f"f{i:02}.txt").write_text("needle\n", encoding="utf-8")

    first = _find(workspace, pattern="needle", limit=10)
    second = _find(workspace, pattern="needle", limit=10, offset=10)
    past = _find(workspace, pattern="needle", offset=100)

    assert first.splitlines()[1] == "many/f00.txt:1:needle"
    assert "30 results; showing 1 to 10. Continue with offset=10" in first
    assert second.splitlines()[1] == "many/f10.txt:1:needle"
    assert "there are only 30 results" in past


def test_the_same_search_gives_the_same_answer(workspace: Workspace) -> None:
    assert _find(workspace, pattern="TODO|todo") == _find(workspace, pattern="TODO|todo")


def test_what_it_cannot_do_is_said(workspace: Workspace) -> None:
    assert "Nothing matches 'nowhere'" in _find(workspace, pattern="nowhere")
    assert "No file name matches" in _find(workspace, pattern="*.rs", target="files")
    assert "ripgrep refused the search" in _find(workspace, pattern="(unclosed")
    assert "does not exist" in _find(workspace, pattern="x", path="missing")
    assert "needs a pattern" in _find(workspace, pattern="")


def test_a_search_past_its_time_is_stopped(workspace: Workspace) -> None:
    slow = find_files(workspace, seconds=0.000_001)

    said = _find(workspace, slow, pattern="TODO")

    assert "was stopped because it ran past 1e-06 seconds" in said


def test_without_ripgrep_it_says_how_to_install_it(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", "")

    said = _find(workspace, pattern="TODO")

    assert "ripgrep (rg) is not installed" in said
    assert "winget install BurntSushi.ripgrep.MSVC" in said
