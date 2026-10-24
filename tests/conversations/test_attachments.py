"""The files the user attaches (step 16g): kept in the home, and what each call's model reads of
them, built from the real files."""

import json
from pathlib import Path

import pytest

from atlas.conversations import (
    IMAGE_BYTES,
    INLINE_TEXT_BYTES,
    AttachmentError,
    Attachments,
    Role,
    StoredAttachment,
    decode,
    encode,
    identify,
    safe_name,
)
from atlas.conversations.codec import shown
from atlas.conversations.model import Kind, StoredMessage
from atlas.home import Home
from atlas.models import (
    Attachment,
    AttachmentKind,
    ModelInfo,
    UserMessage,
    Wire,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.7\n" + b"x" * 64
SEES = ModelInfo("vision", "Vision", input_modalities=("text", "image"))
TEXT_ONLY = ModelInfo("plain", "Plain", input_modalities=("text",))
VIDEO = ModelInfo("gemini", "Gemini", input_modalities=("text", "image", "video", "audio"))


@pytest.fixture
def attachments(home: Home) -> Attachments:
    return Attachments(home.attachments_folder)


def _add(attachments: Attachments, name: str, data: bytes) -> Attachment:
    added = attachments.add("", name, len(data), 0, data)
    assert added.attachment is not None
    return added.attachment


# -- what a file is ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "head", "kind", "media_type"),
    [
        ("photo.png", PNG, AttachmentKind.IMAGE, "image/png"),
        ("named-wrong.txt", PNG, AttachmentKind.IMAGE, "image/png"),
        ("scan.jpeg", b"\xff\xd8\xff\xe0", AttachmentKind.IMAGE, "image/jpeg"),
        ("still.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 ", AttachmentKind.IMAGE, "image/webp"),
        ("report", PDF, AttachmentKind.DOCUMENT, "application/pdf"),
        ("contract.docx", b"PK\x03\x04", AttachmentKind.DOCUMENT, None),
        ("budget.xlsx", b"PK\x03\x04", AttachmentKind.DOCUMENT, None),
        ("deck.pptx", b"PK\x03\x04", AttachmentKind.DOCUMENT, None),
        ("data.csv", b"a,b\n1,2\n", AttachmentKind.TEXT, None),
        ("notes.md", b"# Notes\n", AttachmentKind.TEXT, None),
        ("script.py", b"print('hi')\n", AttachmentKind.TEXT, None),
        ("Makefile", "olá: tudo\n".encode(), AttachmentKind.TEXT, "text/plain"),
        ("clip.mp4", b"\x00\x00\x00\x18ftypmp42", AttachmentKind.VIDEO, "video/mp4"),
        ("voice.mp3", b"ID3\x04\x00", AttachmentKind.AUDIO, "audio/mpeg"),
        ("blob.bin", b"\x00\x01\x02\xff", AttachmentKind.OTHER, "application/octet-stream"),
    ],
)
def test_a_file_is_known_by_its_first_bytes_where_they_say_else_by_its_name(
    name: str, head: bytes, kind: AttachmentKind, media_type: str | None
) -> None:
    found_kind, found_type = identify(name, head)
    assert found_kind is kind
    if media_type is not None:
        assert found_type == media_type


def test_a_name_never_leaves_its_folder() -> None:
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("C:\\Users\\x\\notas finais.pdf") == "notas finais.pdf"
    assert safe_name("a<b>:c?.txt") == "a_b__c_.txt"
    assert safe_name(" .. ") == "file"


# -- coming in, in parts -------------------------------------------------------------------------


def test_a_file_comes_in_parts_and_lands_in_the_home(home: Home, attachments: Attachments) -> None:
    data = b"%PDF-1.7\n" + b"y" * 1000
    first = attachments.add("", "Relatório final.pdf", len(data), 0, data[:400])
    assert (first.received, first.attachment) == (400, None)
    second = attachments.add(first.upload, "Relatório final.pdf", len(data), 400, data[400:])

    attachment = second.attachment
    assert attachment is not None
    assert (attachment.name, attachment.kind, attachment.size) == (
        "Relatório final.pdf",
        AttachmentKind.DOCUMENT,
        len(data),
    )
    path = attachments.path(attachment.id)
    assert path is not None
    assert path.parent.parent == home.attachments_folder
    assert path.read_bytes() == data
    assert attachments.get(attachment.id) == attachment
    assert not list((home.attachments_folder / ".uploads").iterdir())


def test_a_part_out_of_place_is_refused(attachments: Attachments) -> None:
    first = attachments.add("", "a.txt", 10, 0, b"12345")
    with pytest.raises(AttachmentError, match="byte 7"):
        attachments.add(first.upload, "a.txt", 10, 7, b"67890")
    with pytest.raises(AttachmentError, match="longer than"):
        attachments.add(first.upload, "a.txt", 10, 5, b"6789012345")


def test_an_upload_nobody_started_is_refused(attachments: Attachments) -> None:
    with pytest.raises(AttachmentError):
        attachments.add("0123456789abcdef", "a.txt", 3, 0, b"abc")
    with pytest.raises(AttachmentError):
        attachments.add("../../x", "a.txt", 3, 0, b"abc")


def test_an_image_past_the_models_limit_is_refused_whole(attachments: Attachments) -> None:
    heavy = PNG + b"\x00" * IMAGE_BYTES
    with pytest.raises(AttachmentError, match="image attachments take at most"):
        attachments.add("", "huge.png", len(heavy), 0, heavy)
    with pytest.raises(AttachmentError, match="at most 50 MB"):
        attachments.add("", "huge.zip", 51 * 1024 * 1024, 0, b"")


