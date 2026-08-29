"""The room's game-end persistence hook, proven against a fake store — no
database needed to pin the semantics: exactly one record per finished game,
built before a rematch can clear the history it reads."""

from __future__ import annotations

import asyncio

from app.game.events import Rematch, RevealElapsed, StartGame, SubmitAnswer
from app.game.state import GameConfig, LoadedQuestion, Phase
from app.store import GameRecord, ModeInfo
from tests.game.conftest import make_deck
from tests.rooms.conftest import Harness

CONFIG = GameConfig(question_count=1, seconds_per_question=20.0)


class RecordingStore:
    def __init__(self) -> None:
        self.records: list[GameRecord] = []
        self.record_started = asyncio.Event()

    async def list_modes(self) -> list[ModeInfo]:
        return []

    async def load_deck(self, mode_id: int) -> tuple[tuple[LoadedQuestion, ...], GameConfig]:
        raise NotImplementedError  # the room never loads decks itself

    async def record_game(self, record: GameRecord) -> None:
        self.records.append(record)
        self.record_started.set()


async def play_one_round_game(h: Harness) -> None:
    await h.connect("p0")
    await h.connect("p1")
    h.room.inbox.put_nowait(
        StartGame(player_id="p0", deck=make_deck(1), config=CONFIG, mode_id=7, game_id="game-1")
    )
    await h.settle()
    await h.advance(3.0)  # intro -> question
    for pid, option in (("p0", 0), ("p1", 1)):
        h.room.inbox.put_nowait(
            SubmitAnswer(player_id=pid, round_seq=h.room.state.round_seq, option_id=option)
        )
    await h.settle()  # both in -> early advance to REVEAL
    h.room.inbox.put_nowait(RevealElapsed(round_seq=h.room.state.round_seq))
    await h.settle()
    assert h.room.state.phase is Phase.GAME_OVER


async def test_game_over_records_exactly_once(harness: Harness) -> None:
    store = RecordingStore()
    harness.room._store = store
    await play_one_round_game(harness)
    await asyncio.wait_for(store.record_started.wait(), timeout=1)

    assert len(store.records) == 1
    record = store.records[0]
    assert record.game_id == "game-1"
    assert record.mode_id == 7
    assert record.room_code == "TEST"
    assert record.question_count == 1
    assert len(record.history) == 1
    entries = record.history[0].entries
    assert entries["p0"][0] == 0 and entries["p1"][0] == 1
    ranks = {row.player_id: row.final_rank for row in record.standings}
    assert ranks == {"p0": 1, "p1": 2}  # p0 answered correctly
    assert record.started_at <= record.ended_at


async def test_rematch_after_game_over_does_not_rerecord(harness: Harness) -> None:
    store = RecordingStore()
    harness.room._store = store
    await play_one_round_game(harness)
    await asyncio.wait_for(store.record_started.wait(), timeout=1)
    harness.room.inbox.put_nowait(Rematch(player_id="p0"))
    await harness.settle()
    assert harness.room.state.phase is Phase.LOBBY
    assert len(store.records) == 1  # meta was consumed exactly once


async def test_storeless_room_finishes_games_without_persistence(harness: Harness) -> None:
    # The default harness room has no store: the game must complete cleanly
    # and schedule no persistence work at all.
    assert harness.room._store is None
    await play_one_round_game(harness)
    assert harness.room._persist_tasks == set()
