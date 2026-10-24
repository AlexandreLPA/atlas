"""The agent subject's files: the spill folder, `ATLAS_HOME/spill/`.

A tool result too large for the context is written here whole, and the model gets its start, its
end and the file's path.
The folder is private, like the vault's: a command's output can hold a secret. Files live a day;
each new spill sweeps the older ones, so the folder never grows without bound. It is `machine` in
the home's boundary and never travels in a backup.
"""

import logging
import re
import time
from pathlib import Path

from atlas.home import HomeError, private_folder, utc_now

#: How long a spill file is kept.
KEEP_SECONDS = 24 * 3600

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")
_logger = logging.getLogger("atlas.agent")


class SpillFolder:
    """Where oversized tool results are kept whole for a day."""

    def __init__(self, folder: Path, keep_seconds: float = KEEP_SECONDS) -> None:
        self.folder = folder
        self.keep_seconds = keep_seconds

    def save(self, call_id: str, text: str) -> Path | None:
        """Write a result whole. `None` when the folder cannot be written: the result is then cut
        without a copy, and the turn goes on."""
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        path = self.folder / f"{stamp}-{_UNSAFE.sub('_', call_id)[:64]}.txt"
        try:
            private_folder(self.folder)
            self.sweep()
            path.write_text(text, encoding="utf-8", newline="")
        except (OSError, HomeError) as error:
            _logger.warning("A tool result could not be spilled to %s: %s", path, error)
            return None
        return path

    def sweep(self) -> int:
        """Remove the files older than a day. Returns how many went."""
        deadline = time.time() - self.keep_seconds
        removed = 0
        for path in self.folder.glob("*.txt"):
            try:
                if path.stat().st_mtime < deadline:
                    path.unlink()
                    removed += 1
            except OSError as error:
                _logger.warning("An old spill file could not be removed: %s", error)
        return removed
