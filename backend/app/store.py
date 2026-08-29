"""The GameStore seam: where the live game meets durable storage.

The room layer talks to this protocol and nothing below it. Two
implementations exist: DbStore (app/db/store.py — Postgres, the real thing
from M4 on) and SeedStore (below — the seed file, no database), which keeps
dev-without-postgres and the fast test path alive.

The boundary discipline (spec §06) lives in the protocol's shape: one call
at game start (load_deck), one at game end (record_game), nothing during a
round.
"""

from __future__ import annotations

import dataclasses
import datetime
import random
from dataclasses import dataclass
from typing import Protocol

from app.game.errors import GameError
from app.game.state import GameConfig, LoadedQuestion, PlayerId
from app.game.state import RoundRecord as RoundRecord


@dataclass(slots=True, frozen=True)
class ModeInfo:
    id: int
    slug: str
    name: str
    blurb: str | None
    question_count: int
    seconds_per_q: int


@dataclass(slots=True, frozen=True)
class StandingRow:
    player_id: PlayerId
    name: str
    final_score: int
    final_rank: int
    total_elapsed_ms: int


@dataclass(slots=True, frozen=True)
class GameRecord:
    """Everything the room layer hands over at game end — one INSERT batch."""

    game_id: str
    room_code: str
    mode_id: int
    started_at: datetime.datetime
    ended_at: datetime.datetime
    question_count: int
    history: tuple[RoundRecord, ...]
    standings: tuple[StandingRow, ...]


class GameStore(Protocol):
    async def list_modes(self) -> list[ModeInfo]: ...

    async def load_deck(self, mode_id: int) -> tuple[tuple[LoadedQuestion, ...], GameConfig]: ...

    async def record_game(self, record: GameRecord) -> None: ...


class SeedStore:
    """File-backed store: the whole bank as one synthetic mode, results
    discarded. What M1-M3 ran on, kept as the no-database fallback."""

    PLACEHOLDER_MODE_ID = 1

    def __init__(self, bank: tuple[LoadedQuestion, ...], base_config: GameConfig) -> None:
        self._bank = bank
        self._base = base_config

    async def list_modes(self) -> list[ModeInfo]:
        count = min(self._base.question_count, len(self._bank))
        return [
            ModeInfo(
                id=self.PLACEHOLDER_MODE_ID,
                slug="placeholder",
                name="Placeholder bank",
                blurb="Every seed question, no database attached.",
                question_count=count,
                seconds_per_q=int(self._base.seconds_per_question),
            )
        ]

    async def load_deck(self, mode_id: int) -> tuple[tuple[LoadedQuestion, ...], GameConfig]:
        if mode_id != self.PLACEHOLDER_MODE_ID:
            raise GameError("mode_not_found")
        count = min(self._base.question_count, len(self._bank))
        deck = tuple(random.Random().sample(self._bank, count))  # R-15: one draw per game
        return deck, dataclasses.replace(self._base, question_count=count)

    async def record_game(self, record: GameRecord) -> None:
        return None  # nowhere durable to put it — by design
