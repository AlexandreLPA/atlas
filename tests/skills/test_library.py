"""Changing skills on real folders and a real database: whole or nothing, the previous version kept
first, archiving in place of deleting, and the usage each change records.

The version rules come from Atlas V3's test_skill_revisions.py, ported: the clock's resolution is
not a unique key, the order is by name and not by file time, the vault survives the skill, and a
revert keeps the current state before it replaces it.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import pytest

from atlas.home import Database, migrate
from atlas.skills import (
    ARCHIVE_FOLDER,
    SCHEMA,
    VERSIONS_FOLDER,
    VERSIONS_KEPT,
    Origin,
    SkillChangeError,
    SkillLibrary,
    SkillOperation,
    SkillUsage,
)
from atlas.skills.model import (
    SKILL_DIFF_FILES,
    SKILL_DIFF_LINES,
    newest_first,
    skill_diff,
    skill_text_problem,
    support_path,
    version_name,
)

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def _text(name: str, body: str = "Do the thing.", description: str = "Use when testing.") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n{body}\n"


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def usage(tmp_path: Path) -> SkillUsage:
    database = Database(tmp_path / "atlas.db")
    migrate(database, (SCHEMA,))
    return SkillUsage(database)


@pytest.fixture
def library(tmp_path: Path, usage: SkillUsage, clock: _Clock) -> SkillLibrary:
    return SkillLibrary(tmp_path / "skills", usage, clock)


def _create(library: SkillLibrary, name: str, body: str = "Do the thing.") -> None:
    library.apply([SkillOperation("create", name, content=_text(name, body))], Origin.USER)


# -- the rules a write passes --------------------------------------------------------------------


def test_a_skill_text_needs_frontmatter_its_own_name_a_short_description_and_a_body() -> None:
    assert skill_text_problem(_text("deploy"), "deploy", new=True) is None
    assert "start with YAML frontmatter" in str(skill_text_problem("Just text.", "x", new=True))
    assert "not closed" in str(skill_text_problem("---\nname: x\nbody", "x", new=True))
    no_description = "---\nname: x\n---\nbody\n"
    assert "'description'" in str(skill_text_problem(no_description, "x", new=True))
    assert "must be the same" in str(skill_text_problem(_text("other"), "x", new=True))
    assert "content after" in str(skill_text_problem(_text("x", body=""), "x", new=True))
    long = _text("x", description="Use when " + "y" * 60)
    assert "60-char listing" in str(skill_text_problem(long, "x", new=True))
    assert skill_text_problem(long, "x", new=False) is None  # an old skill stays editable


def test_a_support_file_lives_in_one_of_four_folders_inside_its_skill() -> None:
    assert str(support_path("references/api.md")) == "references/api.md"
    assert str(support_path("references\\api.md")) == "references/api.md"
    assert str(support_path("SKILL.md")) == "SKILL.md"
    assert "traversal" in str(support_path("references/../../x.md"))
    assert "absolute" in str(support_path("/etc/passwd"))
    assert "absolute" in str(support_path("C:/x.md"))
    assert "must be under one of" in str(support_path("notes.md"))
    assert "not just a folder" in str(support_path("references"))


# -- create, patch, files ------------------------------------------------------------------------


def test_create_writes_the_skill_and_records_it_as_the_users(
    library: SkillLibrary, usage: SkillUsage
) -> None:
    lines = library.apply(
        [SkillOperation("create", "deploy", content=_text("deploy"))], Origin.USER
    )

    assert "created" in lines[0]
    assert library.catalog.get("deploy") is not None
    record = usage.record("deploy")
    assert record is not None
    assert record.origin is Origin.USER
    assert record.created_at == T0


def test_a_batch_applies_together_and_a_refused_operation_changes_nothing(
    library: SkillLibrary,
) -> None:
    _create(library, "deploy")
    with pytest.raises(SkillChangeError, match=r"Operation 2 .*Nothing was changed"):
        library.apply(
            [
                SkillOperation("patch", "deploy", old_string="Do the thing.", new_string="Ship."),
                SkillOperation("patch", "deploy", old_string="absent", new_string="x"),
            ],
            Origin.USER,
        )
    folder = library.folder / "deploy"
    assert "Do the thing." in (folder / "SKILL.md").read_text(encoding="utf-8")

    library.apply(
        [
            SkillOperation("create", "release", content=_text("release")),
            SkillOperation(
                "write_file", "release", file_path="references/steps.md", file_content="1. Tag."
            ),
            SkillOperation("patch", "deploy", old_string="Do the thing.", new_string="Ship it."),
        ],
        Origin.USER,
    )
    assert (library.folder / "release" / "references" / "steps.md").is_file()
    assert "Ship it." in (folder / "SKILL.md").read_text(encoding="utf-8")


def test_a_patch_must_keep_skill_md_whole_and_find_its_text_once(library: SkillLibrary) -> None:
    _create(library, "deploy", body="Step.\nStep.")

    for operation, error in [
        (SkillOperation("patch", "deploy", old_string="Step.", new_string="Go."), "2 times"),
        (SkillOperation("patch", "deploy", old_string="---\nname", new_string="x"), "break"),
        (SkillOperation("patch", "deploy", old_string="nothing", new_string="x"), "not found"),
        (SkillOperation("patch", "ghost", old_string="a", new_string="b"), "not found"),
        (SkillOperation("create", "deploy", content=_text("deploy")), "already exists"),
        (SkillOperation("create", "Bad Name", content=_text("Bad Name")), "Invalid skill name"),
        (SkillOperation("write_file", "deploy", file_path="SKILL.md", file_content="x"), "patch"),
        (SkillOperation("remove_file", "deploy", file_path="references/x.md"), "not found"),
    ]:
        with pytest.raises(SkillChangeError, match=error):
            library.apply([operation], Origin.USER)

    library.apply(
        [SkillOperation("patch", "deploy", old_string="Step.", new_string="Go.", replace_all=True)],
        Origin.USER,
    )
    assert "Go.\nGo." in (library.folder / "deploy" / "SKILL.md").read_text(encoding="utf-8")


def test_a_rewrite_replaces_skill_md_whole(library: SkillLibrary) -> None:
    _create(library, "deploy")

    library.apply(
        [SkillOperation("patch", "deploy", content=_text("deploy", body="All new."))], Origin.USER
    )

    assert "All new." in (library.folder / "deploy" / "SKILL.md").read_text(encoding="utf-8")


def test_a_guard_can_refuse_changing_a_file_that_exists(library: SkillLibrary) -> None:
    _create(library, "deploy")
    asked: list[tuple[str, str]] = []

    def guard(name: str, path: Path, action: str) -> str | None:
        asked.append((path.name, action))
        return "not read yet"

    with pytest.raises(SkillChangeError, match="not read yet"):
        library.apply(
            [SkillOperation("patch", "deploy", old_string="Do", new_string="Did")],
            Origin.REVIEW,
            guard,
        )
    library.apply(  # a new support file asks nothing
        [SkillOperation("write_file", "deploy", file_path="references/a.md", file_content="A")],
        Origin.REVIEW,
        guard,
    )

    assert asked == [("SKILL.md", "patch")]


# -- versions ------------------------------------------------------------------------------------


def test_the_clock_alone_is_not_a_unique_version_name() -> None:
    first = version_name([], T0)
    second = version_name([first], T0)
    third = version_name([first, second], T0)

    assert len({first, second, third}) == 3
    assert newest_first([first, third, second, "notes", f"{first}__10"]) == [
        f"{first}__10",
        third,
        second,
        first,
    ]


def test_every_write_keeps_the_previous_version_and_creating_keeps_none(
    library: SkillLibrary, clock: _Clock
) -> None:
    _create(library, "deploy")
    assert library.versions("deploy") == []

    lines = library.apply(
        [SkillOperation("patch", "deploy", old_string="Do the thing.", new_string="Ship.")],
        Origin.USER,
    )

    assert "previous version of deploy was kept" in lines[-1]
    (version,) = library.versions("deploy")
    kept = library.folder / VERSIONS_FOLDER / "deploy" / version / "files" / "SKILL.md"
    assert "Do the thing." in kept.read_text(encoding="utf-8")
    assert library.catalog.get(VERSIONS_FOLDER) is None  # versions are not skills


def test_versions_are_capped_and_the_newest_stay(library: SkillLibrary, clock: _Clock) -> None:
    _create(library, "deploy", body="v0")
    for n in range(VERSIONS_KEPT + 3):
        clock.now = T0 + timedelta(seconds=n + 1)
        library.apply(
            [SkillOperation("patch", "deploy", old_string=f"v{n}", new_string=f"v{n + 1}")],
            Origin.USER,
        )

    kept = library.versions("deploy")
    assert len(kept) == VERSIONS_KEPT
    newest = library.folder / VERSIONS_FOLDER / "deploy" / kept[0] / "files" / "SKILL.md"
    assert f"v{VERSIONS_KEPT + 2}" in newest.read_text(encoding="utf-8")


def test_revert_puts_the_files_back_and_keeps_the_current_state_first(
    library: SkillLibrary, clock: _Clock
) -> None:
    _create(library, "deploy")
    clock.now = T0 + timedelta(seconds=1)
    library.apply(
        [
            SkillOperation("patch", "deploy", old_string="Do the thing.", new_string="Ship."),
            SkillOperation(
                "write_file", "deploy", file_path="references/new.md", file_content="New."
            ),
        ],
        Origin.USER,
    )
    folder = library.folder / "deploy"
    clock.now = T0 + timedelta(seconds=2)

    library.revert("deploy")

    assert "Do the thing." in (folder / "SKILL.md").read_text(encoding="utf-8")
    assert not (folder / "references" / "new.md").exists()  # created by the change it undid
    assert len(library.versions("deploy")) == 2
    library.revert("deploy")  # the revert itself can be undone
    assert "Ship." in (folder / "SKILL.md").read_text(encoding="utf-8")
    with pytest.raises(SkillChangeError, match="No version"):
        library.revert("ghost")
    with pytest.raises(SkillChangeError, match="is not a version"):
        library.revert("deploy", "19990101T000000000000")


def test_versions_survive_the_skill_and_a_revert_brings_it_back(library: SkillLibrary) -> None:
    _create(library, "deploy")
    library.apply(
        [SkillOperation("patch", "deploy", old_string="Do the thing.", new_string="Ship.")],
        Origin.USER,
    )
    folder = library.folder / "deploy"
    for path in sorted(folder.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    folder.rmdir()  # removed by hand, outside Atlas

    library.revert("deploy")

    assert "Do the thing." in (folder / "SKILL.md").read_text(encoding="utf-8")


def test_a_write_whose_version_cannot_be_kept_is_refused(library: SkillLibrary) -> None:
    _create(library, "deploy")
    (library.folder / VERSIONS_FOLDER).write_text("a file where the folder goes", encoding="utf-8")

    with pytest.raises(SkillChangeError, match="could not be kept"):
        library.apply(
            [SkillOperation("patch", "deploy", old_string="Do the thing.", new_string="Ship.")],
            Origin.USER,
        )

    text = (library.folder / "deploy" / "SKILL.md").read_text(encoding="utf-8")
    assert "Do the thing." in text


# -- archive and restore -------------------------------------------------------------------------


def test_delete_archives_and_restore_brings_the_skill_back(
    library: SkillLibrary, usage: SkillUsage, clock: _Clock
) -> None:
    _create(library, "deploy")
    with pytest.raises(SkillChangeError, match="only operation"):
        library.apply(
            [SkillOperation("delete", "deploy"), SkillOperation("delete", "other")], Origin.USER
        )

    (line,) = library.apply([SkillOperation("delete", "deploy")], Origin.USER)

    assert "atlas skills restore deploy" in line
    assert library.catalog.get("deploy") is None
    assert library.archived() == ["deploy"]
    record = usage.record("deploy")
    assert record is not None
    assert record.archived_at == T0

    clock.now = T0 + timedelta(days=3)
    library.restore("deploy")
    assert library.catalog.get("deploy") is not None
    record = usage.record("deploy")
    assert record is not None
    assert record.archived_at is None
    assert record.restored_at == clock.now


def test_a_second_archive_of_the_same_name_is_kept_apart(
    library: SkillLibrary, clock: _Clock
) -> None:
    _create(library, "deploy", body="first")
    library.archive("deploy")
    _create(library, "deploy", body="second")
    clock.now = T0 + timedelta(hours=1)
    second = library.archive("deploy")

    assert second.name == "deploy-20261009130000"
    assert sorted(path.name for path in (library.folder / ARCHIVE_FOLDER).iterdir()) == [
        "deploy",
        "deploy-20261009130000",
    ]
    library.restore("deploy")
    with pytest.raises(SkillChangeError, match="exists already"):
        library.restore("deploy")
    with pytest.raises(SkillChangeError, match="No archived skill"):
        library.restore("ghost")


# -- usage ---------------------------------------------------------------------------------------


def test_usage_counts_loads_and_changes_and_a_hand_written_skill_is_the_users(
    usage: SkillUsage, library: SkillLibrary, clock: _Clock
) -> None:
    usage.used("by-hand", T0)
    usage.used("by-hand", T0 + timedelta(days=1))
    library.apply([SkillOperation("create", "made", content=_text("made"))], Origin.REVIEW)
    library.apply(
        [SkillOperation("patch", "made", old_string="Do the thing.", new_string="Done.")],
        Origin.REVIEW,
    )

    hand = usage.record("by-hand")
    made = usage.record("made")
    assert hand is not None
    assert hand.origin is Origin.USER
    assert hand.use_count == 2
    assert hand.last_used_at == T0 + timedelta(days=1)
    assert made is not None
    assert made.origin is Origin.REVIEW
    assert made.patch_count == 1
    assert {record.name for record in usage.records()} == {"by-hand", "made"}
    assert usage.record("nobody") is None


# -- the skill diff ------------------------------------------------------------------------------


def test_a_skill_diff_shows_only_what_changed_with_the_skill_file_first() -> None:
    before = "---\nname: notes\n---\nOne.\nTwo.\nThree.\nFour.\n"
    diff = skill_diff(
        {
            "references/new.md": (None, "Depth.\n"),
            "references/same.md": ("Kept.\n", "Kept.\n"),
            "references/gone.md": ("Old.\n", None),
            "SKILL.md": (before, before.replace("Three.", "Three, at most.")),
        }
    )

    assert diff.splitlines() == [
        "--- a/SKILL.md",
        "+++ b/SKILL.md",
        "@@ -5,3 +5,3 @@",
        " Two.",  # one line around the change, not three
        "-Three.",
        "+Three, at most.",
        " Four.",
        "--- a/references/gone.md",
        "+++ /dev/null",
        "@@ -1 +0,0 @@",
        "-Old.",
        "--- /dev/null",
        "+++ b/references/new.md",
        "@@ -0,0 +1 @@",
        "+Depth.",
    ]
    assert skill_diff({"SKILL.md": (before, before)}) == ""


def test_a_skill_diff_stops_at_its_line_limit_and_counts_what_it_left_out() -> None:
    long = "".join(f"line {n}\n" for n in range(200))
    cut = skill_diff({"SKILL.md": (None, long)}).splitlines()

    assert len(cut) == SKILL_DIFF_LINES + 1
    assert cut[-1] == "[Atlas: 123 more line(s); the skill's folder has the rest.]"  # 203 - 80

    many = skill_diff({f"references/{n}.md": (None, "x\n") for n in range(SKILL_DIFF_FILES + 2)})

    assert many.count("+++ b/references/") == SKILL_DIFF_FILES
    assert many.endswith(
        "[Atlas: 8 more line(s) in 2 more file(s); the skill's folder has the rest.]"
    )


def test_an_applied_call_tells_what_each_file_held_and_holds(library: SkillLibrary) -> None:
    _create(library, "deploy")
    heard: list[tuple[str, str, bytes | None, bytes | None]] = []

    def note(name: str, path: PurePosixPath, before: bytes | None, after: bytes | None) -> None:
        heard.append((name, str(path), before, after))

    library.apply(
        [
            SkillOperation("patch", "deploy", old_string="thing", new_string="deploy"),
            SkillOperation(
                "write_file", "deploy", file_path="references/hosts.md", file_content="One host."
            ),
        ],
        Origin.USER,
        note=note,
    )
    with pytest.raises(SkillChangeError):  # refused: nothing written, nothing told
        library.apply(
            [SkillOperation("patch", "deploy", old_string="absent", new_string="x")],
            Origin.USER,
            note=note,
        )

    assert heard == [
        (
            "deploy",
            "SKILL.md",
            _text("deploy").encode(),
            _text("deploy", "Do the deploy.").encode(),
        ),
        ("deploy", "references/hosts.md", None, b"One host."),
    ]
