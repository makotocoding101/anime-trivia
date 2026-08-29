"""Shared harness for domain tests.

Everything here is synchronous: transition() takes `now` as an argument, so a
Driver with a float and an advance() method replaces clocks, sleeps and
sockets entirely (spec §11).
"""

from __future__ import annotations

import pytest

from app.game.events import Command, Event, IntroElapsed, Join, StartGame
from app.game.state import GameConfig, LoadedQuestion, Option, Phase, RoomState
from app.game.transition import transition

CONFIG = GameConfig(
    question_count=10,
    seconds_per_question=20.0,
    intro_seconds=3.0,
    reveal_seconds=5.0,
    grace_seconds=0.35,
)


def make_question(
    qid: int = 1, difficulty: int = 1, correct: int = 0, n_options: int = 4
) -> LoadedQuestion:
    return LoadedQuestion(
        id=qid,
        kind="text",
        prompt=f"Q{qid}: pick option {correct}",
        options=tuple(Option(id=i, label=f"Option {i}") for i in range(n_options)),
        correct_option_id=correct,
        difficulty=difficulty,
    )


def make_deck(n: int, difficulty: int = 1) -> tuple[LoadedQuestion, ...]:
    return tuple(make_question(qid=i + 1, difficulty=difficulty) for i in range(n))


class Driver:
    """A room plus a hand-cranked clock."""

    def __init__(self) -> None:
        self.state = RoomState(code="TEST")
        self.now = 100.0

    def do(self, cmd: Command) -> list[Event]:
        return transition(self.state, cmd, self.now)

    def advance(self, dt: float) -> None:
        self.now += dt

    # -- shortcuts used all over the suite ---------------------------------

    def join(self, *pids: str) -> None:
        for pid in pids:
            self.do(Join(player_id=pid, name=pid))

    def start(self, host: str = "p0", deck_n: int = 3, difficulty: int = 1) -> None:
        self.do(StartGame(player_id=host, deck=make_deck(deck_n, difficulty), config=CONFIG))

    def open_question(self) -> list[Event]:
        """INTRO -> QUESTION_OPEN by firing the intro timer at its deadline."""
        assert self.state.phase is Phase.INTRO
        assert self.state.phase_deadline is not None
        self.now = max(self.now, self.state.phase_deadline)
        return self.do(IntroElapsed(round_seq=self.state.round_seq))


@pytest.fixture
def driver() -> Driver:
    return Driver()


@pytest.fixture
def open_room() -> Driver:
    """Three players, game started, first question open."""
    d = Driver()
    d.join("p0", "p1", "p2")
    d.start()
    d.open_question()
    assert d.state.phase is Phase.QUESTION_OPEN
    return d


def events_of(events: list[Event], kind: type) -> list[Event]:
    return [e for e in events if isinstance(e, kind)]


def one_event(events: list[Event], kind: type) -> Event:
    found = events_of(events, kind)
    assert len(found) == 1, f"expected exactly one {kind.__name__}, got {len(found)}"
    return found[0]
