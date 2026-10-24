"""The loop guards, on Atlas's tools: attended work warns, unattended work halts."""

from atlas.agent import GuardLimits, LoopGuard, ToolTraits, is_failure
from atlas.agent.guards import RESULT_POINTER_MIN_CHARS, Action, halted

READ = ToolTraits(read_only=True)
CHANGE = ToolTraits()
COMMAND = ToolTraits(failure_tolerant=True)
POLLER = ToolTraits(repeatable=True)


def _streak(guard: LoopGuard, times: int, *, traits: ToolTraits = CHANGE, result: str = "same"):
    return [
        guard.observe("status", {"id": 7}, result, traits, call_id=f"c{n}", failed=False)
        for n in range(times)
    ]


# -- the identical-call streak ---------------------------------------------------------------------


def test_the_third_identical_call_with_the_same_result_gets_the_notice_and_keeps_getting_it() -> (
    None
):
    seen = _streak(LoopGuard(), 5)
    assert [s.notice is not None for s in seen] == [False, False, True, True, True]
    assert seen[2].notice is not None
    assert "3rd call in a row to status" in seen[2].notice
    assert "5th call in a row" in (seen[4].notice or "")


def test_a_different_argument_result_or_call_in_between_resets_the_streak() -> None:
    guard = LoopGuard()
    guard.observe("status", {"id": 7}, "down", CHANGE, call_id="a", failed=False)
    guard.observe("status", {"id": 8}, "down", CHANGE, call_id="b", failed=False)
    guard.observe("status", {"id": 8}, "up", CHANGE, call_id="c", failed=False)
    third = guard.observe("status", {"id": 8}, "up", CHANGE, call_id="d", failed=False)
    assert third.notice is None


def test_key_order_does_not_make_a_call_different() -> None:
    guard = LoopGuard()
    guard.observe("search", {"q": "x", "n": 5}, "r", CHANGE, call_id="a", failed=False)
    guard.observe("search", {"n": 5, "q": "x"}, "r", CHANGE, call_id="b", failed=False)
    third = guard.observe("search", {"q": "x", "n": 5}, "r", CHANGE, call_id="c", failed=False)
    assert third.notice is not None


def test_a_poller_never_gets_the_notice() -> None:
    assert all(s.notice is None for s in _streak(LoopGuard(), 9, traits=POLLER))


def test_attended_the_streak_never_halts_unattended_it_halts_at_the_fifth() -> None:
    attended, unattended = LoopGuard(), LoopGuard(attended=False)
    _streak(attended, 9)
    _streak(unattended, 4)
    assert attended.halt is None
    assert unattended.halt is None
    _streak(unattended, 1)
    assert unattended.halt is not None
    assert unattended.halt.code == "identical_call_streak_halt"


# -- the cycle -------------------------------------------------------------------------------------


def _cycle(guard: LoopGuard, laps: int, *, a: ToolTraits = READ, b: ToolTraits = READ):
    seen = []
    for lap in range(laps):
        seen.append(
            guard.observe("read_file", {"path": "a"}, "A", a, call_id=f"a{lap}", failed=False)
        )
        seen.append(
            guard.observe("find_files", {"q": "b"}, "B", b, call_id=f"b{lap}", failed=False)
        )
    return seen


def test_a_repeating_two_call_cycle_gets_the_notice_and_halts_unattended() -> None:
    attended, unattended = LoopGuard(), LoopGuard(attended=False)
    seen = _cycle(attended, 3)
    _cycle(unattended, 5)
    assert seen[-1].notice is not None
    assert "same cycle of 2 tool calls" in seen[-1].notice
    assert all(s.notice is None for s in seen[:4])
    assert attended.halt is None
    assert unattended.halt is not None
    assert unattended.halt.code == "identical_cycle_halt"


def test_a_cycle_that_makes_progress_or_only_polls_stays_silent() -> None:
    guard = LoopGuard()
    for lap in range(5):
        guard.observe("read_file", {"path": "a"}, f"A{lap}", READ, call_id="x", failed=False)
        seen = guard.observe("find_files", {"q": "b"}, "B", READ, call_id="y", failed=False)
        assert seen.notice is None
    pollers = _cycle(LoopGuard(), 5, a=POLLER, b=POLLER)
    assert all(s.notice is None for s in pollers)


