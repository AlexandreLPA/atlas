"""The voice plugin's audio (step 17a): a plugin's reader (SDK version 21) and the voice plugin
Atlas ships, which writes down what an audio file says through OpenAI's transcription.

The voice plugin is the shipped folder, loaded as Atlas loads it, and it reaches the fake model
server over real HTTP, as it would reach OpenAI; the conversations run on the assistant against the
same server, so what reaches the model is what a provider would get.
"""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from atlas.__main__ import SCHEMAS
from atlas.assistant import Assistant
from atlas.home import Database, Home, ModelSettings, Settings, WorkspaceSettings, migrate
from atlas.models import CallLedger, UserMessage
from atlas.plugins import BUNDLED_FOLDER, ManifestError, Plugins
from atlas.plugins.model import parse_manifest
from atlas.secrets import Vault
from fake_model import FakeError, FakeModelServer, FakeReply
from model_support import Connect

CHAT = "/v1/chat/completions"
#: A voice note's first bytes, as Telegram sends one: an Ogg page.
VOICE = b"OggS\x00\x02" + b"\x00" * 64
SAID = "Remind me to call the dentist on Thursday."


@pytest.fixture
def plugins(home: Home, vault: Vault) -> Plugins:
    database = Database(home.database_file)
    migrate(database, SCHEMAS)
    return Plugins(home, database, vault, bundled_folder=BUNDLED_FOLDER)


def _voice_on(plugins: Plugins, fake: FakeModelServer, key: str | None = "fake-key") -> None:
    plugins.set_setting("voice", "api_url", fake.base_url)
    if key is not None:
        plugins.set_secret("voice", "OPENAI_API_KEY", key)
    manifest = plugins.view("voice").manifest
    assert manifest is not None
    plugins.enable("voice", manifest.fingerprint())


def _assistant(
    home: Home, vault: Vault, fake: FakeModelServer, connect: Connect, tmp_path: Path
) -> Assistant:
    chosen = ModelSettings(provider="openrouter")
    # OpenAI on the fake too: the key the plugin reads is the title model's as well.
    models = connect(
        fake,
        "openrouter",
        "openai",
        settings=chosen,
        ledger=CallLedger(Database(home.database_file)),
    )
    work = tmp_path / "work"
    work.mkdir(exist_ok=True)
    settings = Settings(home=home.root, model=chosen, workspace=WorkspaceSettings(work))
    return Assistant(home, settings, models, vault, listener=lambda _c, _e: None)


def _said(body: dict[str, Any]) -> str:
    content = body["messages"][-1]["content"]
    return content if isinstance(content, str) else json.dumps(content)


# -- the manifest ----------------------------------------------------------------------------------


def test_a_reader_is_declared_and_shown_at_consent() -> None:
    manifest = parse_manifest(
        json.dumps({"name": "ears", "atlas": {"api": 21, "readers": ["audio"]}}),
        {"__init__.py"},
        folder_name="ears",
    )
    assert manifest.readers == ("audio",)
    assert any("Reads the audio files you send" in line for line in manifest.declaration())
    without = parse_manifest(
        json.dumps({"name": "ears", "atlas": {"api": 21}}), {"__init__.py"}, folder_name="ears"
    )
    assert manifest.fingerprint() != without.fingerprint(), "a new reader asks consent again"


@pytest.mark.parametrize(
    ("atlas", "present", "problem"),
    [
        ({"api": 21, "readers": ["audio"]}, set(), "brings no __init__.py"),
        ({"api": 20, "readers": ["audio"]}, {"__init__.py"}, "need atlas.api 21"),
        ({"api": 21, "readers": ["video"]}, {"__init__.py"}, "readers for video"),
    ],
)
def test_a_reader_declared_badly_is_refused(
    atlas: dict[str, Any], present: set[str], problem: str
) -> None:
    with pytest.raises(ManifestError, match=problem):
        parse_manifest(json.dumps({"name": "ears", "atlas": atlas}), present, folder_name="ears")


def _ears(root: Path, name: str, atlas: dict[str, Any]) -> None:
    folder = root / name
    (folder / ".claude-plugin").mkdir(parents=True)
    manifest = {"name": name, "atlas": atlas}
    (folder / ".claude-plugin" / "plugin.json").write_text(json.dumps(manifest), "utf-8")
    (folder / "__init__.py").write_text(
        "def register(ctx):\n"
        "    async def read(path, media_type):\n"
        f"        return {name!r}\n"
        "    ctx.register_reader('audio', read)\n",
        encoding="utf-8",
    )


def test_a_reader_it_does_not_declare_marks_the_plugin(plugins: Plugins, home: Home) -> None:
    # A call keeps it among the plugins whose Python the loop's parts load.
    _ears(home.plugins_folder, "ears", {"api": 21, "calls": ["ping"]})
    manifest = plugins.view("ears").manifest
    assert manifest is not None
    plugins.enable("ears", manifest.fingerprint())
    parts = asyncio.run(plugins.parts())
    assert parts.readers == []
    assert "a reader for audio, which it does not declare" in str(plugins.view("ears").problem)


