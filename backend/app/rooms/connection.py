"""One socket, one bounded outbox, one writer task (R-05).

The room task never awaits a socket. It calls try_send(), which either
enqueues without blocking or reports failure; a full queue means this client
cannot keep up, and the room drops the connection rather than waiting on it.
Dropping is recoverable — reconnect is a first-class path (§08).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

from app.game.state import PlayerId

logger = logging.getLogger(__name__)

OUTBOX_SIZE = 64
"""Frames of headroom per client. At one frame per event and a handful of
events per round, a healthy client sits near zero; hitting 64 means seconds
of backlog on a game whose rounds are 20s long."""

# 4000-4999 is the private-use range for WebSocket close codes.
CLOSE_CODES = {
    "slow_consumer": 4001,
    "kicked": 4002,
    "left": 4000,
    "timeout": 4003,
    "room_closed": 4004,
}


class WireSocket(Protocol):
    """What Connection needs from a socket. Satisfied by starlette's
    WebSocket and by test stubs."""

    async def send_text(self, data: str) -> None: ...

    async def close(self, code: int = 1000, reason: str = "") -> None: ...


class Connection:
    def __init__(self, player_id: PlayerId, socket: WireSocket, outbox_size: int = OUTBOX_SIZE):
        self.player_id = player_id
        self.socket = socket
        self.outbox: asyncio.Queue[str] = asyncio.Queue(maxsize=outbox_size)
        self.closing = False
        self._writer: asyncio.Task[None] | None = None
        self._closer: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._writer = asyncio.create_task(self._drain(), name=f"writer:{self.player_id}")

    async def _drain(self) -> None:
        try:
            while True:
                frame = await self.outbox.get()
                await self.socket.send_text(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The socket died mid-send; the recv side of the ws handler will
            # observe the disconnect and tell the room. Nothing to do here.
            logger.debug("writer for %s stopped on dead socket", self.player_id)

    def try_send(self, frame: str) -> bool:
        """Non-blocking enqueue. False means the client is beyond saving:
        already closing, or its outbox is full (R-05)."""
        if self.closing:
            return False
        try:
            self.outbox.put_nowait(frame)
        except asyncio.QueueFull:
            return False
        return True

    def close_soon(self, reason: str) -> None:
        """Schedule teardown without blocking the caller (the room task)."""
        if self.closing:
            return
        self.closing = True
        self._closer = asyncio.create_task(self._close(reason), name=f"close:{self.player_id}")

    async def _close(self, reason: str) -> None:
        if self._writer is not None:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer
        with contextlib.suppress(Exception):  # already-dead sockets are fine
            await self.socket.close(code=CLOSE_CODES.get(reason, 1000), reason=reason)

    async def stop(self) -> None:
        """Detach-time cleanup for a socket whose recv side already ended."""
        self.closing = True
        if self._writer is not None:
            self._writer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._writer
