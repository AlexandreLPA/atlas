"""One client of the gateway: its socket, its queue of frames, and the conversations it is
attached to. A channel's bridge is one too (`atlas.server.channels`)."""

import asyncio
import json
from typing import Any, Protocol

#: A client that cannot take a frame in this long is gone.
SEND_SECONDS = 30


class PeerGoneError(Exception):
    """The client's socket closed while something was being sent."""


class Peer(Protocol):
    """One client's socket, as the transport gives it."""

    async def send(self, text: str) -> None:
        """Send one frame; raises `PeerGoneError` when the socket closed."""
        ...

    async def receive(self) -> str | None:
        """The next frame, or `None` once the socket closed."""
        ...

    async def close(self, code: int) -> None: ...


class Connection:
    """One client: its socket, its queue of frames, and the conversations it is attached to."""

    def __init__(self, peer: Peer, send_seconds: float = SEND_SECONDS) -> None:
        self.peer = peer
        self.conversations: set[str] = set()
        self._send_seconds = send_seconds
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.gone = False

    def put(self, frame: dict[str, Any]) -> None:
        if self.gone:
            return
        text = json.dumps(frame, ensure_ascii=False)
        # A lone surrogate from a model or a tool cannot be sent as UTF-8.
        self._queue.put_nowait(text.encode("utf-8", "replace").decode("utf-8"))

    def stop(self) -> None:
        self._queue.put_nowait(None)

    async def write(self) -> None:
        """Send the queued frames in order until stopped, or until the socket gives up."""
        while (text := await self._queue.get()) is not None:
            try:
                async with asyncio.timeout(self._send_seconds):
                    await self.peer.send(text)
            except (PeerGoneError, TimeoutError):
                self.gone = True
                await self.peer.close(1011)
                return
