"""Property tests over command interleavings (spec §11, layer 3).

Hypothesis generates orderings nobody would write by hand — a join landing
between a disconnect and its reap, a stale deadline racing a rematch — and
asserts the invariants that must hold under every one of them.
"""

from __future__ import annotations

from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from app.game.errors import GameError
from app.game.events import (
    DeadlineReached,
    Disconnect,
    IntroElapsed,
    Join,
    Leave,
    QuestionOpened,
    ReapPlayer,
    ReconnectPlayer,
    Rematch,
    RevealElapsed,
    StartGame,
    SubmitAnswer,
)
from app.game.state import Phase, RoomState
from app.game.transition import transition
from tests.game.conftest import CONFIG, make_deck

PIDS = [f"p{i}" for i in range(5)]


@dataclass(frozen=True)
class Op:
    kind: str
    pid: int  # index into PIDS
    arg: int  # option id / round_seq offset / etc.
    dt: float  # time to advance before applying


ops_strategy = st.lists(
    st.builds(
        Op,
        kind=st.sampled_from(
            [
                "join",
                "disconnect",
                "reconnect",
                "leave",
                "reap",
                "reap_stale",
                "start",
                "submit",
                "submit_stale",
                "intro",
                "deadline",
                "deadline_stale",
                "reveal_done",
                "rematch",
            ]
        ),
        pid=st.integers(min_value=0, max_value=len(PIDS) - 1),
        arg=st.integers(min_value=0, max_value=4),
        dt=st.floats(min_value=0.0, max_value=30.0, allow_nan=False),
    ),
    max_size=80,
)


def build_command(op: Op, state: RoomState, now: float) -> object:
    pid = PIDS[op.pid]
    seq = state.round_seq
    match op.kind:
        case "join":
            return Join(player_id=pid, name=pid)
        case "disconnect":
            return Disconnect(player_id=pid)
        case "reconnect":
            return ReconnectPlayer(player_id=pid)
        case "leave":
            return Leave(player_id=pid)
        case "reap":
            player = state.players.get(pid)
            dropped_at = player.dropped_at if player and player.dropped_at else now
            return ReapPlayer(player_id=pid, dropped_at=dropped_at)
        case "reap_stale":
            return ReapPlayer(player_id=pid, dropped_at=now - 999.0)
        case "start":
            return StartGame(player_id=pid, deck=make_deck(3), config=CONFIG)
        case "submit":
            return SubmitAnswer(player_id=pid, round_seq=seq, option_id=op.arg)
        case "submit_stale":
            return SubmitAnswer(player_id=pid, round_seq=max(0, seq - 1), option_id=op.arg)
        case "intro":
            return IntroElapsed(round_seq=seq)
        case "deadline":
            return DeadlineReached(round_seq=seq)
        case "deadline_stale":
            return DeadlineReached(round_seq=max(0, seq - 1))
        case "reveal_done":
            return RevealElapsed(round_seq=seq)
        case "rematch":
            return Rematch(player_id=pid)
    raise AssertionError(op.kind)


@settings(max_examples=200, deadline=None)
@given(ops=ops_strategy)
def test_invariants_hold_under_arbitrary_interleavings(ops: list[Op]) -> None:
    state = RoomState(code="PROP")
    now = 1000.0
    prev_scores: dict[str, int] = {}
    prev_round_seq = 0

    for op in ops:
        now += op.dt
        cmd = build_command(op, state, now)
        try:
            events = transition(state, cmd, now)
        except GameError:
            events = []  # rejection is a valid outcome; corruption is not

        # -- invariants, after every single command --------------------------

        # round_seq is monotone for the room's whole life (fencing token).
        assert state.round_seq >= prev_round_seq
        prev_round_seq = state.round_seq

        # LOCKED is never dwelled: commands always leave a settled phase.
        assert state.phase is not Phase.LOCKED

        # No score ever decreases (except the rematch reset, which re-baselines).
        if isinstance(cmd, Rematch) and state.phase is Phase.LOBBY:
            prev_scores = {}
        for pid, player in state.players.items():
            assert player.score >= prev_scores.get(pid, 0), "score decreased"
            assert player.streak >= 0
            prev_scores[pid] = player.score

        # Answers only ever belong to players the room knows about.
        if state.round is not None:
            assert set(state.round.answers) <= set(state.players)
            assert state.round.opened_at <= state.round.deadline

        # A question payload never leaks correctness (R-12): the event type
        # carrying the prompt has no correctness field at all.
        for event in events:
            if isinstance(event, QuestionOpened):
                assert not hasattr(event, "correct_option_id")

        # No player is ever scored twice for one round.
        for record in state.history:
            assert len(record.entries) == len(set(record.entries))

    # After the dust settles: history is one record per completed round, in order.
    indices = [r.index for r in state.history]
    if state.phase in (Phase.INTRO, Phase.QUESTION_OPEN, Phase.REVEAL, Phase.GAME_OVER):
        assert indices == sorted(indices)
