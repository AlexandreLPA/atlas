"""The history rules: repair, measuring, clearing, cutting and what a compaction keeps.

Ported from V3's history repair (langgraph_engine), context_budget and compaction tests, with the
incidents that shaped each rule. Pure functions over Atlas's own message types.
"""

import pytest

from atlas.agent import (
    NO_RESULT,
    SUMMARY_MARK,
    cleared,
    estimate_tokens,
    input_window,
    is_model_switch,
    is_notice,
    is_skill,
    model_switch_note,
    plan_compaction,
    repaired,
    skill_command,
    skill_text,
    typed_text,
)
from atlas.agent.history import (
    CUT_HEAD_CHARS,
    cut,
    is_context_overflow,
    summary_message,
    transcript,
    user_words,
)
from atlas.models import (
    AssistantMessage,
    Message,
    ModelInfo,
    Origin,
    SystemMessage,
    ToolCall,
    ToolResult,
    UserMessage,
)


def _asks(*calls: str, text: str = "") -> AssistantMessage:
    return AssistantMessage(text, tuple(ToolCall(call, "read_file", "{}") for call in calls))


# -- repair --------------------------------------------------------------------------------------


def test_a_well_formed_history_goes_as_it_is() -> None:
    history: list[Message] = [
        SystemMessage("You are Atlas."),
        UserMessage("Read it."),
        _asks("a", "b"),
        ToolResult("a", "A"),
        ToolResult("b", "B"),
        AssistantMessage("Done."),
        UserMessage("Thanks."),
    ]
    assert repaired(history) == history


def test_the_previous_answer_is_never_dropped() -> None:
    # The code this rewrite replaced dropped a trailing answer "for Gemini" before every turn, so
    # the model lost its own last reply. Gemini refuses a request that ENDS with the assistant; a
    # history does not end there once the user's new message follows.
    history: list[Message] = [UserMessage("Hi"), AssistantMessage("Hello!"), UserMessage("Again")]
    assert repaired(history) == history


def test_a_call_without_result_gets_one_that_claims_nothing() -> None:
    # V3's placeholder said "interrupted or failed" even for calls that had finished, and the
    # model told the user a tool could not do what nobody had tried.
    fixed = repaired([UserMessage("Read"), _asks("a", "b"), ToolResult("b", "B")])
    assert fixed[2:] == [ToolResult("a", NO_RESULT), ToolResult("b", "B")]
    assert "unknown" in NO_RESULT
    assert repaired([UserMessage("Read"), _asks("a")]) == repaired(
        [UserMessage("Read"), _asks("a")]
    )


def test_a_result_that_landed_after_the_next_message_goes_back_to_its_call() -> None:
    # V3, 26/09/2026: the user wrote while a tool still ran; its result landed after the new
    # message, and every later turn failed with HTTP 400.
    history: list[Message] = [
        UserMessage("Delegate it"),
        _asks("a"),
        UserMessage("And now?"),
        ToolResult("a", "delegated"),
    ]
    assert repaired(history) == [
        UserMessage("Delegate it"),
        _asks("a"),
        ToolResult("a", "delegated"),
        UserMessage("And now?"),
    ]


def test_orphan_and_duplicate_results_are_dropped() -> None:
    history: list[Message] = [
        ToolResult("z", "before any call"),
        UserMessage("Read"),
        _asks("a"),
        ToolResult("a", "first"),
        ToolResult("a", "second"),
        ToolResult("x", "nobody asked"),
    ]
    assert repaired(history) == [UserMessage("Read"), _asks("a"), ToolResult("a", "first")]


def test_an_empty_assistant_message_is_dropped_and_one_with_provider_fields_is_kept() -> None:
    sealed = AssistantMessage(origin=Origin("openai", "gpt-6-luna"), provider_fields={"output": []})
    history: list[Message] = [UserMessage("Hi"), AssistantMessage(), sealed, UserMessage("?")]
    assert repaired(history) == [UserMessage("Hi"), sealed, UserMessage("?")]


def test_repair_never_changes_the_history_it_reads() -> None:
    history: list[Message] = [UserMessage("Read"), _asks("a")]
    repaired(history)
    assert history == [UserMessage("Read"), _asks("a")]


# -- measuring -----------------------------------------------------------------------------------


def test_the_estimate_counts_bytes_over_four() -> None:
    assert estimate_tokens([UserMessage("a" * 400)]) == 100 + 4
    assert estimate_tokens([UserMessage("ã" * 400)]) == 200 + 4  # two bytes each in UTF-8


