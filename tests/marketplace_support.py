"""What the marketplace's tests stand on: git repositories made in the test, served by the fake
GitHub, and a marketplace's repository written in them."""

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

from fake_github import FakeGitHub, git

#: The marketplace's own repository, as the tests name it.
MARKETPLACE = "atlas-test/marketplace"


class Repository:
    """A git repository of the test's, served by the fake GitHub as `name` (`owner/repo`)."""

    def __init__(self, root: Path, name: str, github: FakeGitHub) -> None:
        self.name = name
        self.folder = root / name.replace("/", "--")
        self.folder.mkdir(parents=True)
        git(self.folder, "init", "-q", "-b", "main")
        github.serve(name, self.folder)

    def write(self, files: Mapping[str, str | bytes]) -> "Repository":
        for path, content in files.items():
            file = self.folder / path
            file.parent.mkdir(parents=True, exist_ok=True)
            data = content.encode("utf-8") if isinstance(content, str) else content
            file.write_bytes(data)
        return self

    def delete(self, path: str) -> "Repository":
        (self.folder / path).unlink()
        return self

    def commit(self, message: str = "Change") -> str:
        """Commit everything; the commit's id."""
        git(self.folder, "add", "-A")
        git(self.folder, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.folder, "rev-parse", "HEAD").decode().strip()


def skill_text(name: str, *, requires: Mapping[str, Any] | None = None, body: str = "") -> str:
    """A `SKILL.md`, with what it needs under `metadata.requires`."""
    head: dict[str, Any] = {"name": name, "description": f"Use {name}."}
    if requires is not None:
        head["metadata"] = {"requires": dict(requires)}
    return f"---\n{yaml.safe_dump(head, sort_keys=False)}---\n{body or f'How to use {name}.'}\n"


def plugin_files(
    name: str,
    *,
    executors: list[str] | None = None,
    tools: list[str] | None = None,
    api: int = 1,
    code: str | None = None,
) -> dict[str, str]:
    """A plugin's files: its manifest and a skill, and Python when `code` is given (an executor
    brings its Python and SDK 2 at least; a tool, SDK 14)."""
    atlas: dict[str, Any] = {"api": max(api, 2) if executors else api}
    if executors:
        atlas["executors"] = executors
        code = code or "def register(ctx):\n    pass\n"
    if tools:
        atlas["tools"] = tools
        atlas["api"] = max(atlas["api"], 14)
    manifest = {
        "name": name,
        "version": "1.0.0",
        "description": f"The {name} plugin.",
        "atlas": atlas,
    }
    files = {
        ".claude-plugin/plugin.json": json.dumps(manifest),
        f"skills/{name}-help/SKILL.md": skill_text(f"{name}-help"),
        "README.md": f"# {name}\n\nWhat {name} does.\n",
    }
    if code is not None:
        files["__init__.py"] = code
    return files


def entry(
    name: str,
    kind: str,
    *,
    repo: Repository | None = None,
    sha: str = "",
    path: str = "",
    **fields: Any,
) -> dict[str, Any]:
    """An item's file, as a marketplace's repository holds it."""
    found: dict[str, Any] = {
        "name": name,
        "kind": kind,
        "title": name.replace("-", " ").title(),
        "summary": f"The {name} item.",
        "author": "someone",
        "license": "MIT",
    }
    if repo is not None:
        found.update(repo=repo.name, sha=sha)
        if path:
            found["path"] = path
    found.update(fields)
    return found


def marketplace(
    root: Path,
    github: FakeGitHub,
    items: Mapping[str, Mapping[str, Any]] = {},
    *,
    removed: Sequence[Mapping[str, str]] | None = None,
) -> Repository:
    """The marketplace's repository, with `items` and what it retired."""
    repository = Repository(root, MARKETPLACE, github)
    write_marketplace(repository, items, removed=removed)
    return repository


def write_marketplace(
    repository: Repository,
    items: Mapping[str, Mapping[str, Any]],
    *,
    removed: Sequence[Mapping[str, str]] | None = None,
) -> str:
    """Write the marketplace's files over what was there, and commit them."""
    items_folder = repository.folder / "items"
    if items_folder.is_dir():
        for old in items_folder.glob("*.yaml"):
            old.unlink()
    files: dict[str, str] = {
        "marketplace.yaml": "format: 1\nname: Test marketplace\ndescription: For the tests.\n"
    }
    for name, fields in items.items():
        files[f"items/{name}.yaml"] = yaml.safe_dump(dict(fields), sort_keys=False)
    if removed is not None:
        files["removed.yaml"] = yaml.safe_dump([dict(entry) for entry in removed])
    repository.write(files)
    return repository.commit("The marketplace")
