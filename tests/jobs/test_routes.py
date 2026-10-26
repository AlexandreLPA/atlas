"""A session's route as the database keeps it (decided with the user on 21/10 and 25/10/2026):
its destinations in order, the model and the effort each goes on with, and whether a copy on
another account compacts first, which it does unless the user turned it off."""

from pathlib import Path

from atlas.__main__ import SCHEMAS
from atlas.home import Database, migrate
from atlas.jobs import AccountStore, RouteTarget, SessionRoute


def _store(tmp_path: Path) -> AccountStore:
    database = Database(tmp_path / "atlas.db")
    migrate(database, SCHEMAS)
    return AccountStore(database)


def test_a_route_keeps_each_stops_model_and_effort_and_whether_it_compacts(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_route(
        SessionRoute(
            "c1",
            (RouteTarget("claude-code", "a1", model="opus", effort="high"), RouteTarget("codex")),
            enabled=True,
            compact=False,
        )
    )

    kept = store.route("c1")

    assert kept is not None
    assert [(t.executor, t.account, t.model, t.effort) for t in kept.targets] == [
        ("claude-code", "a1", "opus", "high"),
        ("codex", "", None, None),
    ]
    assert (kept.enabled, kept.compact) == (True, False)


def test_a_route_and_an_accounts_hand_over_compact_first_unless_turned_off(tmp_path: Path) -> None:
    store = _store(tmp_path)
    store.set_route(SessionRoute("c1", (RouteTarget("codex"),)))

    kept = store.route("c1")

    assert kept is not None
    assert kept.compact
    assert store.failover("claude-code").compact, "until the user sets it"


def test_a_stop_is_its_executor_and_account_whatever_it_chooses() -> None:
    assert RouteTarget("claude-code", "a1", model="opus") == RouteTarget("claude-code", "a1")
    assert len(dict.fromkeys([RouteTarget("codex", effort="low"), RouteTarget("codex")])) == 1
