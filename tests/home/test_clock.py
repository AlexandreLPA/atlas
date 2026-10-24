"""Every stored time is UTC in one format; V3 mixed local and UTC times and joins went wrong."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from atlas.home import TimeFormatError, format_utc, parse_utc, utc_now


def test_now_is_aware_and_in_utc() -> None:
    assert utc_now().utcoffset() == timedelta(0)


def test_the_format_is_the_one_javascript_writes() -> None:
    moment = datetime(2026, 10, 7, 12, 34, 56, 789_000, tzinfo=UTC)

    assert format_utc(moment) == "2026-10-07T12:34:56.789Z"


def test_a_time_in_another_zone_is_written_as_utc() -> None:
    sao_paulo = timezone(timedelta(hours=-3))
    moment = datetime(2026, 10, 7, 9, 0, tzinfo=sao_paulo)

    assert format_utc(moment) == "2026-10-07T12:00:00.000Z"


def test_a_naive_time_is_refused() -> None:
    with pytest.raises(TimeFormatError, match="no time zone"):
        format_utc(datetime(2026, 10, 7, 12, 0))  # noqa: DTZ001 - the case under test


def test_reading_back_gives_the_same_moment() -> None:
    moment = datetime(2026, 10, 7, 12, 34, 56, 789_000, tzinfo=UTC)

    assert parse_utc(format_utc(moment)) == moment
    assert parse_utc(format_utc(moment)).tzinfo == UTC


@pytest.mark.parametrize(
    "text",
    [
        "2026-10-07T12:34:56",  # no zone
        "2026-10-07T12:34:56.789+00:00",  # another spelling of UTC
        "2026-10-07T09:34:56.789-03:00",  # local time
        "2026-10-07 12:34:56.789Z",  # no T
        "2026-13-07T12:34:56.789Z",  # month 13
    ],
)
def test_only_the_one_format_is_read(text: str) -> None:
    with pytest.raises(TimeFormatError):
        parse_utc(text)


def test_sorting_the_text_sorts_the_time() -> None:
    start = datetime(2026, 10, 7, 23, 59, 59, 999_000, tzinfo=UTC)
    moments = [start + timedelta(milliseconds=step * 37) for step in range(50)]
    texts = [format_utc(moment) for moment in reversed(moments)]

    assert sorted(texts) == [format_utc(moment) for moment in moments]
    assert len({len(text) for text in texts}) == 1
