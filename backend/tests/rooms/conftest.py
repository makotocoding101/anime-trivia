"""Harness for actor-layer tests: a FakeClock room with stub sockets.

These tests exercise the real Room actor — inbox, run loop, timers, bounded
fan-out — with no FastAPI and no real time. clock.advance() plus a few loop
ticks replaces every sleep.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest_asyncio

from app.game.clock import FakeClock
from app.game.events import Join
from app.game.state import PlayerId
from app.rooms.connection import Connection
from app.rooms.room import Room


class StubSocket:
    """Records frames; optionally never completes a send (a wedged client)."""

    def __init__(self, wedge: bool = False) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int, str] | None = None
        self._wedge = wedge

    async def send_text(self, data: str) -> None:
        if self._wedge:
            await asyncio.Event().wait()  # never returns — send buffer is stuck
        self.sent.append(json.loads(data))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    def frames(self, type_: str) -> list[dict[str, Any]]:
        return [f for f in self.sent if f["type"] == type_]


class Harness:
    def __init__(self, room: Room, clock: FakeClock) -> None:
        self.room = room
        self.clock = clock
        self.sockets: dict[PlayerId, StubSocket] = {}

    async def settle(self) -> None:
        """Let the actor drain its inbox and the writers flush their queues."""
        for _ in range(20):
            await asyncio.sleep(0)

    async def advance(self, dt: float) -> None:
        """Advance fake time and let whatever woke up run to quiescence."""
        self.clock.advance(dt)
        await self.settle()

    async def connect(self, pid: str, wedge: bool = False, outbox_size: int = 64) -> StubSocket:
        socket = StubSocket(wedge=wedge)
        conn = Connection(pid, socket, outbox_size=outbox_size)
        self.room.attach(conn)
        self.room.inbox.put_nowait(Join(player_id=pid, name=pid))
        self.sockets[pid] = socket
        await self.settle()
        return socket


@pytest_asyncio.fixture
async def harness() -> Any:
    clock = FakeClock(start=1000.0)
    room = Room("TEST", clock)
    room.start()
    h = Harness(room, clock)
    yield h
    if room._task is not None and not room._task.done():
        room._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await room._task
