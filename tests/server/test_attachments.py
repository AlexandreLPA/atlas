"""Attachments over the client protocol (step 16g): a file sent in parts, a message with files,
what the model got, and the conversation reopened with its chips."""

import base64
import json
import os
from pathlib import Path

import pytest

from claude_code_support import claude_code
from fake_model import FakeModelServer, FakeReply
from server_support import Client, Serve

CHAT = "/v1/chat/completions"
#: JSON-RPC's code for parameters the method does not take.
INVALID_PARAMS = -32602
PNG = b"\x89PNG\r\n\x1a\n" + b"\x01" * 200


def _add(client: Client, name: str, data: bytes, part: int | None = None) -> dict[str, object]:
    """A file sent as the app sends it, in parts of `part` bytes."""
    size = part or max(1, len(data))
    upload, offset = "", 0
    while True:
        chunk = data[offset : offset + size]
        added = client.result(
            "attachment.add",
            {
                "name": name,
                "size": len(data),
                "offset": offset,
                "upload": upload,
                "data": base64.b64encode(chunk).decode(),
            },
        )
        if added["attachment"] is not None:
            return added["attachment"]
        upload, offset = added["upload"], added["received"]


def _conversation(client: Client) -> str:
    return client.result("conversation.create")["conversation_id"]


def test_files_sent_in_parts_reach_the_model_and_come_back_as_chips(
    serve: Serve, fake: FakeModelServer
) -> None:
    fake.reply(FakeReply(text="A tiny image and your notes."))
    server = serve()
    with server.client() as client:
        limits = client.result("attachment.limits")
        image = _add(client, "chart.png", PNG, part=64)
        notes = _add(client, "notes.md", b"# Notes\nShip the attachments on Friday, then rest.\n")
        conversation = _conversation(client)
        sent = client.result(
            "conversation.send",
            {
                "conversation_id": conversation,
                "text": "",
                "attachments": [image["id"], notes["id"]],
            },
        )
        events = client.events_until("message.complete")
        opened = client.result("conversation.open", {"conversation_id": conversation})
        listed = client.result("conversation.list")["conversations"]

    assert limits["image"] == 5_000_000
    assert limits["part"] == 4 * 1024 * 1024
    assert (image["kind"], image["media_type"], image["size"]) == ("image", "image/png", len(PNG))
    assert notes["kind"] == "text"
    assert Path(str(image["path"])).read_bytes() == PNG
    assert sent == {"status": "started"}
    start = events[0]["payload"]
    assert [row["name"] for row in start["attachments"]] == ["chart.png", "notes.md"]
    assert start["text"] == ""
    # The model saw the image as itself, and read the notes whole.
    (body,) = fake.bodies(CHAT)
    parts = body["messages"][-1]["content"]
    assert [part["type"] for part in parts] == ["text", "image_url"]
    assert parts[1]["image_url"]["url"] == "data:image/png;base64," + base64.b64encode(PNG).decode()
    assert "Ship the attachments on Friday" in parts[0]["text"]
    assert "[Image attached at:" in parts[0]["text"]
    # Reopened, the message shows its files again, never the notes the model read.
    user = opened["messages"][0]
    assert (user["role"], user["text"]) == ("user", "")
    assert [(row["name"], row["kind"]) for row in user["attachments"]] == [
        ("chart.png", "image"),
        ("notes.md", "text"),
    ]
    assert listed[0]["title"] == "chart.png, notes.md"


def test_a_message_with_an_attachment_that_is_gone_is_refused(serve: Serve) -> None:
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        refused = client.call(
            "conversation.send",
            {
                "conversation_id": conversation,
                "text": "Read it",
                "attachments": ["0123456789abcdef"],
            },
        )
        empty = client.call("conversation.send", {"conversation_id": conversation, "text": " "})
        bad = client.call(
            "attachment.add", {"name": "a.txt", "size": 3, "data": "not base64!", "upload": ""}
        )

    assert refused["error"]["code"] == INVALID_PARAMS
    assert "attach it again" in refused["error"]["message"]
    assert empty["error"]["code"] == INVALID_PARAMS
    assert bad["error"]["code"] == INVALID_PARAMS


def test_an_executors_session_takes_files_and_reads_them_back_as_chips(
    serve: Serve, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, work: Path
) -> None:
    """Step 16i: the same files, sent to Claude Code from its session's box; the screen hears
    them with the prompt, and reads them back from its session as the same chips."""
    session = "9c9c9c9c-0000-4000-8000-0000000016e1"
    script = tmp_path / "claude-script.json"
    script.write_text(json.dumps([{"result": "Seen."}]), encoding="utf-8")
    monkeypatch.setenv("FAKE_CLAUDE_SCRIPT", str(script))
    monkeypatch.setenv("FAKE_CLAUDE_AUTH", str(tmp_path / "auth.json"))
    monkeypatch.setenv("FAKE_CLAUDE_SESSION", session)
    monkeypatch.setenv(
        "FAKE_CLAUDE_PROJECTS", str(Path(os.environ["CLAUDE_CONFIG_DIR"]) / "projects")
    )
    server = serve(executors={"claude-code": claude_code()})
    with server.client() as client:
        [executor] = client.result("workbench.list")["executors"]
        assert executor["files"] is True
        image = _add(client, "chart.png", PNG)
        report = _add(client, "report.pdf", b"%PDF-1.7\n")
        card = client.result("workbench.start", {"executor": "claude-code", "folder": "."})
        prompt = {
            "instance_id": card["instance_id"],
            "text": "What is wrong here?",
            "attachments": [image["id"], report["id"]],
        }
        assert client.result("workbench.prompt", prompt) == {"steered": False}
        heard = client.event("workbench.activity", timeout=20)["payload"]
        done = client.event("workbench.update", timeout=20)
        while done["payload"]["state"] != "idle" or not done["payload"]["session_id"]:
            done = client.event("workbench.update", timeout=20)
        read = client.result(
            "executor.session",
            {"executor": "claude-code", "session_id": session, "folder": str(work)},
        )
        gone = client.call("workbench.prompt", {**prompt, "attachments": ["0" * 16]})

    assert (heard["kind"], heard["text"]) == ("prompted", "What is wrong here?")
    assert heard["attachments"] == [image, report]
    [said] = [e for e in read["entries"] if e["by"] == "user"]
    assert (said["text"], said["attachments"]) == ("What is wrong here?", [image, report])
    assert gone["error"]["code"] == INVALID_PARAMS
