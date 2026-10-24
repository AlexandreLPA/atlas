"""conversation_search: its four shapes, its filters and its hints, over a real database of
conversations."""

import asyncio
from typing import Any

import pytest

from atlas.agent import SUMMARY_MARK, Tool
from atlas.conversations import BOT, ROUTINE, SCHEMA, ConversationStore
from atlas.home import Database, Home, migrate
from atlas.models import AssistantMessage, Message, SystemMessage, ToolCall, ToolResult, UserMessage
from atlas.tools import conversation_search


@pytest.fixture
def store(home: Home) -> ConversationStore:
    database = Database(home.database_file)
    migrate(database, (SCHEMA,))
    return ConversationStore(database)


def _saved(store: ConversationStore, *messages: Message) -> str:
    conversation = store.create()
    conversation.history += [SystemMessage("You are Atlas."), *messages]
    conversation.save()
    return conversation.id


def _call(tool: Tool, **arguments: Any) -> str:
    async def call() -> object:
        return await tool.run(arguments)

    result = asyncio.run(call())
    assert isinstance(result, str)
    return result


def test_it_is_deferred_behind_tool_search(store: ConversationStore) -> None:
    assert conversation_search(store).deferred


def test_discovery_shows_the_best_conversation_in_full_and_the_others_compact(
    store: ConversationStore,
) -> None:
    best = _saved(
        store,
        UserMessage("Plan the trip to Lisbon"),
        *(UserMessage(f"filler {n}") for n in range(10)),
        UserMessage("the Lisbon hotel budget is 200 a night"),
        AssistantMessage("Noted: hotel budget 200."),
        *(UserMessage(f"later {n}") for n in range(10)),
        AssistantMessage("Trip planned."),
    )
    other = _saved(store, UserMessage("hotel budget for Porto"))

    said = _call(conversation_search(store), query="hotel budget")

    head, full, compact, tip = said.split("\n\n")
    assert head == "2 conversation(s) found for hotel budget."
    assert f"conversation {best}" in full
    assert "First messages:" in full
    assert "Plan the trip to Lisbon" in full  # the start: what it was about
    assert "the Lisbon hotel budget is 200 a night" in full
    assert "Last messages:" in full
    assert "Trip planned." in full  # the end: how it was left
    assert f"conversation {other}" in compact
    assert "Match #" in compact
    assert "First messages:" not in compact
    assert tip.startswith("To read more around a compact result, scroll")


def test_detail_full_shows_every_result_in_full(store: ConversationStore) -> None:
    _saved(store, UserMessage("the budget"))
    _saved(store, UserMessage("another budget"))

    said = _call(conversation_search(store), query="budget", detail="full")

    assert said.count("Around the match") == 2


def test_an_exact_title_is_found_first(store: ConversationStore) -> None:
    trip = _saved(store, UserMessage("Plan the trip"), AssistantMessage("Where to?"))

    said = _call(conversation_search(store), query='"Plan the trip"')

    assert f"## Plan the trip — conversation {trip}" in said.split("\n\n")[1]
    assert "Match: the conversation's title is 'Plan the trip'." in said
    assert said.startswith("1 conversation(s)")


def test_the_current_conversation_is_left_out_unless_archived(store: ConversationStore) -> None:
    current = _saved(store, UserMessage("the blue whale"))

    said = _call(conversation_search(store, current), query="whale")

    assert said.startswith("No matching conversation. By default FTS5 needs every term to appear.")


def test_a_search_that_finds_nothing_teaches_the_syntax_and_names_missing_words(
    store: ConversationStore,
) -> None:
    _saved(store, UserMessage("the budget for march"))

    said = _call(conversation_search(store), query="budget zebra")

    assert "join terms with OR (`alpha OR beta`)" in said
    assert "No conversation contains zebra; leave it out." in said


def test_a_word_only_the_current_conversation_has_is_named_as_missing(
    store: ConversationStore,
) -> None:
    # Seen in the step 6 proof: "Orion launch date" found nothing, and "date" was only in the
    # question being asked, so the reply must name it.
    _saved(store, UserMessage("the Orion launch moved to November 14"))
    current = _saved(store, UserMessage("what date did the Orion launch get?"))

    said = _call(conversation_search(store, current), query="Orion launch date")

    assert "No conversation contains date; leave it out." in said


def test_time_bounds_and_exclusions_narrow_the_search(store: ConversationStore) -> None:
    first = _saved(store, UserMessage("budget for march"))
    second = _saved(store, UserMessage("budget for april"))
    tool = conversation_search(store)

    assert first not in _call(tool, query="budget", exclude_conversation_ids=[first])
    assert second in _call(tool, query="budget", exclude_conversation_ids=[first])
    assert _call(tool, query="budget", before="2000-01-01").startswith("No matching")
    assert second in _call(tool, query="budget", after="7d")
    assert "is not a time bound" in _call(tool, query="budget", after="last week")


def test_tool_output_is_searched_only_when_asked(store: ConversationStore) -> None:
    _saved(
        store,
        UserMessage("check memory"),
        AssistantMessage(tool_calls=(ToolCall("c1", "run_command", "{}"),)),
        ToolResult("c1", "Mem: 16Gi total"),
        AssistantMessage("16 GB."),
    )
    tool = conversation_search(store)

    assert _call(tool, query="16Gi").startswith("No matching")
    assert "tool run_command" in _call(tool, query="16Gi", role_filter="user,assistant,tool")


