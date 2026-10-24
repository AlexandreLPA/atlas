"""Every stored time is UTC, in one text format.

The format is ISO 8601 with milliseconds and a `Z`: `2026-10-07T12:34:56.789Z`. It is what
JavaScript's `Date.toISOString()` produces, so the database, the client protocol and the desktop app
write the same string. Every value has the same width, so sorting the text sorts the time.

Atlas V3 wrote local time without a zone in some tables and UTC in others, and joining two tables by
time gave wrong answers in silence. Parsing here accepts only this format, so a second one cannot
creep in.
"""

import re
from datetime import UTC, datetime, tzinfo
from zoneinfo import ZoneInfo

from atlas.home.errors import TimeFormatError

_UTC_TEXT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z")


def utc_now() -> datetime:
    """The current time, aware and in UTC."""
    return datetime.now(UTC)


def format_utc(moment: datetime) -> str:
    """Write an aware datetime as UTC text. A naive datetime is refused: its zone is a guess."""
    if moment.utcoffset() is None:
        raise TimeFormatError(
            f"{moment!r} has no time zone. Build it with datetime.now(UTC) or attach a tzinfo."
        )
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_utc(text: str) -> datetime:
    """Read text written by `format_utc` back into an aware UTC datetime."""
    problem = (
        f"{text!r} is not a UTC time in Atlas's format, which looks like 2026-10-07T12:34:56.789Z."
    )
    if not _UTC_TEXT.fullmatch(text):
        raise TimeFormatError(problem)
    try:
        return datetime.fromisoformat(text)
    except ValueError as error:  # the shape is right, a field is not (month 13, hour 25)
        raise TimeFormatError(problem) from error


def time_zone(name: str | None) -> tzinfo:
    """The zone routines run in: the IANA zone named, or the system's as it is now, which keeps
    one offset: name a zone where clocks change for daylight saving."""
    if name is not None:
        return ZoneInfo(name)
    local = datetime.now().astimezone().tzinfo
    return local if local is not None else UTC
