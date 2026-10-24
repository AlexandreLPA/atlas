"""What the voice plugin's three calls to OpenAI share: the error they raise, and how a refusal
is told."""

from typing import Any, cast

import httpx2


class OpenAIError(Exception):
    """OpenAI could not do what was asked; the message says why."""


def refusal(what: str, response: httpx2.Response) -> str:
    """`OpenAI refused <what> (HTTP 401): <its message>`."""
    reason = "no reason given"
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        error = cast("dict[str, Any]", body).get("error")
        if isinstance(error, dict):
            message = cast("dict[str, Any]", error).get("message")
            if isinstance(message, str) and message:
                reason = message
    return f"OpenAI refused {what} (HTTP {response.status_code}): {reason}"
