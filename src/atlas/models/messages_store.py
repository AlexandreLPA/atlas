"""The Messages wire's I/O: Anthropic's official SDK (step 3b), an extra.

`atlas.models.store` loads this module only for a provider that speaks the Messages API and only
when the extra is installed, so a bare install starts without it and says how to add it (the
`ddgs` and `mcp` extras are loaded the same way). It sends the body `atlas.models.messages` built
and hands back each streamed event as a plain dictionary; the SDK's errors become Atlas's.

From Anthropic's error documentation on 13/10/2026:

- The key is given to the SDK, so it reads no credential from the environment: otherwise it may
  send `ANTHROPIC_AUTH_TOKEN` beside the key.
- The SDK's own retries stay on (connection errors, 408, 409, 429, 5xx, with `retry-after`): they
  are the only ones Atlas has.
- An empty account answers 402 `billing_error`, or a 400 "Your credit balance is too low" (not on
  the error page, seen in reports); the monthly spending cap is a 429 that waiting does not lift;
  529 is an overloaded API, which passes.
"""

from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Any, cast

import anthropic

from atlas.models.errors import (
    ModelAuthError,
    ModelOutOfCreditError,
    ModelRateLimitError,
    ModelRequestError,
    ModelsError,
    ModelUnavailableError,
)
from atlas.models.model import KeyCheck, KeyStatus, Provider

#: A limit the provider says lifts within this many seconds passes; a longer one does not
#: (atlas.models.store's PASSING_WAIT).
PASSING_WAIT = 60.0
#: Words in an error that say the account cannot pay, whatever its status.
_NO_CREDIT = ("credit balance is too low", "spend limit", "spending limit", "billing")


class MessagesConnection:
    """One Anthropic-speaking provider's address and key, through Anthropic's SDK."""

    def __init__(
        self, provider: Provider, key: str | None, *, retries: int, silence: float, connect: float
    ) -> None:
        self.provider = provider
        self._silence = silence
        self._client = anthropic.AsyncAnthropic(
            api_key=key or "no-key",
            base_url=provider.base_url,
            max_retries=retries,
            # The wait for a connection stays short; the read timeout is the longest silence.
            timeout=anthropic.Timeout(silence, connect=connect),
        )

    async def stream(
        self, body: Mapping[str, Any], betas: Sequence[str]
    ) -> AsyncGenerator[dict[str, Any]]:
        """Send the body; yield each event as a plain dictionary."""
        client = self._client
        rest = {
            key: value
            for key, value in body.items()
            if key not in ("model", "max_tokens", "messages", "stream")
        }
        messages = body["messages"]
        try:
            if betas:
                stream = await client.beta.messages.create(
                    model=body["model"],
                    max_tokens=body["max_tokens"],
                    messages=messages,
                    stream=True,
                    betas=list(betas),
                    extra_body=rest,
                )
            else:
                stream = await client.messages.create(
                    model=body["model"],
                    max_tokens=body["max_tokens"],
                    messages=messages,
                    stream=True,
                    extra_body=rest,
                )
            async with stream:
                async for event in stream:
                    yield event.model_dump(mode="json", warnings=False)
        except anthropic.APIError as error:
            raise anthropic_error(self.provider, error, self._silence) from error

    async def check_key(self) -> KeyCheck:
        """The models list, which answers only to a valid key and spends nothing. Never retried."""
        provider = self.provider
        name = provider.name
        try:
            await self._client.with_options(max_retries=0).models.list(limit=1)
        except anthropic.APIStatusError as error:
            status, detail = error.status_code, _detail(error)
            if status in (401, 403):
                found = KeyStatus.REFUSED, f"{name} refused the key (HTTP {status}): {detail}."
            elif _no_credit(status, detail, error):
                found = KeyStatus.NO_CREDIT, f"The key is valid; its credit: {detail}."
            elif status == 429:
                found = KeyStatus.LIMITED, f"The key is valid; {name} is limiting requests for now."
            else:
                found = KeyStatus.UNEXPECTED, f"{name} answered HTTP {status}: {detail}."
            return KeyCheck(provider.id, *found)
        except anthropic.APIConnectionError as error:
            return KeyCheck(
                provider.id,
                KeyStatus.UNREACHABLE,
                f"{name} could not be reached at {provider.base_url} ({error.message}). "
                "This says nothing about the key.",
            )
        return KeyCheck(provider.id, KeyStatus.VALID, f"The key opens {name}.")

    async def close(self) -> None:
        await self._client.close()


