"""Writing down what an audio file says, through OpenAI's transcription endpoint.

One request a file, with a deadline, the file sent as it is: the endpoint takes the formats a
person sends (Telegram's OGG voice notes, MP3, M4A, WAV, the app's WebM recordings) up to 25 MB,
past Atlas's own 20 MB limit for audio. What fails raises with what to tell, and Atlas leaves the
file's note.
"""

from pathlib import Path
from typing import Any, cast

import httpx2

from .endpoint import OpenAIError, refusal

#: A request's deadline: a long recording takes a while to write down.
REQUEST_SECONDS = 110.0


class Transcriber:
    """OpenAI's transcription, with one key and one model."""

    def __init__(self, key: str | None, api_url: str, model: str) -> None:
        self._key = key
        self._url = f"{api_url.rstrip('/')}/audio/transcriptions"
        self._model = model.strip()

    async def transcribe(self, path: Path, media_type: str) -> str:
        """What the file says, as text; raises `OpenAIError`."""
        return await self.transcribe_data(path.name, path.read_bytes(), media_type)

    async def transcribe_data(self, name: str, data: bytes, media_type: str) -> str:
        """What a recording held in memory says (the app's dictation), as text."""
        if not self._key:
            raise OpenAIError(NO_KEY)
        async with httpx2.AsyncClient(timeout=REQUEST_SECONDS) as client:
            try:
                response = await client.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._key}"},
                    data={"model": self._model, "response_format": "json"},
                    files={"file": (name, data, media_type or "application/octet-stream")},
                )
            except httpx2.HTTPError as error:
                raise OpenAIError(f"OpenAI could not be reached: {error}") from error
        if response.status_code != 200:
            raise OpenAIError(refusal("the transcription", response))
        try:
            body = cast("dict[str, Any]", response.json())
        except ValueError:
            body = {}
        text = body.get("text")
        if not isinstance(text, str):
            raise OpenAIError("OpenAI's answer held no text.")
        return text.strip()


#: What a call without the key says.
NO_KEY = (
    "There is no OPENAI_API_KEY in the vault: store it on the Plugins screen, or with "
    "`atlas secrets set OPENAI_API_KEY`."
)
