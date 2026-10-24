"""Voice, talking to Atlas by voice: the plugin's `register` (step 17a).

The audio the user sends, dropped in the app or sent on a channel, is written down by OpenAI's
transcription model before the turn, and the model reads what it says. Atlas keeps the text with
the file, so each is written down once.

It imports from Atlas only `atlas.plugins.sdk`, as every plugin must; the rest is its own.
"""

from pathlib import Path

from atlas.plugins.sdk import PluginContext

from .transcribe import Transcriber

#: The vault name of the OpenAI key, the one Atlas's OpenAI provider reads.
KEY_SECRET = "OPENAI_API_KEY"  # noqa: S105 - a name, not the key


def register(ctx: PluginContext) -> None:
    async def read(path: Path, media_type: str) -> str:
        # Read on each file, so a key stored after the plugin was turned on is used.
        transcriber = Transcriber(
            ctx.secret(KEY_SECRET), ctx.setting("api_url"), ctx.setting("transcription_model")
        )
        return await transcriber.transcribe(path, media_type)

    ctx.register_reader("audio", read)
