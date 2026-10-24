"""The skills Atlas ships, synced into a real library folder from a real source folder: copied once,
updated while untouched, left alone once changed, never brought back after a delete, and put back
on request with the way back kept."""

from pathlib import Path

import pytest

from atlas.skills import (
    BUNDLED_FOLDER,
    SkillChangeError,
    SkillLibrary,
    bundled_skills,
    changed,
    read_manifest,
    reset_bundled,
    sync_bundled,
)


def _skill(root: Path, name: str, body: str = "Do it.", **files: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Uses {name}.\n---\n\n{body}\n", encoding="utf-8"
    )
    for relative, text in files.items():
        path = folder / relative.replace("__", "/")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return folder


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "bundled"
    _skill(root, "github", references__auth="gh auth login")
    _skill(root, "gws-gmail")
    return root


@pytest.fixture
def library(tmp_path: Path) -> SkillLibrary:
    return SkillLibrary(tmp_path / "home" / "skills")


def _text(library: SkillLibrary, name: str) -> str:
    return (library.folder / name / "SKILL.md").read_text(encoding="utf-8")


def test_a_first_sync_copies_every_skill_and_records_it(
    source: Path, library: SkillLibrary
) -> None:
    result = sync_bundled(library, source)

    assert sorted(result.copied) == ["github", "gws-gmail"]
    assert (library.folder / "github" / "references" / "auth").read_text() == "gh auth login"
    assert sorted(read_manifest(library.folder)) == ["github", "gws-gmail"]
    assert library.catalog.get("github") is not None
    assert sync_bundled(library, source).copied == []


def test_an_untouched_copy_is_updated_and_the_old_files_are_kept_as_a_version(
    source: Path, library: SkillLibrary
) -> None:
    sync_bundled(library, source)
    _skill(source, "github", "Do it better.")
    (source / "github" / "references" / "auth").unlink()

    result = sync_bundled(library, source)

    assert result.updated == ["github"]
    assert "Do it better." in _text(library, "github")
    assert not (library.folder / "github" / "references" / "auth").exists()
    assert len(library.versions("github")) == 1
    library.revert("github")
    assert "Do it." in _text(library, "github")


def test_a_copy_the_user_or_the_agent_changed_is_left_alone(
    source: Path, library: SkillLibrary
) -> None:
    sync_bundled(library, source)
    (library.folder / "github" / "SKILL.md").write_text("mine", encoding="utf-8")
    _skill(source, "github", "Do it better.")

    result = sync_bundled(library, source)

    assert result.kept == ["github"]
    assert _text(library, "github") == "mine"
    assert changed(library, "github", read_manifest(library.folder)) is True
    assert changed(library, "gws-gmail", read_manifest(library.folder)) is False


def test_what_running_a_script_leaves_is_not_a_change(source: Path, library: SkillLibrary) -> None:
    sync_bundled(library, source)
    cache = library.folder / "github" / "scripts" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "x.cpython-313.pyc").write_bytes(b"\0")
    _skill(source, "github", "Do it better.")

    assert sync_bundled(library, source).updated == ["github"]


def test_a_skill_the_user_deleted_is_not_copied_again(source: Path, library: SkillLibrary) -> None:
    sync_bundled(library, source)
    library.archive("gws-gmail")

    assert sync_bundled(library, source).copied == []
    assert not (library.folder / "gws-gmail").exists()


def test_a_skill_of_the_library_with_the_same_name_is_kept(
    source: Path, library: SkillLibrary
) -> None:
    _skill(library.folder, "github", "My own.")

    result = sync_bundled(library, source)

    assert result.taken == ["github"]
    assert "My own." in _text(library, "github")
    assert "github" not in read_manifest(library.folder)


def test_a_skill_gone_from_the_repository_leaves_the_manifest_once_no_copy_is_left(
    source: Path, library: SkillLibrary
) -> None:
    sync_bundled(library, source)
    for path in sorted((source / "gws-gmail").rglob("*"), reverse=True):
        path.unlink()
    (source / "gws-gmail").rmdir()

    assert sync_bundled(library, source).dropped == []  # the copy is still there
    library.archive("gws-gmail")
    assert sync_bundled(library, source).dropped == ["gws-gmail"]
    assert "gws-gmail" not in read_manifest(library.folder)


def test_reset_puts_the_bundled_skill_back_and_it_is_updated_again(
    source: Path, library: SkillLibrary
) -> None:
    sync_bundled(library, source)
    (library.folder / "github" / "SKILL.md").write_text("mine", encoding="utf-8")

    reset_bundled(library, "github", source)

    assert "Do it." in _text(library, "github")
    assert changed(library, "github", read_manifest(library.folder)) is False
    _skill(source, "github", "Do it better.")
    assert sync_bundled(library, source).updated == ["github"]
    with pytest.raises(SkillChangeError, match="not a skill Atlas ships"):
        reset_bundled(library, "nope", source)


def test_the_skills_atlas_ships_are_valid_skills() -> None:
    shipped = bundled_skills(BUNDLED_FOLDER)
    library = SkillLibrary(BUNDLED_FOLDER)

    for name in shipped:
        skill = library.catalog.get(name)
        assert skill is not None, name
        assert skill.description, name
