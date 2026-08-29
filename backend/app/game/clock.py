"""Time as a capability.

The domain itself never reads a clock — `transition()` takes `now` as an
argument. This protocol exists for the room layer (M2): timers sleep through
it, so tests drive a FakeClock and a twenty-second round takes microseconds.

This is the one module in app/game/ allowed to import asyncio (the purity
test in tests/game/test_purity.py special-cases it).
"""

from __future__ import annotations

import asyncio
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Monotonic seconds. Never wall-clock — immune to NTP steps (§03)."""
        ...

    async def sleep_until(self, deadline: float) -> None:
        """Return no earlier than `deadline`. May return late; callers must
        treat the stored deadline, not this wake-up, as the authority (R-04)."""
        ...


class RealClock:
    """asyncio's monotonic loop clock."""

    def now(self) -> float:
        return asyncio.get_running_loop().time()

    async def sleep_until(self, deadline: float) -> None:
        delay = deadline - self.now()
        # sleep(0) still yields once, like a real sleep would.
        await asyncio.sleep(delay if delay > 0 else 0)


class FakeClock:
    """Deterministic clock for tests. `advance()` releases due sleepers."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        self._waiters: list[tuple[float, asyncio.Event]] = []

    def now(self) -> float:
        return self._now

    def advance(self, dt: float) -> None:
        if dt < 0:
            raise ValueError("time does not run backwards")
        self._now += dt
        due = [ev for t, ev in self._waiters if t <= self._now]
        self._waiters = [(t, ev) for t, ev in self._waiters if t > self._now]
        for ev in due:
            ev.set()

    async def sleep_until(self, deadline: float) -> None:
        if deadline <= self._now:
            await asyncio.sleep(0)
            return
        ev = asyncio.Event()
        self._waiters.append((deadline, ev))
        await ev.wait()
