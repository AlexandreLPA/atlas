"""The files the user attaches to messages: kept in the home's `attachments/`, and read for each
model call the cheapest way that call's model allows (docs/ARCHITECTURE.md, Attachments; step 16g).

- **The file lands in the home**, one folder each, so the container and another machine see it.
  It comes in parts (`add`), since a WebSocket frame holds 16 MiB and a document may be 50.
- **What it is** is read from its first bytes where they say (an image, a PDF), else from its name.
- **What a model reads of it is built for each call** (`view`), never written into the stored
  message, so nothing has to strip a note back out of the stored prompt to show the turn.
  - an image goes to a model that sees images as itself, with its path
    (`[Image attached at: …]`); to one that does not, the description the description model wrote
    when it was sent, or a note that it could not be seen;
  - a text file up to 100 KB goes whole, wrapped as untrusted; every other file gets a document
    note, its name, type and path and the order to read it, which read_file does;
  - a video goes as itself to a model that takes video over Chat Completions, else a note; audio
    gets what a plugin's reader wrote down of it when one did (step 17a), else a note;
  - what could not be read is told to the model, at most five markers a message.
"""

import logging
import mimetypes
import re
import secrets
import shutil
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from atlas.conversations.errors import AttachmentError
from atlas.models import (
    Attachment,
    AttachmentKind,
    Image,
    ModelInfo,
    UserMessage,
    Wire,
    sees_images,
    takes_video,
)
from atlas.trust import untrusted

#: The largest image a model takes: Claude's limit, the lowest of the providers' (read_file's).
IMAGE_BYTES = 5_000_000
_MIB = 1024 * 1024
#: The most each kind may weigh; the image's limit is the providers'. The app refuses a drop past
#: them first and says why.
LIMITS: dict[AttachmentKind, int] = {
    AttachmentKind.IMAGE: IMAGE_BYTES,
    AttachmentKind.DOCUMENT: 50 * _MIB,
    AttachmentKind.TEXT: 50 * _MIB,
    AttachmentKind.VIDEO: 50 * _MIB,
    AttachmentKind.AUDIO: 20 * _MIB,
    AttachmentKind.OTHER: 50 * _MIB,
}
#: No attachment weighs more than the heaviest kind.
MOST_BYTES = max(LIMITS.values())
#: A text file this small goes whole into the message.
INLINE_TEXT_BYTES = 100 * 1024
#: The failure markers one message carries; the rest fold into one.
MARKERS = 5
#: An upload left unfinished this long is thrown away.
_STALE_SECONDS = 24 * 3600
_UPLOADS = ".uploads"
_SNIFF_BYTES = 8_192

#: The text extensions, read as text whatever their bytes look like.
_TEXT_EXTENSIONS = frozenset(
    {".txt", ".md", ".csv", ".log", ".json", ".xml", ".yaml", ".yml", ".toml", ".ini", ".cfg"}
)
#: The documents read_file converts (`atlas.tools.documents`), by their names.
_DOCUMENTS = {
    ".pdf": "application/pdf",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".rtf": "application/rtf",
    ".epub": "application/epub+zip",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
}
#: The images every provider reads, by their first bytes (read_file's, with WebP).
_IMAGES = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
}
#: What a display name may not carry.
_UNSAFE = re.compile(r"[^\w.\- ()]")
_ID = re.compile(r"^[0-9a-f]{16}$")

_logger = logging.getLogger("atlas.conversations")


@dataclass(frozen=True, slots=True)
class Upload:
    """Where an upload stands: its id, the bytes received, and the attachment once whole."""

    upload: str
    received: int
    attachment: Attachment | None = None


def safe_name(name: str) -> str:
    """A file name that stays inside its folder on every system: no path, no odd characters."""
    base = re.split(r"[\\/]", name.strip())[-1]
    cleaned = _UNSAFE.sub("_", base).strip(" .")[:120]
    return cleaned or "file"


def identify(name: str, head: bytes) -> tuple[AttachmentKind, str]:
    """What a file is, and its media type: from its first bytes where they say, else its name."""
    for media_type, signatures in _IMAGES.items():
        if head.startswith(signatures):
            return AttachmentKind.IMAGE, media_type
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return AttachmentKind.IMAGE, "image/webp"
    if head.startswith(b"%PDF"):
        return AttachmentKind.DOCUMENT, "application/pdf"
    suffix = Path(name).suffix.lower()
    if suffix in _DOCUMENTS:
        return AttachmentKind.DOCUMENT, _DOCUMENTS[suffix]
    guessed = mimetypes.guess_type(name)[0] or ""
    if suffix in _TEXT_EXTENSIONS:
        return AttachmentKind.TEXT, guessed or "text/plain"
    if guessed.startswith("video/"):
        return AttachmentKind.VIDEO, guessed
    if guessed.startswith("audio/"):
        return AttachmentKind.AUDIO, guessed
    if guessed.startswith("image/"):
        # An image no provider reads (HEIC, TIFF, SVG): a file the agent may still work on.
        return AttachmentKind.OTHER, guessed
    if guessed.startswith("text/") or _reads_as_text(head):
        return AttachmentKind.TEXT, guessed or "text/plain"
    return AttachmentKind.OTHER, guessed or "application/octet-stream"


