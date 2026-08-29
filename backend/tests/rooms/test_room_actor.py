"""The actor under deterministic time: single writer, fenced timers, dense
broadcast seq, and the R-05 slow-consumer drop."""

from __future__ import annotations

from app.game.events import StartGame, SubmitAnswer
from app.game.state import ConnState, GameConfig, Phase
from tests.game.conftest import make_deck
from tests.rooms.conftest import Harness

CONFIG = GameConfig(
    question_count=2,
    seconds_per_question=20.0,
    intro_seconds=3.0,
    reveal_seconds=5.0,
    grace_seconds=0.35,
)


def submit(h: Harness, pid: str, option: int = 0) -> None:
    h.room.inbox.put_nowait(
        SubmitAnswer(player_id=pid, round_seq=h.room.state.round_seq, option_id=option)
    )


async def start_game(h: Harness, host: str = "p0", deck_n: int = 2) -> None:
    h.room.inbox.put_nowait(StartGame(player_id=host, deck=make_deck(deck_n), config=CONFIG))
    await h.settle()


async def test_join_delivers_targeted_snapshot_and_broadcasts(harness: Harness) -> None:
    s0 = await harness.connect("p0")
    s1 = await harness.connect("p1")

    assert len(s0.frames("snapshot")) == 1  # own snapshot only
    assert len(s1.frames("snapshot")) == 1
    assert s1.frames("snapshot")[0]["data"]["you"] == "p1"
    # p0 saw p1 arrive; p1 must not see its own join replayed as history.
    assert [f["data"]["player_id"] for f in s0.frames("player_joined")] == ["p0", "p1"]


async def test_timer_drives_the_round_without_any_sleep(harness: Harness) -> None:
    s0 = await harness.connect("p0")
    await harness.connect("p1")
    await start_game(harness)
    assert harness.room.state.phase is Phase.INTRO
    assert s0.frames("question") == []

    await harness.advance(3.0)  # intro timer fires -> question opens
    assert harness.room.state.phase is Phase.QUESTION_OPEN
    question = s0.frames("question")[0]["data"]
    assert question["prompt"].startswith("Q1")
    assert "correct" not in str(question)  # R-12 at the wire

    await harness.advance(20.0 + 0.35)  # deadline + grace -> locked & revealed
    assert harness.room.state.phase is Phase.REVEAL
    assert len(s0.frames("reveal")) == 1

    await harness.advance(5.0)  # reveal timer -> next intro
    assert harness.room.state.phase is Phase.INTRO
    assert harness.room.state.round_seq == 2


async def test_early_advance_cancels_or_fences_the_deadline_timer(harness: Harness) -> None:
    s0 = await harness.connect("p0")
    await harness.connect("p1")
    await start_game(harness)
    await harness.advance(3.0)

    submit(harness, "p0")
    submit(harness, "p1")
    await harness.settle()
    assert harness.room.state.phase is Phase.REVEAL
    reveals_before = len(s0.frames("reveal"))

    # Let the (cancelled or stale) deadline timer's moment pass anyway: the
    # fence must make it a no-op, not a second scoring pass (R-03).
    await harness.advance(20.0 + 0.35)
    assert len(s0.frames("reveal")) == reveals_before
    assert len(harness.room.state.history) == 1


async def test_broadcast_seq_is_dense_per_client(harness: Harness) -> None:
    from app.schemas.wire import TARGETED_TYPES

    s0 = await harness.connect("p0")
    await harness.connect("p1")
    await start_game(harness)
    await harness.advance(3.0)
    submit(harness, "p0")
    submit(harness, "p1")
    await harness.settle()

    broadcast_seqs = [f["seq"] for f in s0.sent if f["type"] not in TARGETED_TYPES]
    assert broadcast_seqs == list(
        range(broadcast_seqs[0], broadcast_seqs[0] + len(broadcast_seqs))
    ), "gap or duplicate in the broadcast stream — snapshot-resync would misfire (R-11)"


async def test_slow_consumer_is_dropped_not_waited_on(harness: Harness) -> None:  # R-05
    healthy = await harness.connect("p0")
    # p1's socket never completes a send and its outbox holds only 2 frames.
    wedged = await harness.connect("p1", wedge=True, outbox_size=2)

    # A burst of broadcasts: start_game -> phase events, question, etc.
    await start_game(harness)
    await harness.advance(3.0)
    submit(harness, "p0")
    await harness.settle()

    # The wedged client is gone; the room and the healthy client are fine.
    assert wedged.closed is not None and wedged.closed[1] == "slow_consumer"
    assert harness.room.state.players["p1"].conn is ConnState.DROPPED
    assert not harness.room.stopped
    assert healthy.frames("question")  # the round kept moving for everyone else
    # And the drop excluded p1 from "all answered": p0 alone completed it.
    assert harness.room.state.phase is Phase.REVEAL


async def test_stopped_room_rejects_attach(harness: Harness) -> None:
    import pytest

    from app.rooms.connection import Connection
    from app.rooms.room import RoomClosed
    from tests.rooms.conftest import StubSocket

    await harness.connect("p0")
    harness.room._task.cancel()  # type: ignore[union-attr]
    await harness.settle()
    assert harness.room.stopped
    with pytest.raises(RoomClosed):
        harness.room.attach(Connection("p9", StubSocket()))


async def test_rejected_command_bounces_error_to_sender_only(harness: Harness) -> None:
    s0 = await harness.connect("p0")
    s1 = await harness.connect("p1")
    submit(harness, "p0")  # no round open -> round_closed
    await harness.settle()
    errors = s0.frames("error")
    assert [e["data"]["code"] for e in errors] == ["round_closed"]
    assert s1.frames("error") == []
