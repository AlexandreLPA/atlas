"""The secrets tool, end to end (step 16d).

A real Atlas server, the fake model, and a WebSocket client. The model finds the deferred tool
with `tool_search` and calls `secrets.request`; the server shows the `secret` request to the
client; the client answers with the pasted value; the server writes it to the vault and tells
the model `stored`, never the value. The frames the client saw have no value in them.
"""

import json
import time

import pytest

from atlas.secrets import Vault
from atlas.secrets.errors import VaultLockedError
from fake_model import FakeCall, FakeModelServer, FakeReply
from server_support import Client, Serve

CHAT = "/v1/chat/completions"


def _conversation(client: Client) -> str:
    return client.result("conversation.create")["conversation_id"]


def _tool_results(fake: FakeModelServer, call: int) -> list[str]:
    messages = fake.bodies(CHAT)[call]["messages"]
    return [m["content"] for m in messages if m["role"] == "tool"]


def _load_secrets(call_id: str = "s0") -> FakeReply:
    """A call to tool_search to load the deferred secrets tool."""
    return FakeReply(
        calls=[FakeCall("tool_search", '{"query": "secrets", "limit": 1}', id=call_id)]
    )


def _request_secret(name: str, reason: str, call_id: str = "s1") -> FakeReply:
    return FakeReply(
        calls=[
            FakeCall(
                "secrets",
                json.dumps({"action": "request", "name": name, "reason": reason}),
                id=call_id,
            )
        ]
    )


def _list_secrets(call_id: str = "s1") -> FakeReply:
    return FakeReply(calls=[FakeCall("secrets", '{"action": "list"}', id=call_id)])


def test_a_pasted_key_lands_in_the_vault_and_the_model_hears_stored(
    serve: Serve, fake: FakeModelServer, vault: Vault
) -> None:
    """The request shows, the key is stored byte for byte, and
    the model receives only `stored`."""
    fake.reply(
        _load_secrets(),
        _request_secret("STRIPE_API_KEY", "to list my charges"),
        FakeReply(text="Stored."),
    )
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result(
            "conversation.send", {"conversation_id": conversation, "text": "Set up Stripe"}
        )
        asked = client.request("secret")
        params = asked["params"]
        assert params["name"] == "STRIPE_API_KEY"
        assert params["reason"] == "to list my charges"
        assert params["exists"] is False
        assert params["updated_at"] == ""
        pasted = "sk-live-STRIPE-" + "A" * 40
        client.answer(asked["id"], {"value": pasted})
        assert client.event("message.complete")["payload"]["text"] == "Stored."

    # The value lands in the vault, byte for byte.
    assert vault.get("STRIPE_API_KEY") == pasted
    # The model never saw the value: the tool result is only the status.
    result = json.loads(_tool_results(fake, 2)[-1])
    assert result == {"status": "stored", "name": "STRIPE_API_KEY"}
    # And the value is not in any body the fake model received.
    for body in fake.bodies(CHAT):
        assert pasted not in json.dumps(body)


def test_an_existing_key_warns_the_card_that_pasting_replaces_it(
    serve: Serve, fake: FakeModelServer, vault: Vault
) -> None:
    vault.set("OPENAI_API_KEY", "sk-old-value-0000000000000000000000000000")
    fake.reply(
        _load_secrets(),
        _request_secret("OPENAI_API_KEY", "to call GPT"),
        FakeReply(text="Replaced."),
    )
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result(
            "conversation.send", {"conversation_id": conversation, "text": "Rotate OpenAI"}
        )
        asked = client.request("secret")
        params = asked["params"]

        assert params["exists"] is True
        assert params["updated_at"] != ""
        client.answer(asked["id"], {"value": "sk-new-value-11111111111111111111111"})
        assert client.event("message.complete")["payload"]["text"] == "Replaced."

    assert vault.get("OPENAI_API_KEY") == "sk-new-value-11111111111111111111111"


def test_a_dismissed_card_sends_skipped_to_the_model(
    serve: Serve, fake: FakeModelServer, vault: Vault
) -> None:
    """A dismissed card returns a status; the request does not hang."""
    fake.reply(
        _load_secrets(),
        _request_secret("STRIPE_API_KEY", "to list my charges"),
        FakeReply(text="Noted."),
    )
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result(
            "conversation.send", {"conversation_id": conversation, "text": "Set up Stripe"}
        )
        asked = client.request("secret")
        client.answer(asked["id"], {"skipped": True})
        assert client.event("message.complete")["payload"]["text"] == "Noted."

    assert vault.get("STRIPE_API_KEY") is None
    result = json.loads(_tool_results(fake, 2)[-1])
    assert result == {"status": "skipped", "name": "STRIPE_API_KEY"}


