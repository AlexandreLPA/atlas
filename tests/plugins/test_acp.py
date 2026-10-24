"""The SDK's ACP client (step 16p), its pure parts: what Atlas writes and how what a harness writes
becomes the SDK's events. The channel itself runs against tests/fake_gemini.py, in
tests/plugins/test_gemini_cli.py; the session files the Gemini CLI writes are read here too."""

import json
from datetime import UTC, datetime
from pathlib import Path

from atlas.plugins import (
    Called,
    CommandsChanged,
    ExecutorFile,
    Returned,
    Said,
    SlashCommand,
    Thought,
    TranscriptEntry,
)
from atlas.plugins.acp import (
    AcpError,
    Turn,
    initialize_params,
    permission_of,
    permission_outcome,
    prompt_blocks,
    update_events,
)
from gemini_support import plugin_module


def _chunk(kind: str, text: str) -> dict[str, object]:
    return {"sessionUpdate": kind, "content": {"type": "text", "text": text}}


def test_chunks_are_joined_until_something_else_comes() -> None:
    turn = Turn()
    events = [
        *update_events(_chunk("agent_thought_chunk", "Look"), turn),
        *update_events(_chunk("agent_thought_chunk", "ing."), turn),
        *update_events(_chunk("agent_message_chunk", "I will "), turn),
        *update_events(_chunk("agent_message_chunk", "look."), turn),
        *update_events(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": "c1",
                "title": "ls -la",
                "kind": "execute",
                "status": "pending",
                "locations": [{"path": "/w/a.txt", "line": 3}],
                "rawInput": {"command": "ls -la"},
            },
            turn,
        ),
        *update_events({"sessionUpdate": "tool_call", "toolCallId": "c1", "title": "ls"}, turn),
        *update_events(
            {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "c1",
                "status": "completed",
                "content": [
                    {"type": "content", "content": {"type": "text", "text": "a.txt"}},
                    {"type": "diff", "path": "/w/a.txt", "oldText": "", "newText": "x"},
                ],
            },
            turn,
        ),
        *update_events(_chunk("agent_message_chunk", "Done."), turn),
        *turn.flush(),
    ]

    assert events == [
        Thought("Looking."),
        Said("I will look."),
        Called("c1", "shell", '{"title": "ls -la", "command": "ls -la"}', ("/w/a.txt",)),
        Returned("c1", "a.txt\nChanged /w/a.txt"),
        Said("Done."),
    ]
    assert turn.last == "Done."


def test_a_failed_tool_and_its_commands_are_told() -> None:
    turn = Turn()
    failed = update_events(
        {"sessionUpdate": "tool_call_update", "toolCallId": "c2", "status": "failed"}, turn
    )
    commands = update_events(
        {
            "sessionUpdate": "available_commands_update",
            "availableCommands": [{"name": "/init", "description": "Write GEMINI.md."}, {}],
        },
        turn,
    )
    ignored = update_events({"sessionUpdate": "plan", "entries": []}, turn)

    assert failed == [Returned("c2", "", failed=True)]
    assert commands == [CommandsChanged((SlashCommand("init", "Write GEMINI.md.", builtin=True),))]
    assert ignored == []


def test_a_permission_is_answered_by_the_kind_of_its_option() -> None:
    options = [
        {"optionId": "always", "kind": "allow_always"},
        {"optionId": "once", "kind": "allow_once"},
        {"optionId": "no", "kind": "reject_once"},
    ]
    assert permission_outcome(options, True) == {
        "outcome": {"outcome": "selected", "optionId": "once"}
    }
    assert permission_outcome(options, False) == {
        "outcome": {"outcome": "selected", "optionId": "no"}
    }
    assert permission_outcome(options[:1], False) == {"outcome": {"outcome": "cancelled"}}
    assert permission_outcome(options[:1], True)["outcome"]["optionId"] == "always"

    asked = permission_of(
        {
            "toolCall": {
                "kind": "edit",
                "title": "Writing to notes.txt",
                "content": [{"type": "diff", "path": "/w/notes.txt", "newText": "x"}],
            }
        }
    )
    assert (asked.tool, asked.title, asked.summary, asked.description) == (
        "edit",
        "Change files",
        "Change files: Writing to notes.txt",
        "Changed /w/notes.txt",
    )


def test_what_atlas_writes() -> None:
    params = initialize_params("atlas", "Atlas workbench", "1")
    assert params["protocolVersion"] == 1
    assert params["clientCapabilities"]["terminal"] is False

    assert prompt_blocks("Hi.") == [{"type": "text", "text": "Hi."}]
    missing = ExecutorFile("gone.png", "image/png", Path("/no/such.png"))
    assert prompt_blocks("", [missing], "note") == [{"type": "text", "text": "note"}]
    assert AcpError("no", -32000).code == -32000


def _lines(*records: dict[str, object]) -> list[str]:
    return [json.dumps(record) + "\n" for record in records]


