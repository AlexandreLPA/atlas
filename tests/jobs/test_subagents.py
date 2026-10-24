"""Subagents' records and rules: the summary budget and its footer, the batch notice, the
list's answer, and the table that outlives the process."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from atlas.__main__ import SCHEMAS
from atlas.home import Database, migrate
from atlas.jobs import (
    MAX_SUMMARY_CHARS,
    MIN_SUMMARY_CHARS,
    Subagent,
    SubagentStatus,
    SubagentStore,
    batch_notice,
    listed,
    missed_steer,
    summary_cap,
    trimmed_summary,
)

NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)


def _child(index: int = 0, **fields: object) -> Subagent:
    values: dict[str, object] = {
        "id": f"sa-{index}",
        "delegation_id": "d-1",
        "parent_id": "p",
        "conversation_id": f"c{index}",
        "index": index,
        "count": 2,
        "goal": f"Goal number {index}.",
        "context": "",
        "model": "gpt-6-luna",
        "started_at": NOW - timedelta(seconds=90),
    }
    return Subagent(**(values | fields))  # pyright: ignore[reportArgumentType]


# -- the summary budget -----------------------------------------------------------------------


def test_with_nothing_known_only_the_ceiling_holds() -> None:
    assert summary_cap(None, None, 3) == MAX_SUMMARY_CHARS
    assert summary_cap(200_000, None, 3) == MAX_SUMMARY_CHARS


def test_half_the_remaining_context_is_split_over_the_batch() -> None:
    # 10,000 tokens left: half of it over two summaries, four characters a token.
    assert summary_cap(30_000, 20_000, 2) == 10_000
    assert summary_cap(1_000_000, 10_000, 1) == MAX_SUMMARY_CHARS


def test_a_nearly_full_parent_still_gets_the_floor() -> None:
    assert summary_cap(30_000, 29_900, 10) == MIN_SUMMARY_CHARS
    assert summary_cap(30_000, 40_000, 1) == MIN_SUMMARY_CHARS


def test_an_over_budget_summary_keeps_its_head_and_tail_and_says_where_the_rest_is() -> None:
    summary = "\n".join(f"line {n:04d}" for n in range(3_000))

    cut = trimmed_summary(summary, 2_000, "C:/home/spill/sa-1.txt")

    assert cut.startswith("line 0000\n")
    assert "line 2999" in cut
    assert "line 1500" not in cut
    assert "[... middle omitted — see footer ...]" in cut
    assert "[SUMMARY TRUNCATED]" in cut
    assert "Full subagent output saved to: C:/home/spill/sa-1.txt" in cut
    head_lines = cut.split("\n\n[... middle omitted")[0].count("\n") + 1
    assert f'read_file path="C:/home/spill/sa-1.txt" offset={head_lines + 1} limit=200' in cut


def test_a_summary_that_could_not_be_saved_says_so() -> None:
    cut = trimmed_summary("x" * 5_000, 2_000, None)
    assert "nothing was kept beyond the head and tail above" in cut


def test_a_late_steer_is_said_after_the_summary() -> None:
    assert missed_steer("Done.", []) == "Done."
    said = missed_steer("Done.", ["in English", "be brief"])
    assert said.endswith("delivered: in English / be brief]")


# -- the notice -------------------------------------------------------------------------------


def test_the_notice_names_each_task_its_status_and_what_it_said() -> None:
    children = [
        _child(1, status=SubagentStatus.FAILED, error="Out of credit.", ended_at=NOW),
        _child(0, status=SubagentStatus.COMPLETED, summary="All 12 found.", model_calls=3,
               ended_at=NOW, truncated=True),
    ]  # fmt: skip

    notice = batch_notice(children, NOW, UTC)

    lines = notice.splitlines()
    assert lines[0] == "[ASYNC DELEGATION BATCH COMPLETE — d-1]"
    assert "Dispatched: 2026-10-12 14:58:30 (1m ago)" in notice
    assert "Role: leaf   Model: gpt-6-luna   Total duration: 90s" in notice
    first = notice.index("TASK 1/2")
    assert first < notice.index("TASK 2/2"), "in the order of the tasks"
    assert (
        "--- ⚠ TASK 1/2: Goal number 0.  (status=completed, api_calls=3, 90s, TRUNCATED: hit "
        "max_iterations — work may be incomplete) ---" in notice
    )
    assert "[TRUNCATED — subagent hit its iteration cap" in notice
    assert "--- ✗ TASK 2/2: Goal number 1.  (status=failed, 90s) ---" in notice
    assert "(no summary — status=failed: Out of credit.)" in notice
    assert "Atlas restarted" not in notice


def test_the_list_says_when_nothing_runs() -> None:
    assert listed([], NOW)["note"].startswith("No live subagents right now.")  # pyright: ignore[reportAttributeAccessIssue]
    running = listed([_child(0), _child(1, status=SubagentStatus.COMPLETED)], NOW)
    assert running["count"] == 1
    assert running["subagents"] == [
        {
            "subagent_id": "sa-0",
            "goal": "Goal number 0.",
            "model": "gpt-6-luna",
            "status": "running",
            "running_seconds": 90,
            "accepting_steer": True,
        }
    ]


# -- the table --------------------------------------------------------------------------------


def test_a_child_is_kept_when_it_starts_and_when_it_ends(tmp_path: Path) -> None:
    database = Database(tmp_path / "atlas.db")
    migrate(database, SCHEMAS)
    store = SubagentStore(database)
    store.add([_child(0), _child(1)])

    ended = _child(0, status=SubagentStatus.COMPLETED, ended_at=NOW, summary="Done.",
                   model_calls=2, cost_usd=Decimal("0.0012"), truncated=True)  # fmt: skip
    store.finish(ended)

    assert store.delegation("d-1") == [ended, _child(1)]
    assert store.undelivered(NOW - timedelta(hours=1)) == [], "a child still runs"


def test_a_later_process_marks_running_children_unknown_and_finds_their_batch(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "atlas.db")
    migrate(database, SCHEMAS)
    store = SubagentStore(database)
    store.add([_child(0), _child(1, status=SubagentStatus.COMPLETED, ended_at=NOW)])
    store.add([_child(0, id="sa-x", delegation_id="d-2")])
    store.delivered("d-2")

    assert store.abandon_running(NOW) == 2

    batches = store.undelivered(NOW - timedelta(hours=1))
    assert [[child.id for child in batch] for batch in batches] == [["sa-0", "sa-1"]]
    assert batches[0][0].status is SubagentStatus.UNKNOWN
    assert "outcome unknown" in (batches[0][0].error or "")
    assert store.undelivered(NOW) == [], "older than the window"
