"""Where the vault key comes from. The key is an age X25519 identity (`AGE-SECRET-KEY-1...`).

The order, first found wins (the convention of SOPS, with Atlas's names):

1. `ATLAS_VAULT_KEY`: the key itself, in the environment. Read once into the settings and removed
   from the environment, so no child process inherits it.
2. `ATLAS_VAULT_KEY_FILE`: a file holding the key, such as a Docker secret mounted read-only.
   The safest choice in a container: the key never sits in the data volume.
3. The home's own key, in the private `secrets/` folder. It is created with the first secret, so
   an empty container works without a setup step. On Windows it is stored encrypted by the
   Windows account (DPAPI); elsewhere it is a file only the owner can open, like an SSH key.
   `secrets/` is `machine` in the home's boundary: the key never travels in a backup.

Whoever can read the whole home as its owner can read this key. The vault still keeps secrets out
of plain-text files, out of copies of `vault.json`, and out of logs.
"""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import pyrage

from atlas.home import VaultSettings, format_utc, is_private, utc_now
from atlas.secrets.errors import VaultLockedError

if sys.platform == "win32":
    from atlas.secrets import windows

KEY_VARIABLE = "ATLAS_VAULT_KEY"
KEY_FILE_VARIABLE = "ATLAS_VAULT_KEY_FILE"
PLAIN_KEY_FILE = "key.txt"
PROTECTED_KEY_FILE = "key.dpapi"
_KEY_LINE_PREFIX = "AGE-SECRET-KEY-1"
#: Windows opens files in text mode unless told otherwise; the flag does not exist elsewhere.
_BINARY: int = getattr(os, "O_BINARY", 0)


@dataclass(frozen=True, slots=True)
class VaultKey:
    identity: pyrage.x25519.Identity
    #: The public half (`age1...`), which the vault records as its recipient.
    recipient: str
    #: Where the key came from, for messages. Never the key itself.
    origin: str


def find_key(settings: VaultSettings, folder: Path) -> VaultKey | None:
    """The key, by the order above. `None` when no key exists yet; a broken one raises."""
    if settings.key is not None:
        return _parse(settings.key.reveal(), KEY_VARIABLE)
    if settings.key_file is not None:
        return _from_file(settings.key_file, f"{KEY_FILE_VARIABLE} ({settings.key_file})")
    return _home_key(folder)


def create_home_key(folder: Path) -> VaultKey:
    """A new key in the private `folder`. Called only for a vault with no entries yet."""
    identity = pyrage.x25519.Identity.generate()
    recipient = str(identity.to_public())
    text = f"# created: {format_utc(utc_now())}\n# public key: {recipient}\n{identity}\n"
    if sys.platform == "win32":
        path = folder / PROTECTED_KEY_FILE
        _write_new(path, windows.protect(text.encode("utf-8")))
    else:
        path = folder / PLAIN_KEY_FILE
        _write_new(path, text.encode("utf-8"))
    return VaultKey(identity, recipient, str(path))


def missing_key_message(folder: Path) -> str:
    return (
        f"The vault has secrets but no key to open them: {KEY_VARIABLE} and {KEY_FILE_VARIABLE} "
        f"are not set, and {folder} has no {PLAIN_KEY_FILE} or {PROTECTED_KEY_FILE}. Set "
        f"{KEY_FILE_VARIABLE} to the key file this vault was created with, or restore the key "
        "into that folder."
    )


def _home_key(folder: Path) -> VaultKey | None:
    protected, plain = folder / PROTECTED_KEY_FILE, folder / PLAIN_KEY_FILE
    if not protected.exists() and not plain.exists():
        return None
    if not is_private(folder):
        raise VaultLockedError(
            f"{folder} holds the vault key and can be opened by other accounts, so Atlas will not "
            "use it. Make the folder private (chmod 700 on Linux and macOS; on Windows, remove "
            "inherited access and keep only your account)."
        )
    if not protected.exists():
        return _from_file(plain, str(plain))
    if sys.platform != "win32":
        raise VaultLockedError(
            f"{protected} was encrypted by a Windows account (DPAPI) and opens only there. "
            f"Set {KEY_FILE_VARIABLE} to a plain copy of the key exported on that Windows account."
        )
    try:
        text = windows.unprotect(protected.read_bytes()).decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise VaultLockedError(
            f"{protected} cannot be opened by this Windows account: it was written by another "
            f"account or machine, or it is damaged ({error})."
        ) from error
    return _parse(text, str(protected))


def _from_file(path: Path, origin: str) -> VaultKey:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise VaultLockedError(f"The vault key file {origin} does not exist.") from None
    except (OSError, UnicodeDecodeError) as error:
        raise VaultLockedError(f"The vault key file {origin} cannot be read: {error}.") from error
    return _parse(text, origin)


def _parse(text: str, origin: str) -> VaultKey:
    """The first `AGE-SECRET-KEY-1` line of `text` (an age-keygen file also has comments)."""
    line = next(
        (line.strip() for line in text.splitlines() if line.strip().startswith(_KEY_LINE_PREFIX)),
        None,
    )
    if line is None:
        raise VaultLockedError(f"{origin} has no line starting with {_KEY_LINE_PREFIX}.")
    try:
        identity = pyrage.x25519.Identity.from_str(line)
    except pyrage.IdentityError:
        raise VaultLockedError(f"{origin} does not hold a valid age key.") from None
    return VaultKey(identity, str(identity.to_public()), origin)


def _write_new(path: Path, content: bytes) -> None:
    """Write a file that must not exist yet, readable only by the owner from its first byte."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _BINARY, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
