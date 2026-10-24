"""An attached image in a conversation whose model does not see images (step 16g): the description
model describes it before the turn, and the conversation's model reads the
description in its place. The models are the fake model server through the real adapters."""

import asyncio
from pathlib import Path

from atlas.__main__ import SCHEMAS
from atlas.assistant import Assistant
from atlas.assistant.describe import DESCRIPTION_PROMPT, DESCRIPTION_PURPOSE
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.models import AttachmentKind, CallLedger, UserMessage
from atlas.secrets import Vault
from fake_model import FakeModelServer, FakeReply
from model_support import Connect

MESSAGES = "/v1/messages"
CHAT = "/v1/chat/completions"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _assistant(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> Assistant:
    migrate(Database(home.database_file), SCHEMAS)
    home.models_file.write_text(
        '[[models]]\nprovider = "openrouter"\nid = "words"\ntool_call = true\n'
        'modalities = { input = ["text"], output = ["text"] }\n',
        encoding="utf-8",
    )
    chosen = ModelSettings(provider="openrouter", name="words")
    models = connect(
        fake,
        "openrouter",
        "anthropic",
        settings=chosen,
        ledger=CallLedger(Database(home.database_file)),
    )
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    return Assistant(home, settings, models, vault, listener=lambda _c, _e: None)


def test_a_text_only_model_reads_what_the_description_model_saw(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> None:
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    fake.reply(
        FakeReply(text="A bar chart: sales rose from 10 to 40.", when="You describe an image"),
        FakeReply(text='{"title": "Sales chart"}', when="Your job is naming chat sessions."),
        FakeReply(text="Sales quadrupled.", when="You are Atlas"),
    )

    async def scenario() -> str:
        added = assistant.attachments.add("", "sales.png", len(PNG), 0, PNG)
        assert added.attachment is not None
        stored = assistant.conversations.create()
        conversation = assistant.conversation(stored.id)
        conversation.send(UserMessage("What does this show?", (added.attachment,)))
        await conversation.wait()
        await assistant.close()
        return stored.id

    conversation_id = asyncio.run(scenario())

    described = [
        body for body in fake.bodies(MESSAGES) if DESCRIPTION_PROMPT in str(body["messages"])
    ]
    assert len(described) == 1
    blocks = described[0]["messages"][0]["content"]
    assert [block["type"] for block in blocks] == ["text", "image"]
    (turn,) = fake.bodies(CHAT)
    said = turn["messages"][-1]["content"]
    assert isinstance(said, str)
    assert "A bar chart: sales rose from 10 to 40." in said
    assert "image_url" not in str(turn["messages"])
    # Kept with the message: the next call reads it without describing again.
    history = assistant.conversations.open(conversation_id).history
    user = next(message for message in history if isinstance(message, UserMessage))
    assert user.attachments[0].kind is AttachmentKind.IMAGE
    assert user.attachments[0].description == "A bar chart: sales rose from 10 to 40."
    purposes = Database(home.database_file).connect().execute("SELECT purpose FROM models_calls")
    assert DESCRIPTION_PURPOSE in {row[0] for row in purposes}
