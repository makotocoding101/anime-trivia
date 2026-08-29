"""Room registry: code -> live actor, in one process (invariant 7).

The RoomRegistry protocol is the scale-out seam (spec §01): going
multi-worker later means implementing it over Redis with room-to-worker
affinity — a routing change, not a rewrite. Nothing below the protocol ever
learns which implementation is running.
"""

from __future__ import annotations

import secrets
from typing import Protocol

from app.game.clock import Clock
from app.game.errors import GameError
from app.game.events import Command
from app.rooms.room import RECONNECT_GRACE_S, Room
from app.store import GameStore

# No vowels, so the generator cannot spell words players have to read aloud
# awkwardly; no easily-confused glyphs either (spec §13).
CODE_ALPHABET = "BCDFGHJKMPQRTVWXZ"
CODE_LENGTH = 4


class RoomRegistry(Protocol):
    async def dispatch(self, code: str, cmd: Command) -> None: ...


class InProcessRegistry:
    """The v1 implementation: a dict. Creation is synchronous today, so two
    concurrent creates for one code cannot interleave (R-08 has no window
    until M4 makes creation await the database — the placeholder-future
    pattern lands there, behind this same interface)."""

    def __init__(
        self,
        clock: Clock,
        store: GameStore | None = None,
        reconnect_grace_s: float = RECONNECT_GRACE_S,
    ) -> None:
        self._clock = clock
        self._store = store
        self._reconnect_grace_s = reconnect_grace_s
        self._rooms: dict[str, Room] = {}

    def create_room(self) -> Room:
        """Mint an unused code and start its actor. Collisions retry — with
        ~83k codes and tens of rooms, a second draw is already rare."""
        for _ in range(64):
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
            if code not in self._rooms:
                return self._start_room(code)
        raise RuntimeError("could not find a free room code; registry is implausibly full")

    def get(self, code: str) -> Room | None:
        room = self._rooms.get(code)
        if room is not None and room.stopped:
            # Grabbed mid-sweep: the actor already exited. Treat as absent;
            # _on_stopped will have removed it (or is about to).
            return None
        return room

    async def dispatch(self, code: str, cmd: Command) -> None:
        room = self.get(code)
        if room is None:
            raise GameError("room_not_found")
        room.inbox.put_nowait(cmd)

    def _start_room(self, code: str) -> Room:
        room = Room(
            code,
            self._clock,
            on_stopped=self._forget,
            store=self._store,
            reconnect_grace_s=self._reconnect_grace_s,
        )
        self._rooms[code] = room
        room.start()
        return room

    async def drain_persistence(self) -> None:
        """Await every in-flight game-results write. Called at app shutdown so
        a game that just ended is not lost to an engine.dispose() race."""
        import asyncio

        tasks = [t for room in self._rooms.values() for t in room._persist_tasks]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _forget(self, room: Room) -> None:
        if self._rooms.get(room.code) is room:
            del self._rooms[room.code]

    def __len__(self) -> int:
        return len(self._rooms)