def test_scroll_reads_around_a_message_with_its_hint(store: ConversationStore) -> None:
    conversation = _saved(store, *(UserMessage(f"line {n}") for n in range(30)))
    numbers = [m.number for m in store.messages(conversation)]
    tool = conversation_search(store)

    said = _call(tool, conversation_id=conversation, around_message_id=numbers[15], window=2)

    lines = said.splitlines()
    assert [line.split(": ")[-1] for line in lines[1:6]] == [f"line {n}" for n in range(13, 18)]
    assert lines[-1].startswith("Scroll forward: re-call with around_message_id")
    assert "is not in conversation" in _call(
        tool, conversation_id=conversation, around_message_id=999_999
    )


def test_read_shows_a_long_conversation_by_its_head_and_tail(store: ConversationStore) -> None:
    conversation = _saved(store, *(UserMessage(f"line {n}") for n in range(40)))

    said = _call(conversation_search(store), conversation_id=conversation)

    assert "line 19" in said
    assert "line 20" not in said
    assert "line 30" in said
    assert "line 39" in said
    assert said.endswith(
        "Conversation has 40 messages; showing first 20 + last 10. Pass around_message_id (any "
        "number above) to scroll the middle."
    )


def test_each_shape_cuts_a_long_message_and_says_so(store: ConversationStore) -> None:
    long = "word " * 1_000  # with "needle ", 5,006 characters once stripped
    conversation = _saved(store, UserMessage(f"needle {long}"))
    number = store.messages(conversation)[0].number
    tool = conversation_search(store)

    read = _call(tool, conversation_id=conversation)
    scroll = _call(tool, conversation_id=conversation, around_message_id=number)

    assert "[cut: 5,006 chars; scroll to read it whole]" in read
    assert "[cut:" not in scroll  # a scroll shows messages whole


def test_an_archived_summary_and_its_messages_read_in_order(store: ConversationStore) -> None:
    conversation = store.create()
    conversation.history += [SystemMessage("rules"), UserMessage("old talk"), UserMessage("recent")]
    conversation.save()
    summary = AssistantMessage(f"{SUMMARY_MARK} They talked.")
    conversation.history[:] = [conversation.history[0], summary, conversation.history[-1]]
    conversation.save()

    lines = _call(conversation_search(store), conversation_id=conversation.id).splitlines()[1:]

    assert "old talk" in lines[0]
    assert "archived" in lines[0]
    assert "summary of earlier messages" in lines[1]
    assert "recent" in lines[2]


def test_nothing_browses_the_latest_conversations(store: ConversationStore) -> None:
    assert _call(conversation_search(store)) == "There are no past conversations yet."
    first = _saved(store, UserMessage("Plan the trip"))
    current = _saved(store, UserMessage("Today's chat"))

    said = _call(conversation_search(store, current))

    assert said.startswith("Showing 1 most recent conversations:")
    assert f"Plan the trip — conversation {first}" in said
    assert current not in said
    assert said.endswith("Pass a query= to search, or conversation_id+around_message_id to scroll.")


def test_an_unknown_conversation_and_an_empty_query_are_explained(
    store: ConversationStore,
) -> None:
    tool = conversation_search(store)

    assert "There is no conversation 'nope'" in _call(tool, conversation_id="nope")
    assert "no words to search" in _call(tool, query="::")


# -- a bot finds only its own conversations (step 13b) -------------------------------------------


def _with_bots(store: ConversationStore) -> dict[str, str]:
    """The main agent's conversation, two bots' fixed ones, and a run of a bot's routine."""
    made: dict[str, str] = {}
    for key, source, bot, said in (
        ("mine", "app", None, "the salary negotiation with the new job"),
        ("finance", BOT, "finance", "the salary lands on the 5th, rent is paid on the 6th"),
        ("finance-run", ROUTINE, "finance", "monthly close: the salary came in"),
        ("client", BOT, "client", "the client asked about the delivery date"),
    ):
        conversation = store.create(source=source, bot=bot, title=f"{key} talk")
        conversation.history += [UserMessage(said), AssistantMessage("Noted.")]
        conversation.save()
        made[key] = conversation.id
    return made


def test_a_bot_finds_only_its_own_conversations_and_the_main_agent_finds_every_agents(
    store: ConversationStore,
) -> None:
    made = _with_bots(store)
    as_client = conversation_search(store, made["client"], bot="client")
    as_finance = conversation_search(store, made["finance"], bot="finance")
    as_atlas = conversation_search(store, made["mine"])

    # The bot that talks to a client never finds what was said to the finance bot.
    assert "No conversation contains salary" in _call(as_client, query="salary")
    # A bot finds its own other conversations: here, its routine's run.
    found = _call(as_finance, query="salary")
    assert made["finance-run"] in found
    assert made["mine"] not in found
    assert made["client"] not in found
    # The main agent reads every agent's, and is told whose each is.
    everything = _call(as_atlas, query="salary", limit=5)
    assert made["finance"] in everything
    assert made["finance-run"] in everything
    assert "(with the bot finance)" in everything


def test_a_bot_cannot_read_scroll_or_list_another_agents_conversation(
    store: ConversationStore,
) -> None:
    made = _with_bots(store)
    as_client = conversation_search(store, made["client"], bot="client")
    as_atlas = conversation_search(store, made["mine"])

    for theirs in (made["finance"], made["mine"]):
        refused = _call(as_client, conversation_id=theirs)
        assert "There is no conversation" in refused
        assert "salary" not in refused
        assert "salary" not in _call(as_client, conversation_id=theirs, around_message_id=1)
    assert "delivery date" in _call(as_client, conversation_id=made["client"])
    # Browsing: a bot lists its own, the main agent lists every agent's.
    assert _call(as_client) == "There are no past conversations yet."
    listed = _call(as_atlas)
    assert "finance talk (with the bot finance)" in listed
    assert "client talk (with the bot client)" in listed
    assert "salary" in _call(as_atlas, conversation_id=made["finance"])