def test_an_attachment_that_is_gone_is_said_so(attachments: Attachments) -> None:
    with pytest.raises(AttachmentError, match="attach it again"):
        attachments.get("0123456789abcdef")
    assert attachments.path("../secrets") is None


# -- what a model reads of it --------------------------------------------------------------------


def test_an_image_goes_to_a_model_that_sees_images_as_itself_with_its_path(
    attachments: Attachments,
) -> None:
    image = _add(attachments, "chart.png", PNG)
    seen = attachments.view(UserMessage("What does it show?", (image,)), SEES, Wire.CHAT)

    assert [(i.media_type, i.data) for i in seen.images] == [("image/png", PNG)]
    assert f"[Image attached at: {attachments.path(image.id)}]" in seen.text
    assert seen.text.endswith("What does it show?")
    assert seen.attachments == (image,)


def test_a_model_that_does_not_see_images_reads_the_description_in_its_place(
    attachments: Attachments,
) -> None:
    image = _add(attachments, "chart.png", PNG)
    described = Attachment(image.id, image.name, image.media_type, image.size, image.kind, "Bars.")

    seen = attachments.view(UserMessage("Explain", (described,)), TEXT_ONLY, Wire.CHAT)
    blind = attachments.view(UserMessage("Explain", (image,)), TEXT_ONLY, Wire.CHAT)

    assert seen.images == ()
    assert "Bars." in seen.text
    assert str(attachments.path(image.id)) in seen.text
    assert "could not be described" in blind.text


def test_a_small_text_file_goes_whole_and_wrapped_as_outside_content(
    attachments: Attachments,
) -> None:
    text = _add(attachments, "notes.md", b"# Plan\nShip on Friday, then rest for a whole week.\n")
    seen = attachments.view(UserMessage("Summarize", (text,)), TEXT_ONLY, Wire.RESPONSES)

    assert "Ship on Friday" in seen.text
    assert '<untrusted_tool_result source="attachment:notes.md">' in seen.text


def test_a_large_text_file_and_a_document_are_read_with_read_file(
    attachments: Attachments,
) -> None:
    large = _add(attachments, "log.txt", b"line\n" * (INLINE_TEXT_BYTES // 5 + 10))
    pdf = _add(attachments, "contract.pdf", PDF)
    seen = attachments.view(UserMessage("", (large, pdf)), SEES, Wire.MESSAGES)

    assert "line\nline" not in seen.text
    assert seen.text.count("read_file") == 2
    assert f"saved at: {attachments.path(pdf.id)}" in seen.text
    assert "instead of asking the user to paste the contents" in seen.text
    assert seen.images == ()


def test_a_video_goes_as_itself_only_to_a_model_that_takes_video_over_chat_completions(
    attachments: Attachments,
) -> None:
    clip = _add(attachments, "clip.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32)
    over_chat = attachments.view(UserMessage("What happens?", (clip,)), VIDEO, Wire.CHAT)
    over_responses = attachments.view(UserMessage("What happens?", (clip,)), VIDEO, Wire.RESPONSES)
    blind = attachments.view(UserMessage("What happens?", (clip,)), SEES, Wire.CHAT)

    assert [image.media_type for image in over_chat.images] == ["video/mp4"]
    assert over_responses.images == blind.images == ()
    assert "a video attachment" in blind.text


def test_audio_gets_a_note_with_its_path(attachments: Attachments) -> None:
    voice = _add(attachments, "voice.mp3", b"ID3\x04\x00" + b"\x00" * 16)
    seen = attachments.view(UserMessage("Transcribe", (voice,)), VIDEO, Wire.CHAT)

    assert seen.images == ()
    assert "an audio file attachment: 'voice.mp3'" in seen.text


def test_what_could_not_be_read_is_told_five_markers_at_most(attachments: Attachments) -> None:
    gone = tuple(
        Attachment(f"{n:016x}", f"lost-{n}.pdf", "application/pdf", 1, AttachmentKind.DOCUMENT)
        for n in range(7)
    )
    seen = attachments.view(UserMessage("Read them", gone), SEES, Wire.CHAT)

    assert seen.text.count("[Attachment could not be read:") == 5
    assert "[2 more attachments skipped]" in seen.text


def test_a_message_with_no_attachments_is_left_as_it_is(attachments: Attachments) -> None:
    message = UserMessage("Hi")
    assert attachments.view(message, SEES, Wire.CHAT) is message


# -- kept with the message -----------------------------------------------------------------------


def test_the_message_keeps_the_files_facts_never_their_bytes_or_the_note(
    attachments: Attachments,
) -> None:
    image = _add(attachments, "chart.png", PNG)
    described = Attachment(image.id, image.name, image.media_type, image.size, image.kind, "Bars.")
    message = UserMessage("Look", (described,))

    body = encode(message)

    assert decode("user", body) == message
    assert "Image attached at" not in body
    assert json.loads(body)["attachments"][0]["kind"] == "image"
    assert json.loads(encode(UserMessage("Plain"))) == {"text": "Plain"}
    stored = StoredMessage(1, "c", Role.USER, Kind.MESSAGE, "Look", None, "", True)
    assert shown(stored, body).attachments == (
        StoredAttachment(image.id, "chart.png", "image/png", image.size, "image"),
    )


def test_a_kind_a_newer_atlas_wrote_reads_as_a_file(tmp_path: Path) -> None:
    body = json.dumps(
        {
            "text": "x",
            "attachments": [
                {"id": "a", "name": "m.glb", "media_type": "model/gltf", "size": 3, "kind": "3d"}
            ],
        }
    )
    message = decode("user", body)
    assert isinstance(message, UserMessage)
    assert message.attachments[0].kind is AttachmentKind.OTHER
