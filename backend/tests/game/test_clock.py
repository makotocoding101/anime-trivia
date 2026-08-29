"""FakeClock semantics — the thing that makes M2's timer tests deterministic."""

from __future__ import annotations

import asyncio

import pytest

from app.game.clock import FakeClock, RealClock


def test_fake_clock_advances_and_never_rewinds() -> None:
    clock = FakeClock(start=10.0)
    clock.advance(5.0)
    assert clock.now() == 15.0
    with pytest.raises(ValueError):
        clock.advance(-1.0)


def test_fake_sleep_wakes_only_at_deadline() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        woke_at: list[float] = []

        async def sleeper() -> None:
            await clock.sleep_until(5.0)
            woke_at.append(clock.now())

        task = asyncio.create_task(sleeper())
        await asyncio.sleep(0)  # let the sleeper register its waiter
        clock.advance(4.9)
        await asyncio.sleep(0)
        assert woke_at == []  # not yet — 4.9 < 5.0
        clock.advance(0.2)
        await task
        # Woke late (5.1-ish, not 5.0), as real sleeps may (R-04).
        assert woke_at == [pytest.approx(5.1)]

    asyncio.run(scenario())


def test_fake_sleep_past_deadline_returns_immediately() -> None:
    async def scenario() -> None:
        clock = FakeClock(start=100.0)
        await asyncio.wait_for(clock.sleep_until(50.0), timeout=1.0)

    asyncio.run(scenario())


def test_fake_clock_wakes_multiple_sleepers_in_one_advance() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        woke: list[str] = []

        async def sleeper(name: str, deadline: float) -> None:
            await clock.sleep_until(deadline)
            woke.append(name)

        tasks = [
            asyncio.create_task(sleeper("a", 1.0)),
            asyncio.create_task(sleeper("b", 2.0)),
            asyncio.create_task(sleeper("c", 9.0)),
        ]
        await asyncio.sleep(0)
        clock.advance(2.5)
        await asyncio.gather(tasks[0], tasks[1])
        assert set(woke) == {"a", "b"}
        clock.advance(10.0)
        await tasks[2]

    asyncio.run(scenario())


def test_real_clock_is_monotonic_and_returns_promptly_when_late() -> None:
    async def scenario() -> None:
        clock = RealClock()
        t0 = clock.now()
        # A deadline already in the past must not block (R-04's premise: sleeps
        # can be late; they must never be the authority anyway).
        await asyncio.wait_for(clock.sleep_until(t0 - 5.0), timeout=1.0)
        assert clock.now() >= t0

    asyncio.run(scenario())