def test_one_reader_a_kind_the_first_loaded(plugins: Plugins, home: Home) -> None:
    for name in ("ears", "more-ears"):
        _ears(home.plugins_folder, name, {"api": 21, "readers": ["audio"]})
        manifest = plugins.view(name).manifest
        assert manifest is not None
        plugins.enable(name, manifest.fingerprint())
    parts = asyncio.run(plugins.parts())
    assert [(reader.plugin, reader.kind) for reader in parts.readers] == [("ears", "audio")]


# -- the voice plugin ------------------------------------------------------------------------------


def test_an_audio_file_reaches_the_model_written_down_once(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _voice_on(plugins, fake)
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    fake.transcribe(SAID)
    fake.reply(FakeReply(text="I will remind you."), FakeReply(text="Thursday, yes."))

    async def scenario() -> str:
        parts = await plugins.parts()
        await assistant.set_plugins([], {}, {}, readers=parts.readers)
        added = assistant.attachments.add("", "voice.ogg", len(VOICE), 0, VOICE)
        assert added.attachment is not None
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send(UserMessage("", (added.attachment,)))
        await conversation.wait()
        conversation.send("Which day was it?")
        await conversation.wait()
        await assistant.close()
        await plugins.stop()
        return conversation.id

    conversation_id = asyncio.run(scenario())

    (heard,) = fake.heard
    assert heard["model"] == "gpt-transcribe"
    assert (heard["file_name"], heard["file_type"], heard["file_bytes"]) == (
        "voice.ogg",
        "audio/ogg",
        VOICE,
    )
    first, second = fake.bodies(CHAT)
    assert SAID in _said(first)
    assert "may have misheard" in _said(first)
    assert SAID in json.dumps(second["messages"]), "the next call reads it again, unheard"
    history = assistant.conversations.open(conversation_id).history
    user = next(message for message in history if isinstance(message, UserMessage))
    assert user.attachments[0].description == SAID, "kept with the file"


@pytest.mark.parametrize(
    ("key", "answer"),
    [(None, None), ("fake-key", FakeError(500, "The transcription service is down."))],
    ids=["no key in the vault", "OpenAI fails"],
)
def test_audio_not_written_down_keeps_its_note_and_the_turn_goes_on(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
    key: str | None,
    answer: FakeError | None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _voice_on(plugins, fake, key)
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    if key is None:
        vault.delete("OPENAI_API_KEY")  # the one pointing OpenAI at the fake stored
    if answer is not None:
        fake.transcribe(answer)
    fake.reply(FakeReply(text="I could not hear it."))

    async def scenario() -> None:
        parts = await plugins.parts()
        await assistant.set_plugins([], {}, {}, readers=parts.readers)
        added = assistant.attachments.add("", "voice.ogg", len(VOICE), 0, VOICE)
        assert added.attachment is not None
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send(UserMessage("", (added.attachment,)))
        await conversation.wait()
        await assistant.close()
        await plugins.stop()

    asyncio.run(scenario())

    (turn,) = fake.bodies(CHAT)
    assert "an audio file attachment: 'voice.ogg'" in _said(turn), "the note, as before"
    expected = "no OPENAI_API_KEY" if key is None else "HTTP 500"
    assert any(
        "voice could not read voice.ogg" in record.getMessage() and expected in record.getMessage()
        for record in caplog.records
    )


def test_audio_sent_while_a_turn_runs_is_written_down_before_it_steers(
    plugins: Plugins,
    home: Home,
    vault: Vault,
    fake: FakeModelServer,
    connect: Connect,
    tmp_path: Path,
) -> None:
    _voice_on(plugins, fake)
    assistant = _assistant(home, vault, fake, connect, tmp_path)
    fake.transcribe(SAID)
    fake.reply(FakeReply(text="Working on the first thing.", pause=0.05), FakeReply(text="Noted."))

    async def scenario() -> None:
        parts = await plugins.parts()
        await assistant.set_plugins([], {}, {}, readers=parts.readers)
        added = assistant.attachments.add("", "voice.ogg", len(VOICE), 0, VOICE)
        assert added.attachment is not None
        conversation = assistant.conversation(assistant.conversations.create().id)
        conversation.send("Plan my week.")
        assert conversation.running
        conversation.send(UserMessage("", (added.attachment,)))
        await conversation.wait()
        await assistant.close()
        await plugins.stop()

    asyncio.run(scenario())

    assert len(fake.heard) == 1
    bodies = fake.bodies(CHAT)
    assert SAID not in _said(bodies[0])
    assert any(SAID in _said(body) for body in bodies[1:]), "it reached the model, written down"