def _reads_as_text(head: bytes) -> bool:
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as error:
        # A character cut at the end of the sniffed bytes is still text.
        return error.start >= len(head) - 3
    return True


class Attachments:
    """The home's attached files: each in `folder/<id>/<name>`, uploads in `folder/.uploads`."""

    def __init__(self, folder: Path) -> None:
        self._folder = folder
        #: Files read for a model, kept while they do not change: an image goes with every call.
        self._read: OrderedDict[tuple[Path, int, int], bytes] = OrderedDict()

    @property
    def folder(self) -> Path:
        return self._folder

    # -- coming in --------------------------------------------------------------------------------

    def add(self, upload: str, name: str, size: int, offset: int, data: bytes) -> Upload:
        """One part of a file: the first with `upload` empty, which names the upload, and the
        rest at their `offset`. The part that completes `size` makes the attachment. Raises
        `AttachmentError` with what to tell the user."""
        if size < 0 or size > MOST_BYTES:
            raise AttachmentError(
                f"{safe_name(name)} weighs {size:,} bytes; an attachment takes at most "
                f"{MOST_BYTES // _MIB} MB."
            )
        uploads = self._folder / _UPLOADS
        if not upload:
            uploads.mkdir(parents=True, exist_ok=True)
            self._sweep(uploads)
            upload = secrets.token_hex(8)
            (uploads / upload).touch()
        elif not _ID.match(upload):
            raise AttachmentError(f"There is no upload {upload!r}.")
        partial = uploads / upload
        if not partial.is_file():
            raise AttachmentError(f"There is no upload {upload!r}; send the file again.")
        received = partial.stat().st_size
        if offset != received:
            raise AttachmentError(
                f"The upload has {received:,} bytes, and a part came for byte {offset:,}."
            )
        if received + len(data) > size:
            partial.unlink(missing_ok=True)
            raise AttachmentError(f"{safe_name(name)} came longer than {size:,} bytes.")
        with partial.open("ab") as out:
            out.write(data)
        received += len(data)
        if received < size:
            return Upload(upload, received)
        return Upload(upload, received, self._finish(partial, name, size))

    def _finish(self, partial: Path, name: str, size: int) -> Attachment:
        with partial.open("rb") as raw:
            head = raw.read(_SNIFF_BYTES)
        kind, media_type = identify(name, head)
        if size > LIMITS[kind]:
            partial.unlink(missing_ok=True)
            raise AttachmentError(
                f"{safe_name(name)} weighs {size:,} bytes; {kind.value} attachments take at most "
                f"{LIMITS[kind]:,} bytes."
            )
        attachment_id = secrets.token_hex(8)
        target = self._folder / attachment_id / safe_name(name)
        target.parent.mkdir(parents=True)
        shutil.move(partial, target)
        return Attachment(attachment_id, target.name, media_type, size, kind)

    def _sweep(self, uploads: Path) -> None:
        """Throw away uploads nobody finished."""
        cutoff = time.time() - _STALE_SECONDS
        for left in uploads.iterdir():
            try:
                if left.stat().st_mtime < cutoff:
                    left.unlink()
            except OSError:
                _logger.warning("An unfinished upload could not be removed: %s", left)

    def get(self, attachment_id: str) -> Attachment:
        """An attachment by its id, as it was made. Raises `AttachmentError`."""
        path = self.path(attachment_id)
        if path is None:
            raise AttachmentError(f"There is no attachment {attachment_id!r}; attach it again.")
        with path.open("rb") as raw:
            head = raw.read(_SNIFF_BYTES)
        kind, media_type = identify(path.name, head)
        return Attachment(attachment_id, path.name, media_type, path.stat().st_size, kind)

    def path(self, attachment_id: str) -> Path | None:
        """Where an attachment's file is; `None` when it is gone."""
        if not _ID.match(attachment_id):
            return None
        folder = self._folder / attachment_id
        found = [entry for entry in folder.iterdir() if entry.is_file()] if folder.is_dir() else []
        return found[0] if len(found) == 1 else None

    def image(self, attachment: Attachment) -> Image | None:
        """An image attachment's bytes, for a model to see; `None` when the file is gone."""
        data = self._bytes(attachment)
        return None if data is None else Image(attachment.media_type, data)

    def _bytes(self, attachment: Attachment) -> bytes | None:
        path = self.path(attachment.id)
        if path is None:
            return None
        stat = path.stat()
        key = (path, stat.st_mtime_ns, stat.st_size)
        data = self._read.get(key)
        if data is None:
            data = path.read_bytes()
            self._read[key] = data
            while sum(len(kept) for kept in self._read.values()) > 8 * IMAGE_BYTES:
                self._read.popitem(last=False)
        else:
            self._read.move_to_end(key)
        return data

    # -- going to a model -------------------------------------------------------------------------

    def view(self, message: UserMessage, info: ModelInfo | None, wire: Wire) -> UserMessage:
        """What this call's model reads of the message's attachments: notes before the user's
        words, and the images it sees (an `AttachmentView`)."""
        if not message.attachments:
            return message
        sees, video = sees_images(info), takes_video(info, wire)
        notes: list[str] = []
        failed: list[str] = []
        shown: list[Image] = []
        for attachment in message.attachments:
            path = self.path(attachment.id)
            if path is None:
                failed.append(f"[Attachment could not be read: {attachment.name}]")
                continue
            match attachment.kind:
                case AttachmentKind.IMAGE if sees:
                    image = self.image(attachment)
                    if image is None:
                        failed.append(f"[Attachment could not be read: {attachment.name}]")
                        continue
                    shown.append(image)
                    notes.append(f"[Image attached at: {path}]")
                case AttachmentKind.IMAGE:
                    notes.append(_unseen_image(attachment, path))
                case AttachmentKind.VIDEO if video:
                    data = self._bytes(attachment)
                    if data is None:
                        failed.append(f"[Attachment could not be read: {attachment.name}]")
                        continue
                    shown.append(Image(attachment.media_type, data))
                    notes.append(f"[Video attached at: {path}]")
                case AttachmentKind.AUDIO if attachment.description.strip():
                    notes.append(_heard_audio(attachment, path))
                case AttachmentKind.VIDEO | AttachmentKind.AUDIO:
                    notes.append(_media_note(attachment, path))
                case AttachmentKind.TEXT if attachment.size <= INLINE_TEXT_BYTES:
                    notes.append(self._inlined(attachment, path))
                case _:
                    notes.append(_document_note(attachment, path))
        if len(failed) > MARKERS:
            failed = [*failed[:MARKERS], f"[{len(failed) - MARKERS} more attachments skipped]"]
        said = "\n\n".join([*notes, *failed, *([message.text] if message.text.strip() else [])])
        return UserMessage(said, message.attachments, tuple(shown))

    def _inlined(self, attachment: Attachment, path: Path) -> str:
        data = self._bytes(attachment) or b""
        text = data.decode("utf-8-sig", errors="replace")
        if not text.strip():
            return f"[The user sent an empty text file: '{attachment.name}'. No extractable text.]"
        return (
            f"[The user sent a text document, '{attachment.name}', and its text follows. The file "
            f"itself is saved at: {path}]\n" + untrusted(f"attachment:{attachment.name}", text)
        )


