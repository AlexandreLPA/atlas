"""The curator on real folders and a real database, with a clock that moves: the rule, the schedule,
and the user's skills never touched."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from atlas.home import Database, migrate
from atlas.skills import (
    ARCHIVE_AFTER,
    SCHEMA,
    Curator,
    CuratorClaim,
    Origin,
    SkillLibrary,
    SkillOperation,
    SkillRecord,
    SkillUsage,
    to_archive,
)

T0 = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


class _Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


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


def _made(library: SkillLibrary, name: str, origin: Origin) -> None:
    text = f"---\nname: {name}\ndescription: Use when testing.\n---\nSteps.\n"
    library.apply([SkillOperation("create", name, content=text)], origin)


# -- the rule ------------------------------------------------------------------------------------


def _record(**fields: object) -> SkillRecord:
    base: dict[str, object] = {"name": "s", "origin": Origin.REVIEW, "created_at": T0}
    return SkillRecord(**(base | fields))  # pyright: ignore[reportArgumentType]


def test_the_rule_archives_the_reviews_skills_idle_for_thirty_days() -> None:
    now = T0 + 31 * DAY
    assert to_archive([_record()], now) == ["s"]  # never used, 31 days old
    assert to_archive([_record()], T0 + 29 * DAY) == []
    assert to_archive([_record(origin=Origin.USER)], now) == []
    assert to_archive([_record(archived_at=T0)], now) == []
    assert to_archive([_record(use_count=1, last_used_at=T0 + 2 * DAY)], now) == []
    assert to_archive([_record(last_patched_at=T0 + 2 * DAY)], now) == []
    assert to_archive([_record(restored_at=T0 + 2 * DAY)], now) == []  # a restore counts
    assert to_archive([_record(use_count=1, last_used_at=T0)], T0 + ARCHIVE_AFTER) == ["s"]


# -- the pass ------------------------------------------------------------------------------------


def test_the_first_pass_only_records_itself_and_the_next_waits_seven_days(
    library: SkillLibrary, usage: SkillUsage, clock: _Clock
) -> None:
    _made(library, "old-trick", Origin.REVIEW)
    clock.now = T0 + 40 * DAY
    curator = Curator(library, usage, clock)

    first = curator.run()
    assert first is not None
    assert first.claim is CuratorClaim.SEEDED
    assert first.archived == ()
    assert library.catalog.get("old-trick") is not None

    clock.now += 6 * DAY
    assert curator.run() is None
    clock.now += DAY
    assert curator.run(idle_for=timedelta(hours=1)) is None  # Atlas was busy

    run = curator.run(idle_for=timedelta(hours=3))
    assert run is not None
    assert run.claim is CuratorClaim.CLAIMED
    assert run.archived == ("old-trick",)
    assert library.catalog.get("old-trick") is None
    assert library.archived() == ["old-trick"]


def test_the_curator_never_touches_the_users_skills_and_spares_the_used_ones(
    library: SkillLibrary, usage: SkillUsage, clock: _Clock
) -> None:
    _made(library, "users-own", Origin.USER)
    _made(library, "in-use", Origin.REVIEW)
    _made(library, "idle", Origin.REVIEW)
    curator = Curator(library, usage, clock)
    curator.run()  # seeds
    clock.now = T0 + 20 * DAY
    usage.used("in-use", clock.now)
    clock.now = T0 + 35 * DAY

    run = curator.run()

    assert run is not None
    assert run.archived == ("idle",)
    assert {skill.name for skill in library.catalog.skills()} == {"users-own", "in-use"}


def test_a_restored_skill_is_left_alone_by_the_next_pass(
    library: SkillLibrary, usage: SkillUsage, clock: _Clock
) -> None:
    _made(library, "idle", Origin.REVIEW)
    curator = Curator(library, usage, clock)
    curator.run()
    clock.now = T0 + 31 * DAY
    run = curator.run()
    assert run is not None
    assert run.archived == ("idle",)

    library.restore("idle")
    clock.now += 8 * DAY
    again = curator.run()

    assert again is not None
    assert again.archived == ()
    assert library.catalog.get("idle") is not None
