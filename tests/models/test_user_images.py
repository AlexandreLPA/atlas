"""The images and videos a user attached, in the user's message, on the three wires (step 16g)."""

from typing import Any, cast

from atlas.home import Home
from atlas.models import (
    NO_USER_IMAGES,
    Image,
    ModelInfo,
    ModelRequest,
    UserMessage,
    Wire,
    load_catalog,
)
from atlas.models.chat import chat_request
from atlas.models.messages import messages_request
from atlas.models.responses import responses_request

PNG = Image("image/png", b"\x89PNG\r\n\x1a\n")
MP4 = Image("video/mp4", b"\x00\x00\x00\x18ftypmp42")
SEES = ModelInfo("m", "M", input_modalities=("text", "image"))
TEXT_ONLY = ModelInfo("m", "M", input_modalities=("text",))
ASKED = ModelRequest(
    (UserMessage("[Image attached at: /h/a/chart.png]\n\nWhat is it?", images=(PNG,)),)
)


def _provider(home: Home, wire: Wire) -> Any:
    catalog = load_catalog(home.models_file)
    return next(found for found in catalog.providers.values() if found.wire is wire)


def test_chat_completions_sends_the_text_then_each_image_and_a_video_as_video_url(
    home: Home,
) -> None:
    provider = _provider(home, Wire.CHAT)
    request = ModelRequest((UserMessage("Both?", images=(PNG, MP4)),))
    body = chat_request(provider, "m", request, SEES, None)

    (message,) = body["messages"]
    assert message["role"] == "user"
    assert [part["type"] for part in message["content"]] == ["text", "image_url", "video_url"]
    assert message["content"][1]["image_url"]["url"] == PNG.data_url()
    assert message["content"][2]["video_url"]["url"] == MP4.data_url()


def test_responses_sends_the_image_as_input_image_in_the_users_message(home: Home) -> None:
    provider = _provider(home, Wire.RESPONSES)
    body = responses_request(provider, "m", ASKED, SEES, None)

    (item,) = body["input"]
    assert item["role"] == "user"
    assert [part["type"] for part in item["content"]] == ["input_text", "input_image"]
    assert item["content"][1]["image_url"] == PNG.data_url()


def test_messages_sends_the_image_as_an_image_block(home: Home) -> None:
    provider = _provider(home, Wire.MESSAGES)
    call = messages_request(provider, "m", ASKED, SEES, None)

    blocks = cast("list[dict[str, Any]]", call.body["messages"][0]["content"])
    assert [block["type"] for block in blocks] == ["text", "image"]
    assert blocks[1]["source"]["media_type"] == "image/png"


def test_a_model_that_does_not_see_images_gets_the_note_on_every_wire(home: Home) -> None:
    chat = chat_request(_provider(home, Wire.CHAT), "m", ASKED, TEXT_ONLY, None)
    responses = responses_request(_provider(home, Wire.RESPONSES), "m", ASKED, TEXT_ONLY, None)
    messages = messages_request(_provider(home, Wire.MESSAGES), "m", ASKED, TEXT_ONLY, None)

    assert chat["messages"][0]["content"].endswith(NO_USER_IMAGES)
    assert responses["input"][0]["content"].endswith(NO_USER_IMAGES)
    assert messages.body["messages"][0]["content"][0]["text"].endswith(NO_USER_IMAGES)
