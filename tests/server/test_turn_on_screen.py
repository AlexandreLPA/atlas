"""The turn on screen (step 9c) as the server says it: the interim text, how each call ended, and a
conversation reopened with the same parts it showed live.

A real uvicorn, a real WebSocket, the real assistant and the fake model, as the gateway's tests.
"""

import json
from pathlib import Path
from typing import Any

from fake_model import FakeCall, FakeModelServer, FakeReply
from server_support import Client, Serve


def _conversation(client: Client) -> str:
    return client.result("conversation.create")["conversation_id"]


def _kinds(events: list[dict[str, Any]]) -> list[str]:
    streamed = ("message.delta", "reasoning.delta")
    return [event["type"] for event in events if event["type"] not in streamed]


def test_interim_text_a_failed_call_and_the_answer_reopen_as_they_were_seen(
    serve: Serve, fake: FakeModelServer, work: Path
) -> None:
    fake.reply(
        FakeReply(
            reasoning="**Finding the notes**\n\nThey should be here.",
            text="Let me read the notes.",
            calls=[FakeCall("read_file", '{"path": "missing.md"}', id="r1")],
        ),
        FakeReply(text="There are no notes."),
    )
    with serve().client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Notes?"})
        events = client.events_until("message.complete")
        opened = client.result("conversation.open", {"conversation_id": conversation})

    assert _kinds(events) == [
        "message.start",
        "message.interim",
        "tool.start",
        "tool.complete",
        "message.complete",
    ]
    interim = next(e for e in events if e["type"] == "message.interim")["payload"]
    assert interim == {"text": "Let me read the notes."}
    done = next(e for e in events if e["type"] == "tool.complete")["payload"]
    assert done["status"] == "failed"
    assert isinstance(done["duration_s"], float)
    assert events[-1]["payload"]["text"] == "There are no notes."  # only the final answer

    user, asked, result, answer = opened["messages"]
    assert user["text"] == "Notes?"
    assert asked["reasoning"] == "**Finding the notes**\n\nThey should be here."
    assert asked["text"] == "Let me read the notes."
    assert asked["calls"] == [
        {"id": "r1", "name": "read_file", "args_text": '{"path": "missing.md"}'}
    ]
    assert (result["call_id"], result["status"]) == ("r1", "failed")
    assert (answer["text"], answer["calls"], answer["status"]) == ("There are no notes.", [], None)


def test_a_stop_says_every_cut_call_was_cancelled_and_keeps_it(
    serve: Serve, fake: FakeModelServer
) -> None:
    ask = json.dumps({"questions": [{"question": "Which file?"}]})
    fake.reply(
        FakeReply(
            calls=[
                FakeCall("ask_user", ask, id="a1"),
                FakeCall("write_file", '{"path": "x.md", "content": "x"}', id="w1"),
            ]
        )
    )
    with serve().client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Write"})
        client.request("ask_user")
        client.result("conversation.interrupt", {"conversation_id": conversation})
        events = client.events_until("message.complete")
        opened = client.result("conversation.open", {"conversation_id": conversation})

    tools = [
        (e["type"], e["payload"]["tool_id"], e["payload"].get("status"))
        for e in events
        if e["type"].startswith("tool.")
    ]
    # The question was asked, so it had started; the write never had. Both end cancelled, the
    # write told as started first, and both before the turn's end.
    assert tools == [
        ("tool.start", "a1", None),
        ("tool.complete", "a1", "cancelled"),
        ("tool.start", "w1", None),
        ("tool.complete", "w1", "cancelled"),
    ]
    assert events[-1]["payload"]["status"] == "interrupted"
    stored = [(m["call_id"], m["status"]) for m in opened["messages"] if m["role"] == "tool"]
    assert stored == [("a1", "cancelled"), ("w1", "cancelled")]