def _unseen_image(attachment: Attachment, path: Path) -> str:
    if attachment.description.strip():
        return (
            f"[The user sent an image: '{attachment.name}'. This model does not see images; here "
            f"is what a model that does saw:\n{attachment.description.strip()}\n"
            f"The image is saved at: {path}]"
        )
    return (
        f"[The user sent an image: '{attachment.name}', saved at: {path}. This model does not see "
        "images, and it could not be described. Say so if the request depends on what it shows.]"
    )


def _heard_audio(attachment: Attachment, path: Path) -> str:
    """The note for an audio file a plugin's reader wrote down (step 17a)."""
    return (
        f"[The user sent an audio file: '{attachment.name}', saved at: {path}. A speech model "
        "wrote down what it says, and may have misheard a word; take it as what the user said:\n"
        f"{attachment.description.strip()}]"
    )


def _media_note(attachment: Attachment, path: Path) -> str:
    """The note for an audio file or a video."""
    noun = "video" if attachment.kind is AttachmentKind.VIDEO else "audio"
    kind = "a video attachment" if noun == "video" else "an audio file attachment"
    return (
        f"[The user sent {kind}: '{attachment.name}', saved at: {path}. What it holds is not "
        f"written out here. When the request turns on what the {noun} contains, work it out "
        "yourself, for instance with run_command and a media tool, rather than asking the user "
        "to describe it. Ask what to do with it only when what they want is really unclear.]"
    )


def _document_note(attachment: Attachment, path: Path) -> str:
    """The document note, naming read_file, which converts PDF, Office, OpenDocument, RTF and
    EPUB."""
    if attachment.kind is AttachmentKind.TEXT:
        return (
            f"[The user sent a text document: '{attachment.name}'. It is saved at: {path}. Its "
            "content is not inlined here. Read it yourself with read_file before answering when "
            "the user's request involves its contents.]"
        )
    if attachment.kind is AttachmentKind.DOCUMENT:
        return (
            f"[The user sent a document: '{attachment.name}' ({attachment.media_type}). It is "
            f"saved at: {path}. Its text is not inlined here. Read it with read_file, which "
            "converts it to Markdown, before answering, instead of asking the user to paste the "
            "contents.]"
        )
    return (
        f"[The user sent a file: '{attachment.name}' ({attachment.media_type}). It is saved at: "
        f"{path}. Its content is not inlined here. Look at it yourself (read_file, or run_command) "
        "when the user's request involves it.]"
    )
