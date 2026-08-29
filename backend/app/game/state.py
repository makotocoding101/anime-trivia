"""Ephemeral room state — the single-writer object (invariants 1-2).

Everything here is a plain dataclass mutated only by transition(). Nothing in
app/game/ imports anything framework-shaped; tests/game/test_purity.py
enforces the boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto

PlayerId = str
"""Opaque id minted by the connection layer; also appears in wire payloads."""

MAX_PLAYERS = 8


class Phase(Enum):
    LOBBY = auto()
    INTRO = auto()  # fixed-length breather; media preload slot (§02)
    QUESTION_OPEN = auto()  # the only phase that accepts answers
    LOCKED = auto()  # scores commit here; never dwelled (see transition)
    REVEAL = auto()  # the only phase whose events carry the answer key
    GAME_OVER = auto()


class ConnState(Enum):
    LIVE = auto()
    DROPPED = auto()  # socket gone, seat and score held for the grace window
    GONE = auto()  # left/kicked/reaped — out of the game for good


@dataclass(slots=True, frozen=True)
class Option:
    id: int  # per-question ordinal in M1; becomes the DB option id in M4
    label: str


@dataclass(slots=True, frozen=True)
class LoadedQuestion:
    id: int
    kind: str  # 'text' | 'image' — audio is out of scope for v1
    prompt: str
    options: tuple[Option, ...]
    correct_option_id: int  # never serialised before reveal (R-12)
    difficulty: int  # 1..3
    media_ref: str | None = None  # opaque key, never a descriptive name (R-12)


@dataclass(slots=True, frozen=True)
class GameConfig:
    question_count: int = 10
    seconds_per_question: float = 20.0
    intro_seconds: float = 3.0
    reveal_seconds: float = 5.0
    grace_seconds: float = 0.35  # fairness knob, §03 — tune from data


@dataclass(slots=True, frozen=True)
class Answer:
    option_id: int
    elapsed_ms: int  # from server receive time, clamped to the deadline (R-13)


@dataclass(slots=True)
class Player:
    id: PlayerId
    name: str
    seat: int  # join order; host migration picks the lowest live seat (R-16)
    score: int = 0
    streak: int = 0
    total_elapsed_ms: int = 0  # tiebreak key (§07); timeouts add the full limit
    conn: ConnState = ConnState.LIVE
    dropped_at: float | None = None  # doubles as the reaper's fencing token
    spectating_until: int = 0  # round_seq before which this player watches (R-10)
    is_host: bool = False
    ready: bool = False


@dataclass(slots=True)
class Round:
    index: int  # 0-based position in the deck
    question: LoadedQuestion
    opened_at: float  # monotonic
    deadline: float  # monotonic — THE authority on lateness (R-04)
    answers: dict[PlayerId, Answer] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class RoundRecord:
    """History of a completed round, kept for game_over and M4 persistence."""

    index: int
    question_id: int
    correct_option_id: int
    # player -> (option_id or None for no answer, elapsed_ms, points awarded)
    entries: dict[PlayerId, tuple[int | None, int, int]]


@dataclass(slots=True)
class RoomState:
    code: str
    phase: Phase = Phase.LOBBY
    seq: int = 0  # event counter, bumped by the room task's dispatch (M2)
    round_seq: int = 0  # fencing token; monotonic for the room's whole life (R-03)
    players: dict[PlayerId, Player] = field(default_factory=dict)
    deck: list[LoadedQuestion] = field(default_factory=list)
    config: GameConfig | None = None
    round: Round | None = None
    round_index: int = -1  # index of the round currently in play (or just played)
    history: list[RoundRecord] = field(default_factory=list)
    phase_deadline: float | None = None  # when the room task should enqueue the
    # phase-timeout command for the current phase; fenced by round_seq
    next_seat: int = 0

    # -- queries used by transition() and the room layer ---------------------

    def live_players(self) -> list[Player]:
        return [p for p in self.players.values() if p.conn is ConnState.LIVE]

    def eligible(self) -> list[Player]:
        """Players who may answer the current round: live and not spectating.

        DROPPED players are excluded so a dead socket cannot hold the room
        hostage until the deadline (§08).
        """
        return [
            p
            for p in self.players.values()
            if p.conn is ConnState.LIVE and p.spectating_until <= self.round_seq
        ]

    def seated(self) -> list[Player]:
        """Everyone still in the game (live or dropped), spectators excluded."""
        return [
            p
            for p in self.players.values()
            if p.conn is not ConnState.GONE and p.spectating_until <= self.round_seq
        ]

    def host(self) -> Player | None:
        for p in self.players.values():
            if p.is_host:
                return p
        return None