def test_the_input_window_comes_from_the_catalog_or_stays_unknown() -> None:
    assert input_window(None) is None
    assert input_window(ModelInfo("m", "m")) is None
    assert input_window(
        ModelInfo("m", "m", context_window=1_050_000, max_input_tokens=922_000)
    ) == (922_000)
    # The output is reserved inside a window that does not declare its input limit.
    assert input_window(ModelInfo("m", "m", context_window=200_000, max_output_tokens=8_000)) == (
        192_000
    )
    assert input_window(ModelInfo("m", "m", context_window=128_000)) == 108_000


@pytest.mark.parametrize(
    "text",
    [
        "This model's maximum context length is 8192 tokens (context_length_exceeded)",
        "prompt is too long: 210000 tokens > 200000 maximum",
        "The input token count (1100000) exceeds the maximum number of tokens allowed",
    ],
)
def test_a_conversation_that_does_not_fit_is_recognized(text: str) -> None:
    assert is_context_overflow(text)


def test_a_format_error_is_not_an_overflow() -> None:
    # V3 compacted on any HTTP 400, and a format error cost a conversation 112 of its messages.
    assert not is_context_overflow("Requests ending with a model turn are not supported")


# -- clearing and cutting ------------------------------------------------------------------------


def _rounds(count: int, size: int) -> list[Message]:
    history: list[Message] = [UserMessage("Go")]
    for i in range(count):
        history += [_asks(f"c{i}"), ToolResult(f"c{i}", f"result {i} " + "x" * size)]
    return history


def test_old_results_are_cleared_and_the_recent_ones_and_the_round_kept() -> None:
    history = _rounds(6, 5_000)
    copy = cleared(history)

    results = [m for m in copy if isinstance(m, ToolResult)]
    assert all("was cleared from the context" in r.text for r in results[:3])
    assert "Call read_file again if you need it" in results[0].text
    assert [r.text for r in results[3:]] == [m.text for m in history if isinstance(m, ToolResult)][
        3:
    ]
    assert history == _rounds(6, 5_000)  # the stored history keeps everything


def test_a_short_result_is_not_worth_clearing() -> None:
    copy = cleared(_rounds(6, 100))
    assert not any("cleared" in m.text for m in copy if isinstance(m, ToolResult))


def test_a_cleared_spilled_result_still_says_where_the_whole_is() -> None:
    spilled = cut("y" * 60_000, "/data/spill/20261008T120000Z-c0.txt")
    history: list[Message] = [UserMessage("Go"), _asks("c0"), ToolResult("c0", spilled)]
    history += _rounds(4, 5_000)[1:]
    copy = cleared(history)
    assert "The whole result is in /data/spill/20261008T120000Z-c0.txt." in copy[2].text


def test_a_cut_result_keeps_its_start_and_end_and_names_the_file() -> None:
    text = "S" * CUT_HEAD_CHARS + "M" * 50_000 + "E" * 2_000
    shown = cut(text, "/home/spill/x.txt")
    assert shown.startswith("S" * CUT_HEAD_CHARS)
    assert shown.endswith("E" * 2_000)
    assert "M" not in shown
    assert "50,000 characters were cut" in shown
    assert "The whole result is in /home/spill/x.txt." in shown
    assert "They were not kept." in cut(text, None)


# -- compaction ----------------------------------------------------------------------------------


def _conversation(turns: int, size: int = 2_000) -> list[Message]:
    history: list[Message] = [SystemMessage("You are Atlas.")]
    for i in range(turns):
        history += [
            UserMessage(f"question {i} " + "q" * size),
            _asks(f"t{i}"),
            ToolResult(f"t{i}", "r" * size),
            AssistantMessage(f"answer {i} " + "a" * size),
        ]
    return history


def test_the_kept_part_starts_with_the_users_message_and_the_system_prompt_stays() -> None:
    plan = plan_compaction(_conversation(40), tail_tokens=5_000)
    assert plan is not None
    assert plan.head == (SystemMessage("You are Atlas."),)
    assert isinstance(plan.tail[0], UserMessage)
    assert estimate_tokens(plan.tail) >= 5_000
    assert not any(isinstance(m, SystemMessage) for m in plan.middle)
    assert len(plan.head) + len(plan.middle) + len(plan.tail) == len(_conversation(40))


def test_a_single_turn_has_nothing_older_to_summarize() -> None:
    assert plan_compaction(_conversation(1), tail_tokens=5) is None