def anthropic_error(provider: Provider, error: anthropic.APIError, silence: float) -> ModelsError:
    """The SDK's error as Atlas's, saying what happened and what fixes it."""
    name = provider.name
    if isinstance(error, anthropic.APITimeoutError):
        return ModelUnavailableError(
            f"{name} sent nothing for {silence:g} seconds, so the call was given up. Try again, "
            "or choose another model or provider."
        )
    if isinstance(error, anthropic.APIConnectionError):
        return ModelUnavailableError(
            f"{name} could not be reached at {provider.base_url} ({error.message}). Check the "
            "network, or try again."
        )
    status = error.status_code if isinstance(error, anthropic.APIStatusError) else None
    detail = _detail(error)
    # An error event inside the stream reaches the SDK with the stream's own 200.
    where = f"HTTP {status}" if status is not None and status >= 400 else "during the reply"
    if status in (401, 403):
        fix = f"Store a valid one with `atlas secrets set {provider.key}`." if provider.key else ""
        return ModelAuthError(f"{name} refused the key ({where}): {detail}. {fix}".rstrip())
    if _no_credit(status, detail, error):
        return ModelOutOfCreditError(
            f"{name} says the account cannot pay ({where}): {detail}. Add credit or raise the "
            "spending limit in Anthropic's console, or choose another provider; waiting does not "
            "help."
        )
    if status == 429:
        return ModelRateLimitError(
            f"{name} is limiting requests ({where}): {detail}. It passes; try again shortly."
        )
    if status == 529 or _kind(error) == "overloaded_error":
        return ModelUnavailableError(f"{name} is overloaded ({where}): {detail}. Try again.")
    if status is not None and 400 <= status < 500:
        return ModelRequestError(f"{name} refused the request ({where}): {detail}.")
    return ModelUnavailableError(f"{name} failed ({where}): {detail}. Try again.")


def _no_credit(status: int | None, detail: str, error: anthropic.APIError) -> bool:
    """An account that cannot pay: 402, a `billing_error`, or the words that say so on a 400 or
    on the 429 of the monthly spending cap, which no `retry-after` lifts."""
    if status == 402 or _kind(error) == "billing_error":
        return True
    lowered = detail.lower()
    if status == 429 and isinstance(error, anthropic.APIStatusError):
        wait = _wait(error.response.headers)
        return any(words in lowered for words in _NO_CREDIT) or (
            wait is not None and wait > PASSING_WAIT
        )
    return status == 400 and any(words in lowered for words in _NO_CREDIT)


def _wait(headers: Mapping[str, str]) -> float | None:
    value = headers.get("retry-after")
    try:
        return None if value is None else float(value)
    except ValueError:
        return None


def _fields(error: anthropic.APIError) -> Mapping[str, Any]:
    """The error object inside Anthropic's envelope: `{"type": "error", "error": {...}}`."""
    body = error.body
    if not isinstance(body, Mapping):
        return {}
    body = cast("Mapping[str, Any]", body)
    nested = body.get("error")
    return cast("Mapping[str, Any]", nested) if isinstance(nested, Mapping) else body


def _kind(error: anthropic.APIError) -> str | None:
    kind = _fields(error).get("type")
    return kind if isinstance(kind, str) else None


def _detail(error: anthropic.APIError) -> str:
    message = _fields(error).get("message")
    text = message if isinstance(message, str) and message.strip() else error.message
    # One line, without its final period: it goes inside Atlas's own sentence.
    return " ".join(text.split()).rstrip(".")
