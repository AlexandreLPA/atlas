"""The background review's hold on skills, and the curator.

**A review is a context**, marked with a context variable: while one runs, a skill it creates is
the review's (`Origin.REVIEW`), and the files it loads are marked read. These guards apply only
there:

- **Read before write.** An existing `SKILL.md` or support file is changed only after the review
  loaded it, with use_skill or read_file: the review must not patch what it only inferred from the
  conversation.
- **Never delete.** The review may improve any skill but archives none.

**The curator** has no model pass: when Atlas has been idle for 2 hours and the last pass is 7
days old, the review's skills nobody loaded or changed for 30 days are archived
(`atlas.skills.to_archive`). The first pass only records itself, so a new home loses nothing on
its first day.
"""

import logging
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from atlas.home import utc_now
from atlas.skills.errors import SkillChangeError
from atlas.skills.library import SkillLibrary, SkillOperation, WriteGuard, WriteNote
from atlas.skills.model import CURATOR_EVERY, CURATOR_IDLE, skill_diff, to_archive
from atlas.skills.store import CuratorClaim, SkillUsage

_logger = logging.getLogger("atlas.skills")


@dataclass(slots=True)
class Review:
    """A background review running in this context: the files it has loaded, and the skills it
    created or changed, which its summary says. The summary reads the review's own results, so a
    skill another conversation or the user writes meanwhile is never the review's.

    The skill diffs come from the same notes, so the review's diff spans its calls rather than
    one skill_manage call."""

    read: set[Path] = field(default_factory=lambda: set[Path]())
    #: The skills it created, and the others it changed, by name, in order.
    created: list[str] = field(default_factory=list[str])
    updated: list[str] = field(default_factory=list[str])
    #: What each file it wrote held before its first write and holds after its last, by skill
    #: and by the file's path inside it.
    files: dict[str, dict[str, tuple[bytes | None, bytes | None]]] = field(
        default_factory=lambda: {}
    )

    def note(self) -> WriteNote:
        def keep(name: str, path: PurePosixPath, before: bytes | None, after: bytes | None) -> None:
            files = self.files.setdefault(name, {})
            first, _ = files.get(str(path), (before, None))
            files[str(path)] = (first, after)

        return keep

    def diffs(self) -> dict[str, str]:
        """The skill diff of each skill it changed, by name: its own writes only, so what another
        conversation or the user wrote meanwhile is not in it."""
        diffs: dict[str, str] = {}
        for name, files in self.files.items():
            diff = skill_diff(
                {path: (_text(before), _text(after)) for path, (before, after) in files.items()}
            )
            if diff:
                diffs[name] = diff
        return diffs

    def wrote(self, operations: Sequence[SkillOperation]) -> None:
        """Note what an applied call did, skill by skill."""
        for operation in operations:
            name = operation.name
            if operation.action == "create":
                if name not in self.created:
                    self.created.append(name)
            elif name not in self.created and name not in self.updated:
                self.updated.append(name)

    def guard(self) -> WriteGuard:
        def check(name: str, path: Path, action: str) -> str | None:
            if action == "delete":
                return (
                    f"The background review may not delete skills; '{name}' stays. Patch it, or "
                    "say in your reply that it overlaps another."
                )
            if _resolved(path) in self.read:
                return None
            label = path.name if path.name.lower() == "skill.md" else str(path)
            how = (
                f"call use_skill('{name}')"
                if path.name.lower() == "skill.md"
                else f"read {path} with read_file"
            )
            return (
                f"Refusing background review {action} for skill '{name}': the current {label} "
                f"content has not been loaded in this review. First {how}, then retry the write "
                "using the content just returned."
            )

        return check


_review: ContextVar[Review | None] = ContextVar("atlas_skill_review", default=None)


@contextmanager
def reviewing() -> Generator[Review]:
    """Run the enclosed code as a background review."""
    review = Review()
    token = _review.set(review)
    try:
        yield review
    finally:
        _review.reset(token)


def current_review() -> Review | None:
    return _review.get()


def mark_read(path: Path) -> None:
    """Note that the review running in this context loaded `path`; nothing outside a review."""
    review = _review.get()
    if review is not None:
        review.read.add(_resolved(path))


def _text(data: bytes | None) -> str | None:
    return None if data is None else data.decode("utf-8", errors="replace")


def _resolved(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


@dataclass(frozen=True, slots=True)
class CuratorRun:
    started_at: datetime
    #: `SEEDED` on the first pass ever, which archives nothing.
    claim: CuratorClaim
    archived: tuple[str, ...] = ()


class Curator:
    """Archives the review's skills nobody uses."""

    def __init__(
        self,
        library: SkillLibrary,
        usage: SkillUsage,
        clock: Callable[[], datetime] = utc_now,
        *,
        more: Callable[[], Sequence[SkillLibrary]] = lambda: (),
    ) -> None:
        """`more` gives the other folders a pass covers: each bot's own skills."""
        self._library = library
        self._usage = usage
        self._clock = clock
        self._more = more

    def run(self, idle_for: timedelta | None = None) -> CuratorRun | None:
        """A pass when one is due: `None` when Atlas was busy within 2 hours (`idle_for`, when the
        caller measures it) or the last pass is less than 7 days old."""
        if idle_for is not None and idle_for < CURATOR_IDLE:
            return None
        now = self._clock()
        claim = self._usage.claim_curator_run(now, CURATOR_EVERY)
        if claim is CuratorClaim.NOT_DUE:
            return None
        if claim is CuratorClaim.SEEDED:
            _logger.info("The curator ran for the first time; its first pass is in 7 days.")
            return CuratorRun(now, claim)
        archived: list[str] = []
        for library in (self._library, *self._more()):
            usage = self._usage if library is self._library else library.usage
            if usage is None:
                continue
            for name in to_archive(usage.records(), now):
                try:
                    library.archive(name)
                except SkillChangeError as error:
                    _logger.warning("The curator could not archive a skill: %s", error)
                    continue
                archived.append(name)
        self._usage.finish_curator_run(now, len(archived))
        _logger.info("The curator archived %d skill(s).", len(archived), extra={"skills": archived})
        return CuratorRun(now, claim, tuple(archived))
