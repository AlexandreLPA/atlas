"""A key asked in a Telegram conversation (step 16d): the chat gets a notice and never the card.

The Telegram plugin Atlas ships, a fake Telegram, the fake model, and the app. The model asks for
a key; the chat is told where to paste it; a key typed in the chat does not answer the card; and
the key pasted in the app goes to the vault, which the model hears only as `stored`.
"""

import json
import time
from collections.abc import Callable, Iterator

import pytest

from atlas.home import Database, Home
from atlas.plugins import Plugins
from atlas.secrets import Vault
from fake_model import FakeCall, FakeModelServer, FakeReply
from fake_telegram import TOKEN, FakeTelegram
from server_support import Client, Serve

ANA = 100


@pytest.fixture
def telegram() -> Iterator[FakeTelegram]:
    with FakeTelegram() as server:
        yield server


def _until[T](found: Callable[[], T], seconds: float = 10) -> T:
    deadline = time.monotonic() + seconds
    while not (value := found()):
        if time.monotonic() > deadline:
            raise AssertionError("It did not happen in time.")
        time.sleep(0.02)
    return value


def _turn_on(home: Home, vault: Vault, telegram: FakeTelegram) -> Plugins:
    plugins = Plugins(home, Database(home.database_file), vault)
    plugins.set_setting("telegram", "api_url", telegram.url)
    plugins.set_secret("telegram", "TELEGRAM_BOT_TOKEN", TOKEN)
    manifest = plugins.view("telegram").manifest
    assert manifest is not None
    plugins.enable("telegram", manifest.fingerprint())
    return plugins


def _paired(client: Client, telegram: FakeTelegram, user: int = ANA) -> None:
    telegram.say(user, "hi")
    said = _until(lambda: [t for t in telegram.sent(user) if "pairing code" in t])
    waiting = client.result("pairing.list")["pairings"]
    [row] = [p for p in waiting if p["user"] == str(user)]
    assert row["code"] in said[0]
    client.result("pairing.approve", {"code": row["code"]})


def test_a_key_asked_in_telegram_is_pasted_in_the_app_and_never_in_the_chat(
    serve: Serve, fake: FakeModelServer, telegram: FakeTelegram, home: Home, vault: Vault
) -> None:
    _turn_on(home, vault, telegram)
    pasted = "sk-live-TELEGRAM-" + "C" * 40
    fake.reply(
        FakeReply(calls=[FakeCall("tool_search", '{"query": "secrets"}', id="s0")]),
        FakeReply(
            calls=[
                FakeCall(
                    "secrets",
                    json.dumps(
                        {"action": "request", "name": "STRIPE_API_KEY", "reason": "to list charges"}
                    ),
                    id="s1",
                )
            ]
        ),
        FakeReply(text="Stored, thanks."),
    )
    with serve().client() as client:
        _paired(client, telegram)
        telegram.say(ANA, "set up stripe")
        notice = _until(
            lambda: [t for t in telegram.sent(ANA) if t.startswith("Atlas asked for the key")]
        )[0]
        # The chat is told where to paste it, and no button or typed answer is offered there.
        assert "STRIPE_API_KEY" in notice
        assert "Paste it in the Atlas app" in notice
        assert telegram.buttons(ANA) == []

        [row] = [
            r
            for r in client.result("conversation.list")["conversations"]
            if r["source"] == "telegram"
        ]
        opened = client.result("conversation.open", {"conversation_id": row["id"]})
        [asked] = [r for r in opened["open_requests"] if r["method"] == "secret"]
        assert asked["params"]["name"] == "STRIPE_API_KEY"

        # A message typed in the chat is an ordinary message: it does not answer the card.
        telegram.say(ANA, "I will paste it in the app")
        time.sleep(0.5)
        assert vault.get("STRIPE_API_KEY") is None
        assert [
            r["id"]
            for r in client.result("conversation.open", {"conversation_id": row["id"]})[
                "open_requests"
            ]
        ] == [asked["id"]]

        client.answer(asked["id"], {"value": pasted})
        _until(lambda: "Stored, thanks." in telegram.sent(ANA))

    assert vault.get("STRIPE_API_KEY") == pasted
    results = [
        m["content"]
        for m in fake.bodies("/v1/chat/completions")[-1]["messages"]
        if m["role"] == "tool"
    ]
    assert json.loads(results[-1]) == {"status": "stored", "name": "STRIPE_API_KEY"}
    # The value went to the app and the vault only: no chat message carries it.
    assert all(pasted not in text for text in telegram.sent(ANA))
