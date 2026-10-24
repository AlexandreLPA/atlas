"""Logs are JSON lines, one logger per subject, and no secret reaches a line.

**One JSON object per line**, with `ts` (UTC, Atlas's format), `level`, `logger`, `msg`, the
caller's `extra` fields, and `exc` with the traceback when there is one. JSON escapes line breaks,
so a message cannot forge a second log line.

**One logger per subject,** named `atlas.<subject>` and never after a module. A test holds every
call to this rule.

**No secret on a line.** The `Redactor` knows every secret this process has handled (the vault
registers each value it reads or writes) and replaces it with `[REDACTED]` in the finished line,
traceback included. It also removes anything shaped like an age secret key, registered or not.

**Configured only by the process entry point**, never at import. Atlas V3 configured logging when
a module was imported, and the test suite wrote into the production log until a guard learned to
sniff for the test runner. Here, importing Atlas attaches nothing, and a test that wants logs passes
its own temporary file.

**Where lines go:** stderr always (what a container collects), and `logs/atlas.log` when the
setting asks for it, rotated at 10 MB with 5 old files kept. Only the server writes the file: a
rotating file has one owner, or rotation breaks.
"""

import json
import logging
import re
import sys
import threading
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO

from atlas.home.clock import format_utc
from atlas.home.model import LogLevel

LOG_FILE = "atlas.log"
MAX_BYTES = 10 * 1024 * 1024
BACKUPS = 5
REDACTED = "[REDACTED]"

#: Shorter values are not treated as secrets: replacing "abc" in every line would hide more than it
#: protects.
MIN_SECRET_LENGTH = 4

#: Secret shapes removed even when nobody registered the value.
_SECRET_SHAPES = (re.compile(r"AGE-SECRET-KEY-(?:PQ-)?1[0-9A-Z]+"),)

#: Libraries that would drown Atlas's own lines at INFO.
_NOISY_LIBRARIES = ("httpx", "httpcore")

#: Attributes every log record has. Anything else on a record came from the caller's `extra`.
#: `color_message` is uvicorn's colored copy of the message.
_RECORD_ATTRIBUTES = frozenset(vars(logging.makeLogRecord({}))) | {"message", "color_message"}

_logger = logging.getLogger("atlas.home")


class Redactor:
    """The secret values this process knows, and their removal from text. Safe across threads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._secrets: tuple[str, ...] = ()

    def add(self, secret: str) -> None:
        """Remember a secret, so that no later log line shows it."""
        if len(secret) < MIN_SECRET_LENGTH:
            return
        with self._lock:
            if secret not in self._secrets:
                # Longest first: a secret that contains another is removed whole.
                self._secrets = tuple(sorted({*self._secrets, secret}, key=len, reverse=True))

    def redact(self, text: str) -> str:
        """`text` with every known secret, and every secret-shaped string, replaced."""
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
            # Inside a JSON line the secret appears escaped (quotes, backslashes, line breaks).
            escaped = json.dumps(secret, ensure_ascii=False)[1:-1]
            if escaped != secret:
                text = text.replace(escaped, REDACTED)
        for shape in _SECRET_SHAPES:
            text = shape.sub(REDACTED, text)
        return text


class JsonFormatter(logging.Formatter):
    """One redacted JSON object per record."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, object] = {
            "ts": format_utc(datetime.fromtimestamp(record.created, UTC)),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _RECORD_ATTRIBUTES and key not in line:
                line[key] = value
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        if record.stack_info:
            line["stack"] = self.formatStack(record.stack_info)
        text = self._redactor.redact(json.dumps(line, ensure_ascii=False, default=repr))
        # JSON leaves these two Unicode line separators raw; some readers split lines on them.
        return text.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def configure_logging(
    level: LogLevel,
    redactor: Redactor,
    *,
    log_file: Path | None = None,
    stream: TextIO | None = None,
) -> None:
    """Send every log record of this process, uvicorn's included, through one JSON formatter.

    Calling it again replaces the previous configuration. A log file that cannot be opened does not
    stop the process: lines keep going to the stream, and the first one says why.
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    root.setLevel(level)
    formatter = JsonFormatter(redactor)

    to_stream = logging.StreamHandler(sys.stderr if stream is None else stream)
    to_stream.setFormatter(formatter)
    root.addHandler(to_stream)

    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            to_file = RotatingFileHandler(
                log_file, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8"
            )
        except OSError as error:
            _logger.warning("Logging to stderr only: %s cannot be opened (%s).", log_file, error)
        else:
            to_file.setFormatter(formatter)
            root.addHandler(to_file)

    for library in _NOISY_LIBRARIES:
        logging.getLogger(library).setLevel(logging.WARNING)
