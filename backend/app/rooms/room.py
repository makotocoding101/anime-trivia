"""The room actor (spec §01, Fig. 1): one task, one inbox, single writer.

Everything that can happen to a room — client commands, timer expiries, the
empty-room sweep — arrives as a message on the inbox, and run() is the only
coroutine that touches self.state. The return path never blocks the actor:
dispatch is put_nowait into bounded per-connection queues (R-05).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.game.clock import Clock
from app.game.errors import GameError
from app.game.events import (
    Command,
    DeadlineReached,
    Disconnect,
    Error,
    Event,
    IntroElapsed,
    PlayerLeft,
    RevealElapsed,
)
from app.game.state import Phase, PlayerId, RoomState
from app.game.transition import transition
from app.rooms.connection import Connection
from app.schemas.wire import WireEncoder

logger = logging.getLogger(__name__)

EMPTY_ROOM_TTL_S = 300.0
"""How long a room may sit with zero connections before the sweep reaps it —
covers both freshly created rooms nobody joined and abandoned games."""

# Which command a phase's timeout enqueues. The timer never mutates state; it
# is just another sender on the inbox (invariant 1).
_PHASE_TIMEOUT: dict[Phase, type[IntroElapsed] | type[DeadlineReached] | type[RevealElapsed]] = {
    Phase.INTRO: IntroElapsed,
    Phase.QUESTION_OPEN: DeadlineReached,
    Phase.REVEAL: RevealElapsed,
}

# A PlayerLeft with one of these reasons means the seat is gone for good, so
# the room also closes the socket. "dropped" is absent on purpose: that
# socket is already dead, and the player may reconnect.
_CLOSING_REASONS = frozenset({"kicked", "left", "timeout"})


@dataclass(slots=True, frozen=True)
class _SweepCheck:
    """Internal: re-check occupancy at execution time, on the inbox, so a
    join and the sweep serialize through the same queue (R-09)."""

    epoch: int


class RoomClosed(Exception):
    """Raised on attach() to a room whose task has already stopped."""


class Room:
    def __init__(
        self,
        code: str,
        clock: Clock,
        on_stopped: Callable[[Room], None] | None = None,
    ) -> None:
        self.code = code
        self.clock = clock
        self.state = RoomState(code=code)
        self.inbox: asyncio.Queue[Command | _SweepCheck] = asyncio.Queue()
        self.conns: dict[PlayerId, Connection] = {}
        self.stopped = False
        self._on_stopped = on_stopped
        self._encoder = WireEncoder(clock)
        self._timer: asyncio.Task[None] | None = None
        self._task: asyncio.Task[None] | None = None
        self._sweeper: asyncio.Task[None] | None = None
        self._epoch = 0  # bumped on every attach; fences _SweepCheck

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._task = asyncio.create_task(self.run(), name=f"room:{self.code}")
        self._schedule_sweep()  # rooms are born empty; unclaimed ones expire

    async def run(self) -> None:
        """The single writer. The only coroutine permitted to mutate state."""
        try:
            while True:
                cmd = await self.inbox.get()
                if isinstance(cmd, _SweepCheck):
                    if cmd.epoch == self._epoch and not self.conns:
                        break  # nobody home, and nobody arrived since scheduling
                    continue
                try:
                    events = transition(self.state, cmd, self.clock.now())
                except GameError as exc:
                    events = _rejection(cmd, exc)
                except Exception:
                    # A domain bug must not kill the room for everyone (§05's
                    # schema discipline, applied to ourselves). Log loudly.
                    logger.exception("transition failed on %r in room %s", cmd, self.code)
                    continue
                self._dispatch(events)
                self._close_departed(events)
                self._reschedule_timer()
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        self.stopped = True
        if self._timer is not None:
            self._timer.cancel()
        for conn in list(self.conns.values()):
            conn.close_soon("room_closed")
        self.conns.clear()
        if self._on_stopped is not None:
            self._on_stopped(self)
        logger.info("room %s stopped", self.code)

    # ------------------------------------------------------------------
    # Connections (called from ws handlers — same loop, never another thread)
    # ------------------------------------------------------------------

    def attach(self, conn: Connection) -> None:
        if self.stopped:
            raise RoomClosed(self.code)
        self._epoch += 1  # any pending sweep is now stale
        old = self.conns.get(conn.player_id)
        if old is not None:
            old.close_soon("left")  # a second socket for one player replaces the first
        self.conns[conn.player_id] = conn
        conn.start()

    async def detach(self, conn: Connection) -> None:
        if self.conns.get(conn.player_id) is conn:
            del self.conns[conn.player_id]
        await conn.stop()
        if not self.conns and not self.stopped:
            self._schedule_sweep()

    def _schedule_sweep(self) -> None:
        epoch = self._epoch

        async def waiter() -> None:
            await self.clock.sleep_until(self.clock.now() + EMPTY_ROOM_TTL_S)
            if not self.stopped:
                self.inbox.put_nowait(_SweepCheck(epoch=epoch))

        self._sweeper = asyncio.create_task(waiter(), name=f"sweep:{self.code}")

    # ------------------------------------------------------------------
    # Fan-out (never awaits — R-05) and timers (fenced — R-03)
    # ------------------------------------------------------------------

    def _dispatch(self, events: list[Event]) -> None:
        for event in events:
            to: PlayerId | None = getattr(event, "to", None)
            if to is None:
                # seq counts broadcasts only, so the stream every client sees
                # is dense; targeted frames carry the current value (wire.py).
                self.state.seq += 1
                frame = self._encoder.encode(event, seq=self.state.seq)
                targets = list(self.conns.values())  # snapshot the set (R-06)
            else:
                frame = self._encoder.encode(event, seq=self.state.seq)
                target = self.conns.get(to)
                targets = [target] if target is not None else []
            for conn in targets:
                if not conn.try_send(frame):
                    # This client cannot keep up. Drop it, never wait for it
                    # (R-05); the drop flows through the inbox like any other.
                    conn.close_soon("slow_consumer")
                    self.inbox.put_nowait(Disconnect(player_id=conn.player_id))

    def _close_departed(self, events: list[Event]) -> None:
        for event in events:
            if isinstance(event, PlayerLeft) and event.reason in _CLOSING_REASONS:
                conn = self.conns.pop(event.player_id, None)
                if conn is not None:
                    conn.close_soon(event.reason)
                    if not self.conns and not self.stopped:
                        self._schedule_sweep()

    def _reschedule_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()  # best-effort, not load-bearing (§03)
            self._timer = None
        deadline = self.state.phase_deadline
        command_cls = _PHASE_TIMEOUT.get(self.state.phase)
        if deadline is None or command_cls is None:
            return
        self._timer = asyncio.create_task(
            self._fire_at(deadline, self.state.round_seq, command_cls),
            name=f"timer:{self.code}",
        )

    async def _fire_at(
        self,
        deadline: float,
        round_seq: int,
        command_cls: type[IntroElapsed] | type[DeadlineReached] | type[RevealElapsed],
    ) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            await self.clock.sleep_until(deadline)
            if round_seq != self.state.round_seq:
                return  # stale generation — the round moved on without us (R-03)
            self.inbox.put_nowait(command_cls(round_seq=round_seq))


def _rejection(cmd: Command | _SweepCheck, exc: GameError) -> list[Event]:
    player_id = getattr(cmd, "player_id", None)
    if player_id is None:
        return []  # a rejected clock command has nobody to tell
    return [Error(to=player_id, code=exc.code, cid=getattr(cmd, "cid", None))]
