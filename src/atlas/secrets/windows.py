"""Windows adapter: the vault key, encrypted by the Windows account (DPAPI) before it is stored.

DPAPI ties the key file to the account that wrote it: a copy of the home on another machine or
account cannot open it. It protects only the key; the vault itself is always age, the same file on
every system. Atlas V3 had two vault formats, one per system, and needed a migration between them.

Called through `ctypes`, so the core needs no Windows-only package (V3 needed pywin32).
The user scope is used, not the machine scope, which would let every account on the computer
decrypt. The entropy is not a secret (it is here in the code); it only keeps a generic DPAPI
sweep from recognizing the blob as Atlas's.
"""

import ctypes
import sys
from ctypes import wintypes

assert sys.platform == "win32"  # noqa: S101 - this module exists only on Windows

_ENTROPY = b"Atlas.VaultKey.v1"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char)))


_crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _call(name: str, data: bytes) -> bytes:
    source, _source_buffer = _blob(data)
    entropy, _entropy_buffer = _blob(_ENTROPY)
    result = _Blob()
    function = getattr(_crypt32, name)
    succeeded = function(
        ctypes.byref(source),
        None,
        ctypes.byref(entropy),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(result),
    )
    if not succeeded:
        raise OSError(ctypes.get_last_error(), f"{name} failed")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        _kernel32.LocalFree(result.pbData)


def protect(data: bytes) -> bytes:
    """Encrypt `data` for the Windows account running this process."""
    return _call("CryptProtectData", data)


def unprotect(data: bytes) -> bytes:
    """Decrypt what `protect` wrote under the same Windows account. Raises OSError otherwise."""
    return _call("CryptUnprotectData", data)
