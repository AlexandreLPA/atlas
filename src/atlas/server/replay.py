"""Event numbers per conversation, and the recent events a client that comes back asks for.

Held by one object instead of module globals:

- every event of a conversation gets the next number (`seq`), and its frame is kept frozen as
  JSON, so later changes to the object cannot change what was recorded;
- a conversation keeps its last 512 events, within 4 MiB, and the process keeps 64 MiB across at
  most 64 conversations; the oldest go first;
- what was dropped leaves a mark, so a client asking from before the mark knows the replay has a
  hole and reads the conversation again instead (`truncated`);
- a conversation's numbering survives its ring being dropped: a client still
  holding a high number must not see the count start over at 1;
- the numbers live in the process, so each process has a random **epoch**; a client that sees
  another epoch knows the server restarted and its numbers mean nothing.
"""

import json
import uuid
from collections import OrderedDict, deque
from typing import Any

#: A long turn sends some hundreds of text pieces; 512 covers minutes of it and every other event.
EVENTS_KEPT = 512
CONVERSATIONS_KEPT = 64
BYTES_PER_CONVERSATION = 4 * 1024 * 1024
BYTES_IN_ALL = 64 * 1024 * 1024


class Replay:
    def __init__(
        self,
        events: int = EVENTS_KEPT,
        conversations: int = CONVERSATIONS_KEPT,
        per_conversation: int = BYTES_PER_CONVERSATION,
        in_all: int = BYTES_IN_ALL,
    ) -> None:
        self.epoch = uuid.uuid4().hex
        self._events = events
        self._conversations = conversations
        self._per_conversation = per_conversation
        self._in_all = in_all
        #: Per conversation: (number, frozen params, size).
        self._rings: OrderedDict[str, deque[tuple[int, bytes, int]]] = OrderedDict()
        self._sizes: dict[str, int] = {}
        self._dropped_through: dict[str, int] = {}
        self._next: dict[str, int] = {}
        self._total = 0

    def record(self, conversation_id: str, params: dict[str, Any]) -> int:
        """Number an event's `params` (setting its `seq`) and keep a frozen copy."""
        frozen = json.dumps(params, ensure_ascii=False, separators=(",", ":")).encode()
        seq = self._next.get(conversation_id, 0) + 1
        self._next[conversation_id] = seq
        params["seq"] = seq
        ring = self._rings.get(conversation_id)
        if ring is None:
            ring = self._rings[conversation_id] = deque()
            self._sizes[conversation_id] = 0
            while len(self._rings) > self._conversations:
                oldest, _ = self._rings.popitem(last=False)
                self._total -= self._sizes.pop(oldest, 0)
                self._dropped_through[oldest] = max(
                    self._dropped_through.get(oldest, 0), self._next.get(oldest, 0)
                )
        else:
            self._rings.move_to_end(conversation_id)
        size = len(frozen)
        if size > self._per_conversation or size > self._in_all:
            self._dropped_through[conversation_id] = seq
            return seq
        ring.append((seq, frozen, size))
        self._sizes[conversation_id] += size
        self._total += size
        while len(ring) > self._events or self._sizes[conversation_id] > self._per_conversation:
            self._drop_oldest(conversation_id)
        while self._total > self._in_all:
            victim = next(name for name, kept in self._rings.items() if kept)
            self._drop_oldest(victim)
        return seq

    def since(self, conversation_id: str, after: int) -> tuple[list[dict[str, Any]], bool]:
        """The events numbered above `after`, and whether some of them were dropped."""
        ring = self._rings.get(conversation_id, deque[tuple[int, bytes, int]]())
        events: list[dict[str, Any]] = []
        for seq, frozen, _ in ring:
            if seq > after:
                event: dict[str, Any] = json.loads(frozen)
                event["seq"] = seq
                events.append(event)
        return events, after < self._dropped_through.get(conversation_id, 0)

    def latest(self, conversation_id: str) -> int:
        """The last number given in a conversation; 0 before any."""
        return self._next.get(conversation_id, 0)

    def _drop_oldest(self, conversation_id: str) -> None:
        seq, _, size = self._rings[conversation_id].popleft()
        self._sizes[conversation_id] -= size
        self._total -= size
        self._dropped_through[conversation_id] = max(
            self._dropped_through.get(conversation_id, 0), seq
        )