# -- the repeated result --------------------------------------------------------------------------


def test_a_byte_identical_long_result_becomes_a_pointer_to_the_first() -> None:
    long = "x" * RESULT_POINTER_MIN_CHARS
    seen = _streak(LoopGuard(), 3, result=long)
    assert seen[0].pointer is None
    assert seen[1].pointer is not None
    assert "byte-identical to the status result earlier this turn (call c0)" in seen[1].pointer
    assert 'Arguments: {"id":7}' in seen[1].pointer
    assert seen[2].pointer is not None  # with the notice too
    assert seen[2].notice is not None


def test_no_pointer_for_a_short_result_a_failure_or_a_changed_result() -> None:
    assert all(s.pointer is None for s in _streak(LoopGuard(), 3, result="short"))
    guard = LoopGuard()
    long = "y" * 600
    guard.observe("status", {}, long, CHANGE, call_id="a", failed=True)
    assert guard.observe("status", {}, long, CHANGE, call_id="b", failed=True).pointer is None
    other = LoopGuard()
    other.observe("status", {}, long, CHANGE, call_id="a", failed=False)
    other.observe("status", {}, long + "!", CHANGE, call_id="b", failed=False)
    third = other.observe("status", {}, long + "!", CHANGE, call_id="c", failed=False)
    assert third.pointer is not None
    assert "(call b)" in third.pointer  # the new first


def test_a_poller_gets_the_pointer_but_not_the_notice() -> None:
    seen = _streak(LoopGuard(), 3, traits=POLLER, result="z" * 600)
    assert seen[1].pointer is not None
    assert seen[2].notice is None


def test_the_pointer_preview_of_long_arguments_is_cut_at_120_characters() -> None:
    guard = LoopGuard()
    arguments = {"q": "w" * 300}
    guard.observe("search", arguments, "v" * 600, CHANGE, call_id="c0", failed=False)
    seen = guard.observe("search", arguments, "v" * 600, CHANGE, call_id="c1", failed=False)
    assert seen.pointer is not None
    assert "…" in seen.pointer
    assert len(seen.pointer.split("Arguments: ")[1]) < 125


# -- failures --------------------------------------------------------------------------------------


def test_attended_a_repeated_failure_warns_and_never_blocks() -> None:
    guard = LoopGuard()
    decisions = [
        guard.after("read_file", {"path": "x"}, "[Atlas: x does not exist.]", READ, failed=True)
        for _ in range(9)
    ]
    assert decisions[0].action is Action.ALLOW
    assert decisions[1].action is Action.WARN
    assert decisions[1].code == "repeated_exact_failure_warning"
    assert guard.before("read_file", {"path": "x"}, READ).runs
    assert guard.halt is None


def test_unattended_the_same_failing_call_is_blocked_before_its_sixth_run() -> None:
    guard = LoopGuard(attended=False)
    for _ in range(5):
        guard.after("read_file", {"path": "x"}, "[Atlas: no]", READ, failed=True)
    decision = guard.before("read_file", {"path": "x"}, READ)
    assert decision.action is Action.BLOCK
    assert decision.code == "repeated_exact_failure_block"
    assert guard.halt == decision


def test_a_change_that_landed_makes_the_next_retry_a_new_experiment() -> None:
    """Edit, run, still red, edit, run — never halted."""
    guard = LoopGuard(attended=False)
    for _ in range(8):
        guard.after(
            "run_command", {"command": "pytest"}, "red\n[exit code 1]", COMMAND, failed=True
        )
        assert guard.before("run_command", {"command": "pytest"}, COMMAND).runs
        guard.after("edit_file", {"path": "a.py"}, "Edited a.py.", CHANGE, failed=False)
    assert guard.halt is None


def test_a_pure_replay_with_nothing_in_between_is_still_blocked() -> None:
    guard = LoopGuard(attended=False)
    for _ in range(5):
        guard.after("edit_file", {"path": "a.py"}, "[Atlas: not found]", CHANGE, failed=True)
    assert guard.before("edit_file", {"path": "a.py"}, CHANGE).action is Action.BLOCK


