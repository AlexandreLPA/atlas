"""Schedules and their next runs, on real time zones."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from atlas.jobs import Lateness, ScheduleError, ScheduleKind, is_silent, next_run, parse_schedule
from atlas.jobs.model import grace_seconds, lateness, parse_duration

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
TORONTO = ZoneInfo("America/Toronto")
NOW = datetime(2026, 10, 12, 10, 0, tzinfo=SAO_PAULO)


def test_durations_read_as_minutes() -> None:
    assert [parse_duration(t) for t in ("30m", "2h", "1d", "hour", "90 minutes")] == [
        30,
        120,
        1440,
        60,
        90,
    ]
    with pytest.raises(ScheduleError, match="not a duration"):
        parse_duration("soon")


def test_every_and_a_bare_duration_repeat_and_in_runs_once() -> None:
    every = parse_schedule("every 30m", NOW)
    bare = parse_schedule("2h", NOW)
    once = parse_schedule("in 2h", NOW)
    assert (every.kind, every.minutes, every.display) == (ScheduleKind.INTERVAL, 30, "every 30m")
    assert (bare.kind, bare.minutes) == (ScheduleKind.INTERVAL, 120)
    assert once.kind is ScheduleKind.ONCE
    assert once.run_at == NOW.astimezone(UTC) + timedelta(hours=2)


def test_natural_phrases_compile_to_cron_on_the_wall_clock() -> None:
    cases = {
        "every monday 9am": "0 9 * * 1",
        "weekdays at 9am": "0 9 * * 1-5",
        "every day at 18:30": "30 18 * * *",
        "every monday, wednesday and friday at noon": "0 12 * * 1,3,5",
        "weekends at 7": "0 7 * * 0,6",
        "0 8 * * *": "0 8 * * *",
    }
    for text, expr in cases.items():
        parsed = parse_schedule(text, NOW)
        assert (parsed.kind, parsed.expr, parsed.display) == (ScheduleKind.CRON, expr, text)


def test_an_iso_time_runs_once_in_the_routine_zone_when_it_names_none() -> None:
    parsed = parse_schedule("2026-10-20T14:00", NOW)
    assert parsed.kind is ScheduleKind.ONCE
    assert parsed.run_at == datetime(2026, 10, 20, 17, 0, tzinfo=UTC)


def test_what_is_not_a_schedule_says_the_formats() -> None:
    for text in ("sometime", "every blue moon", "61 25 * * *", "2026-13-40"):
        with pytest.raises(ScheduleError):
            parse_schedule(text, NOW)
    with pytest.raises(ScheduleError, match="every 30m"):
        parse_schedule("sometime", NOW)


def test_an_interval_counts_from_the_run() -> None:
    parsed = parse_schedule("every 45m", NOW)
    assert next_run(parsed, SAO_PAULO, NOW) == NOW.astimezone(UTC) + timedelta(minutes=45)


def test_cron_runs_at_the_zone_wall_clock() -> None:
    parsed = parse_schedule("weekdays at 9am", NOW)
    friday_evening = datetime(2026, 10, 16, 20, 0, tzinfo=SAO_PAULO)
    assert next_run(parsed, SAO_PAULO, friday_evening) == datetime(
        2026, 10, 19, 9, 0, tzinfo=SAO_PAULO
    )


def test_cron_keeps_its_hour_across_daylight_saving() -> None:
    """09:00 America/Toronto stays 09:00 on the days around the change."""
    parsed = parse_schedule("every day at 9am", NOW)
    before = datetime(2026, 11, 1, 10, 0, tzinfo=TORONTO)  # the clocks went back at 2:00
    upcoming = next_run(parsed, TORONTO, before)
    assert upcoming is not None
    assert upcoming.astimezone(TORONTO).hour == 9
    assert upcoming.astimezone(TORONTO).date().isoformat() == "2026-11-02"


def test_in_the_repeated_autumn_hour_the_next_run_is_after_the_last() -> None:
    parsed = parse_schedule("30 1 * * *", NOW)
    second_one_thirty = datetime(2026, 11, 1, 1, 30, tzinfo=TORONTO, fold=1)
    upcoming = next_run(parsed, TORONTO, second_one_thirty)
    assert upcoming is not None
    assert upcoming > second_one_thirty.astimezone(UTC)


def test_a_one_shot_runs_once_and_one_made_just_past_its_time_still_runs() -> None:
    parsed = parse_schedule("2026-10-12T09:59", NOW)
    assert next_run(parsed, SAO_PAULO, NOW) == parsed.run_at  # 1 minute late, within 2
    assert next_run(parsed, SAO_PAULO, NOW, ran=True) is None
    old = parse_schedule("2026-10-12T09:00", NOW)
    assert next_run(old, SAO_PAULO, NOW) is None


def test_the_grace_is_half_the_period_within_two_minutes_and_two_hours() -> None:
    assert grace_seconds(parse_schedule("every 1m", NOW), SAO_PAULO, NOW) == 120
    assert grace_seconds(parse_schedule("every 1h", NOW), SAO_PAULO, NOW) == 1800
    assert grace_seconds(parse_schedule("every day at 9am", NOW), SAO_PAULO, NOW) == 7200
    assert grace_seconds(parse_schedule("in 1h", NOW), SAO_PAULO, NOW) == 120


def test_a_run_is_on_time_late_or_a_catch_up() -> None:
    due = NOW
    assert lateness(due, due + timedelta(minutes=2), 7200) is Lateness.ON_TIME
    assert lateness(due, due + timedelta(minutes=30), 7200) is Lateness.LATE
    assert lateness(due, due + timedelta(hours=3), 7200) is Lateness.CATCH_UP


def test_silence_is_the_mark_alone_or_on_the_first_or_last_line() -> None:
    assert is_silent("[SILENT]")
    assert is_silent("NO_REPLY")
    assert is_silent("Nothing new today.\n[SILENT]")
    assert is_silent("SILENT\nchecked 3 feeds")
    assert not is_silent("The word [SILENT] inside a sentence.")
    assert not is_silent("")
    assert not is_silent("Two new posts.")