def test_no_app_attached_answers_no_answer_at_once(
    serve: Serve, fake: FakeModelServer, vault: Vault
) -> None:
    """With no app attached, the request is undelivered at once with a hint, so
    the turn does not wait out the fifteen-minute deadline."""
    fake.reply(
        FakeReply(
            calls=[FakeCall("tool_search", '{"query": "secrets"}', id="s0")], wait_before=0.5
        ),
        _request_secret("STRIPE_API_KEY", "to list my charges"),
        FakeReply(text="I will go on without it."),
    )
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result(
            "conversation.send", {"conversation_id": conversation, "text": "Set up Stripe"}
        )
    # The client left before the model asked; nobody is attached when the request comes.
    deadline = time.monotonic() + 20
    while len(fake.bodies(CHAT)) < 3 or server.gateway.assistant.running(conversation):
        assert time.monotonic() < deadline, "the turn waited for an answer nobody could give"
        time.sleep(0.05)

    assert vault.get("STRIPE_API_KEY") is None
    result = json.loads(_tool_results(fake, 2)[-1])
    assert result["status"] == "no_answer"
    assert result["hint"] == "atlas secrets set STRIPE_API_KEY"


def test_list_returns_names_and_dates_without_decrypting(
    serve: Serve, fake: FakeModelServer, vault: Vault
) -> None:
    vault.set("OPENAI_API_KEY", "sk-live-OPENAI-" + "A" * 40)
    fake.reply(_load_secrets(), _list_secrets(), FakeReply(text="One key is stored."))
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result("conversation.send", {"conversation_id": conversation, "text": "Which keys?"})
        assert client.event("message.complete")["payload"]["text"] == "One key is stored."

    result = json.loads(_tool_results(fake, 2)[-1])
    names = [row["name"] for row in result["secrets"]]
    # The listing includes everything in the vault, with no value.
    assert "OPENAI_API_KEY" in names
    assert all("value" not in row for row in result["secrets"])
    # The value never appears in the frames.
    for body in fake.bodies(CHAT):
        assert "sk-live-OPENAI" not in json.dumps(body)


def test_interrupt_withdraws_an_open_secret_card(
    serve: Serve, fake: FakeModelServer, vault: Vault
) -> None:
    """A stop closes an open card so it is not left hanging."""
    fake.reply(_load_secrets(), _request_secret("STRIPE_API_KEY", "to list my charges"))
    server = serve()
    with server.client() as client:
        conversation = _conversation(client)
        client.result(
            "conversation.send", {"conversation_id": conversation, "text": "Set up Stripe"}
        )
        asked = client.request("secret")
        client.result("conversation.interrupt", {"conversation_id": conversation})
        cancel = client.event("request.cancel")["payload"]

    assert cancel == {"id": asked["id"], "method": "secret", "reason": "withdrawn"}
    assert vault.get("STRIPE_API_KEY") is None


def test_a_locked_vault_refuses_the_write_and_the_model_hears_failed(
    serve: Serve, fake: FakeModelServer, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that did not happen must not be reported as stored."""

    def locked(name: str, value: str) -> None:
        del name, value
        raise VaultLockedError("The vault is locked: its key is not here.")

    fake.reply(
        _load_secrets(),
        _request_secret("STRIPE_API_KEY", "to list my charges"),
        FakeReply(text="It did not save."),
    )
    server = serve()
    # Locked only now: the connection's fixture stores the fake provider's key with `vault.set`.
    monkeypatch.setattr(vault, "set", locked)
    with server.client() as client:
        conversation = _conversation(client)
        client.result(
            "conversation.send", {"conversation_id": conversation, "text": "Set up Stripe"}
        )
        asked = client.request("secret")
        client.answer(asked["id"], {"value": "sk-live-LOCKED-" + "A" * 30})
        assert client.event("message.complete")["payload"]["text"] == "It did not save."

    result = json.loads(_tool_results(fake, 2)[-1])
    assert result["status"] == "failed"
    assert result["name"] == "STRIPE_API_KEY"
    assert "locked" in result["detail"]


def test_the_pasted_value_is_in_no_log_line(
    serve: Serve,
    fake: FakeModelServer,
    vault: Vault,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The value goes to the vault and to no log line, even when the write fails: the redactor
    knows it from the moment it is pasted."""
    pasted = "sk-live-LOGS-" + "B" * 40
    fake.reply(
        _load_secrets(),
        _request_secret("STRIPE_API_KEY", "to list my charges"),
        FakeReply(text="Stored."),
    )
    server = serve()
    with caplog.at_level("DEBUG"), server.client() as client:
        conversation = _conversation(client)
        client.result(
            "conversation.send", {"conversation_id": conversation, "text": "Set up Stripe"}
        )
        asked = client.request("secret")
        client.answer(asked["id"], {"value": pasted})
        client.event("message.complete")

    assert vault.get("STRIPE_API_KEY") == pasted
    assert pasted not in caplog.text
