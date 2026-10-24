"""Images: read_file opens one, the loop keeps it with the result, and each wire carries it to the
model as that provider takes it, against the fake model server through the real adapter."""

import asyncio
import base64
from pathlib import Path
from typing import Any

from atlas.agent import AgentLoop, Toolset, cleared
from atlas.home import Home
from atlas.models import (
    NO_IMAGES,
    AssistantMessage,
    Image,
    Message,
    SystemMessage,
    ToolCall,
    ToolResult,
    UserMessage,
)
from atlas.tools import Workspace, file_tools
from documents_support import write_png
from fake_model import FakeCall, FakeModelServer, FakeReply
from model_support import Connect, Run

CHAT = "/v1/chat/completions"
RESPONSES = "/v1/responses"


def _read(workspace: Workspace, path: str) -> Any:
    tool = {t.name: t for t in file_tools(workspace)}["read_file"]

    async def call() -> object:
        return await tool.run({"path": path})

    return asyncio.run(call())


def test_read_file_hands_an_image_to_the_model(tmp_path: Path) -> None:
    write_png(tmp_path / "red.png", (220, 30, 30))
    (tmp_path / "fake.png").write_bytes(b"not an image at all")

    output = _read(Workspace(tmp_path), "red.png")
    text = _read(Workspace(tmp_path), "fake.png")

    assert output.text.startswith(f"Image {tmp_path / 'red.png'}: image/png,")
    assert output.images == (Image("image/png", (tmp_path / "red.png").read_bytes()),)
    assert text == "     1\tnot an image at all"


def test_an_image_too_large_for_a_model_is_refused(tmp_path: Path) -> None:
    big = tmp_path / "big.jpg"
    big.write_bytes(b"\xff\xd8\xff" + b"\x00" * 5_000_001)

    said = _read(Workspace(tmp_path), "big.jpg")

    assert "past the 5,000,000 bytes a model takes" in said


def _png(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "work" / "red.png"
    path.parent.mkdir()
    write_png(path, (220, 30, 30))
    return path, "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def test_on_chat_completions_the_image_follows_the_rounds_results(
    tmp_path: Path, fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    path, url = _png(tmp_path)
    fake.reply(
        FakeReply(
            calls=[
                FakeCall("read_file", f'{{"path": "{path.name}"}}', id="r1"),
                FakeCall("read_file", '{"path": "missing.txt"}', id="r2"),
            ]
        ),
        FakeReply(text="It is red."),
    )
    models = connect(fake, "openrouter")

    async def scenario() -> Any:
        tools = Toolset(file_tools(Workspace(path.parent)))
        return await AgentLoop(models.client("openrouter"), tools).reply(
            [SystemMessage("Atlas.")], "What color?"
        )

    run(models, scenario)

    messages = fake.bodies(CHAT)[1]["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "tool", "tool", "user"]
    assert isinstance(messages[3]["content"], str)
    images = messages[5]["content"]
    assert images[0] == {"type": "text", "text": "[Atlas: the images of tool call r1]"}
    assert images[1] == {"type": "image_url", "image_url": {"url": url}}


def test_on_the_responses_api_the_image_goes_inside_the_result(
    tmp_path: Path, fake: FakeModelServer, connect: Connect, run: Run
) -> None:
    path, url = _png(tmp_path)
    fake.reply(
        FakeReply(calls=[FakeCall("read_file", f'{{"path": "{path.name}"}}', id="r1")]),
        FakeReply(text="It is red."),
    )
    models = connect(fake, "openai")

    async def scenario() -> Any:
        tools = Toolset(file_tools(Workspace(path.parent)))
        return await AgentLoop(models.client("openai"), tools).reply(
            [SystemMessage("Atlas.")], "What color?"
        )

    run(models, scenario)

    output = next(
        item for item in fake.bodies(RESPONSES)[1]["input"]
        if item.get("type") == "function_call_output"
    )  # fmt: skip
    assert output["output"][0]["type"] == "input_text"
    assert output["output"][1] == {"type": "input_image", "image_url": url}


def test_a_model_that_does_not_see_images_gets_a_note(
    tmp_path: Path, connect: Connect, run: Run, home: Home
) -> None:
    home.models_file.write_text(
        '[[models]]\nprovider = "vllm"\nid = "words"\ntool_call = true\n'
        'modalities = { input = ["text"], output = ["text"] }\n\n',
        encoding="utf-8",
    )
    path, _ = _png(tmp_path)
    with FakeModelServer(key=None) as local:
        local.reply(
            FakeReply(calls=[FakeCall("read_file", f'{{"path": "{path.name}"}}', id="r1")]),
            FakeReply(text="I cannot see it."),
        )
        models = connect(local, "vllm")

        async def scenario() -> Any:
            tools = Toolset(file_tools(Workspace(path.parent)))
            return await AgentLoop(models.client("vllm", "words"), tools).reply(
                [SystemMessage("Atlas.")], "What color?"
            )

        run(models, scenario)
        messages = local.bodies(CHAT)[1]["messages"]

    assert [m["role"] for m in messages][-1] == "tool"
    assert messages[-1]["content"].endswith(NO_IMAGES)


def test_clearing_takes_an_old_image_out_of_the_context() -> None:
    picture = Image("image/png", b"\x89PNG....")
    later = tuple(ToolCall(f"r{i}", "read_file", "{}") for i in range(4))
    history: list[Message] = [
        UserMessage("look"),
        AssistantMessage(tool_calls=(ToolCall("old", "read_file", "{}"),)),
        ToolResult("old", "Image a.png", images=(picture,)),
        AssistantMessage(tool_calls=later),
        *(ToolResult(f"r{i}", "x" * 2_000) for i in range(4)),
    ]

    copy = cleared(history)

    old = copy[2]
    assert isinstance(old, ToolResult)
    assert old.images == ()
    assert "was cleared" in old.text