def test_a_session_file_is_read_as_the_gemini_cli_reads_it() -> None:
    protocol = plugin_module("protocol")
    read = protocol.conversation(
        [
            *_lines(
                {"sessionId": "s-1", "projectHash": "h", "startTime": "t0", "lastUpdated": "t0"},
                {"id": "u1", "type": "user", "content": [{"text": "/help"}]},
                {"id": "u2", "type": "user", "content": [{"text": "Fix it.\nPlease."}]},
                {
                    "id": "g1",
                    "type": "gemini",
                    "content": "Looking.",
                    "toolCalls": [{"id": "t1", "name": "read_file", "args": {"file_path": "a"}}],
                },
                {"$patch": {"id": "g1", "toolCalls": [{"id": "t1", "result": "patched"}]}},
                {"id": "g2", "type": "gemini", "content": "Gone soon."},
                {"id": "g3", "type": "gemini", "content": "Later."},
                {"$rewindTo": "g3"},
                {"$patch": {"removeIds": ["g2"], "updates": [{"id": "u2", "content": "Fix it."}]}},
                {"$set": {"lastUpdated": "t1", "summary": "Fixing the tests"}},
            ),
            "not json\n",
            "\n",
        ]
    )

    assert read.id == "s-1"
    assert list(read.messages) == ["u1", "u2", "g1"]
    assert read.messages["g1"]["toolCalls"][0]["result"] == "patched"
    assert read.fields["lastUpdated"] == "t1"
    assert read.title() == "Fixing the tests"
    assert read.resumable()
    entries = protocol.entries_of(read)
    assert [(entry.kind, entry.text) for entry in entries][:2] == [
        ("text", "Fix it."),
        ("text", "Looking."),
    ]
    assert entries[2] == TranscriptEntry(
        "tool", '{"file_path": "a"}', tool="read_file", call="t1", files=("a",)
    )
    assert entries[3].kind == "result"


def test_a_whole_list_set_again_and_reordered() -> None:
    protocol = plugin_module("protocol")
    read = protocol.conversation(
        _lines(
            {"sessionId": "s-2", "projectHash": "h", "kind": "subagent"},
            {"id": "old", "type": "user", "content": "gone"},
            {
                "$set": {
                    "messages": [
                        {"id": "a", "type": "user", "content": "First"},
                        {"id": "b", "type": "gemini", "content": "Second"},
                    ]
                }
            },
            {"$patch": {"orderIds": ["a"]}},
            {"$rewindTo": "missing"},
        )
    )
    assert read.subagent
    assert read.messages == {}, "a rewind to a message it does not have empties it"

    reordered = protocol.conversation(
        _lines(
            {"sessionId": "s-3", "projectHash": "h"},
            {"id": "a", "type": "user", "content": "First"},
            {"id": "b", "type": "gemini", "content": "Second"},
            {"$patch": {"orderIds": ["a"]}},
        )
    )
    assert list(reordered.messages) == ["b", "a"]
    assert reordered.title() == "First"


def test_a_copy_has_an_id_of_its_own() -> None:
    protocol = plugin_module("protocol")
    moment = datetime(2026, 10, 21, 14, 5, tzinfo=UTC)
    copied = protocol.renamed(
        _lines({"sessionId": "old", "projectHash": "h"}, {"id": "u", "type": "user"}), "new", moment
    )
    first = json.loads(copied[0])
    assert (first["sessionId"], first["startTime"]) == ("new", "2026-10-21T14:05:00Z")
    assert (
        protocol.session_file_name("abcdef1234", moment)
        == "session-2026-10-21T14-05-abcdef12.jsonl"
    )


def test_a_quota_error_is_the_plans_limit_and_a_pause_is_not() -> None:
    protocol = plugin_module("protocol")
    assert protocol.is_limit(AcpError("You have exhausted your daily quota.", 429))
    assert not protocol.is_limit(AcpError("Rate limit exceeded. Try again later.", 429))
    assert not protocol.is_limit(AcpError("quota", 500))
    finished = protocol.finished_of({"stopReason": "max_turn_requests"}, "s", "last")
    assert (finished.reason, finished.text, finished.session) == ("max_turn_requests", "last", "s")


def test_versions_and_tiers() -> None:
    protocol = plugin_module("protocol")
    assert protocol.version_of("0.65.0\n") == "0.65.0"
    assert protocol.version_of("nothing") == ""
    assert protocol.at_least("0.65.1-nightly", "0.65.0")
    assert not protocol.at_least("weird", "0.65.0")
    assert protocol.plan_of({"paidTier": {"id": "standard-tier"}}) == "standard"
    assert protocol.plan_of({"currentTier": {"id": "x", "name": "Gemini Code Assist"}}) == (
        "Gemini Code Assist"
    ), "a tier it does not know goes by its name"
    assert protocol.usage_of("free", {"buckets": []}).unavailable
