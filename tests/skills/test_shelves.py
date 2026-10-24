"""A bot's skills on real folders and a real database: its own folder before the home's library,
the library's skills it may see, where a write lands, who created each skill, and a bot's skill
shared with the library."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.home import Database, migrate
from atlas.skills import (
    ARCHIVE_AFTER,
    CURATOR_EVERY,
    SCHEMA,
    Curator,
    Origin,
    SkillChangeError,
    SkillLibrary,
    SkillOperation,
    SkillShelves,
    SkillUsage,
    share,
)

T0 = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)


def _text(name: str, body: str = "Do the thing.") -> str:
    return f"---\nname: {name}\ndescription: Use when testing.\n---\n{body}\n"


def _create(name: str, body: str = "Do the thing.") -> SkillOperation:
    return SkillOperation("create", name, content=_text(name, body))


@pytest.fixture
def usage(tmp_path: Path) -> SkillUsage:
    database = Database(tmp_path / "atlas.db")
    migrate(database, (SCHEMA,))
    return SkillUsage(database)


@pytest.fixture
def library(tmp_path: Path, usage: SkillUsage) -> SkillLibrary:
    made = SkillLibrary(tmp_path / "skills", usage, lambda: T0, creator="atlas")
    made.apply([_create("weekly-report"), _create("deploy")], Origin.USER)
    return made


def _bot(tmp_path: Path, usage: SkillUsage, name: str = "finance") -> SkillLibrary:
    folder = tmp_path / "bots" / name / "skills"
    return SkillLibrary(folder, usage.owned_by(name), lambda: T0, creator=name)


def _names(shelves: SkillShelves) -> list[str]:
    return sorted(skill.name for skill in shelves.catalog.skills())


def test_a_bot_sees_its_own_skills_and_the_librarys(
    tmp_path: Path, usage: SkillUsage, library: SkillLibrary
) -> None:
    own = _bot(tmp_path, usage)
    own.apply([_create("bills"), _create("deploy", "The bot's own way.")], Origin.REVIEW)

    everything = SkillShelves(own, library)
    listed = SkillShelves(own, library, ["weekly-report"])
    nothing = SkillShelves(own, library, [])

    assert _names(everything) == ["bills", "deploy", "weekly-report"]
    # On a same name the bot's own folder wins (the workspace over the shared folder).
    found = everything.catalog.get("deploy")
    assert found is not None
    assert found.body == "The bot's own way."
    # The list filters the library; a bot's own skills are always its to see.
    assert _names(listed) == ["bills", "deploy", "weekly-report"]
    assert listed.catalog.get("deploy") == found
    assert _names(nothing) == ["bills", "deploy"]
    assert (
        SkillShelves(_bot(tmp_path, usage, "work"), library, ["deploy"]).catalog.get(
            "weekly-report"
        )
        is None
    )


def test_a_new_skill_is_the_bots_own_and_a_change_lands_where_the_skill_is(
    tmp_path: Path, usage: SkillUsage, library: SkillLibrary
) -> None:
    own = _bot(tmp_path, usage)
    shelves = SkillShelves(own, library)

    shelves.apply([_create("bills")], Origin.REVIEW)
    shelves.apply(
        [SkillOperation("patch", "weekly-report", old_string="thing", new_string="report")],
        Origin.USER,
    )
    shelves.apply(
        [SkillOperation("patch", "bills", old_string="thing", new_string="bills")], Origin.USER
    )

    assert (own.folder / "bills" / "SKILL.md").is_file()
    assert not (library.folder / "bills").exists()
    assert "Do the report." in (library.folder / "weekly-report" / "SKILL.md").read_text("utf-8")
    assert "Do the bills." in (own.folder / "bills" / "SKILL.md").read_text("utf-8")
    # The rule for shared folders: created locally, updated in place.
    mine = usage.owned_by("finance").record("bills")
    assert mine is not None
    assert (mine.owner, mine.creator, mine.origin, mine.patch_count) == (
        "finance",
        "finance",
        Origin.REVIEW,
        1,
    )
    theirs = usage.record("weekly-report")
    assert theirs is not None
    assert (theirs.owner, theirs.creator, theirs.patch_count) == ("", "atlas", 1)
    assert usage.record("bills") is None


def test_a_call_for_both_folders_or_a_new_skill_that_would_hide_the_librarys_is_refused(
    tmp_path: Path, usage: SkillUsage, library: SkillLibrary
) -> None:
    shelves = SkillShelves(_bot(tmp_path, usage), library)
    patch = SkillOperation("patch", "deploy", old_string="thing", new_string="deploy")

    with pytest.raises(SkillChangeError, match="not both"):
        shelves.apply([_create("bills"), patch], Origin.USER)
    with pytest.raises(SkillChangeError, match="already in the home's library"):
        shelves.apply([_create("deploy")], Origin.USER)
    # A library skill the bot may not see is not there for it: the name is free in its own folder.
    hidden = SkillShelves(_bot(tmp_path, usage, "work"), library, [])
    hidden.apply([_create("deploy", "Work's own.")], Origin.USER)

    assert not (tmp_path / "bots" / "finance" / "skills").exists()
    assert "Do the thing." in (library.folder / "deploy" / "SKILL.md").read_text("utf-8")
    assert "Work's own." in (hidden.own.folder / "deploy" / "SKILL.md").read_text("utf-8")


def test_a_load_is_counted_where_the_skill_is(
    tmp_path: Path, usage: SkillUsage, library: SkillLibrary
) -> None:
    own = _bot(tmp_path, usage)
    shelves = SkillShelves(own, library)
    shelves.apply([_create("bills")], Origin.REVIEW)

    shelves.used("bills", T0)
    shelves.used("deploy", T0)
    shelves.used("deploy", T0)

    counts = {(r.owner, r.name): r.use_count for r in usage.every_record()}
    assert counts == {("", "deploy"): 2, ("", "weekly-report"): 0, ("finance", "bills"): 1}
    assert [record.name for record in usage.owned_by("finance").records()] == ["bills"]


def test_two_bots_may_each_have_a_skill_of_the_same_name(
    tmp_path: Path, usage: SkillUsage, library: SkillLibrary
) -> None:
    finance = SkillShelves(_bot(tmp_path, usage), library)
    work = SkillShelves(_bot(tmp_path, usage, "work"), library)

    finance.apply([_create("notes", "Finance's.")], Origin.REVIEW)
    work.apply([_create("notes", "Work's.")], Origin.USER)

    made = {(r.owner, r.creator, r.origin) for r in usage.every_record() if r.name == "notes"}
    assert made == {("finance", "finance", Origin.REVIEW), ("work", "work", Origin.USER)}
    assert _names(finance) == _names(work) == ["deploy", "notes", "weekly-report"]


def test_a_bots_skill_shared_with_the_library_keeps_who_created_it(
    tmp_path: Path, usage: SkillUsage, library: SkillLibrary
) -> None:
    own = _bot(tmp_path, usage)
    SkillShelves(own, library).apply([_create("bills")], Origin.REVIEW)
    usage.owned_by("finance").used("bills", T0)

    moved = share("bills", own, library)

    assert moved == library.folder / "bills"
    assert not (own.folder / "bills").exists()
    record = usage.record("bills")
    assert record is not None
    assert (record.owner, record.creator, record.origin, record.use_count) == (
        "",
        "finance",
        Origin.REVIEW,
        1,
    )
    assert usage.owned_by("finance").records() == []
    # Every agent sees it now.
    assert "bills" in _names(SkillShelves(_bot(tmp_path, usage, "work"), library))
    with pytest.raises(SkillChangeError, match="There is no skill 'bills'"):
        share("bills", own, library)
    SkillShelves(_bot(tmp_path, usage, "work"), library, []).apply([_create("deploy")], Origin.USER)
    with pytest.raises(SkillChangeError, match="already has a skill 'deploy'"):
        share("deploy", _bot(tmp_path, usage, "work"), library)


def test_the_curator_archives_a_bots_stale_skills_too(
    tmp_path: Path, usage: SkillUsage, library: SkillLibrary
) -> None:
    own = _bot(tmp_path, usage)
    SkillShelves(own, library).apply([_create("bills")], Origin.REVIEW)
    now = [T0]
    curator = Curator(library, usage, lambda: now[0], more=lambda: [own])
    curator.run()  # the first pass only records itself

    now[0] = T0 + ARCHIVE_AFTER + CURATOR_EVERY + timedelta(days=1)
    run = curator.run()

    assert run is not None
    assert run.archived == ("bills",)
    assert not (own.folder / "bills").exists()
    assert (own.folder / ".archive" / "bills" / "SKILL.md").is_file()
    # The library's were the main agent's turns, not the review's: they stay.
    assert (library.folder / "deploy" / "SKILL.md").is_file()


def test_a_deleted_bots_rows_are_forgotten(
    tmp_path: Path, usage: SkillUsage, library: SkillLibrary
) -> None:
    SkillShelves(_bot(tmp_path, usage), library).apply([_create("bills")], Origin.REVIEW)

    usage.owned_by("finance").forget()

    assert [(r.owner, r.name) for r in usage.every_record()] == [
        ("", "deploy"),
        ("", "weekly-report"),
    ]


def test_the_rows_kept_before_owners_move_to_the_library_and_the_reviews_are_the_main_agents(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "atlas.db")
    migrate(database, (type(SCHEMA)(SCHEMA.subject, SCHEMA.migrations[:1]),))
    connection = sqlite3.connect(tmp_path / "atlas.db")
    connection.execute(
        "INSERT INTO skills_usage (name, origin, created_at, use_count, last_used_at) VALUES "
        "('learned', 'review', '2026-10-09T12:00:00.000Z', 3, '2026-10-10T12:00:00.000Z'), "
        "('by-hand', 'user', '2026-10-09T12:00:00.000Z', 1, NULL)"
    )
    connection.commit()
    connection.close()

    migrate(database, (SCHEMA,))

    kept = {record.name: record for record in SkillUsage(database).records()}
    assert (kept["learned"].owner, kept["learned"].creator, kept["learned"].use_count) == (
        "",
        "atlas",
        3,
    )
    assert kept["learned"].last_used_at == datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
    assert (kept["by-hand"].creator, kept["by-hand"].origin) == (None, Origin.USER)
