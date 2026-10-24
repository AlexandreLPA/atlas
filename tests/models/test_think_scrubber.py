"""The think scrubber's tests.

They hold the contract so no reasoning block leaks into a reply's text: MiniMax, DeepSeek and Qwen
send `<think>` and its content in separate chunks, which a strip run per chunk broke.
"""

import pytest

from atlas.models.think_scrubber import THINK_TAG_NAMES, ThinkScrubber


def _drive(scrubber: ThinkScrubber, chunks: list[str]) -> str:
    """Feed the chunks and return the visible text, the flush included."""
    out = [scrubber.feed(chunk) for chunk in chunks]
    out.append(scrubber.flush())
    return "".join(out)


def _hidden(scrubber: ThinkScrubber, chunks: list[str]) -> str:
    hidden: list[str] = []
    for chunk in chunks:
        scrubber.feed(chunk)
        hidden.append(scrubber.hidden)
    scrubber.flush()
    return "".join(hidden)


# -- closed pairs are always taken out, wherever they are ----------------------------------------


def test_a_closed_pair_in_one_chunk() -> None:
    assert _drive(ThinkScrubber(), ["<think>reasoning</think>Hello world"]) == "Hello world"


@pytest.mark.parametrize("tag", THINK_TAG_NAMES)
def test_every_tag(tag: str) -> None:
    assert _drive(ThinkScrubber(), [f"<{tag}>x</{tag}>Hello"]) == "Hello"


def test_tags_in_any_case() -> None:
    assert _drive(ThinkScrubber(), ["<THINK>x</Think>Hello"]) == "Hello"


# -- an opening tag with no closing one ----------------------------------------------------------


def test_an_open_tag_at_the_start_hides_the_rest() -> None:
    assert _drive(ThinkScrubber(), ["<think>reasoning text with no close"]) == ""


def test_prose_that_mentions_the_tag_survives() -> None:
    text = "Use the <think> element for reasoning"
    assert _drive(ThinkScrubber(), [text]) == text


def test_an_open_tag_after_a_newline_starts_a_block() -> None:
    assert _drive(ThinkScrubber(), ["Answer.\n  <think>more reasoning"]) == "Answer.\n  "


# -- a closing tag alone -------------------------------------------------------------------------


def test_a_lone_closing_tag_is_removed() -> None:
    assert _drive(ThinkScrubber(), ["Hello</think>world"]) == "Helloworld"


# -- tags cut across chunks ----------------------------------------------------------------------


def test_an_opening_tag_cut_in_two() -> None:
    assert _drive(ThinkScrubber(), ["<", "think>reasoning</think>done"]) == "done"


def test_a_pair_cut_in_the_middle_of_a_line_is_still_a_pair() -> None:
    # A closed pair is a bounded construct, taken out even off a block boundary.
    assert _drive(ThinkScrubber(), ["word<", "think>prose</think>more"]) == "wordmore"


def test_minimax_sends_the_tag_and_its_content_apart() -> None:
    chunks = ["<think>", "Let me check their config", "</think>", "done"]
    assert _drive(ThinkScrubber(), chunks) == "done"


def test_a_block_open_when_the_stream_ends_is_dropped() -> None:
    assert _drive(ThinkScrubber(), ["<think>", "The user wants", " to know something"]) == ""


def test_half_a_tag_at_the_end_is_text() -> None:
    assert _drive(ThinkScrubber(), ["a <thi"]) == "a <thi"


# -- one scrubber after another stream -----------------------------------------------------------


def test_a_flush_re_arms_the_boundary() -> None:
    # A flush that left the boundary unset made the next stream's opening tag look mid-line, and
    # it leaked.
    scrubber = ThinkScrubber()
    assert scrubber.feed("word") == "word"
    assert scrubber.flush() == ""
    chunks = ["<think>", "secret reasoning", "</think>", "Visible answer"]
    assert _drive(scrubber, chunks) == "Visible answer"


def test_a_flushed_half_tag_does_not_poison_the_next_stream() -> None:
    scrubber = ThinkScrubber()
    scrubber.feed("word<")
    assert scrubber.flush() == "<"
    assert _drive(scrubber, ["<think>hidden</think>Hello"]) == "Hello"


# -- streaming as it really comes ----------------------------------------------------------------


def test_character_by_character() -> None:
    assert _drive(ThinkScrubber(), list("<think>x</think>Hello world")) == "Hello world"


def test_the_answer_keeps_its_first_word() -> None:
    # A regression showed "Let me check" as " me check".
    chunks = ["<think>", "User wants to know things", "</think>", "Let me check their config."]
    assert _drive(ThinkScrubber(), chunks) == "Let me check their config."


def test_text_without_tags_passes_unchanged() -> None:
    chunks = ["Hello ", "world ", "how ", "are ", "you?"]
    assert _drive(ThinkScrubber(), chunks) == "Hello world how are you?"


def test_minimax_chinese_tags() -> None:
    chunks = ["<思", "考>让我想想", "……</思考>", "\n答案是 42"]
    assert _drive(ThinkScrubber(), chunks) == "\n答案是 42"


# -- what was taken out ----------------------------------------------------------------------------


def test_the_hidden_text_is_joined_as_it_came() -> None:
    # Joining the pieces with newlines would split sentences on every token.
    chunks = ["<think>", "The user", " wants 42", "</think>", "42"]
    assert _hidden(ThinkScrubber(), chunks) == "The user wants 42"


def test_the_hidden_text_of_a_pair_excludes_the_tags() -> None:
    scrubber = ThinkScrubber()
    assert scrubber.feed("<thought>a b</thought>c") == "c"
    assert scrubber.hidden == "a b"


def test_a_held_closing_tag_is_not_hidden_text() -> None:
    assert _hidden(ThinkScrubber(), ["<think>abc</th", "ink>d"]) == "abc"


# -- Atlas's own fix -----------------------------------------------------------------------------


def test_text_whose_lowered_form_is_longer_is_cut_in_the_right_place() -> None:
    # "İ" lowers to two characters; finding tags in a lowered copy cuts the original one character
    # off.
    assert _drive(ThinkScrubber(), ["İİ<think>x</think>Yes"]) == "İİYes"
    assert _drive(ThinkScrubber(), ["İ\n<think>x", "</think>ok"]) == "İ\nok"