def test_different_failing_commands_warn_but_never_halt() -> None:
    guard = LoopGuard(attended=False)
    decisions = [
        guard.after("run_command", {"command": f"try {n}"}, "[exit code 2]", COMMAND, failed=True)
        for n in range(12)
    ]
    assert decisions[2].action is Action.WARN
    assert decisions[2].code == "same_tool_failure_warning"
    assert "run a small diagnostic first" in decisions[2].message
    assert guard.halt is None


def test_the_same_tool_failing_with_other_arguments_halts_unattended_at_the_eighth() -> None:
    guard = LoopGuard(attended=False)
    for n in range(7):
        assert (
            guard.after("web_search", {"q": str(n)}, "[Atlas: down]", READ, failed=True).action
            is not Action.HALT
        )
    decision = guard.after("web_search", {"q": "8"}, "[Atlas: down]", READ, failed=True)
    assert decision.action is Action.HALT
    assert decision.code == "same_tool_failure_halt"


def test_a_read_only_call_returning_the_same_result_warns_then_blocks_unattended() -> None:
    guard = LoopGuard(attended=False)
    decisions = [
        guard.after("find_files", {"q": "a"}, "none", READ, failed=False) for _ in range(5)
    ]
    assert decisions[1].code == "idempotent_no_progress_warning"
    assert guard.before("find_files", {"q": "a"}, READ).code == "idempotent_no_progress_block"
    changing = LoopGuard(attended=False)
    for _ in range(9):
        changing.after("write_file", {"path": "a"}, "Wrote a.", CHANGE, failed=False)
    assert changing.before("write_file", {"path": "a"}, CHANGE).runs


def test_limits_can_be_set() -> None:
    guard = LoopGuard(attended=False, limits=GuardLimits(exact_failure_block_after=2))
    for _ in range(2):
        guard.after("read_file", {"path": "x"}, "[Atlas: no]", READ, failed=True)
    assert guard.before("read_file", {"path": "x"}, READ).action is Action.BLOCK


# -- what counts as a failure ----------------------------------------------------------------------


def test_a_failure_is_a_raise_an_atlas_answer_or_a_non_zero_exit_code() -> None:
    assert is_failure("anything", raised=True)
    assert is_failure("[Atlas: x does not exist.]", raised=False)
    assert is_failure("Traceback...\n[exit code 1]", raised=False)
    assert not is_failure("ok\n[exit code 0]", raised=False)
    assert not is_failure("the file says [Atlas: this] inside", raised=False)
    assert not is_failure("plain text", raised=False)


# -- the per-turn cap on subagents (step 12b) ------------------------------------------------------


def test_the_51st_subagent_of_a_turn_is_blocked_whoever_watches() -> None:
    for attended in (True, False):
        guard = LoopGuard(attended=attended)
        batch = {"tasks": [{"goal": f"Task {n} of the batch."} for n in range(10)]}
        decisions = [guard.before("delegate", batch, CHANGE) for _ in range(6)]
        assert [d.runs for d in decisions] == [True] * 5 + [False]
        assert decisions[-1].action is Action.BLOCK
        assert "already spawned 50 subagents (limit 50)" in decisions[-1].message
        assert guard.halt is decisions[-1], "the block is recorded as the turn's halt"


def test_a_control_call_spawns_nothing_and_works_after_the_cap() -> None:
    guard = LoopGuard(max_subagents=1)
    assert guard.before("delegate", {"goal": "x"}, CHANGE).runs
    assert guard.before("delegate", {"action": "list"}, CHANGE).runs
    assert guard.before("delegate", {"action": "stop", "subagent_id": "sa-1"}, CHANGE).runs
    assert not guard.before("delegate", {"tasks": [{"goal": "y"}]}, CHANGE).runs


def test_the_halt_said_to_someone_watching_does_not_claim_nobody_was() -> None:
    guard = LoopGuard(max_subagents=0)
    decision = guard.before("delegate", {"tasks": [{"goal": "y"}]}, CHANGE)
    assert "Nobody was watching" not in halted(decision, attended=True)
    assert "Nobody was watching" in halted(decision)
