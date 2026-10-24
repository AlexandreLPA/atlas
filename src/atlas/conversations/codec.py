"""A message as the database keeps it: its whole body as JSON, which reads back the same message,
and the plain text that search reads.

Raw means whole: provider fields go in, so a conversation reopened tomorrow goes back to the
provider exactly as it left (Gemini refuses a history without its thought signatures), and so do a
result's images.
"""

import base64
import binascii
import json
from dataclasses import replace
from typing import Any, cast

from atlas.agent import SUMMARY_MARK, is_model_switch, is_notice, is_skill, is_terminal
from atlas.conversations.errors import StoredMessageError
from atlas.conversations.model import Kind, Role, StoredAttachment, StoredCall, StoredMessage
from atlas.models import (
    JSON,
    AssistantMessage,
    Attachment,
    AttachmentKind,
    Image,
    JSONObject,
    Message,
    Origin,
    SystemMessage,
    ToolCall,
    ToolResult,
    ToolStatus,
    UserMessage,
)


def role_of(message: Message) -> Role:
    match message:
        case SystemMessage():
            return Role.SYSTEM
        case UserMessage():
            return Role.USER
        case AssistantMessage():
            return Role.ASSISTANT
        case ToolResult():
            return Role.TOOL


def kind_of(message: Message) -> Kind:
    if isinstance(message, AssistantMessage) and message.text.startswith(SUMMARY_MARK):
        return Kind.SUMMARY
    if is_model_switch(message):
        return Kind.MODEL_SWITCH
    if is_notice(message):
        return Kind.NOTICE
    if is_terminal(message):
        return Kind.TERMINAL
    if is_skill(message):
        return Kind.SKILL
    return Kind.MESSAGE


def encode(message: Message) -> str:
    """The message's whole body, as JSON text."""
    body: dict[str, JSON]
    match message:
        case UserMessage(text=text, attachments=attachments) if attachments:
            # The files' facts only: the bytes stay in the home's attachments/, and what a model
            # reads of them is built for each call.
            body = {
                "text": text,
                "attachments": [
                    {
                        "id": attachment.id,
                        "name": attachment.name,
                        "media_type": attachment.media_type,
                        "size": attachment.size,
                        "kind": attachment.kind.value,
                        "description": attachment.description,
                    }
                    for attachment in attachments
                ],
            }
        case SystemMessage(text=text) | UserMessage(text=text):
            body = {"text": text}
        case AssistantMessage():
            body = {
                "text": message.text,
                "reasoning": message.reasoning,
                "tool_calls": [
                    {
                        "id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                        "provider_fields": call.provider_fields,
                    }
                    for call in message.tool_calls
                ],
                "origin": None
                if message.origin is None
                else {"provider": message.origin.provider, "model": message.origin.model},
                "provider_fields": message.provider_fields,
            }
        case ToolResult():
            body = {
                "call_id": message.call_id,
                "text": message.text,
                "loaded": list(message.loaded),
                "status": message.status.value,
                "images": [
                    {"media_type": image.media_type, "data": base64.b64encode(image.data).decode()}
                    for image in message.images
                ],
            }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":"))


def decode(role: str, body: str) -> Message:
    """The message a stored body holds."""
    try:
        data = cast("dict[str, Any]", json.loads(body))
        match Role(role):
            case Role.SYSTEM:
                return SystemMessage(str(data["text"]))
            case Role.USER:
                return UserMessage(str(data["text"]), _attachments(data))
            case Role.ASSISTANT:
                origin = data.get("origin")
                return AssistantMessage(
                    text=str(data["text"]),
                    tool_calls=tuple(
                        ToolCall(
                            str(call["id"]),
                            str(call["name"]),
                            str(call["arguments"]),
                            _object(call.get("provider_fields")),
                        )
                        for call in cast("list[dict[str, Any]]", data["tool_calls"])
                    ),
                    reasoning=str(data.get("reasoning", "")),
                    origin=None
                    if origin is None
                    else Origin(str(origin["provider"]), str(origin["model"])),
                    provider_fields=_object(data.get("provider_fields")),
                )
            case Role.TOOL:
                return ToolResult(
                    str(data["call_id"]),
                    str(data["text"]),
                    tuple(str(name) for name in data.get("loaded", [])),
                    tuple(
                        Image(str(image["media_type"]), base64.b64decode(image["data"]))
                        for image in cast("list[dict[str, Any]]", data.get("images", []))
                    ),
                    # A body stored before the status was kept reads as completed.
                    ToolStatus(str(data.get("status", ToolStatus.COMPLETED.value))),
                )
    except (ValueError, KeyError, TypeError, binascii.Error) as error:
        raise StoredMessageError(
            f"A stored {role} message cannot be read back ({type(error).__name__}: {error}). The "
            "database may have been edited by hand."
        ) from error


def shown(message: StoredMessage, body: str) -> StoredMessage:
    """`message` with what the screen shows of its body: the reasoning and the calls of an
    assistant message, the call and the status of a result, the files a user message came with.
    Images are not decoded."""
    data = cast("dict[str, Any]", json.loads(body))
    match message.role:
        case Role.USER if data.get("attachments"):
            attached = tuple(
                StoredAttachment(item.id, item.name, item.media_type, item.size, item.kind.value)
                for item in _attachments(data)
            )
            return replace(message, attachments=attached)
        case Role.ASSISTANT:
            calls = tuple(
                StoredCall(str(call["id"]), str(call["name"]), str(call["arguments"]))
                for call in cast("list[dict[str, Any]]", data.get("tool_calls", []))
            )
            return replace(message, reasoning=str(data.get("reasoning", "")), calls=calls)
        case Role.TOOL:
            status = str(data.get("status", ToolStatus.COMPLETED.value))
            return replace(message, call_id=str(data["call_id"]), status=status)
        case _:
            return message


def _attachments(data: dict[str, Any]) -> tuple[Attachment, ...]:
    """A stored user message's attachments; a kind this version does not know reads as `other`."""
    known = {kind.value for kind in AttachmentKind}
    return tuple(
        Attachment(
            str(item["id"]),
            str(item["name"]),
            str(item["media_type"]),
            int(item["size"]),
            AttachmentKind(item["kind"] if item.get("kind") in known else AttachmentKind.OTHER),
            str(item.get("description", "")),
        )
        for item in cast("list[dict[str, Any]]", data.get("attachments", []))
    )


def _object(value: object) -> JSONObject:
    return cast("JSONObject", value) if isinstance(value, dict) else {}
