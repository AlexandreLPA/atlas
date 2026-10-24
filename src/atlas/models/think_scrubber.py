"""Reasoning tags inside a model's streamed text, taken out as they arrive.

A tag is held across chunks instead of erased per chunk, the boundary is re-armed after a flush, the
hidden text is handed out per chunk so it can be shown as reasoning and joined as it came, never
with a newline between chunks, and MiniMax's Chinese tags are read. It replaces the one-tag splitter
the `chat` reader had, configured per provider (`reasoning_tag`, Gemini's `thought`).

- A closed pair is taken out wherever it is, even inside a line.
- An opening tag with no closing one yet starts a block only at a block boundary (the start of the
  stream, or a line with only whitespace before it), so prose that mentions `<think>` survives.
- A closing tag with no opening one is removed, with the whitespace after it.
- A tag cut across two chunks is held until it is whole.
- A block that never closes keeps everything after its tag out of the text; at the end of the
  stream, a held partial closing tag is dropped ("leaking partial reasoning is worse than a
  truncated answer").
"""

import re

#: The reasoning tags models write in their text. Matched without regard to case. The Chinese
#: ones are MiniMax-M3's: 思考 (think), 反思 (reflect), 推理 (reason), 推敲 (deliberate).
THINK_TAG_NAMES: tuple[str, ...] = (
    "think",
    "thinking",
    "reasoning",
    "thought",
    "REASONING_SCRATCHPAD",
    "思考",
    "反思",
    "推理",
    "推敲",
)
_OPEN_TAGS = tuple(f"<{name.lower()}>" for name in THINK_TAG_NAMES)
_CLOSE_TAGS = tuple(f"</{name.lower()}>" for name in THINK_TAG_NAMES)
_ALL_TAGS = _OPEN_TAGS + _CLOSE_TAGS
_MAX_TAG = max(len(tag) for tag in _ALL_TAGS)
# Tags are found in the text as it is, never in a lowered copy: lowering changes the length of
# some characters ("İ" becomes two), and positions found in the copy then cut the original in the
# wrong place.
_OPEN = tuple(re.compile(re.escape(tag), re.IGNORECASE) for tag in _OPEN_TAGS)
_CLOSE = tuple(re.compile(re.escape(tag), re.IGNORECASE) for tag in _CLOSE_TAGS)
_ANY_CLOSE = re.compile("|".join(re.escape(tag) for tag in _CLOSE_TAGS), re.IGNORECASE)
#: A closing tag with no opening one, and the whitespace after it.
_ORPHAN_CLOSE = re.compile(
    "(?:" + "|".join(re.escape(tag) for tag in _CLOSE_TAGS) + r")[ \t\n\r]*", re.IGNORECASE
)


class ThinkScrubber:
    """Takes reasoning blocks out of one streamed text, chunk by chunk.

    `feed` returns the visible part of a chunk and leaves what it took out in `hidden`; `flush`
    returns what was held back when the stream ends. One scrubber reads one model reply.
    """

    def __init__(self) -> None:
        self._in_block = False
        self._held = ""
        # Whether the last visible text ended a line, or none came yet: an opening tag at the
        # start of the next chunk is then at a block boundary.
        self._line_start = True
        #: The reasoning the last `feed` took out, tags excluded.
        self.hidden = ""

    def feed(self, text: str) -> str:
        """One chunk; its visible part ("" when it is all reasoning or held back)."""
        self.hidden = ""
        if not text:
            return ""
        data, self._held = self._held + text, ""
        out: list[str] = []
        hidden: list[str] = []
        while data:
            if self._in_block:
                close = _ANY_CLOSE.search(data)
                if close is None:
                    hidden.append(self._hold(data, _CLOSE_TAGS))
                    break
                hidden.append(data[: close.start()])
                data = data[close.end() :]
                self._in_block = False
                continue
            # A closed pair anywhere, or an opening tag at a block boundary: the earliest wins.
            pair = _earliest_pair(data)
            at, size = self._open_at_boundary(data, out)
            if pair is not None and (at == -1 or pair[0] <= at):
                start, end = pair
                self._emit(out, data[:start])
                hidden.append(data[data.index(">", start) + 1 : data.rindex("<", start, end)])
                data = data[end:]
                continue
            if at != -1:
                self._emit(out, data[:at])
                self._in_block = True
                data = data[at + size :]
                continue
            self._emit(out, self._hold(data, _ALL_TAGS))
            break
        self.hidden = "".join(hidden)
        return "".join(out)

    def flush(self) -> str:
        """The end of the stream: what was held back, unless it sits inside an open block."""
        tail = "" if self._in_block else self._held
        self._held = ""
        self._in_block = False
        self._line_start = True
        return _ORPHAN_CLOSE.sub("", tail) if tail else ""

    def _emit(self, out: list[str], text: str) -> None:
        text = _ORPHAN_CLOSE.sub("", text) if "</" in text else text
        if text:
            out.append(text)
            self._line_start = text.endswith("\n")

    def _hold(self, data: str, tags: tuple[str, ...]) -> str:
        """Keep the end of `data` that may begin a tag; return the rest."""
        size = _partial_tail(data, tags)
        self._held = data[-size:] if size else ""
        return data[:-size] if size else data

    def _open_at_boundary(self, data: str, emitted: list[str]) -> tuple[int, int]:
        hits = [
            next(
                (
                    (found.start(), len(found.group()))
                    for found in tag.finditer(data)
                    if self._at_boundary(data, found.start(), emitted)
                ),
                (-1, 0),
            )
            for tag in _OPEN
        ]
        return min((hit for hit in hits if hit[0] != -1), default=(-1, 0))

    def _at_boundary(self, data: str, at: int, emitted: list[str]) -> bool:
        """Whether only whitespace stands between the start of a line and `at`."""
        line_start = emitted[-1].endswith("\n") if emitted else self._line_start
        before = data[:at]
        newline = before.rfind("\n")
        return (line_start if newline == -1 else True) and not before[newline + 1 :].strip()


def _earliest_pair(data: str) -> tuple[int, int] | None:
    """Where the earliest `<tag>...</tag>` starts and ends."""
    pairs: list[tuple[int, int]] = []
    for open_tag, close_tag in zip(_OPEN, _CLOSE, strict=True):
        start = open_tag.search(data)
        end = close_tag.search(data, start.end()) if start is not None else None
        if start is not None and end is not None:
            pairs.append((start.start(), end.end()))
    return min(pairs) if pairs else None


def _partial_tail(data: str, tags: tuple[str, ...]) -> int:
    """The longest end of `data` that is the start of a tag, but not the whole tag."""
    for size in range(min(len(data), _MAX_TAG - 1), 0, -1):
        tail = data[-size:].lower()
        if any(len(tag) > len(tail) and tag.startswith(tail) for tag in tags):
            return size
    return 0
