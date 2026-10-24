"""The image as a user runs it: an empty container, an API key and a working conversation (principle
4; docs/ARCHITECTURE.md, Container), through the real docker-compose.yml. The one stand-in is the
OpenAI provider's address, pointed at the fake model in a container beside Atlas's.
"""

import json
import sqlite3
import time
from pathlib import Path

from atlas.__main__ import main
from docker_support import Stack

KEY = "sk-fake-container-0123456789"


def test_an_empty_container_and_a_key_give_a_working_conversation(stack: Stack) -> None:
    # The token a user gives the app, kept across asks and restarts.
    token = stack.token()
    assert token == stack.token()

    # Nothing chosen and no key: the turn says how to store one.
    with stack.client(token) as client:
        conversation = client.result("conversation.create")["conversation_id"]
        client.result("conversation.send", {"conversation_id": conversation, "text": "Oi"})
        failed = client.event("message.complete", timeout=60)["payload"]
    assert failed["status"] == "failed"
    assert failed["error"] == "ModelChoiceError"
    assert "atlas secrets set OPENAI_API_KEY" in failed["text"]

    # The key, from standard input; the provider's address, the stand-in, is read at start.
    stack.store_key("OPENAI_API_KEY", KEY)
    stack.point_at_fake_model("openai")
    stack.restart()
    assert stack.token() == token

    command = "uname -s && id -un && pwd && echo kept > notes.txt"
    # Only the conversation's turn takes them: the title model asks the fake model too, and
    # took one of them before (the conversation's titles, 14/10/2026).
    turn = "Skills come first"
    stack.reply(
        {
            "calls": [{"name": "run_command", "arguments": json.dumps({"command": command})}],
            "when": turn,
        },
        {"text": "Rodo em Linux, como atlas.", "when": turn},
    )
    with stack.client(token) as client:
        client.result("conversation.open", {"conversation_id": conversation})
        client.result(
            "conversation.send", {"conversation_id": conversation, "text": "Onde você roda?"}
        )
        events = client.events_until("message.complete", timeout=120)
    ran = next(event["payload"] for event in events if event["type"] == "tool.complete")
    assert ran["name"] == "run_command"
    assert ran["status"] == "completed", ran["result_text"]
    assert ran["result_text"].split()[:3] == ["Linux", "atlas", "/data/home"]
    assert events[-1]["payload"]["status"] == "answered"
    asked = stack.model_requests()
    # OpenAI was chosen by itself, the first provider with a key, and its key reached it.
    assert {request["path"] for request in asked} == {"/v1/responses"}
    assert {request["authorization"] for request in asked} == {f"Bearer {KEY}"}

    # A restart keeps the conversation, and what the agent made in the user's home folder.
    stack.restart()
    with stack.client(token) as client:
        opened = client.result("conversation.open", {"conversation_id": conversation})
    said = [(message["role"], message["text"]) for message in opened["messages"]]
    assert ("user", "Oi") in said
    assert ("user", "Onde você roda?") in said
    assert ("assistant", "Rodo em Linux, como atlas.") in said
    assert stack.exec("cat", "/data/home/notes.txt").strip() == "kept"


def test_atlas_runs_as_its_own_user_under_tini_which_reaps_orphans(stack: Stack) -> None:
    assert stack.exec("id", "-un").strip() == "atlas"
    assert stack.exec("cat", "/proc/1/comm").strip() == "tini"

    # A grandchild whose parent is gone is handed to PID 1, which must reap it once it exits
    # (without tini, Atlas's Python would leave it a zombie).
    stack.exec("sh", "-c", "( ( sleep 0.1 & ) & ); sleep 1")
    find = 'for f in /proc/[0-9]*/status; do grep -qs "^State:.*Z" "$f" && echo "$f"; done; true'
    deadline = time.monotonic() + 10
    while (zombies := stack.exec("sh", "-c", find).split()) and time.monotonic() < deadline:
        time.sleep(0.5)
    assert zombies == []


def test_docker_calls_it_healthy(stack: Stack) -> None:
    deadline = time.monotonic() + 60
    while (health := stack.health()) != "healthy" and time.monotonic() < deadline:
        time.sleep(1)
    assert health == "healthy"


def test_a_backup_leaves_the_container_and_restores_on_this_machine(
    stack: Stack, tmp_path: Path
) -> None:
    # `docker compose exec -T atlas atlas backup -o - > atlas.zip`, then a home here.
    stack.store_key("OPENAI_API_KEY", KEY)
    zipped = stack.exec_bytes("atlas", "backup", "-o", "-")
    assert zipped.startswith(b"PK")
    assert KEY.encode() not in zipped
    backup = tmp_path / "atlas.zip"
    backup.write_bytes(zipped)

    here = tmp_path / "here"
    assert main(["restore", str(backup)], {"ATLAS_HOME": str(here)}) == 0

    connection = sqlite3.connect(here / "atlas.db")
    try:
        said = {row[0] for row in connection.execute("SELECT text FROM conversations_messages")}
    finally:
        connection.close()
    assert {"Oi", "Onde você roda?", "Rodo em Linux, como atlas."} <= said
    assert not (here / "secrets").exists()
