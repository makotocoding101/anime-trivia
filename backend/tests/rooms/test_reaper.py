"""The grace reaper at the actor level, under deterministic time: the room
schedules it on every drop, and the dropped_at fence keeps stale ones inert
(§08 — same pattern as R-03)."""

from __future__ import annotations

from app.game.events import Disconnect, ReconnectPlayer
from app.game.state import ConnState
from tests.rooms.conftest import Harness

GRACE = 60.0  # RECONNECT_GRACE_S — rooms in the harness use the default


async def drop(h: Harness, pid: str) -> None:
    """What the ws handler's teardown does: detach the socket, report the
    disconnect."""
    conn = h.room.conns[pid]
    await h.room.detach(conn)
    h.room.inbox.put_nowait(Disconnect(player_id=pid))
    await h.settle()


async def test_grace_expiry_reaps_and_migrates_host(harness: Harness) -> None:
    s1 = await harness.connect("p1")
    await harness.connect("p0")  # seat 1 — but p1 joined first and is host
    await drop(harness, "p1")
    assert harness.room.state.players["p1"].conn is ConnState.DROPPED
    assert harness.room.state.players["p1"].is_host  # blip does not shuffle control

    await harness.advance(GRACE + 0.1)
    p1 = harness.room.state.players["p1"]
    assert p1.conn is ConnState.GONE
    assert not p1.is_host
    assert harness.room.state.players["p0"].is_host  # R-16, driven by the real reaper
    # The reaped player's socket was already gone; nothing tried to close it.
    assert s1.closed is None


async def test_reconnect_inside_grace_defuses_the_reaper(harness: Harness) -> None:
    await harness.connect("p0")
    await harness.connect("p1")
    await drop(harness, "p1")

    await harness.advance(GRACE / 2)
    harness.room.inbox.put_nowait(ReconnectPlayer(player_id="p1"))
    await harness.settle()
    assert harness.room.state.players["p1"].conn is ConnState.LIVE

    # The original reaper still fires — into the fence.
    await harness.advance(GRACE)
    assert harness.room.state.players["p1"].conn is ConnState.LIVE


async def test_redrop_gets_its_own_fresh_grace(harness: Harness) -> None:
    await harness.connect("p0")
    await harness.connect("p1")
    await drop(harness, "p1")

    await harness.advance(GRACE - 1)
    harness.room.inbox.put_nowait(ReconnectPlayer(player_id="p1"))
    await harness.settle()
    harness.room.inbox.put_nowait(Disconnect(player_id="p1"))  # drops again at once
    await harness.settle()

    # The first reaper's moment passes: stale, fenced, inert.
    await harness.advance(2)
    assert harness.room.state.players["p1"].conn is ConnState.DROPPED
    # The second drop's own grace expires: now the seat goes.
    await harness.advance(GRACE)
    assert harness.room.state.players["p1"].conn is ConnState.GONE


async def test_rejected_resume_closes_the_socket(harness: Harness) -> None:
    from app.game.events import Leave

    await harness.connect("p0")
    await harness.connect("p1")
    harness.room.inbox.put_nowait(Leave(player_id="p1"))  # seat gone for good
    await harness.settle()

    # A resume for the expired seat arrives on a fresh socket — exactly what
    # the ws layer does: attach, then enqueue ReconnectPlayer.
    ghost = await harness.connect("p1", join=False)
    harness.room.inbox.put_nowait(ReconnectPlayer(player_id="p1"))
    await harness.settle()
    assert ghost.closed is not None
    assert ghost.closed[1] == "seat_expired"
    assert "p1" not in harness.room.conns
