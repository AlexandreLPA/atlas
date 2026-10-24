"""The vault document: secret names in plain text, each value encrypted on its own. Pure, no I/O.

The file is JSON:

    {"format": 1, "recipient": "age1...",
     "entries": {"OPENAI_API_KEY": {"ciphertext": "<base64 age>", "updated_at": "<UTC>"}}}

**Names stay readable** so that Atlas can list and report the vault without decrypting anything.

**Each value is encrypted on its own**, so damage to one entry costs that entry only. With one
encrypted blob for everything, one bad byte would take every API key with it.

**`recipient`** is the public half of the key the vault is encrypted for. It tells "this is the key
of another vault" without trying to decrypt.

**Each ciphertext is a standard age file**: any `age -d -i <key>` opens it after base64 decoding,
without Atlas. That is the way out if this code is ever gone.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Self, cast

from atlas.secrets.errors import InvalidSecretNameError, VaultDamagedError

FORMAT = 1

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


def check_name(name: str) -> None:
    """Refuse a name outside letters, digits and `_ . : -` (up to 128, not starting with a sign)."""
    if not _NAME.fullmatch(name):
        raise InvalidSecretNameError(
            f"{name!r} is not a secret name. Use letters, digits and _ . : - (up to 128 "
            "characters, starting with a letter or digit), such as OPENAI_API_KEY."
        )


@dataclass(frozen=True, slots=True)
class Entry:
    #: Base64 of an age file. Damaged entries keep whatever text they had and fail on reading.
    ciphertext: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class VaultDocument:
    recipient: str | None = None
    entries: dict[str, Entry] = field(default_factory=dict[str, Entry])

    def with_entry(self, name: str, entry: Entry, recipient: str) -> Self:
        return type(self)(recipient, {**self.entries, name: entry})

    def without(self, name: str) -> Self:
        return type(self)(
            self.recipient, {key: value for key, value in self.entries.items() if key != name}
        )


def parse_document(text: str, where: str) -> VaultDocument:
    """Read a vault file. A damaged one raises; a damaged entry stays, to fail on its own."""
    try:
        data = json.loads(text)
    except ValueError as error:
        raise VaultDamagedError(_damaged(where, f"it is not JSON ({error})")) from error
    if not isinstance(data, dict):
        raise VaultDamagedError(_damaged(where, "it is not a JSON object"))
    document = cast("dict[str, Any]", data)
    version = document.get("format")
    if isinstance(version, int) and not isinstance(version, bool) and version > FORMAT:
        raise VaultDamagedError(
            f"The vault {where} has format {version}, written by a newer Atlas than this one "
            f"(format {FORMAT}). Run that version, or restore an older copy of the file."
        )
    if version != FORMAT:
        raise VaultDamagedError(_damaged(where, f"its format is {version!r}, not {FORMAT}"))
    recipient = document.get("recipient")
    raw_entries = document.get("entries")
    if (recipient is not None and not isinstance(recipient, str)) or not isinstance(
        raw_entries, dict
    ):
        raise VaultDamagedError(_damaged(where, "its recipient or entries have the wrong shape"))
    entries = {
        str(name): _entry(value) for name, value in cast("dict[Any, Any]", raw_entries).items()
    }
    return VaultDocument(recipient, entries)


def render_document(document: VaultDocument) -> str:
    """The vault file's text: stable key order, so two writes of the same vault are the same."""
    data = {
        "format": FORMAT,
        "recipient": document.recipient,
        "entries": {
            name: {"ciphertext": entry.ciphertext, "updated_at": entry.updated_at}
            for name, entry in sorted(document.entries.items())
        },
    }
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def _entry(value: object) -> Entry:
    if not isinstance(value, dict):
        return Entry("", "")
    fields = cast("dict[str, Any]", value)
    ciphertext, updated_at = fields.get("ciphertext"), fields.get("updated_at")
    return Entry(
        ciphertext if isinstance(ciphertext, str) else "",
        updated_at if isinstance(updated_at, str) else "",
    )


def _damaged(where: str, why: str) -> str:
    return (
        f"The vault {where} is damaged: {why}. Atlas will not write over it. "
        "Restore it from a copy, or move it away to start an empty vault."
    )
