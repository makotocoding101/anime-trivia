"""Registry behavior: code minting, lookup, and the empty-room sweep (R-09)."""

from __future__ import annotations

import asyncio

import pytest

from app.game.clock import FakeClock
from app.game.errors import GameError
from app.game.events import Join
from app.rooms.connection import Connection
from app.rooms.registry import CODE_ALPHABET, CODE_LENGTH, InProcessRegistry
from app.rooms.room import EMPTY_ROOM_TTL_S
from tests.rooms.conftest import StubSocket


async def settle(n: int = 20) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def test_create_room_mints_a_wellformed_code() -> None:
    clock = FakeClock()
    registry = InProcessRegistry(clock)
    room = registry.create_room()
    try:
        assert len(room.code) == CODE_LENGTH
        assert all(c in CODE_ALPHABET for c in room.code)
        assert registry.get(room.code) is room
        assert registry.get("ZZZZ") is None or room.code == "ZZZZ"
    finally:
        room._task.cancel()  # type: ignore[union-attr]
        await settle()


async def test_unclaimed_room_expires_and_is_forgotten() -> None:
    clock = FakeClock()
    registry = InProcessRegistry(clock)
    room = registry.create_room()
    await settle()

    clock.advance(EMPTY_ROOM_TTL_S + 1)
    await settle()

    assert room.stopped
    assert registry.get(room.code) is None
    assert len(registry) == 0


async def test_join_before_ttl_defuses_the_sweep() -> None:  # R-09
    clock = FakeClock()
    registry = InProcessRegistry(clock)
    room = registry.create_room()
    await settle()

    # Someone shows up just before the sweep would have fired...
    clock.advance(EMPTY_ROOM_TTL_S - 1)
    conn = Connection("p0", StubSocket())
    room.attach(conn)
    room.inbox.put_nowait(Join(player_id="p0", name="p0"))
    await settle()

    # ...so when the original sweep moment passes, the epoch fence holds.
    clock.advance(10)
    await settle()
    assert not room.stopped
    assert registry.get(room.code) is room

    room._task.cancel()  # type: ignore[union-attr]
    await settle()


async def test_dispatch_to_unknown_room_raises() -> None:
    registry = InProcessRegistry(FakeClock())
    with pytest.raises(GameError) as exc:
        await registry.dispatch("XXXX", Join(player_id="p", name="p"))
    assert exc.value.code == "room_not_found"