def test_when_everything_fits_the_tail_only_the_current_turn_is_kept() -> None:
    plan = plan_compaction(_conversation(3), tail_tokens=1_000_000)
    assert plan is not None
    assert plan.tail[0] == UserMessage("question 2 " + "q" * 2_000)


def test_a_summary_alone_is_not_summarized_again() -> None:
    history: list[Message] = [
        AssistantMessage(f"{SUMMARY_MARK}\n\nEarlier work."),
        UserMessage("go on"),
        AssistantMessage("ok"),
    ]
    assert plan_compaction(history, tail_tokens=1) is None


def test_the_users_words_are_quoted_verbatim_after_the_summary() -> None:
    plan = plan_compaction(_conversation(3, size=10), tail_tokens=1)
    assert plan is not None
    message = summary_message("## Goal\nShip it.", plan)
    assert message.text.startswith(SUMMARY_MARK)
    assert "## Goal\nShip it." in message.text
    assert "> question 0 qqqqqqqqqq" in message.text
    assert message.text.index("question 0") < message.text.index("question 1")


def test_quoted_words_stop_at_the_earlier_summary_which_already_quotes_them() -> None:
    history: list[Message] = [
        UserMessage("old words"),
        AssistantMessage(f"{SUMMARY_MARK}\n\n> old words"),
        UserMessage("new words"),
    ]
    assert user_words(history) == "> new words"


def test_a_model_switch_note_is_not_quoted_as_the_users_words() -> None:
    note = model_switch_note("gemini-3.8-flash", "Google Gemini")
    assert is_model_switch(note)
    assert not is_model_switch(UserMessage("The active model is what?"))
    assert user_words([UserMessage("before"), note, UserMessage("after")]) == "> before\n\n> after"


def test_the_transcript_cuts_long_results_and_keeps_an_earlier_summary() -> None:
    history: list[Message] = [
        AssistantMessage(f"{SUMMARY_MARK}\n\nEarlier."),
        *_conversation(30, size=3_000)[1:],
    ]
    text = transcript(history, max_chars=20_000)
    assert text.startswith("[")  # some older lines were left out
    assert "EARLIER SUMMARY:" in text
    assert len(text) < 22_000
    assert "RESULT OF read_file: " + "r" * 1_500 + " [...]" in text


def test_broken_call_arguments_go_to_the_model_as_an_empty_object() -> None:
    # Gemini refused a whole history that held one call with broken arguments (09/10/2026).
    history: list[Message] = [
        UserMessage("go"),
        AssistantMessage(
            tool_calls=(ToolCall("a", "today", '{}{"query": "x"}'), ToolCall("b", "ok", '{"x": 1}'))
        ),
        ToolResult("a", "[Atlas: the arguments were not valid JSON]"),
        ToolResult("b", "fine"),
    ]

    copy = repaired(history)

    calls = copy[1].tool_calls  # type: ignore[union-attr]
    assert [call.arguments for call in calls] == ["{}", '{"x": 1}']
    stored = history[1]
    assert isinstance(stored, AssistantMessage)
    assert stored.tool_calls[0].arguments == '{}{"query": "x"}'


# -- a skill the user invoked ----------------------------------------------------------------------


def test_a_skill_the_user_invoked_is_announced_and_reads_back_as_typed() -> None:
    text = skill_text("weekly-report", "Gather the numbers.", "/skills/weekly-report", "", " May ")
    bare = skill_text(
        "weekly-report", "Gather the numbers.", "/skills/weekly-report", "Files: a.md.", ""
    )

    assert text == (
        '[IMPORTANT: The user has invoked the "weekly-report" skill: they want you to do as it '
        "says. Its whole text follows.]\n\n"
        "Gather the numbers.\n\n"
        "[Skill directory: /skills/weekly-report]\n"
        "Resolve any relative paths in this skill against it.\n\n"
        "The user has provided the following instruction alongside the skill invocation: May"
    )
    assert "[Files: a.md. Read them from that directory when the skill needs them.]" in bare
    assert skill_command(text) == "/weekly-report May"
    assert skill_command(bare) == "/weekly-report"
    assert skill_command("Hello") is None
    assert typed_text(text) == "/weekly-report May"
    assert typed_text("Hello") == "Hello"
    # The user asked for it: it begins as a notice does, and is not one.
    assert is_skill(UserMessage(text))
    assert not is_notice(UserMessage(text))
    assert is_notice(UserMessage("[IMPORTANT: Background command cmd-1 completed.]"))
